# -*- coding: utf-8 -*-
from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # Marca los productos que aparecen en la franja "Destacados" de /mayorista.
    is_wholesale_featured = fields.Boolean(
        string='Destacado en App Mayorista',
        help='Si está marcado, el producto aparece en la sección de destacados '
             'de la página principal de la App Mayorista.',
    )
