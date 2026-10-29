# -*- coding: utf-8 -*-
{
    'name': 'App Mayorista (Website)',
    'summary': 'Catálogo y toma de pedidos mobile-first para clientes mayoristas',
    'description': """
App web para clientes mayoristas sobre el website de Odoo:
buscador por nombre/código, categorías, destacados, vista lista y galería,
inventario disponible, pedido con cantidades y confirmación a pantalla completa.
El pedido se registra como orden de venta (sale.order).
    """,
    'version': '14.0.1.0.0',
    'category': 'Website/Website',
    'author': 'Desarrollo interno',
    'license': 'LGPL-3',
    'depends': [
        'website',
        'website_sale',   # carrito (sale.order en sesión), listas de precios, categorías públicas
        'sale_stock',     # inventario (free_qty) y entregas al confirmar
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/website_views.xml',
        'views/product_template_views.xml',
        'views/assets.xml',
        'views/templates.xml',
        'data/website_menu.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
