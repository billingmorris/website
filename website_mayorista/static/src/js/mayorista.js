odoo.define('website_mayorista.app', function (require) {
    'use strict';

    /**
     * App Mayorista: comportamiento del catálogo y del pedido.
     *
     * - Búsqueda en tiempo real (debounce) contra /mayorista/productos, que devuelve
     *   el HTML de las tarjetas renderizado con la misma plantilla QWeb del servidor.
     * - Cambio lista/galería (recordado en localStorage).
     * - Selector de cantidad: los toques rápidos en +/− se agrupan en una sola
     *   llamada a /mayorista/carrito/actualizar por producto.
     * - Antes de confirmar, se envían las cantidades pendientes.
     *
     * Sin JS la app sigue funcionando: buscar y filtrar por categoría son GET normales.
     */
    var publicWidget = require('web.public.widget');
    var core = require('web.core');
    var _t = core._t;

    var VIEW_KEY = 'website_mayorista.view';
    var SEARCH_DELAY = 300;   // ms tras dejar de escribir
    var QTY_DELAY = 450;      // ms para agrupar toques en +/−

    function storageGet(key) {
        try { return window.localStorage.getItem(key); } catch (e) { return null; }
    }
    function storageSet(key, value) {
        try { window.localStorage.setItem(key, value); } catch (e) { /* navegación privada */ }
    }

    publicWidget.registry.MayoristaApp = publicWidget.Widget.extend({
        selector: '.o_mayorista_app',
        events: {
            'input .o_my_search_input': '_onSearchInput',
            'submit .o_my_search': '_onSearchSubmit',
            'click .o_my_view_btn': '_onViewClick',
            'click .o_my_load_more': '_onLoadMore',
            'click .o_my_qty_btn': '_onQtyButton',
            'change .o_my_qty_input': '_onQtyInput',
            'focus .o_my_qty_input': '_onQtyFocus',
            'submit .o_my_checkout': '_onCheckoutSubmit',
        },

        start: function () {
            this.search = this.$el.attr('data-search') || '';
            this.category = this.$el.attr('data-category') || '';
            this.offset = parseInt(this.$el.attr('data-offset'), 10) || 0;
            this.isCart = this.$el.hasClass('o_my_is_cart');
            this._reqId = 0;          // descarta respuestas de búsquedas viejas
            this._timers = {};        // product_id -> timeout pendiente
            this._pendingQty = {};    // product_id -> cantidad aún no enviada
            this._seq = {};           // product_id -> número de la última petición
            this._inflight = [];
            this._debouncedSearch = _.debounce(this._fetchProducts.bind(this, false), SEARCH_DELAY);
            if (!this.isCart) {
                this._applyView(storageGet(VIEW_KEY) || 'list');
            }
            return this._super.apply(this, arguments);
        },

        destroy: function () {
            _.each(this._timers, function (t) { clearTimeout(t); });
            clearTimeout(this._toastTimer);
            this._super.apply(this, arguments);
        },

        //----------------------------------------------------------------------
        // Búsqueda y listado
        //----------------------------------------------------------------------

        _onSearchInput: function (ev) {
            var value = ev.currentTarget.value.trim();
            if (value === this.search) {
                return;
            }
            this.search = value;
            this._debouncedSearch();
        },

        _onSearchSubmit: function (ev) {
            // Con JS no recargamos la página; cerramos el teclado del móvil.
            ev.preventDefault();
            var $input = this.$('.o_my_search_input');
            this.search = $input.val().trim();
            $input.blur();
            this._fetchProducts(false);
        },

        _onLoadMore: function (ev) {
            ev.preventDefault();
            this._fetchProducts(true);
        },

        _fetchProducts: function (append) {
            var self = this;
            var reqId = ++this._reqId;
            this.$el.addClass('o_my_loading');
            return this._rpc({
                route: '/mayorista/productos',
                params: {
                    search: this.search,
                    category: this.category || false,
                    offset: append ? this.offset : 0,
                },
            }).then(function (res) {
                if (reqId !== self._reqId) {
                    return;
                }
                var $list = self.$('.o_my_catalog .o_my_products');
                if (append) {
                    $list.append(res.html);
                } else {
                    $list.html(res.html);
                    window.scrollTo(0, 0);
                }
                self.offset = res.next_offset;
                self.$('.o_my_load_more').toggleClass('d-none', !res.has_more);
                self.$('.o_my_result_count').text(res.count);
                self.$('.o_my_empty').toggleClass('d-none', res.count > 0);
                // Los destacados solo tienen sentido en la portada sin búsqueda.
                self.$('.o_my_featured').toggleClass('d-none', !!self.search);
                self._updateUrl();
            }, function () {
                self._toast(_t('No se pudo cargar el catálogo. Revise su conexión.'));
            }).then(function () {
                if (reqId === self._reqId) {
                    self.$el.removeClass('o_my_loading');
                }
            });
        },

        /** Mantiene la URL compartible/recargable sin recargar la página. */
        _updateUrl: function () {
            var params = [];
            if (this.search) {
                params.push('search=' + encodeURIComponent(this.search));
            }
            if (this.category) {
                params.push('category=' + encodeURIComponent(this.category));
            }
            window.history.replaceState(null, '', '/mayorista' + (params.length ? '?' + params.join('&') : ''));
        },

        //----------------------------------------------------------------------
        // Vista lista / galería
        //----------------------------------------------------------------------

        _onViewClick: function (ev) {
            var view = $(ev.currentTarget).data('view');
            this._applyView(view);
            storageSet(VIEW_KEY, view);
        },

        _applyView: function (view) {
            view = view === 'gallery' ? 'gallery' : 'list';
            this.$('.o_my_catalog .o_my_products')
                .toggleClass('o_my_view_list', view === 'list')
                .toggleClass('o_my_view_gallery', view === 'gallery');
            this.$('.o_my_view_btn').each(function () {
                var active = $(this).data('view') === view;
                $(this).toggleClass('active', active).attr('aria-pressed', active ? 'true' : 'false');
            });
        },

        //----------------------------------------------------------------------
        // Cantidades
        //----------------------------------------------------------------------

        _onQtyFocus: function (ev) {
            ev.currentTarget.select();
        },

        _onQtyButton: function (ev) {
            var $btn = $(ev.currentTarget);
            var $input = $btn.closest('.o_my_stepper').find('.o_my_qty_input');
            var qty = (parseInt($input.val(), 10) || 0) + parseInt($btn.data('delta'), 10);
            this._setQty($btn.closest('.o_my_card'), this._clampQty($input, qty), false);
        },

        _onQtyInput: function (ev) {
            var $input = $(ev.currentTarget);
            var qty = parseInt($input.val(), 10) || 0;
            this._setQty($input.closest('.o_my_card'), this._clampQty($input, qty), true);
        },

        /** Límite inferior 0 y, si está configurado, máximo = inventario disponible. */
        _clampQty: function ($input, qty) {
            qty = Math.max(0, qty);
            var max = parseInt($input.attr('max'), 10);
            if (!isNaN(max) && qty > max) {
                qty = max;
                this._toast(max > 0
                    ? _.str.sprintf(_t('Solo hay %s disponibles.'), max)
                    : _t('Producto agotado.'));
            }
            return qty;
        },

        _setQty: function ($card, qty, immediate) {
            var self = this;
            var productId = parseInt($card.attr('data-product-id'), 10);
            // Respuesta visual inmediata en todas las tarjetas del producto
            // (puede estar a la vez en destacados y en el listado).
            this._syncCards(productId, qty);
            clearTimeout(this._timers[productId]);
            this._pendingQty[productId] = qty;
            if (immediate) {
                return this._sendQty(productId);
            }
            this._timers[productId] = setTimeout(function () {
                self._sendQty(productId);
            }, QTY_DELAY);
        },

        _sendQty: function (productId) {
            var self = this;
            var qty = this._pendingQty[productId];
            clearTimeout(this._timers[productId]);
            delete this._timers[productId];
            delete this._pendingQty[productId];
            var seq = this._seq[productId] = (this._seq[productId] || 0) + 1;

            var prom = this._rpc({
                route: '/mayorista/carrito/actualizar',
                params: {product_id: productId, quantity: qty},
            }).then(function (res) {
                // Si ya salió otra petición para este producto, gana la más nueva.
                if (seq === self._seq[productId]) {
                    self._applyCartResult(res);
                }
            }, function () {
                self._toast(_t('No se pudo actualizar el pedido. Intente de nuevo.'));
            });
            this._inflight.push(prom);
            prom.then(function () {
                self._inflight = _.without(self._inflight, prom);
            });
            return prom;
        },

        _cardsFor: function (productId) {
            return this.$('.o_my_card[data-product-id="' + productId + '"]');
        },

        _syncCards: function (productId, qty) {
            var $cards = this._cardsFor(productId);
            $cards.find('.o_my_qty_input').val(qty);
            $cards.toggleClass('o_my_in_cart', qty > 0);
        },

        /** Refleja la respuesta del servidor (cantidad real, subtotal y totales). */
        _applyCartResult: function (res) {
            if (res.error) {
                this._toast(res.error);
            } else if (res.warning) {
                this._toast(res.warning);
            }
            if (res.product_id) {
                this._syncCards(res.product_id, res.quantity);
                var $cards = this._cardsFor(res.product_id);
                $cards.find('.js_my_line_subtotal').text(res.line_subtotal);
                if (this.isCart && !res.quantity) {
                    $cards.remove();
                }
            }
            this.$('.js_my_lines').text(res.lines);
            this.$('.js_my_untaxed').text(res.amount_untaxed);
            this.$('.js_my_tax').text(res.amount_tax);
            this.$('.js_my_total').text(res.amount_total);
            this.$('.o_my_cartbar').toggleClass('o_my_cartbar_empty', !res.has_lines);
            if (this.isCart && !res.has_lines) {
                window.location.reload();   // muestra el estado "pedido vacío"
            }
        },

        //----------------------------------------------------------------------
        // Confirmación
        //----------------------------------------------------------------------

        _onCheckoutSubmit: function (ev) {
            var self = this;
            var form = ev.currentTarget;
            var $btn = this.$('.o_my_confirm_btn');
            if ($btn.prop('disabled')) {
                ev.preventDefault();   // evita doble envío
                return;
            }
            $btn.prop('disabled', true).addClass('o_my_busy');

            var pendingIds = Object.keys(this._pendingQty);
            if (!pendingIds.length && !this._inflight.length) {
                return;   // envío normal del formulario
            }
            // Hay cantidades sin guardar: se envían primero y luego se confirma.
            ev.preventDefault();
            var proms = pendingIds.map(function (pid) {
                return self._sendQty(parseInt(pid, 10));
            });
            Promise.all(proms.concat(this._inflight)).then(function () {
                form.submit();
            }, function () {
                $btn.prop('disabled', false).removeClass('o_my_busy');
            });
        },

        //----------------------------------------------------------------------
        // Avisos
        //----------------------------------------------------------------------

        _toast: function (message) {
            var $toast = this.$('.o_my_toast');
            $toast.text(message).addClass('o_my_toast_show');
            clearTimeout(this._toastTimer);
            this._toastTimer = setTimeout(function () {
                $toast.removeClass('o_my_toast_show');
            }, 3000);
        },
    });

    return publicWidget.registry.MayoristaApp;
});
