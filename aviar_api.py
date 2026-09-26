# -*- coding: utf-8 -*-
"""Rutas FastAPI — Producción aviar."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel

from audit import usuario_desde_headers
from aviar import (
    LINEAS_COMUNES,
    TIPOS_EVENTO_AVI,
    TIPOS_LOTE,
    actualizar_lote,
    crear_galpon,
    crear_lote,
    ficha_lote,
    init_aviar_schema,
    listar_eventos,
    listar_galpones,
    listar_granjas,
    listar_lotes,
    listar_produccion,
    obtener_lote,
    registrar_evento,
    registrar_produccion,
    resumen_aviar,
)


def _firma(request: Request) -> str:
    return usuario_desde_headers(request) or ""


class LoteAviModel(BaseModel):
    codigo: str
    tipo: str = "parrillero"
    linea_genetica: Optional[str] = ""
    granja_id: Optional[int] = None
    galpon_id: Optional[int] = None
    fecha_ingreso: Optional[str] = ""
    n_inicial: int = 0
    n_actual: Optional[int] = None
    edad_dias_ingreso: int = 0
    peso_ingreso_prom: Optional[float] = None
    peso_actual_prom: Optional[float] = None
    sexo: str = "Mixto"
    destino: Optional[str] = ""
    observaciones: Optional[str] = ""


class ProduccionAviModel(BaseModel):
    lote_id: int
    fecha: Optional[str] = ""
    huevos: int = 0
    huevos_rotos: int = 0
    huevos_sucios: int = 0
    mortalidad: int = 0
    descartes: int = 0
    consumo_alimento_kg: Optional[float] = None
    consumo_agua_l: Optional[float] = None
    peso_promedio: Optional[float] = None
    temperatura_c: Optional[float] = None
    humedad_pct: Optional[float] = None
    observaciones: Optional[str] = ""


class EventoAviModel(BaseModel):
    lote_id: Optional[int] = None
    fecha: Optional[str] = ""
    tipo: str
    cantidad: Optional[int] = None
    peso_kg: Optional[float] = None
    detalle: Optional[str] = ""
    medicamento: Optional[str] = ""
    dosis: Optional[str] = ""


class GalponModel(BaseModel):
    nombre: str
    granja_id: Optional[int] = None
    capacidad: int = 0
    tipo_uso: str = "parrillero"
    observaciones: Optional[str] = ""


def register_aviar_routes(app, get_db, get_empresa_activa_id):
    @app.on_event("startup")
    def _init():
        conn = get_db()
        try:
            init_aviar_schema(conn)
        finally:
            conn.close()

    @app.get("/api/aviar/meta")
    def meta():
        return {
            "tipos_lote": [{"codigo": c, "nombre": n} for c, n in TIPOS_LOTE],
            "lineas": list(LINEAS_COMUNES),
            "tipos_evento": list(TIPOS_EVENTO_AVI),
        }

    @app.get("/api/aviar/resumen")
    def api_resumen():
        conn = get_db()
        try:
            return resumen_aviar(conn, get_empresa_activa_id())
        finally:
            conn.close()

    @app.get("/api/aviar/granjas")
    def api_granjas():
        conn = get_db()
        try:
            return listar_granjas(conn, get_empresa_activa_id())
        finally:
            conn.close()

    @app.get("/api/aviar/galpones")
    def api_galpones(granja_id: Optional[int] = None):
        conn = get_db()
        try:
            return listar_galpones(conn, get_empresa_activa_id(), granja_id=granja_id)
        finally:
            conn.close()

    @app.post("/api/aviar/galpones")
    def api_crear_galpon(data: GalponModel):
        conn = get_db()
        try:
            return {"id": crear_galpon(conn, get_empresa_activa_id(), data.model_dump()), "status": "ok"}
        finally:
            conn.close()

    @app.get("/api/aviar/lotes")
    def api_lotes(tipo: Optional[str] = None, estado: str = "activo", q: Optional[str] = None):
        conn = get_db()
        try:
            return listar_lotes(conn, get_empresa_activa_id(), tipo=tipo, estado=estado, q=q)
        finally:
            conn.close()

    @app.post("/api/aviar/lotes")
    def api_crear_lote(data: LoteAviModel, request: Request):
        if not (data.codigo or "").strip():
            raise HTTPException(400, "Código de lote obligatorio")
        conn = get_db()
        try:
            lid = crear_lote(conn, get_empresa_activa_id(), data.model_dump(), usuario=_firma(request))
            return {"id": lid, "status": "ok"}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            conn.close()

    @app.get("/api/aviar/lotes/{lote_id}")
    def api_lote(lote_id: int):
        conn = get_db()
        try:
            row = obtener_lote(conn, get_empresa_activa_id(), lote_id)
            if not row:
                raise HTTPException(404, "Lote no encontrado")
            return row
        finally:
            conn.close()

    @app.get("/api/aviar/lotes/{lote_id}/ficha")
    def api_ficha(lote_id: int):
        conn = get_db()
        try:
            ficha = ficha_lote(conn, get_empresa_activa_id(), lote_id)
            if not ficha:
                raise HTTPException(404, "Lote no encontrado")
            return ficha
        finally:
            conn.close()

    @app.patch("/api/aviar/lotes/{lote_id}")
    def api_upd_lote(lote_id: int, data: LoteAviModel):
        conn = get_db()
        try:
            actualizar_lote(
                conn, get_empresa_activa_id(), lote_id,
                {k: v for k, v in data.model_dump().items() if v is not None},
            )
            return {"status": "ok"}
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        finally:
            conn.close()

    @app.post("/api/aviar/produccion")
    def api_prod(data: ProduccionAviModel, request: Request):
        conn = get_db()
        try:
            pid = registrar_produccion(
                conn, get_empresa_activa_id(), data.model_dump(), usuario=_firma(request)
            )
            return {"id": pid, "status": "ok"}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            conn.close()

    @app.get("/api/aviar/produccion")
    def api_list_prod(lote_id: Optional[int] = None, limit: int = 60):
        conn = get_db()
        try:
            return listar_produccion(conn, get_empresa_activa_id(), lote_id=lote_id, limit=limit)
        finally:
            conn.close()

    @app.post("/api/aviar/eventos")
    def api_evento(data: EventoAviModel, request: Request):
        conn = get_db()
        try:
            eid = registrar_evento(
                conn, get_empresa_activa_id(), data.model_dump(), usuario=_firma(request)
            )
            return {"id": eid, "status": "ok"}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            conn.close()

    @app.get("/api/aviar/eventos")
    def api_eventos(lote_id: Optional[int] = None, limit: int = 100):
        conn = get_db()
        try:
            return listar_eventos(conn, get_empresa_activa_id(), lote_id=lote_id, limit=limit)
        finally:
            conn.close()
