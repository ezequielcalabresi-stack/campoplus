/**
 * Ingreso de fechas al estilo Access, vista dd/mm/aaaa y calendario de Windows.
 * 2-9, 1.9 o 2/9/25 se completan al salir del campo.
 * Lo que lee el resto del sistema sigue siendo aaaa-mm-dd.
 */
(function () {
  "use strict";

  var proto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function interpretar(valor) {
    var s = String(valor == null ? "" : valor).trim();
    if (!s) return null;
    var iso = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
    var d;
    var m;
    var y;
    if (iso) {
      y = parseInt(iso[1], 10);
      m = parseInt(iso[2], 10);
      d = parseInt(iso[3], 10);
    } else {
      var partes = s.split(/[\/\-\.\s]+/).filter(Boolean);
      if (partes.length < 2 || partes.length > 3) return null;
      if (!/^\d{1,4}$/.test(partes[0]) || !/^\d{1,2}$/.test(partes[1])) return null;
      d = parseInt(partes[0], 10);
      m = parseInt(partes[1], 10);
      if (partes.length === 3) {
        if (!/^\d{2,4}$/.test(partes[2])) return null;
        y = parseInt(partes[2], 10);
        if (y < 100) y += y >= 50 ? 1900 : 2000;
      } else {
        y = new Date().getFullYear();
      }
    }
    if (m < 1 || m > 12 || d < 1 || d > 31) return null;
    var dt = new Date(y, m - 1, d);
    if (dt.getFullYear() !== y || dt.getMonth() !== m - 1 || dt.getDate() !== d) return null;
    return { d: d, m: m, y: y };
  }

  function aIso(f) {
    return f.y + "-" + pad(f.m) + "-" + pad(f.d);
  }

  function aVisual(f) {
    return pad(f.d) + "/" + pad(f.m) + "/" + f.y;
  }

  function esCampoFecha(input) {
    if (!input || input.dataset.fechaPicker === "1" || input.dataset.fechaAr === "1") return false;
    var tipo = (input.type || "").toLowerCase();
    if (tipo === "date") return true;
    if (tipo !== "text") return false;
    var ph = (input.getAttribute("placeholder") || "").toLowerCase();
    return ph.indexOf("dd/mm") >= 0 || input.dataset.fecha === "1";
  }

  function completar(input, picker) {
    var f = interpretar(proto.get.call(input));
    if (!f) return false;
    proto.set.call(input, aVisual(f));
    if (picker) picker.value = aIso(f);
    return true;
  }

  function adaptar(input) {
    if (!esCampoFecha(input)) return;
    input.dataset.fechaAr = "1";
    var inicial = proto.get.call(input);
    if ((input.type || "").toLowerCase() === "date") input.type = "text";
    input.placeholder = "dd/mm/aaaa";
    input.autocomplete = "off";
    input.maxLength = 10;
    input.style.paddingRight = "2.1rem";

    var padre = input.parentNode;
    if (padre && !padre.classList.contains("campo-fecha-wrap")) {
      var wrap = document.createElement("span");
      wrap.className = "campo-fecha-wrap";
      wrap.style.position = "relative";
      wrap.style.display = "inline-block";
      wrap.style.width = input.style.width || "100%";
      wrap.style.maxWidth = "100%";
      padre.insertBefore(wrap, input);
      wrap.appendChild(input);
      padre = wrap;
    }
    input.style.width = "100%";

    var picker = document.createElement("input");
    picker.type = "date";
    picker.tabIndex = -1;
    picker.dataset.fechaPicker = "1";
    picker.title = "Abrir calendario";
    picker.setAttribute("aria-label", "Calendario");
    picker.style.position = "absolute";
    picker.style.right = "2px";
    picker.style.top = "50%";
    picker.style.transform = "translateY(-50%)";
    picker.style.width = "1.7rem";
    picker.style.height = "1.6rem";
    picker.style.border = "0";
    picker.style.background = "transparent";
    picker.style.cursor = "pointer";
    picker.style.padding = "0";
    padre.appendChild(picker);

    Object.defineProperty(input, "value", {
      configurable: true,
      get: function () {
        var f = interpretar(proto.get.call(this));
        return f ? aIso(f) : proto.get.call(this);
      },
      set: function (v) {
        var f = interpretar(v);
        proto.set.call(this, f ? aVisual(f) : String(v == null ? "" : v));
        if (f) picker.value = aIso(f);
      },
    });

    input.addEventListener("blur", function () {
      completar(input, picker);
    });
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") completar(input, picker);
    });
    picker.addEventListener("change", function () {
      if (picker.value) input.value = picker.value;
    });
    picker.addEventListener("click", function () {
      if (typeof picker.showPicker === "function") {
        try { picker.showPicker(); } catch (e) { /* el ícono nativo igual abre el calendario */ }
      }
    });

    if (inicial) input.value = inicial;
  }

  function recorrer(raiz) {
    var base = raiz && raiz.querySelectorAll ? raiz : document;
    if (base.matches && base.matches("input")) adaptar(base);
    if (!base.querySelectorAll) return;
    base.querySelectorAll('input[type="date"], input[type="text"]').forEach(adaptar);
  }

  function arrancar() {
    recorrer(document);
    var obs = new MutationObserver(function (cambios) {
      cambios.forEach(function (c) {
        c.addedNodes.forEach(function (n) {
          if (n.nodeType === 1) recorrer(n);
        });
      });
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", arrancar);
  else arrancar();
})();
