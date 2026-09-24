/**
 * CAmpo+ — Firma de operador y trazabilidad.
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

  function ensureBar() {
    if (document.getElementById("campoplus-firma-bar")) return;
    const bar = document.createElement("div");
    bar.id = "campoplus-firma-bar";
    bar.style.cssText =
      "position:fixed;bottom:12px;right:12px;z-index:99999;font-family:system-ui,sans-serif;" +
      "background:#0f172a;color:#f8fafc;border-radius:10px;padding:8px 12px;box-shadow:0 8px 24px rgba(0,0,0,.25);" +
      "display:flex;align-items:center;gap:8px;font-size:12px;max-width:min(96vw,420px);";
    bar.innerHTML =
      '<span style="opacity:.8;white-space:nowrap">Firma</span>' +
      '<select id="campoplus-firma-select" style="flex:1;min-width:140px;border-radius:6px;border:1px solid #334155;background:#1e293b;color:#f8fafc;padding:4px 6px;font-size:12px;font-weight:600"></select>' +
      '<a href="/AuditLog.html" title="Ver log de auditoría" style="color:#6ee7b7;font-weight:700;text-decoration:none;white-space:nowrap">Log</a>' +
      '<a href="/Usuarios.html" title="Usuarios" style="color:#93c5fd;font-weight:700;text-decoration:none;white-space:nowrap">Users</a>';
    document.body.appendChild(bar);

    const sel = document.getElementById("campoplus-firma-select");
    sel.addEventListener("change", function () {
      const opt = sel.options[sel.selectedIndex];
      if (!opt || !opt.value) {
        saveUser(null);
        syncPageSelects();
        return;
      }
      saveUser({
        id: parseInt(opt.value, 10),
        nombre: opt.dataset.nombre || opt.textContent,
        rol: opt.dataset.rol || "",
        email: opt.dataset.email || "",
      });
      syncPageSelects();
    });
  }

  function fillSelect(users) {
    const sel = document.getElementById("campoplus-firma-select");
    if (!sel) return;
    const current = loadUser();
    sel.innerHTML = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "— Elegir operador —";
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
    ensureBar();
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
