-- ============================================================================
-- 01_create_index.sql
-- Índice para acelerar el recálculo de costes de los Despieces
-- ============================================================================
-- PROBLEMA: la tabla stock_move_line tiene ~3,1 millones de filas y el campo
-- production_id NO tenía índice. Las consultas WHERE production_id = X hacían
-- un barrido completo de tabla (587 ms cada una), ralentizando mucho el
-- recálculo de costes.
--
-- SOLUCIÓN: crear un índice btree sobre stock_move_line.production_id.
-- (También queda definido de forma persistente en el módulo
--  custom_mrp_descarga con index=True, por lo que esta sentencia es un
--  refuerzo / seguridad y puede ejecutarse antes de actualizar los módulos.)
--
-- Es idempotente: si el índice ya existe, no hace nada.
-- ============================================================================

CREATE INDEX IF NOT EXISTS stock_move_line_production_id_index
    ON public.stock_move_line
    USING btree (production_id);

-- Verificación opcional:
-- SELECT indexname FROM pg_indexes WHERE tablename='stock_move_line'
--   AND indexname='stock_move_line_production_id_index';
