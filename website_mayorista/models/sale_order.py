# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.tools import html_escape
from odoo.tools.misc import formatLang


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    is_wholesale_app = fields.Boolean('Pedido de la App Mayorista', readonly=True, copy=False, index=True)
    wholesale_website_id = fields.Many2one('website', 'Sitio de la app', readonly=True, copy=False)
    # Con el estado inicial "borrador" el carrito y el pedido enviado comparten estado:
    # este indicador es lo que los distingue.
    wholesale_submitted = fields.Boolean('Enviado por el cliente', readonly=True, copy=False, index=True)
    wholesale_submitted_date = fields.Datetime('Fecha de envío (app)', readonly=True, copy=False)
    wholesale_stage_id = fields.Many2one(
        'wholesale.order.stage', 'Etapa de rastreo', tracking=True, copy=False, index=True,
        group_expand='_read_group_wholesale_stage_ids')
    wholesale_shipping_product_id = fields.Many2one(
        'product.product', 'Producto de envío', copy=False,
        domain=[('sale_ok', '=', True)])
    wholesale_shipping_cost = fields.Monetary('Costo de envío', currency_field='currency_id', copy=False)
    wholesale_list_color = fields.Char(compute='_compute_wholesale_list_color')

    # ------------------------------------------------------------------
    # Campos calculados / agrupación
    # ------------------------------------------------------------------
    @api.model
    def _read_group_wholesale_stage_ids(self, stages, domain, order):
        # Muestra todas las etapas en el kanban aunque estén vacías.
        return stages.search([], order=order)

    def _wholesale_config(self):
        self.ensure_one()
        return (self.wholesale_website_id or self.env['website'].search(
            [('company_id', '=', self.company_id.id)], limit=1) or self.env['website'].search([], limit=1)).sudo()

    @api.depends('is_wholesale_app', 'wholesale_website_id')
    def _compute_wholesale_list_color(self):
        for order in self:
            color = False
            if order.is_wholesale_app:
                config = order._wholesale_config()
                if config.wholesale_list_highlight:
                    color = config.wholesale_list_color or 'info'
            order.wholesale_list_color = color

    # ------------------------------------------------------------------
    # Costo de envío (línea de pedido sincronizada)
    # ------------------------------------------------------------------
    @api.onchange('wholesale_shipping_product_id')
    def _onchange_wholesale_shipping_product_id(self):
        # Precio por defecto = precio de venta del producto de envío.
        self.wholesale_shipping_cost = self._wholesale_default_shipping_cost()

    def _wholesale_default_shipping_cost(self):
        self.ensure_one()
        product = self.wholesale_shipping_product_id
        if not product:
            return 0.0
        price = product.lst_price
        from_currency = product.currency_id or self.company_id.currency_id
        if self.currency_id and from_currency != self.currency_id:
            price = from_currency._convert(
                price, self.currency_id, self.company_id, fields.Date.context_today(self))
        return price

    def _wholesale_sync_shipping_line(self):
        """Mantiene una línea de pedido con el producto y el costo de envío.

        Así el envío suma al total, lleva impuestos y se factura como cualquier línea.
        """
        Line = self.env['sale.order.line'].sudo()
        for order in self:
            if order.state in ('done', 'cancel'):
                continue
            line = order.order_line.filtered('is_wholesale_shipping')[:1]
            product = order.wholesale_shipping_product_id
            if not product:
                if line:
                    if order.state in ('draft', 'sent'):
                        line.unlink()
                    else:
                        # En pedidos confirmados Odoo no deja borrar líneas: se anula.
                        line.write({'product_uom_qty': 0, 'price_unit': 0})
                continue
            vals = {'price_unit': order.wholesale_shipping_cost, 'product_uom_qty': 1}
            if not line or line.product_id != product:
                taxes = product.taxes_id.filtered(lambda t: t.company_id == order.company_id)
                if order.fiscal_position_id:
                    taxes = order.fiscal_position_id.map_tax(taxes, product, order.partner_shipping_id)
                vals.update({
                    'product_id': product.id,
                    'name': product.get_product_multiline_description_sale() or product.display_name,
                    'product_uom': product.uom_id.id,
                    'tax_id': [(6, 0, taxes.ids)],
                })
            if line:
                line.write(vals)
            else:
                vals.update({'order_id': order.id, 'is_wholesale_shipping': True, 'sequence': 10000})
                Line.create(vals)

    def update_prices(self):
        # "Actualizar precios" recalcula todas las líneas con la lista de precios;
        # el envío debe conservar el costo asignado.
        res = super().update_prices()
        self.filtered('wholesale_shipping_product_id')._wholesale_sync_shipping_line()
        return res

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model
    def create(self, vals):
        order = super().create(vals)
        if order.wholesale_shipping_product_id:
            if 'wholesale_shipping_cost' in vals:
                order._wholesale_sync_shipping_line()
            else:
                order.with_context(wholesale_no_notify=True).write(
                    {'wholesale_shipping_cost': order._wholesale_default_shipping_cost()})
        return order

    def write(self, vals):
        notify = not self.env.context.get('wholesale_no_notify')
        old_stage = {o.id: o.wholesale_stage_id for o in self} if notify and 'wholesale_stage_id' in vals else {}
        old_state = {o.id: o.state for o in self} if notify and 'state' in vals else {}
        old_cost = {o.id: o.wholesale_shipping_cost for o in self} \
            if notify and 'wholesale_shipping_cost' in vals else {}

        res = super().write(vals)

        if 'wholesale_shipping_product_id' in vals or 'wholesale_shipping_cost' in vals:
            if vals.get('wholesale_shipping_product_id') and 'wholesale_shipping_cost' not in vals:
                for order in self:
                    order.with_context(wholesale_no_notify=True).write(
                        {'wholesale_shipping_cost': order._wholesale_default_shipping_cost()})
            else:
                self._wholesale_sync_shipping_line()

        for order in self.filtered(lambda o: o.is_wholesale_app and o.wholesale_submitted):
            if order.id in old_stage and order.wholesale_stage_id != old_stage[order.id]:
                order._wholesale_on_stage_change()
            if order.id in old_state and order.state != old_state[order.id]:
                order._wholesale_on_state_change()
            if order.id in old_cost and order.wholesale_shipping_product_id \
                    and order.currency_id.compare_amounts(order.wholesale_shipping_cost, old_cost[order.id]):
                order._wholesale_notify_customer(
                    _('Costo de envío del pedido %s') % order.name,
                    _('Se asignó un costo de envío de %s.') % formatLang(
                        self.env, order.wholesale_shipping_cost, currency_obj=order.currency_id),
                    'shipping')
        return res

    # ------------------------------------------------------------------
    # Carrito y envío desde la app
    # ------------------------------------------------------------------
    @api.model
    def _wholesale_create_cart(self, partner, website, pricelist):
        """Crea el pedido borrador (carrito) del cliente con los datos del canal."""
        config = website.sudo()
        commercial = partner.commercial_partner_id
        addresses = partner.address_get(['delivery', 'invoice'])
        salesman = partner.user_id or commercial.user_id
        team = config.wholesale_team_id or self.env['crm.team']._get_default_team_id(
            user_id=salesman.id or None)
        vals = {
            'partner_id': partner.id,
            'partner_invoice_id': addresses['invoice'],
            'partner_shipping_id': addresses['delivery'],
            'pricelist_id': pricelist.id,
            'payment_term_id': commercial.property_payment_term_id.id,
            'user_id': salesman.id,
            'team_id': team.id,
            'company_id': website.company_id.id,
            'is_wholesale_app': True,
            'wholesale_website_id': website.id,
            'origin': config.wholesale_origin or False,
        }
        if config.wholesale_shipping_product_id:
            vals['wholesale_shipping_product_id'] = config.wholesale_shipping_product_id.id
        order = self.with_company(website.company_id).with_context(wholesale_no_notify=True).create(vals)
        order.onchange_partner_shipping_id()  # posición fiscal según la dirección de entrega
        return order

    def _wholesale_submit(self, note=None, client_ref=None):
        """El cliente envía su pedido: estado inicial, etapa, correo y avisos."""
        self.ensure_one()
        config = self._wholesale_config()
        order = self.with_context(wholesale_no_notify=True)
        now = fields.Datetime.now()
        vals = {'wholesale_submitted': True, 'wholesale_submitted_date': now, 'date_order': now}
        first_stage = self.env['wholesale.order.stage'].search([], limit=1)
        if first_stage and not order.wholesale_stage_id:
            vals['wholesale_stage_id'] = first_stage.id
        if note:
            vals['note'] = '%s\n%s' % (order.note, note) if order.note else note
        if client_ref:
            vals['client_order_ref'] = client_ref[:64]
        order.write(vals)

        target_state = config.wholesale_order_state or 'draft'
        if target_state == 'sent':
            order.write({'state': 'sent'})
        elif target_state == 'sale':
            order.action_confirm()

        order._wholesale_notify_customer(
            _('Pedido %s recibido') % order.name,
            _('Su pedido fue recibido. Le avisaremos cada vez que cambie de etapa.'), 'order')
        if config.wholesale_send_email:
            template = config.wholesale_mail_template_id or self.env.ref(
                'website_mayorista.mail_template_order_received', raise_if_not_found=False)
            if template:
                order.message_post_with_template(
                    template.id, composition_mode='comment', email_layout_xmlid='mail.mail_notification_light')
        if config.wholesale_notify_backend:
            order._wholesale_notify_backend(config)
        return True

    # ------------------------------------------------------------------
    # Notificaciones
    # ------------------------------------------------------------------
    def _wholesale_notify_backend(self, config):
        """Aviso interno (bandeja de Conversaciones / correo según preferencia del usuario)."""
        self.ensure_one()
        partners = config.wholesale_notify_user_ids.mapped('partner_id')
        if config.wholesale_notify_salesperson and self.user_id:
            partners |= self.user_id.partner_id
        if partners:
            body = _('<p>Nuevo pedido desde <b>%(app)s</b>: %(name)s de %(customer)s por %(total)s.</p>') % {
                'app': html_escape(config.wholesale_app_name or 'App Mayorista'),
                'name': html_escape(self.name),
                'customer': html_escape(self.partner_id.display_name),
                'total': html_escape(formatLang(self.env, self.amount_total, currency_obj=self.currency_id)),
            }
            self.message_post(body=body, partner_ids=partners.ids,
                              message_type='comment', subtype_xmlid='mail.mt_note')
        if config.wholesale_notify_activity and self.user_id:
            self.activity_schedule(
                'mail.mail_activity_data_todo', user_id=self.user_id.id,
                summary=_('Revisar pedido de la App Mayorista'))

    def _wholesale_notify_customer(self, title, body='', kind='order', template=None):
        self.ensure_one()
        self.env['wholesale.notification'].sudo().create({
            'partner_id': self.partner_id.id,
            'order_id': self.id,
            'title': title,
            'body': body or False,
            'kind': kind,
        })
        if template:
            self.message_post_with_template(
                template.id, composition_mode='comment', email_layout_xmlid='mail.mail_notification_light')

    def _wholesale_on_stage_change(self):
        stage = self.wholesale_stage_id
        if not stage or not stage.notify_customer:
            return
        template = False
        if stage.send_email:
            template = stage.mail_template_id or self.env.ref(
                'website_mayorista.mail_template_stage_update', raise_if_not_found=False)
        self._wholesale_notify_customer(
            _('Pedido %s: %s') % (self.name, stage.name), stage.description or '', 'stage', template)

    def _wholesale_on_state_change(self):
        if self.state == 'sale':
            self._wholesale_notify_customer(
                _('Pedido %s confirmado') % self.name, _('Su pedido fue confirmado.'), 'order')
        elif self.state == 'cancel':
            self._wholesale_notify_customer(
                _('Pedido %s cancelado') % self.name,
                _('Su pedido fue cancelado. Escríbanos si tiene dudas.'), 'order')


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    is_wholesale_shipping = fields.Boolean('Línea de envío (App Mayorista)', copy=False)
