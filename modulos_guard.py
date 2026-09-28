# -*- coding: utf-8 -*-
"""Si la empresa no contrató el pack, la API de ese módulo no entrega datos."""
from __future__ import annotations

from fastapi.responses import JSONResponse

from actividad_modulos import modulo_habilitado, modulos_empresa

# El primer prefijo que coincida gana. Los más específicos van primero.
PREFIJOS_MODULO = (
    ("/api/tambo", "mod_tambo"),
    ("/api/ganaderia", "mod_ganaderia"),
    ("/api/porcino", "mod_porcino"),
    ("/api/aviar", "mod_aviar"),
    ("/api/agro", "mod_agro"),
    ("/api/financiero", "mod_bancos"),
    ("/api/bancos", "mod_bancos"),
    ("/api/contabilidad", "mod_contabilidad"),
    ("/api/asientos", "mod_contabilidad"),
    ("/api/plan_cuentas", "mod_contabilidad"),
    ("/api/cm05", "mod_cm05"),
    ("/api/arba", "mod_arba"),
    ("/api/sicore", "mod_sicore"),
    ("/api/liquidaciones", "mod_liquidaciones"),
)

EXENTOS = (
    "/api/auth",
    "/api/saas",
    "/api/actividades",
    "/api/empresas",
)


def modulo_de_ruta(path: str) -> str | None:
    path = path or ""
    for prefijo in EXENTOS:
        if path == prefijo or path.startswith(prefijo + "/"):
            return None
    for prefijo, modulo in PREFIJOS_MODULO:
        if path == prefijo or path.startswith(prefijo + "/"):
            return modulo
    return None


def register_modulo_guard(app, get_db, get_empresa_activa_id) -> None:
    @app.middleware("http")
    async def _guard_modulo(request, call_next):
        modulo = modulo_de_ruta(request.url.path)
        if not modulo:
            return await call_next(request)
        try:
            flags = modulos_empresa(get_db, get_empresa_activa_id())
        except Exception:
            return await call_next(request)
        if modulo_habilitado(flags, modulo):
            return await call_next(request)
        return JSONResponse(
            status_code=403,
            content={"detail": "Este módulo no está contratado para la empresa."},
        )
