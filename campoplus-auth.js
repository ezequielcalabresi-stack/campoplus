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

  // Cada pestaña trabaja con la empresa con la que abrió la pantalla. Si en otra pestaña se
  // cambia de empresa, esta sigue mandando la suya: si no, guardaría los datos que muestra
  // (p. ej. el CUIT de Configuración) dentro de la otra empresa.
  var KEY_TENANT = "campoplus_tenant_activo";
  var empresaPestana = localStorage.getItem(KEY_TENANT) || "";
  var nombrePestana = (function () {
    var e = sessionEmpresa();
    return e && String(e.id) === empresaPestana ? e.razon_social || "" : "";
  })();

  if (!window.__campoplusTenantPatched) {
    window.__campoplusTenantPatched = true;
    var _setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (k, v) {
      if (this === window.localStorage) {
        if (k === KEY_TENANT) {
          empresaPestana = String(v || "");
          ocultarAvisoEmpresa();
        } else if (k === KEY_EMP) {
          try {
            var e = JSON.parse(v || "null");
            if (e && String(e.id) === empresaPestana) nombrePestana = e.razon_social || "";
          } catch (_) {}
        }
      }
      return _setItem.apply(this, arguments);
    };
    window.addEventListener("storage", function (ev) {
      if (ev.key === KEY_TENANT && ev.newValue && ev.newValue !== empresaPestana) mostrarAvisoEmpresa();
    });
  }

  function ocultarAvisoEmpresa() {
    var a = document.getElementById("campoplusAvisoEmpresa");
    if (a) a.remove();
  }

  function mostrarAvisoEmpresa() {
    if (!document.body || document.getElementById("campoplusAvisoEmpresa")) return;
    var a = document.createElement("div");
    a.id = "campoplusAvisoEmpresa";
    a.style.cssText =
      "position:fixed;top:0;left:0;right:0;z-index:2147483000;background:#b91c1c;color:#fff;" +
      "padding:10px 16px;font:600 14px 'Segoe UI',sans-serif;display:flex;gap:12px;align-items:center;justify-content:center;flex-wrap:wrap;";
    var txt = document.createElement("span");
    txt.textContent =
      "En otra pestaña se cambió de empresa. Esta pestaña sigue trabajando con " +
      (nombrePestana || "la empresa con la que la abriste") +
      " y guarda ahí. Recargala para trabajar con la empresa nueva.";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "Recargar";
    btn.style.cssText = "background:#fff;color:#b91c1c;border:0;border-radius:6px;padding:4px 12px;font-weight:700;cursor:pointer;";
    btn.onclick = function () {
      location.reload();
    };
    a.appendChild(txt);
    a.appendChild(btn);
    document.body.appendChild(a);
  }

  // Inyecta token en fetch /api/ (excepto login: debe ir siempre a la base master)
  if (!window.__campoplusAuthFetchPatched) {
    window.__campoplusAuthFetchPatched = true;
    var _fetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      init = init || {};
      var url = typeof input === "string" ? input : (input && input.url) || "";
      var isApi =
        url.indexOf("/api/") === 0 ||
        url.indexOf(location.origin + "/api/") === 0;
      var isLogin =
        url.indexOf("/api/auth/login") >= 0;
      if (isApi && !isLogin) {
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
        if (empresaPestana && !hdrs.has("X-Empresa-Id")) {
          hdrs.set("X-Empresa-Id", empresaPestana);
        }
        init.headers = hdrs;
      }
      return _fetch(input, init);
    };
  }

  async function login(usuario, password, empresaId) {
    // Sin sesión/cuenta previa: si no, el middleware apunta a otra base y falla el login.
    clearSession();
    try {
      localStorage.removeItem("campoplus_cuenta_impersonada");
      localStorage.removeItem("campoplus_tenant_activo");
    } catch (_) {}
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
    if (!veAreaEmpresa(data.usuario) && esPaginaAreaEmpresa()) {
      location.href = "Index.html";
      return null;
    }
    return data;
  }

  var PAGINAS_AREA_EMPRESA = {
    "contabilidad.html": 1,
    "bienesuso.html": 1,
    "empleados.html": 1,
    "baul.html": 1,
    "empresas.html": 1,
    "backups.html": 1,
    "usuarios.html": 1,
    "auditlog.html": 1,
    "configuracionempresa.html": 1
  };

  function esPaginaAreaEmpresa() {
    var page = (location.pathname.split("/").pop() || "").toLowerCase();
    return !!PAGINAS_AREA_EMPRESA[page];
  }

  function veAreaEmpresa(usuario) {
    if (!usuario || Number(usuario.es_superadmin)) return true;
    var rol = String(usuario.rol || "").trim().toLowerCase();
    return rol === "" || rol === "administrador total" || rol === "consulta"
      || rol === "administración / carga" || rol === "administracion / carga";
  }

  var usuarioActual = null;
  var ROL_MODULOS = {
    "administración / carga": ["mod_bancos", "mod_contabilidad", "mod_sicore", "mod_arba", "mod_cm05", "mod_liquidaciones", "es_agente_retencion"],
    "administracion / carga": ["mod_bancos", "mod_contabilidad", "mod_sicore", "mod_arba", "mod_cm05", "mod_liquidaciones", "es_agente_retencion"],
    "gestión agropecuaria": ["mod_agro", "mod_almacen"],
    "gestion agropecuaria": ["mod_agro", "mod_almacen"],
    "ganadería": ["mod_ganaderia", "mod_tambo", "mod_porcino", "mod_aviar"],
    "ganaderia": ["mod_ganaderia", "mod_tambo", "mod_porcino", "mod_aviar"],
    "operativo / campo": ["mod_agro", "mod_ganaderia", "mod_tambo", "mod_porcino", "mod_aviar", "mod_almacen"]
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

  /**
   * Liquidaciones ligadas a la actividad: requieren mod_liquidaciones (rol/pack)
   * y el pack de la actividad contratado por la empresa (no depende del rol).
   */
  function liquidacionOn(emp, modActividad) {
    return moduloOn(emp, "mod_liquidaciones") && moduloPagado(emp, modActividad);
  }

  var PAGINA_LIQUIDACION = {
    "liquidacioneshacienda.html": "mod_ganaderia",
    "liquidacionesleche.html": "mod_tambo",
    "liquidacionesgranos.html": "mod_agro"
  };
  var LIQ_TIPO_POR_MODULO = { mod_ganaderia: "HACIENDA", mod_tambo: "LECHE", mod_agro: "GRANO" };

  /** Estado real (pack + actividad cargada) desde el servidor. */
  async function estadoLiquidaciones() {
    try {
      var res = await fetch("/api/actividades/modulos");
      if (!res.ok) return null;
      var data = await res.json();
      return data.liquidaciones || null;
    } catch (_) {
      return null;
    }
  }

  async function guardPaginaLiquidacion() {
    var page = (location.pathname.split("/").pop() || "").toLowerCase();
    var mod = PAGINA_LIQUIDACION[page];
    if (!mod) return;
    var emp = sessionEmpresa();
    var motivo = "";
    if (emp && !liquidacionOn(emp, mod)) {
      motivo = "la empresa no tiene la actividad contratada";
    } else {
      var est = await estadoLiquidaciones();
      var e = est && est[LIQ_TIPO_POR_MODULO[mod]];
      if (e && !e.habilitada) motivo = e.motivo || "actividad no disponible";
    }
    if (!motivo) return;
    alert("Esta liquidación no está disponible: " + motivo + ".");
    location.href = "Index.html";
  }

  async function ocultarLiquidacionesSinActividad() {
    var nodos = document.querySelectorAll("[data-liq-actividad]");
    if (!nodos.length) return;
    var est = await estadoLiquidaciones();
    if (!est) return;
    nodos.forEach(function (el) {
      var e = est[LIQ_TIPO_POR_MODULO[el.getAttribute("data-liq-actividad")]];
      if (e && !e.habilitada) el.style.display = "none";
    });
  }

  /** Oculta nodos con data-mod="mod_xxx" según empresa. */
  function applyModuleVisibility(emp) {
    if (!emp) return;
    document.querySelectorAll("[data-liq-actividad]").forEach(function (el) {
      var key = el.getAttribute("data-liq-actividad");
      el.style.display = liquidacionOn(emp, key) ? "" : "none";
    });
    ocultarLiquidacionesSinActividad();
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

  function quitarFondo() {
    var st = document.getElementById("campoplusFondoCss");
    var capa = document.getElementById("campoplusFondo");
    if (st) st.remove();
    if (capa) capa.remove();
    detenerVeloTextos();
  }

  /*
   * Bloques de texto que quedan directo sobre la foto (títulos, pestañas, filtros sin tarjeta):
   * se les da un velo claro. Las tarjetas con fondo propio no se tocan.
   */
  var VELO = "cp-sobre-foto";
  var veloObs = null;
  var veloTimer = null;

  function fondoOpaco(st) {
    if (st.backgroundImage && st.backgroundImage !== "none") return true;
    var m = /rgba?\(([^)]+)\)/.exec(st.backgroundColor || "");
    if (!m) return false;
    var p = m[1].split(",");
    return p.length < 4 || parseFloat(p[3]) >= 0.6;
  }

  function tieneTextoPropio(el) {
    for (var n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3 && n.nodeValue.trim()) return true;
    }
    return false;
  }

  /* Devuelve {texto, tarjeta}: si el subárbol tiene texto sobre la foto y si contiene una tarjeta opaca grande. */
  function escanearVelo(el, marcar) {
    var res = { texto: tieneTextoPropio(el), tarjeta: false };
    var hijos = [];
    for (var c = el.firstElementChild; c; c = c.nextElementSibling) {
      var tag = c.tagName;
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "TEMPLATE" || tag === "NOSCRIPT" || c.id === "campoplusFondo") continue;
      if (!c.getClientRects().length) continue;
      var st = getComputedStyle(c);
      if (st.visibility === "hidden") continue;
      if (fondoOpaco(st)) {
        if (c.offsetWidth >= 250 && c.offsetHeight >= 60) res.tarjeta = true;
        continue;
      }
      var r = escanearVelo(c, marcar);
      hijos.push({ el: c, r: r });
      if (r.texto) res.texto = true;
      if (r.tarjeta) res.tarjeta = true;
    }
    if (res.tarjeta || el === document.body) {
      hijos.forEach(function (h) {
        if (h.r.texto && !h.r.tarjeta) marcar.push(h.el);
      });
    }
    return res;
  }

  function aplicarVeloTextos() {
    if (!document.body || !document.getElementById("campoplusFondo")) return;
    var previos = document.querySelectorAll("." + VELO);
    for (var i = 0; i < previos.length; i++) previos[i].classList.remove(VELO);
    var marcar = [];
    escanearVelo(document.body, marcar);
    marcar.forEach(function (el) { el.classList.add(VELO); });
  }

  function programarVelo() {
    clearTimeout(veloTimer);
    veloTimer = setTimeout(aplicarVeloTextos, 250);
  }

  function iniciarVeloTextos() {
    aplicarVeloTextos();
    if (!veloObs && window.MutationObserver) {
      veloObs = new MutationObserver(programarVelo);
      veloObs.observe(document.body, { childList: true, subtree: true });
      window.addEventListener("load", programarVelo);
    }
  }

  function detenerVeloTextos() {
    if (veloObs) { veloObs.disconnect(); veloObs = null; }
    clearTimeout(veloTimer);
    var previos = document.querySelectorAll("." + VELO);
    for (var i = 0; i < previos.length; i++) previos[i].classList.remove(VELO);
  }

  /**
   * Foto de fondo propia de la empresa (Configuración de empresa).
   * nitidez 0-100: 100 = foto nítida con velo leve; 0 = muy desenfocada y clara.
   */
  function aplicarFondo(emp) {
    emp = emp || sessionEmpresa();
    var page = (location.pathname.split("/").pop() || "").toLowerCase();
    if (!emp || !emp.fondo_url || page === "login.html") {
      quitarFondo();
      return;
    }
    var n = Number(emp.fondo_nitidez);
    if (!isFinite(n)) n = 40;
    n = Math.max(0, Math.min(100, n));
    var blur = ((100 - n) * 0.08).toFixed(1);
    var velo = (0.15 + (100 - n) * 0.006).toFixed(2);
    var url = emp.fondo_url;
    var img = new Image();
    img.onload = function () {
      var st = document.getElementById("campoplusFondoCss");
      if (!st) {
        st = document.createElement("style");
        st.id = "campoplusFondoCss";
        document.head.appendChild(st);
      }
      st.textContent =
        "body{background-color:transparent!important}" +
        "#campoplusFondo{position:fixed;inset:-24px;z-index:-1;pointer-events:none;" +
        "background:url('" + url.replace(/'/g, "%27") + "') center/cover no-repeat;filter:blur(" + blur + "px)}" +
        "#campoplusFondo::after{content:'';position:absolute;inset:0;background:rgba(241,245,249," + velo + ")}" +
        "body>header,body>h1,body>h2,body>div>h1,body>div>h2{text-shadow:0 1px 3px rgba(255,255,255,.9)}" +
        "." + VELO + "{background-color:rgba(248,250,252,.88)!important;box-shadow:0 0 0 6px rgba(248,250,252,.88);border-radius:6px}" +
        "@media print{#campoplusFondo{display:none}." + VELO + "{background-color:transparent!important;box-shadow:none!important}}";
      if (!document.getElementById("campoplusFondo")) {
        var capa = document.createElement("div");
        capa.id = "campoplusFondo";
        document.body.insertBefore(capa, document.body.firstChild);
      }
      iniciarVeloTextos();
    };
    img.onerror = quitarFondo;
    img.src = url;
  }

  function aplicarFondoInicial() {
    aplicarFondo();
  }

  /** Ventanas emergentes (Bootstrap .modal y overlays Tailwind): agrandar y cambiar tamaño arrastrando. */
  var CSS_VENTANAS =
    ".cp-win{position:relative}" +
    ".cp-win-btn{border:1px solid #cbd5e1;background:#fff;color:#334155;border-radius:6px;width:30px;height:26px;" +
    "font-size:15px;line-height:1;cursor:pointer;flex:0 0 auto;margin-left:auto;margin-right:8px;padding:0}" +
    ".cp-win-btn:hover{background:#f1f5f9;color:#0f172a}" +
    ".cp-win-btn.cp-abs{position:absolute;top:8px;right:44px;z-index:6;margin:0}" +
    ".cp-win-btn+.btn-close{margin-left:0!important}" +
    ".cp-win-grip{position:absolute;right:1px;bottom:1px;width:16px;height:16px;cursor:nwse-resize;z-index:6;" +
    "background:linear-gradient(135deg,transparent 45%,#94a3b8 45%,#94a3b8 55%,transparent 55%,transparent 70%,#94a3b8 70%,#94a3b8 80%,transparent 80%)}" +
    ".modal-dialog.cp-max{max-width:98vw!important;width:98vw!important;margin:1vh auto!important}" +
    ".modal-dialog.cp-max>.modal-content{height:98vh}" +
    ".modal-dialog.cp-max .modal-body,.modal-dialog.cp-sized .modal-body{overflow:auto;flex:1 1 auto}" +
    ".modal-dialog.cp-max .modal-body .table-responsive,.modal-dialog.cp-sized .modal-body .table-responsive{max-height:none!important}" +
    ".cp-win.cp-max-panel{width:98vw!important;max-width:98vw!important;height:96vh!important;max-height:96vh!important;overflow:auto!important}" +
    ".modal-body td>input[type=date].form-control,.modal-body td>.campo-fecha-wrap{min-width:125px}" +
    ".modal-body td>input.money-input{min-width:105px}" +
    ".modal-body td>select.form-select{min-width:110px}" +
    ".modal-body td>input.cq-nota{min-width:140px}" +
    "@media print{.cp-win-btn,.cp-win-grip{display:none}}";

  function claveVentana(raiz) {
    var page = (location.pathname.split("/").pop() || "").toLowerCase();
    return "campoplus_win_max:" + page + ":" + ((raiz && raiz.id) || "");
  }

  function fijarMaximizada(v, on) {
    if (v.dialog) {
      v.dialog.classList.toggle("cp-max", on);
      v.dialog.classList.remove("cp-sized");
      v.dialog.style.width = "";
      v.dialog.style.maxWidth = "";
    } else {
      v.panel.classList.toggle("cp-max-panel", on);
      v.panel.style.width = "";
      v.panel.style.maxWidth = "";
      v.panel.style.maxHeight = "";
    }
    v.panel.style.height = "";
    if (v.boton) {
      v.boton.textContent = on ? "\u2921" : "\u2922";
      v.boton.title = on ? "Achicar ventana" : "Agrandar ventana (doble clic en el título)";
    }
    try {
      if (v.raiz && v.raiz.id) {
        if (on) localStorage.setItem(claveVentana(v.raiz), "1");
        else localStorage.removeItem(claveVentana(v.raiz));
      }
    } catch (_) {}
  }

  function estaMaximizada(v) {
    return v.dialog ? v.dialog.classList.contains("cp-max") : v.panel.classList.contains("cp-max-panel");
  }

  function arrastrarTamano(ev, v) {
    ev.preventDefault();
    ev.stopPropagation();
    var caja = v.panel.getBoundingClientRect();
    var x0 = ev.clientX, y0 = ev.clientY, w0 = caja.width, h0 = caja.height;
    var factorAlto = v.dialog && !v.dialog.classList.contains("modal-dialog-centered") ? 1 : 2;
    if (estaMaximizada(v)) fijarMaximizada(v, false);
    function mover(e) {
      var w = Math.max(320, Math.min(window.innerWidth - 12, w0 + (e.clientX - x0) * 2));
      var h = Math.max(200, Math.min(window.innerHeight - 12, h0 + (e.clientY - y0) * factorAlto));
      if (v.dialog) {
        v.dialog.classList.add("cp-sized");
        v.dialog.style.maxWidth = "none";
        v.dialog.style.width = w + "px";
      } else {
        v.panel.style.maxWidth = "none";
        v.panel.style.width = w + "px";
        v.panel.style.maxHeight = "none";
        v.panel.style.overflow = "auto";
      }
      v.panel.style.height = h + "px";
    }
    function tragarClic(e) {
      e.stopPropagation();
      e.preventDefault();
    }
    function soltar() {
      document.removeEventListener("mousemove", mover);
      document.removeEventListener("mouseup", soltar);
      document.body.style.userSelect = "";
      // El clic que sigue al soltar fuera del recuadro no debe cerrar la ventana por "clic en el fondo".
      document.addEventListener("click", tragarClic, true);
      setTimeout(function () {
        document.removeEventListener("click", tragarClic, true);
      }, 0);
    }
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", mover);
    document.addEventListener("mouseup", soltar);
  }

  function mejorarVentana(panel, dialog, raiz) {
    if (!panel || panel.getAttribute("data-cp-win")) return;
    panel.setAttribute("data-cp-win", "1");
    panel.classList.add("cp-win");
    var v = { panel: panel, dialog: dialog, raiz: raiz, boton: null };
    var cab = panel.querySelector(":scope > .modal-header");
    if (!cab && !dialog) {
      var primero = panel.firstElementChild;
      if (primero && primero.classList.contains("flex") && primero.classList.contains("justify-between")) cab = primero;
    }
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cp-win-btn";
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      fijarMaximizada(v, !estaMaximizada(v));
    });
    if (cab && cab.children.length >= 2) cab.insertBefore(btn, cab.lastElementChild);
    else {
      btn.classList.add("cp-abs");
      panel.appendChild(btn);
    }
    v.boton = btn;
    if (cab) {
      cab.addEventListener("dblclick", function (e) {
        if (e.target.closest("input,select,textarea,button,a")) return;
        fijarMaximizada(v, !estaMaximizada(v));
      });
    }
    var grip = document.createElement("div");
    grip.className = "cp-win-grip";
    grip.title = "Arrastrar para cambiar el tamaño";
    grip.addEventListener("mousedown", function (e) {
      arrastrarTamano(e, v);
    });
    panel.appendChild(grip);
    var recordada = false;
    try {
      recordada = !!(raiz && raiz.id && localStorage.getItem(claveVentana(raiz)));
    } catch (_) {}
    fijarMaximizada(v, recordada);
  }

  function mejorarVentanas(raiz) {
    var base = raiz || document;
    if (!document.getElementById("campoplusVentanasCss")) {
      var st = document.createElement("style");
      st.id = "campoplusVentanasCss";
      st.textContent = CSS_VENTANAS;
      document.head.appendChild(st);
    }
    var modales = base.classList && base.classList.contains("modal") ? [base] : base.querySelectorAll(".modal");
    Array.prototype.forEach.call(modales, function (m) {
      var c = m.querySelector(".modal-dialog > .modal-content");
      if (c) mejorarVentana(c, c.parentElement, m);
    });
    if (!raiz) {
      document.querySelectorAll("div.fixed.inset-0").forEach(function (ov) {
        var p = ov.firstElementChild;
        if (p && p.tagName === "DIV" && p.classList.contains("bg-white")) mejorarVentana(p, null, ov);
      });
    }
  }

  document.addEventListener("show.bs.modal", function (e) {
    if (e.target && e.target.classList) mejorarVentanas(e.target);
  });

  /**
   * Desplegables y listas de sugerencias en orden alfabético (sin distinguir mayúsculas ni acentos).
   * Quedan arriba "Todos…", "Seleccione…" y la opción vacía; al final las que empiezan con "+".
   * No se tocan listas cortas (menos de 6), meses, días, campañas o períodos (26-27, 10/2026, 2026),
   * ni las que tengan el atributo data-orden-fijo.
   */
  var COLLATOR = window.Intl && Intl.Collator ? new Intl.Collator("es", { sensitivity: "base", numeric: true }) : null;
  var RE_CABECERA = /^(todos|todas|seleccion|elegí|elegi|elegir|ninguno|ninguna|cualquier|--|—|\(sin|sin asignar)/i;
  var RE_CALENDARIO = /^(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre|lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)\b/i;
  var RE_PERIODO = /^(\d{2}\s*[-\/]\s*\d{2}\b|\d{1,2}\/\d{4}|\d{4}(-\d{2})?\b|\d{2}\/\d{2}\/\d{2,4})/;

  function textoOpcion(o) {
    return String(o.textContent || o.label || o.value || "").trim();
  }

  function compararOpciones(a, b) {
    var x = textoOpcion(a), y = textoOpcion(b);
    return COLLATOR ? COLLATOR.compare(x, y) : x.toLowerCase() < y.toLowerCase() ? -1 : x.toLowerCase() > y.toLowerCase() ? 1 : 0;
  }

  function ordenarContenedor(cont) {
    var opts = Array.prototype.filter.call(cont.children, function (n) {
      return n.tagName === "OPTION";
    });
    if (opts.length < 6) return;
    var cab = [], cuerpo = [], fin = [];
    var i = 0;
    while (i < opts.length && (opts[i].value === "" || RE_CABECERA.test(textoOpcion(opts[i])))) cab.push(opts[i++]);
    for (; i < opts.length; i++) (/^\+/.test(textoOpcion(opts[i])) ? fin : cuerpo).push(opts[i]);
    if (cuerpo.length < 6) return;
    var fijas = cuerpo.filter(function (o) {
      var t = textoOpcion(o);
      return RE_CALENDARIO.test(t) || RE_PERIODO.test(t);
    }).length;
    if (fijas * 2 >= cuerpo.length) return;
    var deseado = cab.concat(cuerpo.slice().sort(compararOpciones), fin);
    var igual = deseado.every(function (o, k) {
      return o === opts[k];
    });
    if (igual) return;
    deseado.forEach(function (o) {
      cont.appendChild(o);
    });
  }

  function ordenarLista(el) {
    if (!el || !el.isConnected || el.hasAttribute("data-orden-fijo")) return;
    var valor = el.tagName === "SELECT" && !el.multiple ? el.value : null;
    ordenarContenedor(el);
    Array.prototype.forEach.call(el.querySelectorAll("optgroup"), ordenarContenedor);
    if (valor !== null && el.value !== valor) el.value = valor;
  }

  var listasPendientes = [];
  var ordenProgramado = false;

  function programarOrden(el) {
    if (!el || listasPendientes.indexOf(el) >= 0) return;
    listasPendientes.push(el);
    if (ordenProgramado) return;
    ordenProgramado = true;
    (window.queueMicrotask || function (f) { Promise.resolve().then(f); })(function () {
      var lista = listasPendientes;
      listasPendientes = [];
      ordenProgramado = false;
      lista.forEach(ordenarLista);
    });
  }

  function listaDe(nodo) {
    return nodo && nodo.closest ? nodo.closest("select, datalist") : null;
  }

  if (window.MutationObserver && !window.__campoplusOrdenListas) {
    window.__campoplusOrdenListas = true;
    new MutationObserver(function (cambios) {
      cambios.forEach(function (c) {
        var t = c.target;
        if (t.nodeType === 1 && (t.tagName === "SELECT" || t.tagName === "DATALIST" || t.tagName === "OPTGROUP")) {
          programarOrden(listaDe(t));
          return;
        }
        Array.prototype.forEach.call(c.addedNodes, function (n) {
          if (n.nodeType !== 1) return;
          if (n.tagName === "SELECT" || n.tagName === "DATALIST") programarOrden(n);
          else if (n.querySelectorAll) Array.prototype.forEach.call(n.querySelectorAll("select, datalist"), programarOrden);
        });
      });
    }).observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectFormBrand);
    document.addEventListener("DOMContentLoaded", aplicarFondoInicial);
    document.addEventListener("DOMContentLoaded", function () { mejorarVentanas(); });
  } else {
    injectFormBrand();
    aplicarFondoInicial();
    mejorarVentanas();
  }

  if (esPaginaAreaEmpresa()) {
    requireAuth();
  }
  guardPaginaLiquidacion();

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
    liquidacionOn: liquidacionOn,
    applyModuleVisibility: applyModuleVisibility,
    nombreEmpresa: function () {
      var emp = sessionEmpresa();
      return (emp && emp.razon_social) || "";
    },
    paintLogo: paintLogo,
    injectFormBrand: injectFormBrand,
    aplicarFondo: aplicarFondo,
  };
})();
