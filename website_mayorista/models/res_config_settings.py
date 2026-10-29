# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """Ajustes rápidos de la app en Ajustes generales (sección "App Mayorista").

    Son campos relacionados con el sitio web elegido en Ajustes (website_id);
    el formulario completo se abre con el botón "Todos los ajustes de la app".
    """
    _inherit = 'res.config.settings'

    wholesale_app_name = fields.Char(related='website_id.wholesale_app_name', readonly=False)
    wholesale_pricelist_id = fields.Many2one(related='website_id.wholesale_pricelist_id', readonly=False)
    wholesale_order_state = fields.Selection(related='website_id.wholesale_order_state', readonly=False)
    wholesale_origin = fields.Char(related='website_id.wholesale_origin', readonly=False)
    wholesale_send_email = fields.Boolean(related='website_id.wholesale_send_email', readonly=False)
    wholesale_notify_backend = fields.Boolean(related='website_id.wholesale_notify_backend', readonly=False)
    wholesale_shipping_product_id = fields.Many2one(
        related='website_id.wholesale_shipping_product_id', readonly=False)
    wholesale_show_invoice_status = fields.Boolean(
        related='website_id.wholesale_show_invoice_status', readonly=False)
    wholesale_whatsapp_number = fields.Char(related='website_id.wholesale_whatsapp_number', readonly=False)
    wholesale_chat_enabled = fields.Boolean(related='website_id.wholesale_chat_enabled', readonly=False)

    def action_open_wholesale_config(self):
        self.ensure_one()
        website = self.website_id or self.env['website'].get_current_website()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Ajustes de la App Mayorista',
            'res_model': 'website',
            'res_id': website.id,
            'view_mode': 'form',
            'views': [(self.env.ref('website_mayorista.view_website_wholesale_config_form').id, 'form')],
            'target': 'current',
        }
