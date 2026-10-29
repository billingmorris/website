odoo.define('website_mayorista.app', function (require) {
    'use strict';

    /**
     * App Mayorista: comportamiento de todas las pantallas de /mayorista.
     *
     * - Catálogo: búsqueda en tiempo real, "cargar más", vista lista/galería.
     * - Cantidades: los toques rápidos en +/− se agrupan en una llamada por producto.
     * - Pedido en curso: totales en vivo y envío seguro (primero las cantidades pendientes).
     * - Menú lateral, contadores de notificaciones y chat (consulta periódica).
     * - Chat con el comercial: lectura cada pocos segundos mientras está abierto.
     * - PWA: registro del service worker y botón "Instalar app" (Android) o ayuda (iPhone).
     */
    var publicWidget = require('web.public.widget');
    var core = require('web.core');
    var _t = core._t;

    var VIEW_KEY = 'website_mayorista.view';
    var SEARCH_DELAY = 300;     // ms tras dejar de escribir
    var QTY_DELAY = 450;        // ms para agrupar toques en +/−
    var STATUS_EVERY = 30000;   // ms entre consultas de contadores
    var CHAT_EVERY = 4000;      // ms entre lecturas del chat abierto

    var deferredInstallPrompt = null;   // evento beforeinstallprompt (Android/Chrome)
    window.addEventListener('beforeinstallprompt', function (ev) {
        ev.preventDefault();
        deferredInstallPrompt = ev;
        $('.o_my_install').removeClass('d-none');
    });

    function storageGet(key) {
        try { return window.localStorage.getItem(key); } catch (e) { return null; }
    }
    function storageSet(key, value) {
        try { window.localStorage.setItem(key, value); } catch (e) { /* navegación privada */ }
    }
    function isIos() {
        return /iphone|ipad|ipod/i.test(navigator.userAgent) ||
            (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    }
    function isStandalone() {
        return window.navigator.standalone === true ||
            (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
    }
    function badgeText(count) {
        return count > 9 ? '9+' : String(count || '');
    }

    publicWidget.registry.MayoristaApp = publicWidget.Widget.extend({
        selector: '.o_mayorista_app',
        events: {
            // Catálogo
            'input .o_my_search_input': '_onSearchInput',
            'submit .o_my_search': '_onSearchSubmit',
            'click .o_my_view_btn': '_onViewClick',
            'click .o_my_load_more': '_onLoadMore',
            // Cantidades y envío del pedido
            'click .o_my_qty_btn': '_onQtyButton',
            'change .o_my_qty_input': '_onQtyInput',
            'focus .o_my_qty_input': '_onQtyFocus',
            'submit .o_my_checkout': '_onCheckoutSubmit',
            // Menú lateral
            'click .o_my_menu_btn': '_openDrawer',
            'click .o_my_drawer_close': '_closeDrawer',
            'click .o_my_backdrop': '_closeDrawer',
            // Chat
            'click .o_my_chat_open': '_onChatOpen',
            'click .o_my_chat_close': '_closeChat',
            'submit .o_my_chat_form': '_onChatSubmit',
            'keydown .o_my_chat_input': '_onChatKeydown',
            'input .o_my_chat_input': '_autosizeChatInput',
            // Instalación
            'click .o_my_install': '_onInstall',
            'click .o_my_ios_hint_close': '_closeIosHint',
        },

        start: function () {
            var self = this;
            var $catalog = this.$('.o_my_catalog');
            this.search = $catalog.attr('data-search') || '';
            this.category = $catalog.attr('data-category') || '';
            this.offset = parseInt($catalog.attr('data-offset'), 10) || 0;
            this.isCart = this.$el.hasClass('o_my_is_cart');

            this._reqId = 0;          // descarta respuestas de búsquedas viejas
            this._timers = {};        // product_id -> timeout pendiente
            this._pendingQty = {};    // product_id -> cantidad aún no enviada
            this._seq = {};           // product_id -> número de la última petición
            this._inflight = [];
            this._chatLastId = 0;
            this._chatIds = {};
            this._debouncedSearch = _.debounce(this._fetchProducts.bind(this, false), SEARCH_DELAY);

            if ($catalog.length) {
                this._applyView(storageGet(VIEW_KEY) || 'list');
            }
            this._onKeydown = function (ev) {
                if (ev.key === 'Escape') {
                    self._closeDrawer();
                    self._closeChat();
                }
            };
            document.addEventListener('keydown', this._onKeydown);

            this._registerServiceWorker();
            this._setupInstall();
            this._statusTimer = setInterval(this._refreshStatus.bind(this), STATUS_EVERY);
            if (this.$('.o_my_chat').length) {
                this._refreshStatus();
            }
            return this._super.apply(this, arguments);
        },

        destroy: function () {
            _.each(this._timers, function (t) { clearTimeout(t); });
            clearTimeout(this._toastTimer);
            clearInterval(this._statusTimer);
            clearInterval(this._chatTimer);
            document.removeEventListener('keydown', this._onKeydown);
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
            ev.preventDefault();   // con JS no se recarga la página
            var $input = this.$('.o_my_search_input');
            this.search = $input.val().trim();
            $input.blur();         // cierra el teclado del celular
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
                params: {search: this.search, category: this.category || false, offset: append ? this.offset : 0},
            }).then(function (res) {
                if (reqId !== self._reqId) {
                    return;
                }
                var $list = self.$('.o_my_catalog .o_my_products');
                if (append) {
                    $list.append(res.html);
                } else {
                    $list.html(res.html);
                }
                self.offset = res.next_offset;
                self.$('.o_my_load_more').toggleClass('d-none', !res.has_more);
                self.$('.o_my_result_count').text(res.count);
                self.$('.o_my_empty').toggleClass('d-none', res.count > 0);
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
            this._setQty($input.closest('.o_my_card'), this._clampQty($input, parseInt($input.val(), 10) || 0), true);
        },

        _clampQty: function ($input, qty) {
            qty = Math.max(0, qty);
            var max = parseInt($input.attr('max'), 10);
            if (!isNaN(max) && qty > max) {
                qty = max;
                this._toast(max > 0 ? _.str.sprintf(_t('Solo hay %s disponibles.'), max) : _t('Producto agotado.'));
            }
            return qty;
        },

        _setQty: function ($card, qty, immediate) {
            var self = this;
            var productId = parseInt($card.attr('data-product-id'), 10);
            this._syncCards(productId, qty);   // respuesta visual inmediata
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
                if (seq === self._seq[productId]) {   // gana la respuesta más reciente
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
                    var $groups = $cards.closest('.o_my_group');
                    $cards.remove();
                    $groups.each(function () {
                        var $group = $(this);
                        var left = $group.find('.o_my_card').length;
                        if (left) {
                            $group.find('.o_my_group_count').text(left);
                        } else {
                            $group.remove();
                        }
                    });
                }
            }
            this.$('.js_my_lines').text(res.lines);
            this.$('.js_my_products_subtotal').text(res.products_subtotal);
            this.$('.js_my_shipping').text(res.shipping);
            this.$('.js_my_tax').text(res.amount_tax);
            this.$('.js_my_total').text(res.amount_total);
            this.$('.o_my_cartbar').toggleClass('o_my_cartbar_empty', !res.has_lines);
            if (this.isCart && !res.has_lines) {
                window.location.reload();   // muestra el estado "pedido vacío"
            }
        },

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
            // Primero se guardan las cantidades pendientes y luego se envía.
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
        // Menú lateral
        //----------------------------------------------------------------------

        _openDrawer: function () {
            this.$el.addClass('o_my_drawer_open');
            this.$('.o_my_menu_btn').attr('aria-expanded', 'true');
            this.$('.o_my_drawer a, .o_my_drawer button').first().focus();
        },

        _closeDrawer: function () {
            if (!this.$el.hasClass('o_my_drawer_open')) {
                return;
            }
            this.$el.removeClass('o_my_drawer_open');
            this.$('.o_my_menu_btn').attr('aria-expanded', 'false').focus();
        },

        //----------------------------------------------------------------------
        // Contadores (notificaciones y chat)
        //----------------------------------------------------------------------

        _refreshStatus: function () {
            var self = this;
            if (document.hidden) {
                return;
            }
            return this._rpc({route: '/mayorista/estado', params: {}}).then(function (res) {
                self.$('.js_my_notif_badge').text(badgeText(res.notifications))
                    .toggleClass('d-none', !res.notifications);
                var chatOpen = !self.$('.o_my_chat').prop('hidden');
                self.$('.js_my_chat_badge').text(badgeText(res.chat))
                    .toggleClass('d-none', !res.chat || chatOpen);
            }, function () { /* sin conexión: se reintenta en la próxima vuelta */ });
        },

        //----------------------------------------------------------------------
        // Chat con el comercial
        //----------------------------------------------------------------------

        _onChatOpen: function (ev) {
            ev.preventDefault();
            var prefill = $(ev.currentTarget).data('prefill');
            this._closeDrawer();
            var $chat = this.$('.o_my_chat');
            if (!$chat.length) {
                return;
            }
            $chat.prop('hidden', false);
            this.$el.addClass('o_my_chat_is_open');
            this.$('.js_my_chat_badge').addClass('d-none');
            var $input = this.$('.o_my_chat_input');
            if (prefill && !$input.val()) {
                $input.val(prefill);
            }
            $input.focus();
            this._loadChat();
            clearInterval(this._chatTimer);
            this._chatTimer = setInterval(this._loadChat.bind(this), CHAT_EVERY);
        },

        _closeChat: function () {
            var $chat = this.$('.o_my_chat');
            if (!$chat.length || $chat.prop('hidden')) {
                return;
            }
            $chat.prop('hidden', true);
            this.$el.removeClass('o_my_chat_is_open');
            clearInterval(this._chatTimer);
        },

        _loadChat: function () {
            var self = this;
            if (this._chatLoading || document.hidden) {
                return Promise.resolve();
            }
            this._chatLoading = true;
            return this._rpc({
                route: '/mayorista/chat/mensajes',
                params: {after_id: this._chatLastId},
            }).then(function (res) {
                if (!res.enabled) {
                    self._closeChat();
                    return;
                }
                self._renderMessages(res.messages || []);
            }, function () { /* se reintenta en la próxima vuelta */ }).then(function () {
                self._chatLoading = false;
            });
        },

        _renderMessages: function (messages) {
            var self = this;
            var $log = this.$('.o_my_chat_log');
            var added = false;
            messages.forEach(function (m) {
                if (self._chatIds[m.id]) {
                    return;
                }
                self._chatIds[m.id] = true;
                self._chatLastId = Math.max(self._chatLastId, m.id);
                var $msg = $('<div class="o_my_msg"/>').toggleClass('o_my_msg_mine', !!m.mine);
                // El cuerpo viene sanitizado por Odoo (campo HTML de mail.message).
                $('<div class="o_my_msg_body"/>').html(m.body).appendTo($msg);
                var $meta = $('<div class="o_my_msg_meta"/>');
                if (!m.mine) {
                    $('<span class="o_my_msg_author"/>').text(m.author).appendTo($meta);
                }
                $('<time/>').text(m.date).appendTo($meta);
                $meta.appendTo($msg);
                $log.append($msg);
                added = true;
            });
            if (added) {
                $log.find('.o_my_chat_empty').remove();
                $log.scrollTop($log[0].scrollHeight);
            }
        },

        _onChatKeydown: function (ev) {
            // Enter envía; Shift+Enter hace salto de línea.
            if (ev.key === 'Enter' && !ev.shiftKey) {
                ev.preventDefault();
                this.$('.o_my_chat_form').trigger('submit');
            }
        },

        _autosizeChatInput: function (ev) {
            var el = ev.currentTarget;
            el.style.height = 'auto';
            el.style.height = Math.min(el.scrollHeight, 120) + 'px';
        },

        _onChatSubmit: function (ev) {
            var self = this;
            ev.preventDefault();
            var $input = this.$('.o_my_chat_input');
            var $send = this.$('.o_my_chat_send');
            var body = $input.val().trim();
            if (!body || $send.prop('disabled')) {
                return;
            }
            $send.prop('disabled', true);
            this._rpc({route: '/mayorista/chat/enviar', params: {body: body}}).then(function (res) {
                if (res.ok) {
                    $input.val('').css('height', '');
                    return self._loadChat();
                }
                self._toast(res.error || _t('No se pudo enviar el mensaje.'));
            }, function () {
                self._toast(_t('No se pudo enviar el mensaje. Revise su conexión.'));
            }).then(function () {
                $send.prop('disabled', false);
                $input.focus();
            });
        },

        //----------------------------------------------------------------------
        // PWA
        //----------------------------------------------------------------------

        _registerServiceWorker: function () {
            if (!('serviceWorker' in navigator) || window.location.protocol !== 'https:' &&
                    window.location.hostname !== 'localhost') {
                return;
            }
            navigator.serviceWorker.register('/mayorista/sw.js', {scope: '/mayorista'}).catch(function () {
                /* Sin SW la app funciona igual; solo no habrá página sin conexión. */
            });
        },

        _setupInstall: function () {
            if (isStandalone()) {
                return;   // ya está instalada
            }
            if (deferredInstallPrompt || isIos()) {
                this.$('.o_my_install').removeClass('d-none');
            }
        },

        _onInstall: function () {
            var self = this;
            this._closeDrawer();
            if (deferredInstallPrompt) {
                deferredInstallPrompt.prompt();
                deferredInstallPrompt.userChoice.then(function (choice) {
                    if (choice.outcome === 'accepted') {
                        self.$('.o_my_install').addClass('d-none');
                    }
                    deferredInstallPrompt = null;
                });
            } else if (isIos()) {
                this.$('.o_my_ios_hint').prop('hidden', false);
            }
        },

        _closeIosHint: function () {
            this.$('.o_my_ios_hint').prop('hidden', true);
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
