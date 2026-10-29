# -*- coding: utf-8 -*-
"""Controladores de la App Mayorista.

Rutas de la app (requieren usuario autenticado, normalmente de portal)
---------------------------------------------------------------------
/mayorista                       Catálogo
/mayorista/productos             Búsqueda en tiempo real y "cargar más" (JSON con HTML parcial)
/mayorista/carrito               Pedido en curso
/mayorista/carrito/actualizar    Fija la cantidad de un producto (JSON)
/mayorista/confirmar             Envía el pedido (POST)
/mayorista/pedido/<id>           Pantalla de confirmación (access_token)
/mayorista/pedidos               Mis pedidos
/mayorista/pedidos/<id>          Detalle con seguimiento, envío y facturación
/mayorista/notificaciones        Avisos al cliente
/mayorista/estado                Contadores de notificaciones y chat (JSON)
/mayorista/chat/mensajes         Leer el chat con el comercial (JSON)
/mayorista/chat/enviar           Escribir en el chat (JSON)

Rutas públicas de la PWA (el navegador las pide sin sesión)
-----------------------------------------------------------
/mayorista/manifest.webmanifest, /mayorista/sw.js, /mayorista/icon/<n>.png, /mayorista/offline

Carrito
-------
Desde la versión 2 el carrito es un sale.order propio de la app (is_wholesale_app=True,
wholesale_submitted=False) en lugar del carrito de sesión de website_sale: se conserva
entre dispositivos, usa la lista de precios del canal y no se mezcla con la tienda.
"""
import base64
import io
import json
import logging
import re
from urllib.parse import quote

from PIL import Image
from werkzeug.exceptions import NotFound

from odoo import _, fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request
from odoo.modules.module import get_module_resource
from odoo.tools import consteq, plaintext2html
from odoo.tools.misc import formatLang

_logger = logging.getLogger(__name__)

FEATURED_LIMIT = 12
ICON_SIZES = (96, 180, 192, 512)
DEFAULT_COLOR = '#2f5d8a'
HEX_COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')

STATE_LABELS = {
    'draft': 'En revisión',
    'sent': 'Cotización enviada',
    'sale': 'Confirmado',
    'done': 'Cerrado',
    'cancel': 'Cancelado',
}
INVOICE_STATUS_LABELS = {
    'no': 'Sin facturar',
    'to invoice': 'Por facturar',
    'invoiced': 'Facturado',
    'upselling': 'Facturado',
}
PAYMENT_STATE_LABELS = {
    'not_paid': 'Pendiente de pago',
    'in_payment': 'Pago en proceso',
    'paid': 'Pagada',
    'partial': 'Pago parcial',
    'reversed': 'Anulada',
    'invoicing_legacy': 'Histórica',
}


