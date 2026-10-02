# -*- coding: utf-8 -*-
"""Rutas FastAPI — empleados y conformación de sueldos."""
from __future__ import annotations

from typing import List, Optional

from fastapi import File, HTTPException, UploadFile
from pydantic import BaseModel

from sueldos import (
    CONCEPTOS,
    CONVENIOS,
    aplicar_depositos,
    asientos_sueldos_mes,
    borrar_movimiento,
    importar_recibos,
    listar_recibos,
    vincular_recibo,
    conformar_sueldos,
    detalle_sueldos_mes,
    guardar_empleado,
    guardar_movimiento,
    init_sueldos_schema,
    listar_empleados,
    meses_con_movimientos,
    preview_conformacion,
    resumen_empleado_mes,
)


class EmpleadoModel(BaseModel):
    nombre: str
    dni: Optional[str] = ""
    cuil: Optional[str] = ""
    banco: Optional[str] = ""
    cbu: Optional[str] = ""
    convenio: Optional[str] = ""
    centro_costo: Optional[str] = "2"
    activo: int = 1
    excluir_indice: int = 0
    observaciones: Optional[str] = ""
    haber_base: Optional[float] = 0


class ConformarItem(BaseModel):
    empleado_id: int
    porcentaje: float = 0
    sueldo: Optional[float] = None


class ConformarModel(BaseModel):
    mes: str
    detalle_indice: Optional[str] = ""
    fecha: Optional[str] = None
    reemplazar: bool = False
    items: List[ConformarItem]


class VincularReciboModel(BaseModel):
    empleado_id: Optional[int] = None


class AplicarRecibosModel(BaseModel):
    mes: str
    recibo_ids: Optional[List[int]] = None


class AsientosRecibosModel(BaseModel):
    mes: str


class MovimientoModel(BaseModel):
    empleado_id: int
    fecha: str
    detalle: str
    debe: float = 0
    haber: float = 0


def register_sueldos_routes(app, get_db, get_empresa_activa_id):
    def _conn():
        conn = get_db()
        init_sueldos_schema(conn.cursor())
        conn.commit()
        return conn

    @app.on_event("startup")
    def _init():
        conn = _conn()
        conn.close()

    def _run(fn, *args, status=400, **kwargs):
        conn = _conn()
        try:
            return fn(conn, get_empresa_activa_id(), *args, **kwargs)
        except ValueError as e:
            raise HTTPException(status_code=status, detail=str(e))
        finally:
            conn.close()

    @app.get("/api/sueldos/meta")
    def api_meta():
        return {
            "conceptos": CONCEPTOS,
            "convenios": CONVENIOS,
            "meses": _run(meses_con_movimientos),
        }

    @app.get("/api/sueldos/empleados")
    def api_empleados(incluir_inactivos: bool = False):
        return _run(listar_empleados, incluir_inactivos=incluir_inactivos)

    @app.post("/api/sueldos/empleados")
    def api_empleado_nuevo(data: EmpleadoModel):
        return _run(guardar_empleado, data.model_dump())

    @app.put("/api/sueldos/empleados/{empleado_id}")
    def api_empleado_editar(empleado_id: int, data: EmpleadoModel):
        return _run(guardar_empleado, data.model_dump(), empleado_id=empleado_id)

    @app.get("/api/sueldos/conformacion")
    def api_preview(mes: str, porcentaje: float = 0):
        return _run(preview_conformacion, mes, porcentaje)

    @app.post("/api/sueldos/conformar")
    def api_conformar(data: ConformarModel):
        return _run(
            conformar_sueldos,
            data.mes,
            data.detalle_indice or "",
            [i.model_dump() for i in data.items],
            fecha=data.fecha,
            reemplazar=data.reemplazar,
        )

    @app.get("/api/sueldos/empleados/{empleado_id}/mes/{mes}")
    def api_resumen(empleado_id: int, mes: str):
        return _run(resumen_empleado_mes, empleado_id, mes, status=404)

    @app.post("/api/sueldos/movimientos")
    def api_mov_nuevo(data: MovimientoModel):
        return _run(guardar_movimiento, data.model_dump())

    @app.put("/api/sueldos/movimientos/{mov_id}")
    def api_mov_editar(mov_id: int, data: MovimientoModel):
        return _run(guardar_movimiento, data.model_dump(), mov_id=mov_id)

    @app.delete("/api/sueldos/movimientos/{mov_id}")
    def api_mov_borrar(mov_id: int):
        _run(borrar_movimiento, mov_id, status=404)
        return {"status": "ok"}

    @app.get("/api/sueldos/detalle")
    def api_detalle(mes: str, solo_activos: bool = True):
        return _run(detalle_sueldos_mes, mes, solo_activos=solo_activos)

    @app.post("/api/sueldos/recibos/importar")
    async def api_recibos_importar(archivos: List[UploadFile] = File(...)):
        datos = [(a.filename or "recibos.pdf", await a.read()) for a in archivos]
        return _run(importar_recibos, datos)

    @app.get("/api/sueldos/recibos")
    def api_recibos(mes: str):
        return _run(listar_recibos, mes)

    @app.put("/api/sueldos/recibos/{recibo_id}/empleado")
    def api_recibo_vincular(recibo_id: int, data: VincularReciboModel):
        return _run(vincular_recibo, recibo_id, data.empleado_id)

    @app.post("/api/sueldos/recibos/aplicar")
    def api_recibos_aplicar(data: AplicarRecibosModel):
        return _run(aplicar_depositos, data.mes, data.recibo_ids)

    @app.post("/api/sueldos/recibos/asientos")
    def api_recibos_asientos(data: AsientosRecibosModel):
        return _run(asientos_sueldos_mes, data.mes)
