# Documento Técnico — Recálculo de costes Clasificado / Despiece

**Proyecto:** odoo14_paasa
**Versión Odoo:** 14.0
**Fecha:** 2026-09-09
**Módulos implicados:** `custom_mrp_descarga`, `custom_mrp_line_cost`
**Ruta de despliegue:** `/opt/odoo14_paasa/extra-addons/avanzosc/mrp-addons/deploy/`

---

## 1. Objetivo

Corregir y optimizar la cadena de costes entre dos tipos de órdenes de producción:

- **Clasificado** (`mrp.production` con `is_deconstruction = True`): proceso de
  despiece/deconstrucción que genera **lotes** (productos intermedios) a partir de una
  materia prima (pollo entero).
- **Despiece** (`mrp.production` con `is_deconstruction = False`): proceso de producción
  normal que **consume** esos lotes (entradas) y genera productos terminados (salidas).

El flujo de costes es:

```
Clasificado ──recalcula precios──▶ actualiza average_price del lote
                                        │
                                        ▼
Despiece (Entradas) ──bring_cost_from_lots──▶ standard_price = lote.average_price
                                        │
                                        ▼
Despiece (Salidas) ──base_price + reparto──▶ coste de los productos terminados
```

---

## 2. Modelo de datos relevante

### 2.1. Campos en `stock.production.lot` (módulo `custom_mrp_descarga`)

- `average_price` (Float, `compute="_compute_average_price"`): precio medio del lote.

  ```python
  @api.depends("move_line_ids.amount", "move_line_ids.qty_done", "move_line_ids.state")
  def _compute_average_price(self):
      for line in self:
          average_price = 0
          clasified = line.move_line_ids.filtered(
              lambda c: c.state == "done"
              and (c.location_dest_id.usage == "internal")
              and (c.location_id.usage != "internal")
          )
          if clasified:
              amount_total = sum(clasified.mapped("amount"))
              qty_done = sum(clasified.mapped("qty_done"))
              if qty_done != 0:
                  average_price = amount_total / qty_done
          line.average_price = average_price
  ```

  **Filtro clave:** solo se consideran los movimientos "de entrada a almacén interno"
  (`location_dest_id.usage == 'internal'` y `location_id.usage != 'internal'`). Esto
  excluye las salidas de los Despieces (que van a Producción) y cualquier movimiento
  interno, quedando únicamente las entradas reales de mercancía valoradas.

### 2.2. Campos en `stock.move.line` (módulo `custom_mrp_line_cost` / `stock_move_line_cost`)

- `base_price` (Float, `compute="_compute_base_price"`, `store=True`): coste base de la línea.
- `applied_price` (Float): precio aplicado.
- `standard_price` (Float): coste.
- `amount` (Float): importe = `standard_price * qty_done`.
- `expense_kg` (Boolean): indica si el coste se calcula por kg (fórmula con coeficiente).

### 2.3. Cálculo de `base_price` en líneas de salida (subproductos)

Para líneas de salida con `byproduct_id`:

```python
elif line.production_id and line.move_id.byproduct_id:
    cost = line.move_id.byproduct_id.cost
    if line.expense_kg:
        entry_same_lots = line.production_id.move_line_ids.filtered(
            lambda c: c.lot_id.name == line.lot_id.name
        )
        if not entry_same_lots or sum(entry_same_lots.mapped("qty_done")) == 0:
            cost = 0
        else:
            entry_cost = sum(entry_same_lots.mapped("amount")) / sum(
                entry_same_lots.mapped("qty_done")
            )
            cost = (line.month_cost + entry_cost) * (
                line.move_id.byproduct_id.coefficient
            )
```

**Observación:** el filtro `entry_same_lots` usa `lot_id.name` (identificador de "saca"
o lote), no el producto. Por tanto, agrupa todas las entradas del mismo lote
independientemente del producto.

---

## 3. Problema detectado

### 3.1. Incoherencia de costes entre Clasificado y Despiece

Al aplicar la acción **"Recalcular Precio aplicado en Clasificados"** se actualizaban los
precios de las líneas del Clasificado, pero el `average_price` del lote era un campo
**NO almacenado** (`store=False`), que se recalculaba en cada lectura. Además, la acción
no forzaba el recálculo del lote.

En el **Despiece**, las líneas de entrada conservaban un `standard_price` **obsoleto**
(ej. `475,05 €/kg` frente a un precio medio de lote correcto de `2,08 €/kg`), porque:

1. El botón **"Recalcular costes"** (`button_calculate_costs`) solo recalcula las
   **salidas**, y éstas dependen de las entradas (que seguían obsoletas).
