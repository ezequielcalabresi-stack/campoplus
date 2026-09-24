/**
 * CAmpo+ - Firma de operador y trazabilidad.
 * Incluir en todas las pantallas: <script src="/campoplus-firma.js"></script>
 * - Guarda el usuario activo en localStorage
 * - Inyecta X-Usuario-* en todo fetch hacia /api/
 * - Completa usuario_registro en JSON si falta
 * - Barra flotante de firma (esquina inferior derecha)
 */
(function () {
  "use strict";
  const KEY = "campoplus_usuario_activo";
  const API = "/api/usuarios";

  function loadUser() {
    try {
      return JSON.parse(localStorage.getItem(KEY) || "null");
    } catch (_) {
      return null;
    }
  }

  function saveUser(u) {
    if (!u) {
      localStorage.removeItem(KEY);
      return;
    }
    localStorage.setItem(
      KEY,
      JSON.stringify({
        id: u.id,
        nombre: u.nombre,
        email: u.email || "",
        rol: u.rol || "",
      })
    );
  }

  function headersFirma(extra) {
    const u = loadUser();
    const h = Object.assign({}, extra || {});
    if (u && u.nombre) {
      h["X-Usuario-Id"] = String(u.id || "");
      h["X-Usuario-Nombre"] = u.nombre;
      if (u.rol) h["X-Usuario-Rol"] = u.rol;
    }
    return h;
  }

  function enrichBody(body, method) {
    const u = loadUser();
    if (!u || !u.nombre) return body;
    if (typeof body !== "string") return body;
    const m = (method || "GET").toUpperCase();
    if (!["POST", "PUT", "PATCH"].includes(m)) return body;
    try {
      const data = JSON.parse(body);
      if (data && typeof data === "object" && !Array.isArray(data)) {
        if (!data.usuario_registro) data.usuario_registro = u.nombre;
        if (!data.usuario_firma) data.usuario_firma = u.nombre;
        return JSON.stringify(data);
      }
    } catch (_) {}
    return body;
  }

  // Patch fetch una sola vez
  if (!window.__campoplusFetchPatched) {
    window.__campoplusFetchPatched = true;
    const _fetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      init = init || {};
      let url = typeof input === "string" ? input : (input && input.url) || "";
      const isApi =
        url.indexOf("/api/") === 0 ||
        url.indexOf(location.origin + "/api/") === 0;
      if (isApi) {
        const method = (init.method || "GET").toUpperCase();
        const hdrs = new Headers(init.headers || {});
        const firma = headersFirma();
        Object.keys(firma).forEach(function (k) {
          if (!hdrs.has(k)) hdrs.set(k, firma[k]);
        });
        init.headers = hdrs;
        if (init.body) init.body = enrichBody(init.body, method);
        // Advertir escrituras sin firma (no bloquea para no romper flujos)
        if (["POST", "PUT", "PATCH", "DELETE"].includes(method) && !loadUser()) {
          console.warn(
            "[CAmpo+] Escritura sin usuario firmado. Seleccioná operador en la barra de firma."
          );
        }
      }
      return _fetch(input, init);
    };
  }

  async function fetchUsuarios() {
    try {
      const res = await window.fetch(API + "?solo_activos=1");
      if (!res.ok) return [];
      return await res.json();
    } catch (_) {
      return [];
    }
  }

  function isMobile() {
    return window.matchMedia && window.matchMedia("(max-width: 768px)").matches;
  }

  function updateCollapsedLabel() {
    const label = document.getElementById("campoplus-firma-collapsed-label");
    if (!label) return;
    const u = loadUser();
    label.textContent = u && u.nombre ? u.nombre : "Firma";
  }

  function setBarExpanded(expanded) {
    const bar = document.getElementById("campoplus-firma-bar");
    if (!bar) return;
    bar.dataset.expanded = expanded ? "1" : "0";
    const collapsed = document.getElementById("campoplus-firma-collapsed");
    const panel = document.getElementById("campoplus-firma-panel");
    if (collapsed) collapsed.style.display = expanded ? "none" : "flex";
    if (panel) panel.style.display = expanded ? "flex" : "none";
    if (!expanded) updateCollapsedLabel();
  }

  function syncBarVisibility() {
    const bar = document.getElementById("campoplus-firma-bar");
    if (!bar) return;
    // Ocultar cuando hay modal abierto (botones Aceptar/Guardar abajo)
    const modalOpen = !!document.querySelector(".modal.show");
    bar.style.display = modalOpen ? "none" : "flex";
    if (modalOpen) return;
    // En celular: colapsado por defecto para no tapar acciones
    if (isMobile() && bar.dataset.expanded !== "1") {
      setBarExpanded(false);
    } else if (!isMobile()) {
      setBarExpanded(true);
    }
  }

  function ensureBar() {
    if (document.getElementById("campoplus-firma-bar")) return;

    if (!document.getElementById("campoplus-firma-style")) {
      const st = document.createElement("style");
      st.id = "campoplus-firma-style";
      st.textContent =
        "#campoplus-firma-bar{position:fixed;bottom:max(12px,env(safe-area-inset-bottom));" +
        "right:max(12px,env(safe-area-inset-right));z-index:99990;" +
        "font-family:system-ui,sans-serif;background:#0f172a;color:#f8fafc;" +
        "border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.25);font-size:12px;" +
        "max-width:min(96vw,420px);flex-direction:column;align-items:stretch}" +
        "#campoplus-firma-collapsed{align-items:center;gap:8px;padding:8px 12px;cursor:pointer;" +
        "user-select:none;-webkit-tap-highlight-color:transparent}" +
        "#campoplus-firma-collapsed-label{font-weight:700;max-width:140px;overflow:hidden;" +
        "text-overflow:ellipsis;white-space:nowrap}" +
        "#campoplus-firma-panel{align-items:center;gap:8px;padding:8px 12px;flex-wrap:wrap}" +
        "#campoplus-firma-select{flex:1;min-width:140px;border-radius:6px;border:1px solid #334155;" +
        "background:#1e293b;color:#f8fafc;padding:4px 6px;font-size:12px;font-weight:600}" +
        "#campoplus-firma-bar a{font-weight:700;text-decoration:none;white-space:nowrap}" +
        "@media (max-width:768px){#campoplus-firma-bar{max-width:min(92vw,280px)}" +
        "#campoplus-firma-panel{flex-direction:column;align-items:stretch}" +
        "#campoplus-firma-select{min-width:0;width:100%}" +
        "#campoplus-firma-links{display:none !important}}" +
        "@media print{#campoplus-firma-bar{display:none !important}}";
      document.head.appendChild(st);
    }

    const bar = document.createElement("div");
    bar.id = "campoplus-firma-bar";
    bar.className = "no-print";
    bar.dataset.expanded = isMobile() ? "0" : "1";
    bar.innerHTML =
      '<div id="campoplus-firma-collapsed" title="Elegir operador que firma">' +
      '<span style="opacity:.75">✎</span>' +
      '<span id="campoplus-firma-collapsed-label">Firma</span>' +
      "</div>" +
      '<div id="campoplus-firma-panel">' +
      '<div style="display:flex;align-items:center;gap:8px;width:100%">' +
      '<span style="opacity:.8;white-space:nowrap">Firma</span>' +
      '<select id="campoplus-firma-select"></select>' +
      '<button type="button" id="campoplus-firma-minimize" title="Minimizar" ' +
      'style="border:none;background:#334155;color:#f8fafc;border-radius:6px;padding:4px 8px;' +
      'font-size:12px;font-weight:700;cursor:pointer;display:none"></button>' +
      "</div></div>";
    document.body.appendChild(bar);

    const collapsed = document.getElementById("campoplus-firma-collapsed");
    const minimize = document.getElementById("campoplus-firma-minimize");
    collapsed.addEventListener("click", function () {
      setBarExpanded(true);
      if (isMobile()) minimize.style.display = "inline-block";
    });
    minimize.addEventListener("click", function () {
      setBarExpanded(false);
    });

    const sel = document.getElementById("campoplus-firma-select");
    sel.addEventListener("change", function () {
      const opt = sel.options[sel.selectedIndex];
      if (!opt || !opt.value) {
        saveUser(null);
        updateCollapsedLabel();
        syncPageSelects();
        return;
      }
      saveUser({
        id: parseInt(opt.value, 10),
        nombre: opt.dataset.nombre || opt.textContent,
        rol: opt.dataset.rol || "",
        email: opt.dataset.email || "",
      });
      updateCollapsedLabel();
      syncPageSelects();
      // En celular, colapsar al elegir para liberar la pantalla
      if (isMobile()) setBarExpanded(false);
    });

    // Ocultar barra mientras hay modales (Aceptar / Guardar abajo)
    document.addEventListener("shown.bs.modal", syncBarVisibility);
    document.addEventListener("hidden.bs.modal", syncBarVisibility);
    window.addEventListener("resize", syncBarVisibility);

    setBarExpanded(!isMobile());
    updateCollapsedLabel();
    syncBarVisibility();
  }

  function fillSelect(users) {
    const sel = document.getElementById("campoplus-firma-select");
    if (!sel) return;
    const current = loadUser();
    sel.innerHTML = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = " -  Elegir operador - ";
    sel.appendChild(empty);
    (users || []).forEach(function (u) {
      const o = document.createElement("option");
      o.value = String(u.id);
      o.textContent = u.nombre;
      o.dataset.nombre = u.nombre;
      o.dataset.rol = u.rol || "";
      o.dataset.email = u.email || "";
      sel.appendChild(o);
    });
    if (current && current.id) {
      sel.value = String(current.id);
      if (sel.value !== String(current.id)) {
        // Usuario guardado no está en lista: agregar opción temporal
        const o = document.createElement("option");
        o.value = String(current.id);
        o.textContent = current.nombre;
        o.dataset.nombre = current.nombre;
        o.dataset.rol = current.rol || "";
        sel.appendChild(o);
        sel.value = String(current.id);
      }
    } else if (users && users.length === 1) {
      sel.value = String(users[0].id);
      saveUser(users[0]);
    }
    updateCollapsedLabel();
  }

  /** Sincroniza #selectUsuario u otros selects de operador de la página. */
  function syncPageSelects() {
    const u = loadUser();
    const pageSel = document.getElementById("selectUsuario");
    if (!pageSel || !u) return;
    // Si el select tiene option con ese nombre, seleccionarlo; si no, setear value
    let found = false;
    for (let i = 0; i < pageSel.options.length; i++) {
      if (pageSel.options[i].value === u.nombre || pageSel.options[i].textContent.trim() === u.nombre) {
        pageSel.selectedIndex = i;
        found = true;
        break;
      }
    }
    if (!found) {
      const o = document.createElement("option");
      o.value = u.nombre;
      o.textContent = u.nombre;
      pageSel.appendChild(o);
      pageSel.value = u.nombre;
    }
  }

  async function populatePageUsuarioSelect(users) {
    const pageSel = document.getElementById("selectUsuario");
    if (!pageSel) return;
    const cur = pageSel.value;
    pageSel.innerHTML = "";
    (users || []).forEach(function (u) {
      const o = document.createElement("option");
      o.value = u.nombre;
      o.textContent = u.nombre;
      pageSel.appendChild(o);
    });
    const active = loadUser();
    if (active && active.nombre) pageSel.value = active.nombre;
    else if (cur) pageSel.value = cur;
    pageSel.addEventListener("change", function () {
      const nombre = pageSel.value;
      const match = (users || []).find(function (x) {
        return x.nombre === nombre;
      });
      if (match) {
        saveUser(match);
        const barSel = document.getElementById("campoplus-firma-select");
        if (barSel) barSel.value = String(match.id);
      } else {
        saveUser({ id: null, nombre: nombre, rol: "" });
      }
    });
  }

  function cmpEs(a, b) {
    return String(a || "").localeCompare(String(b || ""), "es", {
      sensitivity: "base",
      numeric: true,
    });
  }

  /** Ordena un array por etiqueta (string o función). */
  function sortByLabel(arr, getLabel) {
    return (arr || []).slice().sort(function (a, b) {
      var la = typeof getLabel === "function" ? getLabel(a) : a[getLabel];
      var lb = typeof getLabel === "function" ? getLabel(b) : b[getLabel];
      return cmpEs(la, lb);
    });
  }

  /** Ordena <option> de un <select> alfabéticamente (mantiene 1ª opción vacía). */
  function sortSelectEl(sel, keepFirstEmpty) {
    if (!sel || !sel.options) return;
    keepFirstEmpty = keepFirstEmpty !== false;
    var opts = Array.prototype.slice.call(sel.options);
    var first = null;
    if (keepFirstEmpty && opts.length && (!opts[0].value || opts[0].value === "")) {
      first = opts.shift();
    }
    opts.sort(function (a, b) {
      return cmpEs(a.text, b.text);
    });
    var val = sel.value;
    sel.innerHTML = "";
    if (first) sel.add(first);
    opts.forEach(function (o) {
      sel.add(o);
    });
    if (val) sel.value = val;
  }

  /** Ordena todos los selects del documento (útil al cargar una pantalla). */
  function sortAllSelects(root) {
    var scope = root || document;
    scope.querySelectorAll("select").forEach(function (sel) {
      sortSelectEl(sel, true);
    });
  }

  window.CampoPlusFirma = {
    getUsuario: loadUser,
    getNombre: function () {
      const u = loadUser();
      return (u && u.nombre) || "Sin firmar";
    },
    setUsuario: saveUser,
    headers: headersFirma,
    sortByLabel: sortByLabel,
    sortSelect: sortSelectEl,
    sortAllSelects: sortAllSelects,
    cmpEs: cmpEs,
    firmarEvento: async function (payload) {
      return window.fetch("/api/audit/firmar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
    },
  };

  function boot() {
    var vieja = document.getElementById("campoplus-firma-bar");
    if (vieja) vieja.remove();
    var estilo = document.getElementById("campoplus-firma-style");
    if (estilo) estilo.remove();
    if (!loadUser()) {
      try {
        var ses = JSON.parse(localStorage.getItem("campoplus_session_user") || "null");
        if (ses && ses.nombre) saveUser(ses);
      } catch (_) {}
    }
    fetchUsuarios().then(function (users) {
      users = sortByLabel(users, "nombre");
      fillSelect(users);
      populatePageUsuarioSelect(users);
      syncPageSelects();
      // Ordenar selects estáticos de la página (tipo comprobante, etc. se mantienen)
      try {
        sortAllSelects(document);
      } catch (_) {}
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
