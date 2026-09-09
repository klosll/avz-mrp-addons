-- ============================================================================
-- 02_backfill_average_price.sql
-- Relleno del campo average_price de stock_production_lot tras hacerlo almacenado
-- ============================================================================
-- CONTEXTO: el campo stock_production_lot.average_price era un campo calculado
-- NO almacenado (store=False). Se ha cambiado a store=True en el módulo
-- custom_mrp_descarga. Al actualizar el módulo, Odoo añade la columna pero los
-- registros existentes quedan con valor NULL hasta que se recalculen.
--
-- Este script rellena el valor para TODOS los lotes existentes, replicando
-- EXACTAMENTE la lógica Python de _compute_average_price del módulo
-- custom_mrp_descarga (models/stock_production_lot.py):
--
--   average_price = SUM(amount) / SUM(qty_done)
--   sobre las líneas de movimiento donde:
--     - state = 'done'
--     - location_dest_id.usage = 'internal'
--     - location_id.usage != 'internal'
--
-- NOTA IMPORTANTE: si en el futuro se modifica la lógica Python de
-- _compute_average_price, este SQL debe actualizarse para que coincida.
-- ============================================================================

-- 1) Poner a 0 los lotes (para que los que no tengan movimientos válidos queden a 0,
--    igual que hace el método Python que asigna average_price = 0 por defecto).
UPDATE stock_production_lot
   SET average_price = 0;

-- 2) Calcular y actualizar el precio medio para los lotes con movimientos válidos.
UPDATE stock_production_lot l
   SET average_price = sub.avg_price
  FROM (
        SELECT ml.lot_id,
               CASE
                   WHEN SUM(ml.qty_done) != 0
                   THEN SUM(ml.amount) / SUM(ml.qty_done)
                   ELSE 0
               END AS avg_price
          FROM stock_move_line ml
          JOIN stock_location loc_dest ON loc_dest.id = ml.location_dest_id
          JOIN stock_location loc_src  ON loc_src.id  = ml.location_id
         WHERE ml.state = 'done'
           AND loc_dest.usage = 'internal'
           AND loc_src.usage  != 'internal'
         GROUP BY ml.lot_id
       ) sub
 WHERE l.id = sub.lot_id;
