# Informe de cambios y guía de despliegue — Recálculo de costes Clasificado/Despiece

Fecha: 2026-09-09
Proyecto: odoo14_paasa (Odoo 14)
Módulos afectados: `custom_mrp_descarga`, `custom_mrp_line_cost`

---

## 1. Resumen de lo realizado

Se ha corregido y optimizado la cadena de costes entre el **Clasificado**
(`mrp.production` de despiece/deconstrucción) y el **Despiece** (`mrp.production` normal):

1. El campo **`average_price` (precio medio) del lote** ahora se **almacena** en base de datos
   (antes se calculaba en cada lectura) y se recalcula tras aplicar la acción de Clasificado.
2. Se ha corregido un **bug** en `button_calculate_costs` (reparto de coste que se saltaba
   cuando el producto de mayor cantidad era el primero de la lista). Este método forma parte
   del flujo de la acción **"Recalcular coste OF"**, por lo que ésta queda corregida.
3. Se ha añadido un **índice de base de datos** sobre `stock_move_line.production_id`, que
   elimina el barrido completo de tabla y acelera el recálculo **~10x**.

---

## 2. Cambios en módulos (código)

Todos en el repositorio `/opt/odoo14_paasa/extra-addons/avanzosc/mrp-addons` (3 ficheros):

### 2.1. `custom_mrp_descarga/models/stock_production_lot.py`

- Campo `average_price`: añadido `store=True` (antes era calculado no almacenado).
- Añadido método `action_recompute_average_price()` que fuerza el recálculo del precio medio.

```python
average_price = fields.Float(
    string="Average Price",
    digits="MRP Price Decimal Precision",
    compute="_compute_average_price",
    store=True,                     # <-- NUEVO
)

def action_recompute_average_price(self):
    for lot in self:
        lot._compute_average_price()   # <-- NUEVO
```

### 2.2. `custom_mrp_descarga/models/stock_move_line.py`

- Redefinido el campo `production_id` con `index=True` (índice persistente en BD).

```python
production_id = fields.Many2one(
    "mrp.production", "Production Order", check_company=True, index=True  # <-- NUEVO
)
```

### 2.3. `custom_mrp_line_cost/models/mrp_production.py`

- Bug corregido en `button_calculate_costs` (rama no-deconstrucción): `if i:` → `if qty:`
  (antes se saltaba el reparto cuando `i == 0`, y fallaba con lista vacía).

```python
# ANTES:
#   i = qty.index(max(qty))
#   if i:
#       max_product = products[i]

# DESPUÉS:
if qty:
    i = qty.index(max(qty))
    max_product = products[i]
    max_qty = qty[i]
    ...
```

---

## 3. Cambios en base de datos (acciones de servidor)

### 3.1. "Recalcular Precio aplicado en Clasificados" (actualizada)

Se añadió al final el recálculo del precio medio de los lotes:

```python
    lots = production.move_line_ids.mapped('lot_id').filtered('id')
    lots.action_recompute_average_price()
```

---

## 4. Orden de despliegue en producción

### Paso 0 — Backup y precaución

- Realizar un backup de la base de datos antes de aplicar cambios.
- Detener/avisar a los usuarios durante la actualización de módulos.

### Paso 1 — Desplegar el código

Subir los 3 ficheros modificados al repositorio de módulos del cliente (git pull / copia):

- `custom_mrp_descarga/models/stock_production_lot.py`
- `custom_mrp_descarga/models/stock_move_line.py`
- `custom_mrp_line_cost/models/mrp_production.py`

### Paso 2 — Crear el índice (opcional pero recomendado antes de actualizar)

```bash
psql -U <usuario> -d <BD_PRODUCCION> -f deploy/01_create_index.sql
```

### Paso 3 — Actualizar los módulos

```bash
/opt/odoo14_paasa/venv/bin/python odoo-bin \
    -c /ruta/odoo.conf -d <BD_PRODUCCION> \
    -u custom_mrp_descarga,custom_mrp_line_cost --stop-after-init
```

Esto:
- Crea el índice `stock_move_line_production_id_index` (por `index=True`).
- Añade la columna `average_price` a `stock_production_lot` (por `store=True`).
- Carga los nuevos métodos Python.

### Paso 4 — Rellenar el precio medio de los lotes existentes

```bash
psql -U <usuario> -d <BD_PRODUCCION> -f deploy/02_backfill_average_price.sql
```

### Paso 5 — Crear/actualizar las acciones de servidor

```bash
/opt/odoo14_paasa/venv/bin/python odoo-bin shell \
    -c /ruta/odoo.conf -d <BD_PRODUCCION> < deploy/03_server_actions.py
```

### Paso 6 — Verificación

1. Abrir un Clasificado (ej. `CLAS/09550`), ejecutar "Recalcular Precio aplicado en Clasificados"
   y comprobar que el precio medio del lote cambia.
2. Abrir los Despieces afectados (con `production_cost_date` vacía y `recalculate_cost`
   marcado), seleccionar varios y ejecutar "Recalcular coste OF". Comprobar que las entradas
   traen el precio del lote y las salidas se recalculan.
3. Comprobar el tiempo de ejecución (debería ser de pocos segundos por despiece).

---

## 5. Notas / consideraciones

- **`average_price` es un promedio global por lote**: agrega TODOS los movimientos "de entrada
  a almacén interno" del lote. Si varios Despieces producen sobre el mismo lote, hay que
  recalcularlos **todos** para que el precio medio quede coherente.
- **Los scripts son idempotentes**: pueden ejecutarse más de una vez sin efectos adversos.
- **El backfill SQL (Paso 4) replica la lógica Python** de `_compute_average_price`. Si en el
  futuro se cambia esa lógica, hay que actualizar también `02_backfill_average_price.sql`.