2. La acción **"Recalcular coste OF"** (`action_recalculate_production_cost`) sí recalcaba
   correctamente, pero estaba condicionada por un filtro:
   ```python
   lambda c: not c.production_cost_date and c.state == "done" and c.recalculate_cost
   ```
   lo que hacía que, tras una primera ejecución (que sella `production_cost_date`), las
   siguientes no volvieran a procesar el Despiece, y que otros Despieces con
   `production_cost_date` ya rellena quedaran excluidos.

### 3.2. Bug en el reparto de costes de `button_calculate_costs`

En la rama no-deconstrucción, el reparto de la diferencia de coste usaba:

```python
i = qty.index(max(qty))
if i:
    max_product = products[i]
```

- `if i:` es falso cuando `i == 0` (producto de mayor cantidad en primera posición),
  por lo que **se saltaba el reparto**.
- Si `qty` estaba vacía, `max(qty)` lanzaba `ValueError`.

### 3.3. Rendimiento (barrido completo de tabla)

La tabla `stock_move_line` tiene ~3,1 millones de filas. El campo `production_id` de
`stock.move.line` **no tenía índice** (a diferencia de `stock.move.production_id`, que sí
lo tiene). Las consultas:

```sql
SELECT ... FROM stock_move_line WHERE production_id = X
```

provocadas por el acceso a `production.move_line_ids` / `finished_move_line_ids`
(campos One2many con `relation_field = production_id`) realizaban un **Seq Scan paralelo**
de ~587 ms cada una (59 consultas ≈ 22 s solo en lecturas).

---

## 4. Cambios implementados

### 4.1. `custom_mrp_descarga/models/stock_production_lot.py`

- `average_price`: se añade `store=True` (deja de ser volátil y pasa a persistir en BD).
- Se añade el método:

  ```python
  def action_recompute_average_price(self):
      for lot in self:
          lot._compute_average_price()
  ```

### 4.2. `custom_mrp_descarga/models/stock_move_line.py`

Se redefine `production_id` con índice:

```python
production_id = fields.Many2one(
    "mrp.production", "Production Order", check_company=True, index=True
)
```

### 4.3. `custom_mrp_line_cost/models/mrp_production.py`

Corrección del reparto:

```python
if qty:
    i = qty.index(max(qty))
    max_product = products[i]
    max_qty = qty[i]
    if max_product and max_qty:
        ...
```

### 4.4. Acción de servidor "Recalcular Precio aplicado en Clasificados" (actualizada)

Se añade al final:

```python
lots = production.move_line_ids.mapped('lot_id').filtered('id')
lots.action_recompute_average_price()
```

---

## 5. Resultados de verificación

### 5.1. Coherencia de costes (Despiece `DESP/02085`)

| Concepto | Antes | Después |
|---|---|---|
| Entrada "POLLO 1ª CUELLO" (`standard_price`) | 475,05 € | **2,08188 €** |
| Salida "MUSLOS DE POLLO" (`base_price`) | 422,12 € | **2,13 €** |
| Total entradas | ~257.978 € | **30.330,59 €** |
| Total salidas | descuadrado | **35.324,36 €** |

La diferencia entradas − salidas (−4.993,77 €) corresponde al **valor añadido** del
Despiece (coste de mes + coeficientes).

### 5.2. Rendimiento (3 despieces: DESP/02084, 02085, 02086)

| Métrica | Antes | Después |
|---|---|---|
| Consulta `WHERE production_id = X` | 587 ms (Seq Scan) | 0,28 ms (Index Scan) |
| Tiempo total recálculo (3 despieces) | ~2-3 min | **13,7 s** |
| Tiempo por despiece | ~30-40 s | ~4,4 s |

El tiempo restante se debe al mecanismo interno de Odoo de recálculo de campos
almacenados dependientes (`average_cost`, `entry_total_amount`, `base_price`, etc.),
que ya no constituye un cuello de botella.

---

## 6. Notas y consideraciones

1. **`average_price` es un promedio global por lote.** Agrega todos los movimientos de
   entrada a almacén interno del lote. Si varios Despieces producen sobre el mismo lote,
   deben recalcularse **todos** para que el precio medio sea coherente.
2. **El backfill SQL (`02_backfill_average_price.sql`) replica la lógica Python** de
   `_compute_average_price`. Si ésta cambia, el SQL debe actualizarse.
3. **Índice persistente.** El índice `stock_move_line_production_id_index` queda definido
   por `index=True` en el módulo, por lo que se recrea en cualquier instalación/actualización.
4. **Idempotencia.** Los scripts de `deploy/` pueden reejecutarse sin efectos adversos.

