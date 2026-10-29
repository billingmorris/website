# -*- coding: utf-8 -*-
{
    'name': 'App Mayorista (Website)',
    'summary': 'App web instalable (PWA) para pedidos de clientes mayoristas',
    'description': """
App web para clientes mayoristas sobre el website de Odoo 14:
catálogo con buscador, categorías, destacados, vista lista y galería,
inventario disponible, pedido con cantidades, seguimiento por etapas,
notificaciones, costo de envío, estado de facturación, chat con el
comercial, botón de WhatsApp e instalación como PWA (Android e iPhone).
    """,
    'version': '14.0.2.0.0',
    'category': 'Website/Website',
    'author': 'Desarrollo interno',
    'license': 'LGPL-3',
    'depends': [
        'website',
        'website_sale',   # categorías públicas, image_url, _cart_update
        'sale_stock',     # inventario (free_qty) y entregas al confirmar
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/mail_templates.xml',
        'data/wholesale_stage_data.xml',
        'data/website_menu.xml',
        'views/website_config_views.xml',
        'views/wholesale_stage_views.xml',
        'views/wholesale_notification_views.xml',
        'views/sale_order_views.xml',
        'views/product_template_views.xml',
        'views/menus.xml',
        'views/assets.xml',
        'views/pwa_templates.xml',
        'views/templates.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
