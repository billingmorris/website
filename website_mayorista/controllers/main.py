# -*- coding: utf-8 -*-
"""Controladores de la App Mayorista.

Rutas
-----
/mayorista                      Catálogo (HTML renderizado en servidor; funciona sin JS)
/mayorista/productos            Búsqueda en tiempo real y "cargar más" (JSON con HTML parcial)
/mayorista/carrito              Revisión del pedido (HTML)
/mayorista/carrito/actualizar   Fija la cantidad de un producto en el pedido (JSON)
/mayorista/confirmar            Confirma el pedido (POST)
/mayorista/pedido/<id>          Pantalla de confirmación (protegida con access_token)

Diseño
------
El "carrito" es el mismo pedido borrador de website_sale (sale.order guardado en
la sesión). Así se reutilizan listas de precios, impuestos, posiciones fiscales y
direcciones estándar, y el pedido sobrevive a recargas de página.
Todas las rutas exigen usuario autenticado (auth='user'): el mayorista entra con
su usuario de portal y el pedido queda a nombre de su contacto.
"""
import logging

from werkzeug.exceptions import NotFound

from odoo import _, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request
from odoo.tools import consteq
from odoo.tools.misc import formatLang

_logger = logging.getLogger(__name__)

FEATURED_LIMIT = 12


