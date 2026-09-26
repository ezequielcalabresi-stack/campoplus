# -*- coding: utf-8 -*-
"""Rutas FastAPI — CM05 / Convenio Multilateral."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from cm05 import (
    aplicar_sugerencias,
    asegurar_periodo,
    borrar_linea,
    export_csv,
    ficha_periodo,
    guardar_config_empresa,
    init_cm05_schema,
    listar_jurisdicciones,
    listar_lineas,
    listar_periodos,
    obtener_config_empresa,
    upsert_linea,
)


class ConfigCmModel(BaseModel):
    es_cm: int = 1
    sede_codigo: str = "02"
    nro_inscripcion_cm: Optional[str] = ""
    observaciones: Optional[str] = ""


class PeriodoModel(BaseModel):
    anio: int


class LineaModel(BaseModel):
    juris_codigo: str
    ingresos: float = 0
    gastos: float = 0
    origen: str = "manual"
    notas: Optional[str] = ""


def register_cm05_routes(app, get_db, get_empresa_activa_id):
    @app.on_event("startup")
    def _init():
        conn = get_db()
        try:
            init_cm05_schema(conn)
        finally:
            conn.close()

    @app.get("/api/cm05/jurisdicciones")
    def api_juris():
        conn = get_db()
        try:
            init_cm05_schema(conn)
            return listar_jurisdicciones(conn)
        finally:
            conn.close()

    @app.get("/api/cm05/config")
    def api_get_cfg():
        conn = get_db()
        try:
            init_cm05_schema(conn)
            return obtener_config_empresa(conn, get_empresa_activa_id())
        finally:
            conn.close()

    @app.put("/api/cm05/config")
    def api_put_cfg(data: ConfigCmModel):
        conn = get_db()
        try:
            init_cm05_schema(conn)
            return guardar_config_empresa(conn, get_empresa_activa_id(), data.model_dump())
        finally:
            conn.close()

    @app.get("/api/cm05/periodos")
    def api_periodos():
        conn = get_db()
        try:
            init_cm05_schema(conn)
            return listar_periodos(conn, get_empresa_activa_id())
        finally:
            conn.close()

    @app.post("/api/cm05/periodos")
    def api_crear_periodo(data: PeriodoModel):
        if data.anio < 2000 or data.anio > 2100:
            raise HTTPException(400, "Año inválido")
        conn = get_db()
        try:
            init_cm05_schema(conn)
            return asegurar_periodo(conn, get_empresa_activa_id(), data.anio)
        finally:
            conn.close()

    @app.get("/api/cm05/periodos/{periodo_id}")
    def api_ficha(periodo_id: int):
        conn = get_db()
        try:
            init_cm05_schema(conn)
            ficha = ficha_periodo(conn, get_empresa_activa_id(), periodo_id)
            if not ficha:
                raise HTTPException(404, "Período no encontrado")
            return ficha
        finally:
            conn.close()

    @app.get("/api/cm05/periodos/{periodo_id}/lineas")
    def api_lineas(periodo_id: int):
        conn = get_db()
        try:
            return listar_lineas(conn, get_empresa_activa_id(), periodo_id)
        finally:
            conn.close()

    @app.post("/api/cm05/periodos/{periodo_id}/lineas")
    def api_upsert_linea(periodo_id: int, data: LineaModel):
        conn = get_db()
        try:
            return upsert_linea(conn, get_empresa_activa_id(), periodo_id, data.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            conn.close()

    @app.delete("/api/cm05/lineas/{linea_id}")
    def api_del_linea(linea_id: int):
        conn = get_db()
        try:
            borrar_linea(conn, get_empresa_activa_id(), linea_id)
            return {"status": "ok"}
        finally:
            conn.close()

    @app.post("/api/cm05/periodos/{periodo_id}/sugerir")
    def api_sugerir(periodo_id: int):
        conn = get_db()
        try:
            return aplicar_sugerencias(conn, get_empresa_activa_id(), periodo_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            conn.close()

    @app.get("/api/cm05/periodos/{periodo_id}/export.csv")
    def api_export(periodo_id: int):
        conn = get_db()
        try:
            txt = export_csv(conn, get_empresa_activa_id(), periodo_id)
            return PlainTextResponse(
                txt,
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="cm05_{periodo_id}.csv"'
                },
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        finally:
            conn.close()
