# -*- coding: utf-8 -*-
from odoo import fields, models


class WholesaleOrderStage(models.Model):
    """Etapas de rastreo visibles para el cliente (independientes del estado de venta)."""
    _name = 'wholesale.order.stage'
    _description = 'Etapa de rastreo de pedido mayorista'
    _order = 'sequence, id'

    name = fields.Char('Etapa', required=True, translate=True)
    sequence = fields.Integer(default=10)
    description = fields.Text(
        'Mensaje para el cliente', translate=True,
        help='Texto que ve el cliente en el seguimiento y en la notificación.')
    fold = fields.Boolean('Plegada en el kanban')
    notify_customer = fields.Boolean('Notificar al cliente en la app', default=True)
    send_email = fields.Boolean('Enviar también correo al cliente')
    mail_template_id = fields.Many2one(
        'mail.template', 'Plantilla de correo', domain=[('model', '=', 'sale.order')],
        help='Vacío: se usa "App Mayorista: actualización de pedido".')
    active = fields.Boolean(default=True)
