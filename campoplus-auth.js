/**
 * CAmpo+ - Sesión comercial (login / token / módulos).
 * Incluir antes de campoplus-firma.js en pantallas protegidas:
 *   <script src="/campoplus-auth.js"></script>
 */
(function () {
  "use strict";
  var KEY_TOKEN = "campoplus_session_token";
  var KEY_USER = "campoplus_session_user";
  var KEY_EMP = "campoplus_session_empresa";
  var KEY_FIRMA = "campoplus_usuario_activo";

  function getToken() {
    return localStorage.getItem(KEY_TOKEN) || "";
  }

  function saveSession(payload) {
    if (!payload || !payload.token) {
      clearSession();
      return;
    }
    localStorage.setItem(KEY_TOKEN, payload.token);
    localStorage.setItem(KEY_USER, JSON.stringify(payload.usuario || {}));
    localStorage.setItem(KEY_EMP, JSON.stringify(payload.empresa || {}));
    if (payload.empresa && payload.empresa.id) {
      localStorage.setItem("campoplus_tenant_activo", String(payload.empresa.id));
    }
    // Sincroniza firma de auditoría
    var u = payload.usuario || {};
    localStorage.setItem(
      KEY_FIRMA,
      JSON.stringify({
        id: u.id,
        nombre: u.nombre,
        email: u.email || "",
        rol: u.rol || "",
      })
    );
  }

  function clearSession() {
    localStorage.removeItem(KEY_TOKEN);
    localStorage.removeItem(KEY_USER);
    localStorage.removeItem(KEY_EMP);
  }

  function sessionUser() {
    try {
      return JSON.parse(localStorage.getItem(KEY_USER) || "null");
    } catch (_) {
      return null;
    }
  }

  function sessionEmpresa() {
    try {
      return JSON.parse(localStorage.getItem(KEY_EMP) || "null");
    } catch (_) {
      return null;
    }
  }

  function authHeaders(extra) {
    var h = Object.assign({}, extra || {});
    var t = getToken();
    if (t) {
      h["Authorization"] = "Bearer " + t;
      h["X-Session-Token"] = t;
    }
    return h;
  }

  // Inyecta token en fetch /api/
  if (!window.__campoplusAuthFetchPatched) {
    window.__campoplusAuthFetchPatched = true;
    var _fetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      init = init || {};
      var url = typeof input === "string" ? input : (input && input.url) || "";
      var isApi =
        url.indexOf("/api/") === 0 ||
        url.indexOf(location.origin + "/api/") === 0;
      if (isApi) {
        var hdrs = new Headers(init.headers || {});
        var t = getToken();
        if (t && !hdrs.has("Authorization")) {
          hdrs.set("Authorization", "Bearer " + t);
          hdrs.set("X-Session-Token", t);
        }
        var cuentaImp = localStorage.getItem("campoplus_cuenta_impersonada");
        if (!hdrs.has("X-Cuenta-Id")) {
          hdrs.set("X-Cuenta-Id", cuentaImp || "0");
        }
        var empresaAbierta = localStorage.getItem("campoplus_tenant_activo");
        if (empresaAbierta && !hdrs.has("X-Empresa-Id")) {
          hdrs.set("X-Empresa-Id", empresaAbierta);
        }
        init.headers = hdrs;
      }
      return _fetch(input, init);
    };
  }

  async function login(usuario, password, empresaId) {
    var body = { usuario: usuario, password: password };
    if (empresaId) body.empresa_id = Number(empresaId);
    var res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    var data = await res.json().catch(function () {
      return {};
    });
    if (!res.ok) {
      var detail = data.detail || "Error de login";
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    saveSession(data);
    return data;
  }

  async function logout() {
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } catch (_) {}
    clearSession();
  }

  async function me() {
    var t = getToken();
    if (!t) return null;
    var res = await fetch("/api/auth/me");
    if (!res.ok) {
      clearSession();
      return null;
    }
    var data = await res.json();
    localStorage.setItem(KEY_USER, JSON.stringify(data.usuario || {}));
    localStorage.setItem(KEY_EMP, JSON.stringify(data.empresa || {}));
    if (data.empresa && data.empresa.id) {
      localStorage.setItem("campoplus_tenant_activo", String(data.empresa.id));
    }
    return data;
  }

  /**
   * Si no hay sesión válida, redirige a Login.html.
   * @param {object} opts { redirect?: string }
   */
  async function requireAuth(opts) {
    opts = opts || {};
    var page = (location.pathname.split("/").pop() || "").toLowerCase();
    if (page === "login.html") return null;
    var data = await me();
    if (data && data.usuario) usuarioActual = data.usuario;
    if (!data) {
      var next = opts.redirect || page || "Index.html";
      location.href = "Login.html?next=" + encodeURIComponent(next);
      return null;
    }
    if (data.empresa && !Number(data.empresa.acceso_habilitado) && !Number(data.usuario.es_superadmin)) {
      await logout();
      location.href = "Login.html?blocked=1";
      return null;
    }
    return data;
  }

  var usuarioActual = null;
  var ROL_MODULOS = {
    "administración / carga": ["mod_bancos", "mod_contabilidad", "mod_sicore", "mod_arba", "mod_liquidaciones", "es_agente_retencion"],
    "administracion / carga": ["mod_bancos", "mod_contabilidad", "mod_sicore", "mod_arba", "mod_liquidaciones", "es_agente_retencion"],
    "gestión agropecuaria": ["mod_agro", "mod_almacen", "mod_liquidaciones"],
    "gestion agropecuaria": ["mod_agro", "mod_almacen", "mod_liquidaciones"],
    "ganadería": ["mod_ganaderia", "mod_tambo"],
    "ganaderia": ["mod_ganaderia", "mod_tambo"],
    "operativo / campo": ["mod_agro", "mod_ganaderia", "mod_tambo", "mod_almacen"]
  };

  function moduloPagado(emp, key) {
    if (!emp) return true;
    var v = emp[key];
    if (v === undefined || v === null) return true;
    return Number(v) === 1;
  }

  function moduloOn(emp, key) {
    if (!moduloPagado(emp, key)) return false;
    var u = usuarioActual;
    if (!u || Number(u.es_superadmin)) return true;
    var rol = String(u.rol || "").trim().toLowerCase();
    if (rol === "administrador total" || rol === "consulta") return true;
    var permitidos = ROL_MODULOS[rol];
    if (!permitidos) return true;
    return permitidos.indexOf(key) >= 0;
  }

  /** Oculta nodos con data-mod="mod_xxx" según empresa. */
  function applyModuleVisibility(emp) {
    if (!emp) return;
    document.querySelectorAll("[data-mod]").forEach(function (el) {
      var key = el.getAttribute("data-mod");
      if (!key) return;
      el.style.display = moduloOn(emp, key) ? "" : "none";
    });
    document.querySelectorAll("[data-mod-any]").forEach(function (el) {
      var keys = (el.getAttribute("data-mod-any") || "").split(",").map(function (s) {
        return s.trim();
      });
      var ok = keys.some(function (k) {
        return moduloOn(emp, k);
      });
      el.style.display = ok ? "" : "none";
    });
  }

  function paintLogo(emp, imgSelector, nameSelector) {
    var img = imgSelector ? document.querySelector(imgSelector) : null;
    var nameEl = nameSelector ? document.querySelector(nameSelector) : null;
    if (nameEl && emp && emp.razon_social) nameEl.textContent = emp.razon_social;
    if (!img) return;
    if (emp && emp.logo_url) {
      img.src = emp.logo_url + (emp.logo_url.indexOf("?") >= 0 ? "&" : "?") + "t=" + Date.now();
      img.style.display = "";
      img.alt = (emp.razon_social || "Logo") + "";
    } else {
      img.removeAttribute("src");
      img.style.display = "none";
    }
  }

  /** Barra de marca (logo + razón social) en formularios si no hay #logoEmpresaHeader. */
  function injectFormBrand() {
    var emp = sessionEmpresa();
    if (!emp || !emp.logo_url) return;
    if (document.getElementById("logoEmpresaHeader") || document.getElementById("campoplusBrandBar")) return;
    var page = (location.pathname.split("/").pop() || "").toLowerCase();
    if (page === "login.html" || page === "index.html") return;
    var bar = document.createElement("div");
    bar.id = "campoplusBrandBar";
    bar.style.cssText =
      "display:flex;align-items:center;gap:10px;padding:6px 12px;margin:8px 12px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;font-family:Segoe UI,sans-serif;";
    bar.innerHTML =
      '<img src="' +
      emp.logo_url +
      '" alt="" style="height:28px;width:28px;object-fit:contain;border-radius:4px;">' +
      '<span style="font-size:12px;font-weight:700;color:#1e293b;">' +
      (emp.razon_social || "") +
      "</span>" +
      '<span style="font-size:11px;color:#64748b;margin-left:auto;">CAmpo+</span>';
    var body = document.body;
    if (body.firstChild) body.insertBefore(bar, body.firstChild);
    else body.appendChild(bar);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectFormBrand);
  } else {
    injectFormBrand();
  }

  window.CampoAuth = {
    getToken: getToken,
    saveSession: saveSession,
    clearSession: clearSession,
    sessionUser: sessionUser,
    sessionEmpresa: sessionEmpresa,
    authHeaders: authHeaders,
    login: login,
    logout: logout,
    me: me,
    requireAuth: requireAuth,
    moduloOn: moduloOn,
    applyModuleVisibility: applyModuleVisibility,
    paintLogo: paintLogo,
    injectFormBrand: injectFormBrand,
  };
})();
