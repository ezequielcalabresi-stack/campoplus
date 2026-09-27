# -*- coding: utf-8 -*-
"""Rutas FastAPI — gastos recurrentes del Estado Financiero."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from financiero_gastos_recurrentes import (
    CATEGORIAS,
    FRECUENCIAS,
    borrar_gasto,
    init_gastos_recurrentes_schema,
    listar_gastos,
    upsert_gasto,
)


class GastoRecurrenteModel(BaseModel):
    detalle: str
    categoria: str = "Otros"
    monto: float
    moneda: str = "ARS"
    dia_pago: int = Field(default=10, ge=1, le=28)
    frecuencia: str = "mensual"
    fecha_inicio: Optional[str] = None
    fecha_fin: Optional[str] = None
    activo: int = 1
    observaciones: Optional[str] = ""


def register_financiero_gastos_routes(app, get_db, get_empresa_activa_id):
    @app.on_event("startup")
    def _init():
        conn = get_db()
        try:
            init_gastos_recurrentes_schema(conn.cursor())
            conn.commit()
        finally:
            conn.close()

    @app.get("/api/financiero/gastos_recurrentes/meta")
    def api_meta():
        return {
            "categorias": CATEGORIAS,
            "frecuencias": list(FRECUENCIAS.keys()),
        }

    @app.get("/api/financiero/gastos_recurrentes")
    def api_list(solo_activos: bool = False):
        conn = get_db()
        try:
            return {
                "items": listar_gastos(conn, get_empresa_activa_id(), solo_activos=solo_activos),
                "categorias": CATEGORIAS,
                "frecuencias": list(FRECUENCIAS.keys()),
            }
        finally:
            conn.close()

    @app.post("/api/financiero/gastos_recurrentes")
    def api_create(data: GastoRecurrenteModel):
        conn = get_db()
        try:
            return upsert_gasto(conn, get_empresa_activa_id(), data.model_dump())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            conn.close()

    @app.put("/api/financiero/gastos_recurrentes/{gasto_id}")
    def api_update(gasto_id: int, data: GastoRecurrenteModel):
        conn = get_db()
        try:
            return upsert_gasto(conn, get_empresa_activa_id(), data.model_dump(), gasto_id=gasto_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            conn.close()

    @app.delete("/api/financiero/gastos_recurrentes/{gasto_id}")
    def api_delete(gasto_id: int):
        conn = get_db()
        try:
            borrar_gasto(conn, get_empresa_activa_id(), gasto_id)
            return {"status": "ok"}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        finally:
            conn.close()