class WholesaleApp(http.Controller):

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def _config(self):
        """Configuración de la app (campos wholesale_* del website actual)."""
        return request.website.sudo()

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

    def _stock_context(self, products):
        """Contexto para leer inventario del almacén configurado (o de toda la compañía)."""
        warehouse = self._config().wholesale_warehouse_id
        return products.with_context(warehouse=warehouse.id) if warehouse else products

    def _base_domain(self):
        """Productos vendibles y publicados en este sitio/compañía.

        Se busca con sudo() (el usuario portal no lee inventario), por eso el
        dominio replica a mano las restricciones de publicación y compañía.
        """
        website = request.website
        return [
            ('sale_ok', '=', True),
            ('product_tmpl_id.is_published', '=', True),
            ('product_tmpl_id.website_id', 'in', [False, website.id]),
            ('company_id', 'in', [False, website.company_id.id]),
        ]

    def _search_domain(self, search='', category_id=0, featured=False):
        domain = self._base_domain()
        # Cada palabra debe aparecer en el nombre, el código interno o el código de barras.
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
        products = Product.search(domain, offset=offset, limit=limit)
        count = Product.search_count(domain)
        return products, count

    @staticmethod
    def _cart_quantities(order):
        quantities = {}
        for line in order.order_line:
            if line.product_id:
                pid = line.product_id.id
                quantities[pid] = quantities.get(pid, 0.0) + line.product_uom_qty
        return quantities

    def _prepare_products(self, products, order):
        """Convierte product.product en diccionarios listos para la plantilla QWeb.

        Precio: lista de precios del website/cliente (la misma que usará el pedido).
        Inventario: free_qty = disponible sin reservar, solo en productos almacenables.
        """
        config = self._config()
        website = request.website
        pricelist = website.get_current_pricelist().sudo()
        partner = request.env.user.partner_id
        prices = {}
        if products:
            prices = pricelist.get_products_price(
                products, [1.0] * len(products), [partner] * len(products))
        in_cart = self._cart_quantities(order)

        result = []
        # Un solo recordset con contexto de almacén: free_qty se calcula en lote.
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
                # Strings para que QWeb no omita el atributo cuando el valor es 0.
                'qty': '%d' % qty,
                'in_cart': qty > 0,
                'max_qty': ('%d' % max(stock, 0)) if (tracked and config.wholesale_limit_stock) else None,
            })
        return result

    def _stock_max(self, products):
        """{product_id: máximo pedible} cuando está activo el límite por inventario."""
        if not self._config().wholesale_limit_stock:
            return {}
        return {
            p.id: '%d' % max(p.free_qty, 0)
            for p in self._stock_context(products.sudo()) if p.type == 'product'
        }

    def _cart_values(self, order):
        """Totales del pedido ya formateados con la moneda (para plantilla y JSON)."""
        currency = order.currency_id or request.website.get_current_pricelist().currency_id
        lines = order.order_line.filtered(lambda l: l.product_id and not l.display_type)
        return {
            'lines': str(len(lines)),
            'has_lines': bool(lines),
            'units': self._fmt_qty(sum(lines.mapped('product_uom_qty'))),
            'amount_untaxed': self._fmt_money(order.amount_untaxed, currency),
            'amount_tax': self._fmt_money(order.amount_tax, currency),
            'amount_total': self._fmt_money(order.amount_total, currency),
        }

    def _cart_payload(self, order, product=None):
        values = self._cart_values(order)
        if product:
            lines = order.order_line.filtered(lambda l: l.product_id == product)
            values.update({
                'product_id': product.id,
                'quantity': int(sum(lines.mapped('product_uom_qty'))),
                'line_subtotal': self._fmt_money(sum(lines.mapped('price_subtotal')), order.currency_id),
            })
        return values

    def _get_draft_order(self, force_create=False):
        """Pedido borrador de la sesión; si ya fue confirmado, empieza uno nuevo."""
        website = request.website
        order = website.sale_get_order(force_create=force_create)
        if order and order.state != 'draft':
            website.sale_reset()
            order = website.sale_get_order(force_create=force_create)
        return order

    def _cart_page_values(self, order, error=None):
        lines = order.order_line.filtered(lambda l: l.product_id and not l.display_type)
        return {
            'order': order,
            'lines': lines,
            'cart': self._cart_values(order),
            'stock_max': self._stock_max(lines.mapped('product_id')),
            'config': self._config(),
            'error': error,
        }

    # ------------------------------------------------------------------
    # Catálogo
    # ------------------------------------------------------------------
    @http.route('/mayorista', type='http', auth='user', website=True, sitemap=False)
    def catalog(self, search='', category=None, **kw):
        website = request.website
        search = (search or '').strip()
        order = self._get_draft_order()

        Category = request.env['product.public.category'].sudo()
        cat_domain = website.website_domain()
        category = Category.browse(self._to_int(category)).exists()
        roots = Category.search([('parent_id', '=', False)] + cat_domain)
        active_root = Category.browse()
        subcategories = Category.browse()
        if category:
            active_root = Category.browse(int(category.parent_path.split('/')[0]))
            # Hijas de la categoría actual; si es una hoja, sus hermanas.
            subcategories = (category.child_id or category.parent_id.child_id).filtered_domain(cat_domain)

        products, count = self._search_products(search, category.id)
        featured = []
        if not search and not category:
            featured_products = self._search_products(featured=True, limit=FEATURED_LIMIT)[0]
            featured = self._prepare_products(featured_products, order)

        return request.render('website_mayorista.app_page', {
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
            'currency': website.get_current_pricelist().currency_id,
            'cart': self._cart_values(order),
        })

    @http.route('/mayorista/productos', type='json', auth='user', website=True)
    def products_json(self, search='', category=None, offset=0, **kw):
        """Búsqueda en tiempo real: devuelve el HTML de las tarjetas (misma plantilla QWeb)."""
        search = (search or '').strip()
        offset = max(self._to_int(offset), 0)
        order = self._get_draft_order()
        products, count = self._search_products(search, self._to_int(category), offset)
        html = request.env['ir.ui.view'].sudo()._render_template('website_mayorista.product_items', {
            'products': self._prepare_products(products, order),
            'currency': request.website.get_current_pricelist().currency_id,
        })
        if isinstance(html, bytes):
            html = html.decode('utf-8')
        next_offset = offset + len(products)
        return {
            'html': html,
            'count': count,
            'next_offset': next_offset,
            'has_more': next_offset < count,
        }

    # ------------------------------------------------------------------
    # Pedido
    # ------------------------------------------------------------------
    @http.route('/mayorista/carrito', type='http', auth='user', website=True, sitemap=False)
    def cart(self, **kw):
        return request.render('website_mayorista.cart_page', self._cart_page_values(self._get_draft_order()))

    @http.route('/mayorista/carrito/actualizar', type='json', auth='user', website=True)
    def cart_update(self, product_id=None, quantity=0, **kw):
        """Fija (no suma) la cantidad de un producto. quantity=0 elimina la línea."""
        config = self._config()
        order = self._get_draft_order(force_create=True)
        product = request.env['product.product'].sudo().search(
            self._base_domain() + [('id', '=', self._to_int(product_id))], limit=1)
        if not product:
            return dict(self._cart_payload(order), error=_('Este producto ya no está disponible.'))

        qty = max(self._to_int(quantity), 0)
        warning = ''
        if qty and config.wholesale_limit_stock and product.type == 'product':
            available = max(self._stock_context(product).free_qty, 0.0)
            if qty > available:
                qty = int(available)
                warning = _('Solo hay %s disponibles de %s.') % (self._fmt_qty(available), product.name)

        if qty > 0:
            # _cart_update de website_sale aplica lista de precios, impuestos y descuentos.
            order._cart_update(product_id=product.id, set_qty=qty)
        else:
            # Con set_qty=0 _cart_update conserva la cantidad: se borra la línea directamente.
            order.order_line.filtered(lambda l: l.product_id == product).unlink()

        return dict(self._cart_payload(order, product), warning=warning)

    @http.route('/mayorista/confirmar', type='http', auth='user', methods=['POST'],
                website=True, sitemap=False)
    def confirm(self, note='', client_ref='', **kw):
        website = request.website
        order = self._get_draft_order()
        if not order or not order.order_line.filtered('product_id'):
            return request.redirect('/mayorista/carrito')

        order = order.sudo()
        try:
            # Savepoint: si la confirmación falla no quedan cambios a medias.
            with request.env.cr.savepoint():
                vals = {}
                note = (note or '').strip()
                if note:
                    vals['note'] = '%s\n%s' % (order.note, note) if order.note else note
                if (client_ref or '').strip():
                    vals['client_order_ref'] = client_ref.strip()[:64]
                if vals:
                    order.write(vals)
                if self._config().wholesale_auto_confirm:
                    # send_email=True: envía el correo de confirmación estándar de ventas.
                    order.with_context(send_email=True).action_confirm()
                else:
                    order.write({'state': 'sent'})
        except (UserError, ValidationError) as e:
            _logger.warning('App Mayorista: no se pudo confirmar %s: %s', order.name, e)
            return request.render('website_mayorista.cart_page', self._cart_page_values(
                order, error=_('No se pudo confirmar el pedido: %s') % e.args[0]))

        order._portal_ensure_token()
        website.sale_reset()  # La sesión empieza un pedido nuevo.
        return request.redirect('/mayorista/pedido/%s?access_token=%s' % (order.id, order.access_token))

    @http.route('/mayorista/pedido/<int:order_id>', type='http', auth='user', website=True, sitemap=False)
    def confirmation(self, order_id, access_token=None, **kw):
        order = request.env['sale.order'].sudo().browse(order_id).exists()
        if not order or not access_token or not consteq(order.access_token or '', access_token):
            raise NotFound()
        lines = order.order_line.filtered(lambda l: l.product_id and not l.display_type)
        return request.render('website_mayorista.confirmation_page', {
            'order': order,
            'lines': lines,
            'line_count': str(len(lines)),
            'units': self._fmt_qty(sum(lines.mapped('product_uom_qty'))),
            'confirmed': order.state in ('sale', 'done'),
        })
