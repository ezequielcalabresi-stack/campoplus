# -*- coding: utf-8 -*-
"""API Actividades / Cuentas de imputación."""
from __future__ import annotations

from typing import List, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from actividades_imputacion import (
    arbol_actividades,
    actualizar_actividad,
    actualizar_cuenta,
    crear_actividad,
    crear_cuenta,
    init_actividades_schema,
    listar_actividades,
    listar_cuentas,
    resync_actividades_desde_excel,
    seed_actividades_si_vacio,
    soft_delete_actividad,
    soft_delete_cuenta,
)


class ActividadIn(BaseModel):
    nombre: str
    orden: int = 0
    activo: int = 1


class ActividadUpdate(BaseModel):
    nombre: Optional[str] = None
    orden: Optional[int] = None
    activo: Optional[int] = None


class CuentaIn(BaseModel):
    nombre: str
    tipo_cta: str = "EGRESO"
    activo: int = 1


class CuentaUpdate(BaseModel):
    nombre: Optional[str] = None
    tipo_cta: Optional[str] = None
    activo: Optional[int] = None
    actividad_id: Optional[int] = None


def register_actividades_routes(app, get_db, get_empresa_activa_id) -> None:
    try:
        conn = get_db()
        init_actividades_schema(conn.cursor())
        conn.commit()
        seed_actividades_si_vacio(conn, get_empresa_activa_id())
        conn.close()
    except Exception as exc:
        print(f"AVISO init actividades: {exc}")

    @app.get("/api/actividades")
    def api_list_act(con_cuentas: int = 0, todas: int = 0):
        conn = get_db()
        try:
            eid = get_empresa_activa_id()
            if con_cuentas:
                return arbol_actividades(conn, eid)
            return listar_actividades(conn, eid, solo_activas=not todas)
        finally:
            conn.close()

    @app.post("/api/actividades")
    def api_crear_act(data: ActividadIn):
        conn = get_db()
        try:
            aid = crear_actividad(conn, get_empresa_activa_id(), data.nombre, data.orden)
            return {"id": aid, "status": "ok"}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(400, f"No se pudo crear (¿nombre duplicado?): {e}") from e
        finally:
            conn.close()

    @app.put("/api/actividades/{act_id}")
    def api_upd_act(act_id: int, data: ActividadUpdate):
        conn = get_db()
        try:
            actualizar_actividad(
                conn, get_empresa_activa_id(), act_id, data.model_dump(exclude_unset=True)
            )
            return {"status": "ok"}
        finally:
            conn.close()

    @app.delete("/api/actividades/{act_id}")
    def api_del_act(act_id: int):
        conn = get_db()
        try:
            soft_delete_actividad(conn, get_empresa_activa_id(), act_id)
            return {"status": "ok"}
        finally:
            conn.close()

    @app.get("/api/actividades/{act_id}/cuentas")
    def api_list_ctas(act_id: int, tipo: Optional[str] = None, todas: int = 0):
        conn = get_db()
        try:
            return listar_cuentas(conn, act_id, solo_activas=not todas, tipo=tipo)
        finally:
            conn.close()

    @app.post("/api/actividades/{act_id}/cuentas")
    def api_crear_cta(act_id: int, data: CuentaIn):
        conn = get_db()
        try:
            cid = crear_cuenta(conn, act_id, data.nombre, data.tipo_cta)
            return {"id": cid, "status": "ok"}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(400, f"No se pudo crear cuenta: {e}") from e
        finally:
            conn.close()

    @app.put("/api/actividad-cuentas/{cta_id}")
    def api_upd_cta(cta_id: int, data: CuentaUpdate):
        conn = get_db()
        try:
            actualizar_cuenta(conn, cta_id, data.model_dump(exclude_unset=True))
            return {"status": "ok"}
        finally:
            conn.close()

    @app.delete("/api/actividad-cuentas/{cta_id}")
    def api_del_cta(cta_id: int):
        conn = get_db()
        try:
            soft_delete_cuenta(conn, cta_id)
            return {"status": "ok"}
        finally:
            conn.close()

    @app.post("/api/actividades/reseed")
    def api_reseed(forzar: int = 0):
        """Resincroniza desde Excel maestro. forzar=1 reemplaza catálogo activo."""
        conn = get_db()
        try:
            init_actividades_schema(conn.cursor())
            conn.commit()
            if forzar:
                return resync_actividades_desde_excel(conn, get_empresa_activa_id())
            return seed_actividades_si_vacio(conn, get_empresa_activa_id())
        finally:
            conn.close()
