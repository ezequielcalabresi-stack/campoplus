# -*- coding: utf-8 -*-
"""Rutas FastAPI — Producción porcina."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel

from audit import usuario_desde_headers
from porcino import (
    CATEGORIAS_CERDO,
    TIPOS_EVENTO_POR,
    TIPOS_SALA,
    actualizar_animal,
    buscar_animales,
    crear_animal,
    crear_lote,
    crear_sala,
    ficha_cerdo,
    init_porcino_schema,
    listar_eventos,
    listar_granjas,
    listar_lotes,
    listar_partos,
    listar_salas,
    obtener_animal,
    registrar_destete_parto,
    registrar_evento,
    registrar_parto,
    resumen_porcino,
)


def _firma(request: Request) -> str:
    return usuario_desde_headers(request) or ""


class AnimalPorModel(BaseModel):
    caravana: Optional[str] = ""
    chip: Optional[str] = ""
    nombre: Optional[str] = ""
    sexo: str = "Hembra"
    raza: Optional[str] = ""
    linea_genetica: Optional[str] = ""
    categoria: str = "cerda_vacia"
    fecha_nacimiento: Optional[str] = ""
    madre_id: Optional[int] = None
    padre_id: Optional[int] = None
    sala_id: Optional[int] = None
    granja_id: Optional[int] = None
    lote_id: Optional[int] = None
    peso_ultimo: Optional[float] = None
    fecha_peso: Optional[str] = ""
    condicion_corporal: Optional[float] = None
    origen: Optional[str] = ""
    observaciones: Optional[str] = ""


class EventoPorModel(BaseModel):
    animal_id: Optional[int] = None
    lote_id: Optional[int] = None
    fecha: Optional[str] = ""
    tipo: str
    sala_id: Optional[int] = None
    peso_kg: Optional[float] = None
    cantidad: Optional[int] = None
    detalle: Optional[str] = ""
    medicamento: Optional[str] = ""
    dosis: Optional[str] = ""
    padrillo_id: Optional[int] = None


class PartoModel(BaseModel):
    cerda_id: int
    fecha: Optional[str] = ""
    nacidos_vivos: int = 0
    nacidos_muertos: int = 0
    momificados: int = 0
    destetados: Optional[int] = None
    fecha_destete: Optional[str] = ""
    peso_destete_prom: Optional[float] = None
    sala_id: Optional[int] = None
    observaciones: Optional[str] = ""


class DesteteModel(BaseModel):
    destetados: Optional[int] = None
    fecha_destete: Optional[str] = ""
    peso_destete_prom: Optional[float] = None


class LotePorModel(BaseModel):
    codigo: str
    granja_id: Optional[int] = None
    sala_id: Optional[int] = None
    etapa: str = "engorde"
    fecha_ingreso: Optional[str] = ""
    n_inicial: int = 0
    n_actual: Optional[int] = None
    peso_ingreso_prom: Optional[float] = None
    peso_actual_prom: Optional[float] = None
    observaciones: Optional[str] = ""


class SalaModel(BaseModel):
    nombre: str
    granja_id: Optional[int] = None
    tipo: str = "engorde"
    capacidad: int = 0
    observaciones: Optional[str] = ""


def register_porcino_routes(app, get_db, get_empresa_activa_id):
    @app.on_event("startup")
    def _init():
        conn = get_db()
        try:
            init_porcino_schema(conn)
        finally:
            conn.close()

    @app.get("/api/porcino/meta")
    def meta():
        return {
            "categorias": [{"codigo": c, "nombre": n} for c, n in CATEGORIAS_CERDO],
            "tipos_sala": [{"codigo": c, "nombre": n} for c, n in TIPOS_SALA],
            "tipos_evento": list(TIPOS_EVENTO_POR),
        }

    @app.get("/api/porcino/resumen")
    def api_resumen():
        conn = get_db()
        try:
            return resumen_porcino(conn, get_empresa_activa_id())
        finally:
            conn.close()

    @app.get("/api/porcino/granjas")
    def api_granjas():
        conn = get_db()
        try:
            return listar_granjas(conn, get_empresa_activa_id())
        finally:
            conn.close()

    @app.get("/api/porcino/salas")
    def api_salas(granja_id: Optional[int] = None):
        conn = get_db()
        try:
            return listar_salas(conn, get_empresa_activa_id(), granja_id=granja_id)
        finally:
            conn.close()

    @app.post("/api/porcino/salas")
    def api_crear_sala(data: SalaModel):
        conn = get_db()
        try:
            return {"id": crear_sala(conn, get_empresa_activa_id(), data.model_dump()), "status": "ok"}
        finally:
            conn.close()

    @app.get("/api/porcino/animales")
    def api_animales(
        q: Optional[str] = None,
        categoria: Optional[str] = None,
        sala_id: Optional[int] = None,
        estado: str = "activo",
        limit: int = 500,
    ):
        conn = get_db()
        try:
            return buscar_animales(
                conn, get_empresa_activa_id(),
                q=q, categoria=categoria, sala_id=sala_id, estado=estado, limit=limit,
            )
        finally:
            conn.close()

    @app.post("/api/porcino/animales")
    def api_crear_animal(data: AnimalPorModel, request: Request):
        conn = get_db()
        try:
            aid = crear_animal(conn, get_empresa_activa_id(), data.model_dump(), usuario=_firma(request))
            return {"id": aid, "status": "ok"}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            conn.close()

    @app.get("/api/porcino/animales/{animal_id}")
    def api_animal(animal_id: int):
        conn = get_db()
        try:
            row = obtener_animal(conn, get_empresa_activa_id(), animal_id)
            if not row:
                raise HTTPException(404, "Animal no encontrado")
            return row
        finally:
            conn.close()

    @app.get("/api/porcino/animales/{animal_id}/ficha")
    def api_ficha(animal_id: int):
        conn = get_db()
        try:
            ficha = ficha_cerdo(conn, get_empresa_activa_id(), animal_id)
            if not ficha:
                raise HTTPException(404, "Animal no encontrado")
            return ficha
        finally:
            conn.close()

    @app.patch("/api/porcino/animales/{animal_id}")
    def api_upd_animal(animal_id: int, data: AnimalPorModel):
        conn = get_db()
        try:
            actualizar_animal(
                conn, get_empresa_activa_id(), animal_id,
                {k: v for k, v in data.model_dump().items() if v is not None and v != ""},
            )
            return {"status": "ok"}
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        finally:
            conn.close()

    @app.post("/api/porcino/eventos")
    def api_evento(data: EventoPorModel, request: Request):
        conn = get_db()
        try:
            eid = registrar_evento(conn, get_empresa_activa_id(), data.model_dump(), usuario=_firma(request))
            return {"id": eid, "status": "ok"}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            conn.close()

    @app.get("/api/porcino/eventos")
    def api_eventos(animal_id: Optional[int] = None, lote_id: Optional[int] = None, limit: int = 100):
        conn = get_db()
        try:
            return listar_eventos(conn, get_empresa_activa_id(), animal_id=animal_id, lote_id=lote_id, limit=limit)
        finally:
            conn.close()

    @app.post("/api/porcino/partos")
    def api_parto(data: PartoModel, request: Request):
        conn = get_db()
        try:
            pid = registrar_parto(conn, get_empresa_activa_id(), data.model_dump(), usuario=_firma(request))
            return {"id": pid, "status": "ok"}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            conn.close()

    @app.get("/api/porcino/partos")
    def api_partos(cerda_id: Optional[int] = None, limit: int = 100):
        conn = get_db()
        try:
            return listar_partos(conn, get_empresa_activa_id(), cerda_id=cerda_id, limit=limit)
        finally:
            conn.close()

    @app.post("/api/porcino/partos/{parto_id}/destete")
    def api_destete(parto_id: int, data: DesteteModel, request: Request):
        conn = get_db()
        try:
            registrar_destete_parto(
                conn, get_empresa_activa_id(), parto_id, data.model_dump(), usuario=_firma(request)
            )
            return {"status": "ok"}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            conn.close()

    @app.get("/api/porcino/lotes")
    def api_lotes(estado: str = "activo"):
        conn = get_db()
        try:
            return listar_lotes(conn, get_empresa_activa_id(), estado=estado)
        finally:
            conn.close()

    @app.post("/api/porcino/lotes")
    def api_crear_lote(data: LotePorModel):
        if not (data.codigo or "").strip():
            raise HTTPException(400, "Código de lote obligatorio")
        conn = get_db()
        try:
            return {"id": crear_lote(conn, get_empresa_activa_id(), data.model_dump()), "status": "ok"}
        finally:
            conn.close()
