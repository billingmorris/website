# -*- coding: utf-8 -*-
from odoo import fields, models


class WholesaleNotification(models.Model):
    """Avisos que el cliente ve en la campana de la app."""
    _name = 'wholesale.notification'
    _description = 'Notificación a cliente mayorista'
    _order = 'create_date desc, id desc'
    _rec_name = 'title'

    partner_id = fields.Many2one('res.partner', 'Cliente', required=True, index=True, ondelete='cascade')
    order_id = fields.Many2one('sale.order', 'Pedido', index=True, ondelete='cascade')
    title = fields.Char('Título', required=True)
    body = fields.Text('Mensaje')
    kind = fields.Selection([
        ('order', 'Pedido'),
        ('stage', 'Seguimiento'),
        ('shipping', 'Envío'),
        ('message', 'Mensaje'),
    ], 'Tipo', default='message', required=True)
    is_read = fields.Boolean('Leída', default=False, index=True)
