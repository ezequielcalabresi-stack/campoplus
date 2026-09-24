/**
 * CAmpo+ - formato moneda ARS (miles + 2 decimales, es-AR).
 * Uso: class="money-input" o data-money en <input type="text">.
 * API: CampoMoney.parse / .format / .bind / .onFocus / .onBlur
 */
(function (global) {
    function parseMoney(val) {
        if (val == null || val === '') return 0;
        let s = String(val).trim().replace(/\$/g, '').replace(/\s/g, '');
        if (!s) return 0;
        const lastComma = s.lastIndexOf(',');
        const lastDot = s.lastIndexOf('.');
        if (lastComma >= 0 && lastDot >= 0) {
            const decPos = Math.max(lastComma, lastDot);
            const entero = s.slice(0, decPos).replace(/[.,]/g, '');
            const decimal = s.slice(decPos + 1).replace(/[.,]/g, '');
            s = entero + '.' + decimal;
        } else if (lastComma >= 0) {
            s = s.replace(/\./g, '').replace(',', '.');
        } else if ((s.match(/\./g) || []).length > 1) {
            s = s.replace(/\./g, '');
        }
        const n = parseFloat(s);
        return Number.isFinite(n) ? n : 0;
    }

    function formatMoney(n, dec) {
        const d = dec == null ? 2 : dec;
        return Number(n || 0).toLocaleString('es-AR', {
            minimumFractionDigits: d,
            maximumFractionDigits: d,
        });
    }

    function onFocus(el) {
        if (!el || el.disabled || el.readOnly) return;
        const n = parseMoney(el.value);
        el.value = n ? String(n).replace('.', ',') : '';
        try { el.select(); } catch (_) { /* */ }
    }

    function onBlur(el) {
        if (!el) return;
        el.value = formatMoney(parseMoney(el.value), 2);
        el.dispatchEvent(new Event('money-formatted', { bubbles: true }));
    }

    function wireInput(el) {
        if (!el || el.dataset.moneyBound === '1') return;
        el.dataset.moneyBound = '1';
        el.classList.add('money-input');
        el.setAttribute('inputmode', 'decimal');
        el.setAttribute('autocomplete', 'off');
        if (el.type === 'number') {
            // type=number no admite "1.000,00"
            const n = parseMoney(el.value);
            el.type = 'text';
            el.removeAttribute('step');
            el.removeAttribute('min');
            el.removeAttribute('max');
            el.value = formatMoney(n, 2);
        } else if (el.value === '' || el.value == null) {
            el.value = '0,00';
        } else if (!/[.,]/.test(String(el.value)) || String(el.value).indexOf(',') < 0) {
            el.value = formatMoney(parseMoney(el.value), 2);
        }
        el.addEventListener('focus', function () { onFocus(el); });
        el.addEventListener('blur', function () { onBlur(el); });
    }

    function bind(root) {
        const scope = root && root.querySelectorAll ? root : document;
        scope.querySelectorAll('input[data-money], input.money-input').forEach(wireInput);
    }

    function upgradeSelector(selector, root) {
        const scope = root && root.querySelectorAll ? root : document;
        scope.querySelectorAll(selector).forEach(function (el) {
            el.setAttribute('data-money', '1');
            wireInput(el);
        });
    }

    const api = {
        parse: parseMoney,
        format: formatMoney,
        onFocus: onFocus,
        onBlur: onBlur,
        bind: bind,
        upgrade: upgradeSelector,
        wire: wireInput,
    };

    global.CampoMoney = api;
    // Aliases globales (compat. con páginas que ya usan estos nombres)
    if (typeof global.parseMoney !== 'function') global.parseMoney = parseMoney;
    if (typeof global.formatMoney !== 'function') global.formatMoney = formatMoney;
    if (typeof global.onMoneyFocus !== 'function') global.onMoneyFocus = onFocus;
    if (typeof global.onMoneyBlur !== 'function') global.onMoneyBlur = onBlur;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { bind(document); });
    } else {
        bind(document);
    }
})(typeof window !== 'undefined' ? window : this);
