# -*- coding: utf-8 -*-
"""Rutas de ratios y EECC históricos."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel

from ratios_eecc import borrar_eecc, guardar_eecc, init_eecc_schema, panel_ratios


class EeccIn(BaseModel):
    ejercicio: str
    fecha_cierre: Optional[str] = ""
    origen: str = "manual"
    cerrado: int = 1
    activo_corriente: float = 0
    activo_no_corriente: float = 0
    pasivo_corriente: float = 0
    pasivo_no_corriente: float = 0
    patrimonio: float = 0
    ingresos: float = 0
    gastos: float = 0
    resultado: Optional[float] = None
    existencias: float = 0
    indice_cierre: Optional[float] = None
    notas: Optional[str] = ""


def register_ratios_routes(app, get_db, get_empresa_activa_id) -> None:
    try:
        conn = get_db()
        init_eecc_schema(conn.cursor())
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"AVISO init EECC: {exc}")

    @app.get("/api/contabilidad/ratios")
    def api_ratios():
        conn = get_db()
        try:
            return panel_ratios(conn, get_empresa_activa_id())
        finally:
            conn.close()

    @app.post("/api/contabilidad/eecc")
    def api_guardar(data: EeccIn):
        conn = get_db()
        try:
            return guardar_eecc(conn, get_empresa_activa_id(), data.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            conn.close()

    @app.put("/api/contabilidad/eecc/{eecc_id}")
    def api_editar(eecc_id: int, data: EeccIn):
        conn = get_db()
        try:
            return guardar_eecc(conn, get_empresa_activa_id(), data.model_dump(), eecc_id=eecc_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            conn.close()

    @app.delete("/api/contabilidad/eecc/{eecc_id}")
    def api_borrar(eecc_id: int):
        conn = get_db()
        try:
            borrar_eecc(conn, get_empresa_activa_id(), eecc_id)
            return {"status": "ok"}
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        finally:
            conn.close()
