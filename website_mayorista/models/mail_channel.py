# -*- coding: utf-8 -*-
from odoo import fields, models


class MailChannel(models.Model):
    """Marca los chats creados desde la app (cliente <-> comercial)."""
    _inherit = 'mail.channel'

    wholesale_partner_id = fields.Many2one('res.partner', 'Cliente (App Mayorista)', index=True)
    wholesale_user_id = fields.Many2one('res.users', 'Comercial (App Mayorista)', index=True)
