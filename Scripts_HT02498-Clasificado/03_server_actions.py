# -*- coding: utf-8 -*-
# ============================================================================
# 03_server_actions.py
# Crea / actualiza las Acciones de Servidor necesarias en el modelo mrp.production
# ============================================================================
# Ejecutar con el shell de Odoo:
#   /opt/odoo14_paasa/venv/bin/python odoo-bin shell \
#       -c /ruta/odoo.conf -d <BD_PRODUCCION> < 03_server_actions.py
#
# El script es idempotente: se busca cada acción por su NOMBRE, y si existe se
# actualiza su código; si no existe, se crea.
# ============================================================================

# --- Acción 1: "Recalcular Precio aplicado en Clasificados" (actualizada) ---
# Se añade al final el recálculo del precio medio de los lotes afectados.
CODE_CLASIFICADOS = """productions = records or record
for production in productions:
    if not (production.is_deconstruction and production.move_line_ids):
        continue
    for line in production.move_line_ids:
        line.write({
            'applied_price': line.base_price,
            'standard_price': line.base_price,
            'amount': line.base_price * line.qty_done,
        })
    production._compute_entry_total_amount()
    production.button_calculate_costs()

    lots = production.move_line_ids.mapped('lot_id').filtered('id')
    lots.action_recompute_average_price()
"""

MODEL_XML_ID = 'mrp.model_mrp_production'


def upsert_action(name, code, model_id):
    action = env['ir.actions.server'].search([('name', '=', name)], limit=1)
    if action:
        action.write({
            'model_id': model_id,
            'binding_model_id': model_id,
            'binding_type': 'action',
            'state': 'code',
            'code': code,
        })
        print('Actualizada acción:', name, '(id=%s)' % action.id)
    else:
        action = env['ir.actions.server'].create({
            'name': name,
            'model_id': model_id,
            'binding_model_id': model_id,
            'binding_type': 'action',
            'state': 'code',
            'code': code,
        })
        print('Creada acción:', name, '(id=%s)' % action.id)
    return action


model_id = env.ref(MODEL_XML_ID).id

upsert_action('Recalcular Precio aplicado en Clasificados', CODE_CLASIFICADOS, model_id)

env.cr.commit()
print('OK - acciones de servidor aplicadas y confirmadas.')