---

## 7. Despliegue de los ajustes — paso a paso y por orden

> **Requisito previo:** tener acceso a la base de datos de producción
> (`<BD_PRODUCCION>`), al usuario de PostgreSQL y al `odoo.conf` del servidor. Las rutas
> de Odoo y del venv pueden variar según la instalación; ajustarlas en consecuencia.

### Paso 0 — Backup y precaución

1. Realizar un **backup completo** de la base de datos de producción antes de tocar nada.
2. Avisar a los usuarios: la actualización de módulos deja el servicio **sin responder**
   durante unos minutos.

```bash
pg_dump -U <usuario> -Fc <BD_PRODUCCION> > backup_pre_despliegue.dump
```

### Paso 1 — Desplegar el código de los módulos

Subir al servidor de producción los **3 ficheros modificados** (los que aparecen como `M`
en `git status`):

```
custom_mrp_descarga/models/stock_production_lot.py
custom_mrp_descarga/models/stock_move_line.py
custom_mrp_line_cost/models/mrp_production.py
```

Si se despliega por git: `git pull` (o `git checkout` del commit correspondiente).

### Paso 2 — Crear el índice (opcional pero recomendado)

Ejecutar **antes** de actualizar los módulos, para evitar que el propio proceso de
actualización sufra el barrido completo de tabla:

```bash
psql -U <usuario> -d <BD_PRODUCCION> -f deploy/01_create_index.sql
```

> Es idempotente; si el índice ya existe no hace nada.

### Paso 3 — Actualizar los módulos

```bash
/opt/odoo14_paasa/venv/bin/python odoo-bin \
    -c /ruta/al/odoo.conf -d <BD_PRODUCCION> \
    -u custom_mrp_descarga,custom_mrp_line_cost --stop-after-init
```

Esto produce:

1. La creación del índice `stock_move_line_production_id_index` (por `index=True`).
2. La adición de la columna `average_price` en `stock_production_lot` (por `store=True`).
3. La carga del nuevo método Python (`action_recompute_average_price`).

> Al cambiar `average_price` de no-almacenado a almacenado, los registros existentes
> quedan con valor `NULL` hasta el relleno del Paso 4.

### Paso 4 — Rellenar el precio medio de los lotes existentes

```bash
psql -U <usuario> -d <BD_PRODUCCION> -f deploy/02_backfill_average_price.sql
```

- Primero pone a `0` todos los lotes.
- Después calcula y actualiza el precio medio de los lotes con movimientos de entrada
  válidos (replicando la lógica Python de `_compute_average_price`).

### Paso 5 — Crear / actualizar las acciones de servidor

```bash
/opt/odoo14_paasa/venv/bin/python odoo-bin shell \
    -c /ruta/al/odoo.conf -d <BD_PRODUCCION> \
    < deploy/03_server_actions.py
```

- Actualiza la acción **"Recalcular Precio aplicado en Clasificados"** (añade el recálculo
  del precio medio del lote).

### Paso 6 — Verificación funcional

1. Abrir un **Clasificado** (ej. `CLAS/09550`), ejecutar **"Recalcular Precio aplicado en
   Clasificados"** y comprobar que el **precio medio del lote** cambia.
2. Abrir la lista de **Despieces** afectados (con `production_cost_date` vacía y
   `recalculate_cost` marcado), **seleccionar varios** y ejecutar **"Recalcular coste OF"**.
   Comprobar que:
   - Las **entradas** traen el `standard_price` del lote (precio medio).
   - Las **salidas** se recalculan (`base_price` + reparto).
3. Comprobar el **tiempo de ejecución**: con el índice creado debe ser de pocos segundos
   por despiece.

### Paso 7 — Verificación de la base de datos

```bash
# Índice creado
psql -U <usuario> -d <BD_PRODUCCION> -c \
  "SELECT indexname FROM pg_indexes WHERE tablename='stock_move_line' AND indexname='stock_move_line_production_id_index';"

# Campo average_price almacenado
psql -U <usuario> -d <BD_PRODUCCION> -c \
  "SELECT store FROM ir_model_fields WHERE model='stock.production.lot' AND name='average_price';"
```

---

## 8. Scripts de despliegue

| Fichero | Función |
|---|---|
| `01_create_index.sql` | Crea el índice sobre `stock_move_line.production_id` (idempotente) |
| `02_backfill_average_price.sql` | Rellena `average_price` de los lotes existentes |
| `03_server_actions.py` | Actualiza la acción "Recalcular Precio aplicado en Clasificados" |
| `README.md` | Guía de despliegue paso a paso |
| `documento_tecnico.md` | Este documento |
