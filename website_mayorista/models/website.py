# -*- coding: utf-8 -*-
from odoo import fields, models

LIST_COLORS = [
    ('info', 'Azul'),
    ('success', 'Verde'),
    ('warning', 'Naranja'),
    ('danger', 'Rojo'),
    ('primary', 'Color principal'),
    ('muted', 'Gris'),
]

ORDER_STATES = [
    ('draft', 'Cotización (borrador)'),
    ('sent', 'Cotización enviada'),
    ('sale', 'Orden de venta confirmada'),
]


class Website(models.Model):
    """Configuración de la App Mayorista, una por sitio web."""
    _inherit = 'website'

    # --- Identidad de la app / PWA ---------------------------------------
    wholesale_app_name = fields.Char('Nombre de la app', default='App Mayorista', translate=True)
    wholesale_app_short_name = fields.Char(
        'Nombre corto', default='Mayorista', translate=True,
        help='Texto bajo el ícono en la pantalla de inicio del celular (máx. ~12 caracteres).')
    wholesale_app_logo = fields.Image(
        'Logo de la app', max_width=512, max_height=512,
        help='Se usa en la barra de la app y como ícono al instalarla. Ideal: PNG cuadrado de 512 px.')
    wholesale_background_image = fields.Image(
        'Imagen de fondo', max_width=1920, max_height=1920,
        help='Opcional. Se muestra suavizada detrás del contenido de la app.')
    wholesale_theme_color = fields.Char(
        'Color principal', help='Hexadecimal, por ejemplo #1F6F5C. Vacío: usa el color del tema del sitio.')
    wholesale_hide_website_chrome = fields.Boolean(
        'Ocultar cabecera y pie del sitio', default=True,
        help='Dentro de la app solo se ve la barra de la app (aspecto de aplicación instalada).')

    # --- Pedidos ----------------------------------------------------------
    wholesale_pricelist_id = fields.Many2one(
        'product.pricelist', 'Lista de precios del canal',
        help='Lista de precios para todos los pedidos de la app. '
             'Vacío: se usa la lista de precios asignada a cada cliente.')
    wholesale_team_id = fields.Many2one('crm.team', 'Equipo de ventas')
    wholesale_order_state = fields.Selection(
        ORDER_STATES, 'Estado del pedido al enviarlo', default='draft', required=True)
    wholesale_origin = fields.Char(
        'Documento origen', default='App Mayorista',
        help='Valor del campo "Documento origen" de los pedidos creados desde la app.')
    wholesale_send_email = fields.Boolean('Enviar correo de confirmación al cliente', default=True)
    wholesale_mail_template_id = fields.Many2one(
        'mail.template', 'Plantilla del correo', domain=[('model', '=', 'sale.order')],
        help='Vacío: se usa la plantilla "App Mayorista: pedido recibido".')
    wholesale_notify_backend = fields.Boolean('Notificar en el backend al recibir pedidos', default=True)
    wholesale_notify_salesperson = fields.Boolean('Notificar al comercial del cliente', default=True)
    wholesale_notify_user_ids = fields.Many2many(
        'res.users', 'website_wholesale_notify_user_rel', 'website_id', 'user_id',
        string='Notificar también a', domain=[('share', '=', False)])
    wholesale_notify_activity = fields.Boolean(
        'Crear actividad para el comercial', help='Agenda una tarea "Revisar pedido" al comercial.')
    wholesale_list_highlight = fields.Boolean('Resaltar pedidos de la app en las listas', default=True)
    wholesale_list_color = fields.Selection(LIST_COLORS, 'Color en la lista', default='info')
    wholesale_shipping_product_id = fields.Many2one(
        'product.product', 'Producto de envío',
        help='Producto (servicio) que representa el costo de envío. Su precio de venta es el costo por defecto.')
    wholesale_show_invoice_status = fields.Boolean('El cliente ve el estado de facturación', default=True)

    # --- Inventario y catálogo ---------------------------------------------
    wholesale_warehouse_id = fields.Many2one(
        'stock.warehouse', 'Almacén para inventario',
        help='Vacío: se suma el inventario de todos los almacenes de la compañía.')
    wholesale_limit_stock = fields.Boolean('Limitar cantidades al inventario disponible')
    wholesale_low_stock = fields.Float('Umbral de inventario bajo', default=10.0)
    wholesale_page_size = fields.Integer('Productos por página', default=40)

    # --- Comunicación -----------------------------------------------------
    wholesale_whatsapp_number = fields.Char(
        'Número de WhatsApp', help='Con indicativo de país, por ejemplo 573001234567.')
    wholesale_whatsapp_message = fields.Char(
        'Mensaje inicial de WhatsApp', default='Hola, les escribo desde la App Mayorista.', translate=True)
    wholesale_whatsapp_use_salesperson = fields.Boolean(
        'Usar el celular del comercial del cliente',
        help='Si el comercial tiene celular (o teléfono) se usa en lugar del número general.')
    wholesale_chat_enabled = fields.Boolean(
        'Activar chat con el cliente',
        help='Los mensajes del cliente llegan a Conversaciones (Discuss) del comercial asignado al cliente.')
    wholesale_chat_fallback_user_id = fields.Many2one(
        'res.users', 'Responsable del chat si no hay comercial', domain=[('share', '=', False)])