class WholesaleApp(http.Controller):

    # ==================================================================
    # Utilidades generales
    # ==================================================================
    def _config(self):
        """Configuración de la app (campos wholesale_* del website actual)."""
        return request.website.sudo()

    def _partner(self):
        return request.env.user.partner_id.sudo()

    @staticmethod
    def _to_int(value):
        try:
            return int(value) if value else 0
        except (TypeError, ValueError):
            return 0

    def _fmt_money(self, amount, currency):
        return formatLang(request.env, amount or 0.0, currency_obj=currency)

    def _fmt_qty(self, qty):
        qty = qty or 0.0
        return formatLang(request.env, qty, digits=0 if float(qty).is_integer() else 2)

    @staticmethod
    def _badge(count):
        return '9+' if count > 9 else str(count or '')

    def _salesperson(self, partner):
        return partner.user_id or partner.commercial_partner_id.user_id

    def _pricelist(self):
        """Lista de precios del canal; si no hay, la del cliente; si no, la del sitio."""
        config = self._config()
        return (config.wholesale_pricelist_id
                or self._partner().property_product_pricelist
                or request.website.get_current_pricelist()).sudo()

    def _theme_color(self, config):
        color = (config.wholesale_theme_color or '').strip()
        return color if HEX_COLOR.match(color) else False

    def _whatsapp_url(self, config, partner):
        number = config.wholesale_whatsapp_number or ''
        if config.wholesale_whatsapp_use_salesperson:
            salesman = self._salesperson(partner)
            if salesman:
                number = salesman.partner_id.mobile or salesman.partner_id.phone or number
        digits = re.sub(r'\D', '', number or '')
        if not digits:
            return False
        url = 'https://wa.me/%s' % digits
        if config.wholesale_whatsapp_message:
            url += '?text=' + quote(config.wholesale_whatsapp_message)
        return url

    def _app_values(self, page, **values):
        """Variables comunes a todas las pantallas (cabecera, menú, chat, PWA)."""
        config = self._config()
        website = request.website
        partner = self._partner()
        unread = request.env['wholesale.notification'].sudo().search_count(
            [('partner_id', '=', partner.id), ('is_read', '=', False)])
        style = []
        color = self._theme_color(config)
        if color:
            style.append('--my-primary: %s;' % color)
        bg_url = website.image_url(config, 'wholesale_background_image') \
            if config.wholesale_background_image else False
        if bg_url:
            style.append("--my-bg-image: url('%s');" % bg_url)
        chat_user = self._chat_user()
        values.update({
            'app': config,
            'wholesale_app': config,          # activa las cabeceras PWA en web.layout
            'app_name': config.wholesale_app_name or 'App Mayorista',
            'app_page': page,
            'app_style': ' '.join(style) or None,
            'bg_url': bg_url,
            'logo_url': website.image_url(config, 'wholesale_app_logo') if config.wholesale_app_logo else False,
            'whatsapp_url': self._whatsapp_url(config, partner),
            'chat_enabled': bool(chat_user),
            'chat_agent': chat_user.name if chat_user else '',
            'notif_unread': unread,
            'notif_unread_label': self._badge(unread),
        })
        return values

    # ==================================================================
    # Catálogo
    # ==================================================================
    def _stock_context(self, products):
        """Contexto para leer inventario del almacén configurado (o de toda la compañía)."""
        warehouse = self._config().wholesale_warehouse_id
        return products.with_context(warehouse=warehouse.id) if warehouse else products

    def _base_domain(self):
        """Productos vendibles y publicados en este sitio/compañía (se busca con sudo)."""
        website = request.website
        return [
            ('sale_ok', '=', True),
            ('product_tmpl_id.is_published', '=', True),
            ('product_tmpl_id.website_id', 'in', [False, website.id]),
            ('company_id', 'in', [False, website.company_id.id]),
        ]

    def _search_domain(self, search='', category_id=0, featured=False):
        domain = self._base_domain()
        for term in (search or '').split():
            domain += ['|', '|',
                       ('name', 'ilike', term),
                       ('default_code', 'ilike', term),
                       ('barcode', 'ilike', term)]
        if category_id:
            domain.append(('public_categ_ids', 'child_of', category_id))
        if featured:
            domain.append(('is_wholesale_featured', '=', True))
        return domain

    def _search_products(self, search='', category_id=0, offset=0, limit=None, featured=False):
        Product = request.env['product.product'].sudo()
        domain = self._search_domain(search, category_id, featured)
        limit = limit or self._config().wholesale_page_size or 40
        return Product.search(domain, offset=offset, limit=limit), Product.search_count(domain)

    @staticmethod
    def _product_lines(order):
        """Líneas de producto (sin secciones, notas ni la línea de envío)."""
        return order.order_line.filtered(
            lambda l: l.product_id and not l.display_type and not l.is_wholesale_shipping)

    def _cart_quantities(self, order):
        quantities = {}
        for line in self._product_lines(order):
            pid = line.product_id.id
            quantities[pid] = quantities.get(pid, 0.0) + line.product_uom_qty
        return quantities

    def _prepare_products(self, products, order):
        """product.product -> dicts para QWeb (precio del canal e inventario free_qty)."""
        config = self._config()
        website = request.website
        pricelist = self._pricelist()
        partner = self._partner()
        prices = {}
        if products:
            prices = pricelist.get_products_price(
                products, [1.0] * len(products), [partner] * len(products))
        in_cart = self._cart_quantities(order)
        result = []
        for product in self._stock_context(products):
            tracked = product.type == 'product'
            stock = product.free_qty if tracked else 0.0
            if not tracked:
                stock_state = ''
            elif stock <= 0:
                stock_state = 'out'
            elif stock < config.wholesale_low_stock:
                stock_state = 'low'
            else:
                stock_state = 'ok'
            qty = in_cart.get(product.id, 0.0)
            result.append({
                'id': product.id,
                'name': product.with_context(display_default_code=False).display_name,
                'code': product.default_code or '',
                'price': prices.get(product.id, product.lst_price),
                'image_url': website.image_url(product, 'image_256'),
                'stock_state': stock_state,
                'stock_display': self._fmt_qty(stock),
                'qty': '%d' % qty,      # string: QWeb no omite el atributo con 0
                'in_cart': qty > 0,
                'max_qty': ('%d' % max(stock, 0)) if (tracked and config.wholesale_limit_stock) else None,
            })
        return result

    def _stock_max(self, products):
        if not self._config().wholesale_limit_stock:
            return {}
        return {p.id: '%d' % max(p.free_qty, 0)
                for p in self._stock_context(products.sudo()) if p.type == 'product'}

    # ==================================================================
    # Carrito
    # ==================================================================
    def _get_cart(self, create=False):
        """Pedido borrador del cliente en la app (uno por contacto y compañía)."""
        website = request.website
        partner = self._partner()
        SaleOrder = request.env['sale.order'].sudo()
        order = SaleOrder.search([
            ('partner_id', '=', partner.id),
            ('is_wholesale_app', '=', True),
            ('wholesale_submitted', '=', False),
            ('state', '=', 'draft'),
            ('company_id', '=', website.company_id.id),
        ], order='id desc', limit=1)
        pricelist = self._pricelist()
        if order and order.pricelist_id != pricelist:
            # Cambió la lista de precios del canal o del cliente: recalcular el carrito.
            order.pricelist_id = pricelist
            order.update_prices()
        if not order and create:
            order = SaleOrder._wholesale_create_cart(partner, website, pricelist)
        return order

    def _cart_values(self, order):
        """Totales formateados (para plantilla y JSON)."""
        currency = order.currency_id or self._pricelist().currency_id
        lines = self._product_lines(order)
        shipping_line = order.order_line.filtered('is_wholesale_shipping')
        return {
            'lines': str(len(lines)),
            'has_lines': bool(lines),
            'units': self._fmt_qty(sum(lines.mapped('product_uom_qty'))),
            'products_subtotal': self._fmt_money(sum(lines.mapped('price_subtotal')), currency),
            'has_shipping': bool(shipping_line),
            'shipping': self._fmt_money(sum(shipping_line.mapped('price_subtotal')), currency),
            'amount_untaxed': self._fmt_money(order.amount_untaxed, currency),
            'amount_tax': self._fmt_money(order.amount_tax, currency),
            'amount_total': self._fmt_money(order.amount_total, currency),
        }

    def _group_lines(self, order):
        """Agrupa las líneas por categoría de eCommerce (A-Z) y ordena por nombre (A-Z)."""
        website = request.website
        groups = {}
        for line in self._product_lines(order):
            categories = line.product_id.public_categ_ids.filtered(
                lambda c: not c.website_id or c.website_id == website)
            category = categories.sorted(lambda c: (c.display_name or '').lower())[:1]
            groups.setdefault(category.display_name if category else '', []).append(line)
        result = []
        # Categorías en orden alfabético; los productos sin categoría al final.
        for key in sorted(groups, key=lambda k: (k == '', k.lower())):
            lines = sorted(groups[key], key=lambda l: (l.product_id.name or '').lower())
            result.append({'name': key or _('Otros productos'), 'lines': lines, 'count': str(len(lines))})
        return result

    def _cart_page_values(self, order, error=None):
        lines = self._product_lines(order)
        return self._app_values('cart', **{
            'page_title': _('Mi pedido'),
            'order': order,
            'groups': self._group_lines(order),
            'cart': self._cart_values(order),
            'stock_max': self._stock_max(lines.mapped('product_id')),
            'error': error,
        })

    # ==================================================================
    # Pedidos enviados
    # ==================================================================
    def _my_order(self, order_id):
        """Pedido de la app perteneciente a la empresa del usuario, o 404."""
        order = request.env['sale.order'].sudo().browse(order_id).exists()
        commercial = self._partner().commercial_partner_id
        if not order or not order.is_wholesale_app or not order.wholesale_submitted \
                or order.partner_id.commercial_partner_id != commercial:
            raise NotFound()
        return order

    def _timeline(self, order):
        stages = request.env['wholesale.order.stage'].sudo().search([])
        current = order.wholesale_stage_id
        index = stages.ids.index(current.id) if current.id in stages.ids else -1
        return [{
            'name': stage.name,
            'description': stage.description or '',
            'status': 'done' if i < index else ('current' if i == index else 'todo'),
        } for i, stage in enumerate(stages)]

    def _invoices(self, order):
        invoices = order.invoice_ids.filtered(lambda m: m.state == 'posted')
        return [{
            'name': inv.name,
            'date': inv.invoice_date,
            'amount': self._fmt_money(inv.amount_total, inv.currency_id),
            'is_refund': inv.move_type == 'out_refund',
            'payment_label': PAYMENT_STATE_LABELS.get(inv.payment_state, ''),
            'payment_state': inv.payment_state,
            'url': inv.get_portal_url(),
        } for inv in invoices]

    # ==================================================================
    # Rutas: catálogo
    # ==================================================================
    @http.route('/mayorista', type='http', auth='user', website=True, sitemap=False)
    def catalog(self, search='', category=None, **kw):
        website = request.website
        search = (search or '').strip()
        order = self._get_cart()

        Category = request.env['product.public.category'].sudo()
        cat_domain = website.website_domain()
        category = Category.browse(self._to_int(category)).exists()
        roots = Category.search([('parent_id', '=', False)] + cat_domain)
        active_root = Category.browse()
        subcategories = Category.browse()
        if category:
            active_root = Category.browse(int(category.parent_path.split('/')[0]))
            subcategories = (category.child_id or category.parent_id.child_id).filtered_domain(cat_domain)

        products, count = self._search_products(search, category.id)
        featured = []
        if not search and not category:
            featured = self._prepare_products(
                self._search_products(featured=True, limit=FEATURED_LIMIT)[0], order)

        return request.render('website_mayorista.app_page', self._app_values('catalog', **{
            'search': search,
            'category': category,
            'active_root': active_root,
            'categories': roots,
            'subcategories': subcategories,
            'featured': featured,
            'products': self._prepare_products(products, order),
            'count': count,
            'count_label': str(count),
            'next_offset': len(products),
            'has_more': len(products) < count,
            'currency': self._pricelist().currency_id,
            'cart': self._cart_values(order),
        }))

    @http.route('/mayorista/productos', type='json', auth='user', website=True)
    def products_json(self, search='', category=None, offset=0, **kw):
        """Búsqueda en tiempo real: devuelve el HTML de las tarjetas (misma plantilla QWeb)."""
        search = (search or '').strip()
        offset = max(self._to_int(offset), 0)
        products, count = self._search_products(search, self._to_int(category), offset)
        html = request.env['ir.ui.view'].sudo()._render_template('website_mayorista.product_items', {
            'products': self._prepare_products(products, self._get_cart()),
            'currency': self._pricelist().currency_id,
        })
        if isinstance(html, bytes):
            html = html.decode('utf-8')
        next_offset = offset + len(products)
        return {'html': html, 'count': count, 'next_offset': next_offset, 'has_more': next_offset < count}

    # ==================================================================
    # Rutas: pedido en curso
    # ==================================================================
    @http.route('/mayorista/carrito', type='http', auth='user', website=True, sitemap=False)
    def cart(self, **kw):
        return request.render('website_mayorista.cart_page', self._cart_page_values(self._get_cart()))

    @http.route('/mayorista/carrito/actualizar', type='json', auth='user', website=True)
    def cart_update(self, product_id=None, quantity=0, **kw):
        """Fija (no suma) la cantidad de un producto. quantity=0 elimina la línea."""
        config = self._config()
        order = self._get_cart(create=True)
        product = request.env['product.product'].sudo().search(
            self._base_domain() + [('id', '=', self._to_int(product_id))], limit=1)
        if not product:
            return dict(self._cart_values(order), error=_('Este producto ya no está disponible.'))

        qty = max(self._to_int(quantity), 0)
        warning = ''
        if qty and config.wholesale_limit_stock and product.type == 'product':
            available = max(self._stock_context(product).free_qty, 0.0)
            if qty > available:
                qty = int(available)
                warning = _('Solo hay %s disponibles de %s.') % (self._fmt_qty(available), product.name)

        lines = self._product_lines(order).filtered(lambda l: l.product_id == product)
        if qty > 0:
            # _cart_update (website_sale) aplica lista de precios, impuestos y descuentos.
            order._cart_update(product_id=product.id, set_qty=qty)
        elif lines:
            # Con set_qty=0 _cart_update conserva la cantidad: se borra la línea directamente.
            lines.unlink()

        lines = self._product_lines(order).filtered(lambda l: l.product_id == product)
        values = self._cart_values(order)
        values.update({
            'product_id': product.id,
            'quantity': int(sum(lines.mapped('product_uom_qty'))),
            'line_subtotal': self._fmt_money(sum(lines.mapped('price_subtotal')), order.currency_id),
            'warning': warning,
        })
        return values

    @http.route('/mayorista/confirmar', type='http', auth='user', methods=['POST'],
                website=True, sitemap=False)
    def confirm(self, note='', client_ref='', **kw):
        order = self._get_cart()
        if not order or not self._product_lines(order):
            return request.redirect('/mayorista/carrito')
        try:
            # Savepoint: si algo falla (p. ej. al confirmar) no quedan cambios a medias.
            with request.env.cr.savepoint():
                order._wholesale_submit(note=(note or '').strip(), client_ref=(client_ref or '').strip())
        except (UserError, ValidationError) as e:
            _logger.warning('App Mayorista: no se pudo enviar %s: %s', order.name, e)
            return request.render('website_mayorista.cart_page', self._cart_page_values(
                order, error=_('No se pudo enviar el pedido: %s') % e.args[0]))
        order._portal_ensure_token()
        return request.redirect('/mayorista/pedido/%s?access_token=%s' % (order.id, order.access_token))

    @http.route('/mayorista/pedido/<int:order_id>', type='http', auth='user', website=True, sitemap=False)
    def confirmation(self, order_id, access_token=None, **kw):
        order = request.env['sale.order'].sudo().browse(order_id).exists()
        if not order or not access_token or not consteq(order.access_token or '', access_token):
            raise NotFound()
        return request.render('website_mayorista.confirmation_page', self._app_values('orders', **{
            'page_title': order.name,
            'page_class': 'o_my_page_confirm',
            'order': order,
            'groups': self._group_lines(order),
            'cart': self._cart_values(order),
            'confirmed': order.state in ('sale', 'done'),
        }))

    # ==================================================================
    # Rutas: mis pedidos y notificaciones
    # ==================================================================
    @http.route('/mayorista/pedidos', type='http', auth='user', website=True, sitemap=False)
    def orders(self, **kw):
        commercial = self._partner().commercial_partner_id
        orders = request.env['sale.order'].sudo().search([
            ('partner_id', 'child_of', commercial.id),
            ('is_wholesale_app', '=', True),
            ('wholesale_submitted', '=', True),
        ], order='wholesale_submitted_date desc, id desc', limit=60)
        return request.render('website_mayorista.orders_page', self._app_values('orders', **{
            'page_title': _('Mis pedidos'),
            'orders': orders,
            'state_labels': STATE_LABELS,
            'invoice_labels': INVOICE_STATUS_LABELS,
        }))

    @http.route('/mayorista/pedidos/<int:order_id>', type='http', auth='user', website=True, sitemap=False)
    def order_detail(self, order_id, **kw):
        order = self._my_order(order_id)
        return request.render('website_mayorista.order_detail_page', self._app_values('orders', **{
            'page_title': order.name,
            'order': order,
            'groups': self._group_lines(order),
            'cart': self._cart_values(order),
            'timeline': self._timeline(order),
            'state_label': STATE_LABELS.get(order.state, ''),
            'invoice_label': INVOICE_STATUS_LABELS.get(order.invoice_status, ''),
            'invoices': self._invoices(order),
        }))

    @http.route('/mayorista/notificaciones', type='http', auth='user', website=True, sitemap=False)
    def notifications(self, **kw):
        Notification = request.env['wholesale.notification'].sudo()
        notifications = Notification.search([('partner_id', '=', self._partner().id)], limit=60)
        unread = notifications.filtered(lambda n: not n.is_read)
        # Se renderiza antes de marcarlas como leídas, para resaltar las nuevas.
        response = request.render('website_mayorista.notifications_page', self._app_values('notifications', **{
            'page_title': _('Notificaciones'),
            'notifications': notifications,
            'unread_ids': unread.ids,
        }))
        response.flatten()
        unread.write({'is_read': True})
        return response

    @http.route('/mayorista/estado', type='json', auth='user', website=True)
    def status(self, **kw):
        partner = self._partner()
        unread = request.env['wholesale.notification'].sudo().search_count(
            [('partner_id', '=', partner.id), ('is_read', '=', False)])
        return {'notifications': unread, 'chat': self._chat_unread(self._chat_channel(), partner)}

    # ==================================================================
    # Chat cliente <-> comercial (mail.channel tipo "chat" = mensaje directo en Conversaciones)
    # ==================================================================
    def _chat_user(self):
        config = self._config()
        if not config.wholesale_chat_enabled:
            return request.env['res.users']
        return self._salesperson(self._partner()) or config.wholesale_chat_fallback_user_id

    def _chat_channel(self, create=False):
        user = self._chat_user()
        if not user:
            return request.env['mail.channel']
        partner = self._partner()
        Channel = request.env['mail.channel'].sudo()
        channel = Channel.search([
            ('wholesale_partner_id', '=', partner.id),
            ('wholesale_user_id', '=', user.id),
        ], limit=1)
        if not channel and create:
            # Mismo esquema que mail.channel.channel_get(): chat directo entre dos personas.
            channel = Channel.create({
                'name': '%s, %s' % (partner.name, user.name),
                'channel_type': 'chat',
                'public': 'private',
                'email_send': False,
                'wholesale_partner_id': partner.id,
                'wholesale_user_id': user.id,
                'channel_last_seen_partner_ids': [
                    (0, 0, {'partner_id': partner.id}),
                    (0, 0, {'partner_id': user.partner_id.id}),
                ],
            })
        return channel

    def _chat_unread(self, channel, partner):
        if not channel:
            return 0
        member = channel.channel_last_seen_partner_ids.filtered(lambda m: m.partner_id == partner)[:1]
        return request.env['mail.message'].sudo().search_count([
            ('model', '=', 'mail.channel'), ('res_id', '=', channel.id),
            ('message_type', '=', 'comment'),
            ('id', '>', member.seen_message_id.id or 0),
            ('author_id', '!=', partner.id),
        ])

    def _chat_message(self, message, partner):
        date = fields.Datetime.context_timestamp(request.env.user, message.date)
        return {
            'id': message.id,
            'body': message.body or '',
            'author': message.author_id.name or '',
            'mine': message.author_id == partner,
            'date': date.strftime('%d/%m %H:%M'),
        }

    @http.route('/mayorista/chat/mensajes', type='json', auth='user', website=True)
    def chat_messages(self, after_id=0, **kw):
        user = self._chat_user()
        if not user:
            return {'enabled': False, 'messages': []}
        partner = self._partner()
        channel = self._chat_channel()
        messages = request.env['mail.message'].sudo()
        if channel:
            domain = [('model', '=', 'mail.channel'), ('res_id', '=', channel.id),
                      ('message_type', '=', 'comment')]
            after_id = self._to_int(after_id)
            if after_id:
                messages = messages.search(domain + [('id', '>', after_id)], order='id asc', limit=100)
            else:
                messages = messages.search(domain, order='id desc', limit=50).sorted('id')
            if messages:
                member = channel.channel_last_seen_partner_ids.filtered(lambda m: m.partner_id == partner)
                member.write({'seen_message_id': messages[-1].id, 'fetched_message_id': messages[-1].id})
        return {
            'enabled': True,
            'agent': user.name,
            'messages': [self._chat_message(m, partner) for m in messages],
        }

    @http.route('/mayorista/chat/enviar', type='json', auth='user', website=True)
    def chat_send(self, body='', **kw):
        body = (body or '').strip()[:2000]
        if not body:
            return {'ok': False}
        channel = self._chat_channel(create=True)
        if not channel:
            return {'ok': False, 'error': _('El chat no está disponible.')}
        # Queda a nombre del cliente y le llega al comercial como mensaje directo.
        channel.with_context(mail_create_nosubscribe=True).message_post(
            author_id=self._partner().id, body=plaintext2html(body),
            message_type='comment', subtype_xmlid='mail.mt_comment')
        return {'ok': True}

    # ==================================================================
    # PWA
    # ==================================================================
    @http.route('/mayorista/manifest.webmanifest', type='http', auth='public', website=True, sitemap=False)
    def pwa_manifest(self, **kw):
        config = self._config()
        name = config.wholesale_app_name or 'App Mayorista'
        data = {
            'id': '/mayorista',
            'name': name,
            'short_name': config.wholesale_app_short_name or name[:12],
            'description': _('Catálogo y pedidos para clientes mayoristas.'),
            'lang': (request.env.lang or 'es').replace('_', '-'),
            'start_url': '/mayorista?source=pwa',
            'scope': '/mayorista',
            'display': 'standalone',
            'orientation': 'portrait',
            'background_color': '#ffffff',
            'theme_color': self._theme_color(config) or DEFAULT_COLOR,
            'icons': [
                {'src': '/mayorista/icon/192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
                {'src': '/mayorista/icon/512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
                {'src': '/mayorista/icon/512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
            ],
        }
        return request.make_response(json.dumps(data, ensure_ascii=False), headers=[
            ('Content-Type', 'application/manifest+json; charset=utf-8'),
            ('Cache-Control', 'public, max-age=3600'),
        ])

    @http.route('/mayorista/sw.js', type='http', auth='public', website=True, sitemap=False)
    def pwa_service_worker(self, **kw):
        path = get_module_resource('website_mayorista', 'static', 'src', 'js', 'service_worker.js')
        with open(path, 'rb') as f:
            body = f.read()
        return request.make_response(body, headers=[
            ('Content-Type', 'application/javascript; charset=utf-8'),
            # El SW vive en /mayorista/sw.js; este encabezado le permite controlar también /mayorista.
            ('Service-Worker-Allowed', '/mayorista'),
            ('Cache-Control', 'no-cache'),
        ])

    @http.route('/mayorista/icon/<int:size>.png', type='http', auth='public', website=True, sitemap=False)
    def pwa_icon(self, size, **kw):
        """Ícono cuadrado y opaco generado desde el logo configurado (o el ícono del módulo)."""
        if size not in ICON_SIZES:
            raise NotFound()
        config = self._config()
        if config.wholesale_app_logo:
            source = base64.b64decode(config.wholesale_app_logo)
        else:
            with open(get_module_resource('website_mayorista', 'static', 'src', 'img', 'icon.png'), 'rb') as f:
                source = f.read()
        try:
            image = Image.open(io.BytesIO(source)).convert('RGBA')
        except Exception:
            raise NotFound()
        canvas = Image.new('RGBA', (size, size), (255, 255, 255, 255))
        inner = int(size * 0.8)   # margen de seguridad para íconos "maskable" de Android
        ratio = min(inner / image.width, inner / image.height)
        image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))), Image.LANCZOS)
        canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2), image)
        out = io.BytesIO()
        canvas.convert('RGB').save(out, 'PNG')
        return request.make_response(out.getvalue(), headers=[
            ('Content-Type', 'image/png'),
            ('Cache-Control', 'public, max-age=86400'),
        ])

    @http.route('/mayorista/offline', type='http', auth='public', website=True, sitemap=False)
    def pwa_offline(self, **kw):
        config = self._config()
        return request.render('website_mayorista.offline_page', {
            'app_name': config.wholesale_app_name or 'App Mayorista',
            'color': self._theme_color(config) or DEFAULT_COLOR,
        })
