# -*- coding: utf-8 -*-
from odoo import fields, models


class Website(models.Model):
    """Configuración de la App Mayorista por sitio web.

    Se guarda en `website` (y no en res.config.settings) para soportar
    varios sitios con almacenes o reglas distintas.
    """
    _inherit = 'website'

    wholesale_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Almacén para inventario',
        help='Almacén del que se lee la cantidad disponible. '
             'Si se deja vacío se suma el inventario de todos los almacenes de la compañía.',
    )
    wholesale_auto_confirm = fields.Boolean(
        string='Confirmar pedidos automáticamente',
        default=True,
        help='Marcado: el pedido se confirma como orden de venta (genera la entrega). '
             'Desmarcado: queda como cotización enviada para revisión del vendedor.',
    )
    wholesale_limit_stock = fields.Boolean(
        string='Limitar cantidades al inventario disponible',
        help='Impide pedir más unidades de las disponibles en productos almacenables.',
    )
    wholesale_low_stock = fields.Float(
        string='Umbral de inventario bajo',
        default=10.0,
        help='Por debajo de esta cantidad el inventario se resalta como bajo.',
    )
    wholesale_page_size = fields.Integer(
        string='Productos por página',
        default=40,
    )
