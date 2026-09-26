# -*- coding: utf-8 -*-
"""Rutas FastAPI — Ganadería y Tambo."""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from ganaderia import (
    CATEGORIAS_AAA,
    COLORES_ANGUS,
    SISTEMAS,
    TIPOS_EVENTO,
    actualizar_animal,
    buscar_animales,
    composicion_rodeos_tambo,
    crear_animal,
    crear_rodeo,
    ficha_animal,
    importar_eventos_masivo,
    importar_lecturas_eid,
    importar_masivo_tambo,
    init_ganaderia_schema,
    listar_categorias,
    listar_eventos,
    listar_produccion_diaria,
    listar_protocolos_iatf,
    listar_rodeos,
    listar_tambos,
    obtener_animal,
    registrar_control_lechero,
    registrar_evento,
    registrar_produccion_diaria,
    resumen_stock,
    resumen_tambo,
)
from audit import usuario_desde_headers


class RodeoModel(BaseModel):
    nombre: str
    sistema: str = "cria"
    campo_ref: Optional[str] = ""
    superficie_has: float = 0
    capacidad: int = 0
    observaciones: Optional[str] = ""


class AnimalModel(BaseModel):
    caravana_visual: Optional[str] = ""
    caravana_electronica: Optional[str] = ""
    senasa_id: Optional[str] = ""
    nombre: Optional[str] = ""
    raza: Optional[str] = ""
    sexo: str = "Indefinido"
    fecha_nacimiento: Optional[str] = ""
    madre_id: Optional[int] = None
    padre_id: Optional[int] = None
    categoria_id: Optional[int] = None
    rodeo_id: Optional[int] = None
    sistema_actual: str = "cria"
    estado: str = "activo"
    peso_ultimo: Optional[float] = None
    fecha_peso_ultimo: Optional[str] = ""
    fecha_alta: Optional[str] = ""
    origen: Optional[str] = ""
    color: Optional[str] = ""
    observaciones: Optional[str] = ""
    es_tambo: int = 0
    rp: Optional[str] = ""
    pedigree: Optional[str] = ""
    registro_aaa: Optional[str] = ""
    categoria_aaa: Optional[str] = ""
    color_capa: Optional[str] = ""
    criador_aaa: Optional[str] = ""
    prefijo_cabana: Optional[str] = ""
    fecha_registro_aaa: Optional[str] = ""
    dep_pn: Optional[float] = None
    dep_pd: Optional[float] = None
    dep_pf: Optional[float] = None
    dep_leche: Optional[float] = None


class AnimalUpdateModel(BaseModel):
    caravana_visual: Optional[str] = None
    caravana_electronica: Optional[str] = None
    senasa_id: Optional[str] = None
    nombre: Optional[str] = None
    raza: Optional[str] = None
    sexo: Optional[str] = None
    fecha_nacimiento: Optional[str] = None
    madre_id: Optional[int] = None
    padre_id: Optional[int] = None
    categoria_id: Optional[int] = None
    rodeo_id: Optional[int] = None
    sistema_actual: Optional[str] = None
    estado: Optional[str] = None
    origen: Optional[str] = None
    color: Optional[str] = None
    observaciones: Optional[str] = None
    es_tambo: Optional[int] = None
    rp: Optional[str] = None
    pedigree: Optional[str] = None
    registro_aaa: Optional[str] = None
    categoria_aaa: Optional[str] = None
    color_capa: Optional[str] = None
    criador_aaa: Optional[str] = None
    prefijo_cabana: Optional[str] = None
    fecha_registro_aaa: Optional[str] = None
    dep_pn: Optional[float] = None
    dep_pd: Optional[float] = None
    dep_pf: Optional[float] = None
    dep_leche: Optional[float] = None


class EventoModel(BaseModel):
    animal_id: Optional[int] = None
    fecha: Optional[str] = ""
    tipo: str
    rodeo_id: Optional[int] = None
    categoria_id: Optional[int] = None
    peso_kg: Optional[float] = None
    eid_leido: Optional[str] = ""
    resultado: Optional[str] = ""
    protocolo: Optional[str] = ""
    toro_id: Optional[int] = None
    semen_lote: Optional[str] = ""
    tecnico: Optional[str] = ""
    medicamento: Optional[str] = ""
    dosis: Optional[str] = ""
    litros: Optional[float] = None
    grasa_pct: Optional[float] = None
    proteina_pct: Optional[float] = None
    rcs: Optional[float] = None
    costo: float = 0
    detalle: Optional[str] = ""
    sistema_destino: Optional[str] = ""
    score_celo: Optional[str] = ""
    toro_nombre: Optional[str] = ""
    pajuela: Optional[str] = ""
    facilidad_parto: Optional[str] = ""
    cria_sexo: Optional[str] = ""
    cria_peso: Optional[float] = None
    cria_destino: Optional[str] = ""
    cria_caravana: Optional[str] = ""
    motivo_baja: Optional[str] = ""
    comprador: Optional[str] = ""
    origen_dato: Optional[str] = ""
    ref_externa: Optional[str] = ""
    # aliases para import
    caravana: Optional[str] = None
    caravana_visual: Optional[str] = None
    eid: Optional[str] = None
    rp: Optional[str] = None


class ImportEventosModel(BaseModel):
    eventos: List[EventoModel] = Field(default_factory=list)


class LecturaEIDItem(BaseModel):
    eid: str
    fecha: Optional[str] = ""
    peso_kg: Optional[float] = None
    caravana_visual: Optional[str] = ""
    sexo: Optional[str] = "Indefinido"
    sistema_actual: Optional[str] = "cria"
    rodeo_id: Optional[int] = None
    crear_si_no_existe: bool = False
    detalle: Optional[str] = ""


class ImportEIDModel(BaseModel):
    lecturas: List[LecturaEIDItem] = Field(default_factory=list)


class ProduccionDiariaModel(BaseModel):
    tambo_id: Optional[int] = None
    fecha: Optional[str] = ""
    turno: str = "mañana"
    litros_total: float = 0
    vacas_ordenadas: int = 0
    grasa_pct: Optional[float] = None
    proteina_pct: Optional[float] = None
    rcs: Optional[int] = None
    urea: Optional[float] = None
    temperatura_tanque: Optional[float] = None
    observaciones: Optional[str] = ""


class ControlLecheroModel(BaseModel):
    animal_id: int
    fecha: Optional[str] = ""
    litros: float = 0
    grasa_pct: Optional[float] = None
    proteina_pct: Optional[float] = None
    rcs: Optional[int] = None
    lactancia_id: Optional[int] = None
    observaciones: Optional[str] = ""


def _firma(request: Request) -> str:
    u = usuario_desde_headers(request)
    return u["nombre"] if u["nombre"] != "Sin firmar" else ""


def register_ganaderia_routes(app, get_db, get_empresa_activa_id) -> None:
    # Asegurar schema
    try:
        conn = get_db()
        init_ganaderia_schema(conn.cursor())
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"AVISO init ganadería: {exc}")

    @app.get("/api/ganaderia/meta")
    def meta():
        return {
            "sistemas": list(SISTEMAS),
            "tipos_evento": list(TIPOS_EVENTO),
            "categorias_aaa": [{"codigo": c, "nombre": n} for c, n in CATEGORIAS_AAA],
            "colores_angus": list(COLORES_ANGUS),
        }

    @app.get("/api/ganaderia/resumen")
    def api_resumen():
        conn = get_db()
        try:
            return resumen_stock(conn, get_empresa_activa_id())
        finally:
            conn.close()

    @app.get("/api/ganaderia/categorias")
    def api_categorias():
        conn = get_db()
        try:
            return listar_categorias(conn, get_empresa_activa_id())
        finally:
            conn.close()

    @app.get("/api/ganaderia/rodeos")
    def api_rodeos(sistema: Optional[str] = None):
        conn = get_db()
        try:
            return listar_rodeos(conn, get_empresa_activa_id(), sistema=sistema)
        finally:
            conn.close()

    @app.post("/api/ganaderia/rodeos")
    def api_crear_rodeo(data: RodeoModel):
        if not (data.nombre or "").strip():
            raise HTTPException(400, "Nombre de rodeo obligatorio")
        if data.sistema not in SISTEMAS:
            raise HTTPException(400, f"Sistema inválido: {data.sistema}")
        conn = get_db()
        try:
            rid = crear_rodeo(conn, get_empresa_activa_id(), data.model_dump())
            return {"id": rid, "status": "ok"}
        finally:
            conn.close()

    @app.get("/api/ganaderia/animales")
    def api_animales(
        q: Optional[str] = None,
        sistema: Optional[str] = None,
        rodeo_id: Optional[int] = None,
        estado: str = "activo",
        es_tambo: Optional[int] = None,
        categoria_aaa: Optional[str] = None,
        raza: Optional[str] = None,
        limit: int = 500,
    ):
        conn = get_db()
        try:
            return buscar_animales(
                conn,
                get_empresa_activa_id(),
                q=q,
                sistema=sistema,
                rodeo_id=rodeo_id,
                estado=estado,
                es_tambo=es_tambo,
                categoria_aaa=categoria_aaa,
                raza=raza,
                limit=limit,
            )
        finally:
            conn.close()

    @app.get("/api/ganaderia/animales/{animal_id}")
    def api_animal(animal_id: int):
        conn = get_db()
        try:
            row = obtener_animal(conn, get_empresa_activa_id(), animal_id)
            if not row:
                raise HTTPException(404, "Animal no encontrado")
            eventos = listar_eventos(
                conn, get_empresa_activa_id(), animal_id=animal_id, limit=100
            )
            return {"animal": row, "eventos": eventos}
        finally:
            conn.close()

    @app.get("/api/ganaderia/animales/{animal_id}/ficha")
    def api_ficha_animal(animal_id: int):
        conn = get_db()
        try:
            ficha = ficha_animal(conn, get_empresa_activa_id(), animal_id)
            if not ficha:
                raise HTTPException(404, "Animal no encontrado")
            return ficha
        finally:
            conn.close()

    @app.post("/api/ganaderia/animales")
    def api_crear_animal(data: AnimalModel, request: Request):
        if data.sistema_actual not in SISTEMAS:
            raise HTTPException(400, "Sistema inválido")
        conn = get_db()
        try:
            aid = crear_animal(
                conn,
                get_empresa_activa_id(),
                data.model_dump(),
                usuario=_firma(request),
            )
            return {"id": aid, "status": "ok"}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            conn.close()

    @app.put("/api/ganaderia/animales/{animal_id}")
    def api_upd_animal(animal_id: int, data: AnimalUpdateModel):
        conn = get_db()
        try:
            payload = data.model_dump(exclude_unset=True)
            actualizar_animal(conn, get_empresa_activa_id(), animal_id, payload)
            return {"status": "ok"}
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        finally:
            conn.close()

    @app.get("/api/ganaderia/eventos")
    def api_eventos(
        animal_id: Optional[int] = None,
        tipo: Optional[str] = None,
        desde: Optional[str] = None,
        hasta: Optional[str] = None,
        limit: int = 200,
    ):
        conn = get_db()
        try:
            return listar_eventos(
                conn,
                get_empresa_activa_id(),
                animal_id=animal_id,
                tipo=tipo,
                desde=desde,
                hasta=hasta,
                limit=limit,
            )
        finally:
            conn.close()

    @app.post("/api/ganaderia/eventos")
    def api_evento(data: EventoModel, request: Request):
        conn = get_db()
        try:
            return registrar_evento(
                conn,
                get_empresa_activa_id(),
                data.model_dump(),
                usuario=_firma(request),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            conn.close()

    @app.post("/api/ganaderia/eventos/importar")
    def api_import_eventos(data: ImportEventosModel, request: Request):
        if not data.eventos:
            raise HTTPException(400, "Sin eventos para importar")
        conn = get_db()
        try:
            result = importar_eventos_masivo(
                conn,
                get_empresa_activa_id(),
                [x.model_dump() for x in data.eventos],
                usuario=_firma(request),
            )
            return {"status": "ok", **result}
        finally:
            conn.close()

    @app.get("/api/ganaderia/protocolos-iatf")
    def api_protocolos():
        conn = get_db()
        try:
            return listar_protocolos_iatf(conn, get_empresa_activa_id())
        finally:
            conn.close()

    @app.post("/api/ganaderia/lecturas-eid")
    def api_import_eid(data: ImportEIDModel, request: Request):
        if not data.lecturas:
            raise HTTPException(400, "Sin lecturas")
        conn = get_db()
        try:
            result = importar_lecturas_eid(
                conn,
                get_empresa_activa_id(),
                [x.model_dump() for x in data.lecturas],
                usuario=_firma(request),
            )
            return {"status": "ok", **result}
        finally:
            conn.close()

    # ——— Tambo ———
    @app.get("/api/tambo/resumen")
    def api_tambo_resumen():
        conn = get_db()
        try:
            out = resumen_tambo(conn, get_empresa_activa_id())
            out["composicion_rodeos"] = composicion_rodeos_tambo(
                conn, get_empresa_activa_id()
            )
            return out
        finally:
            conn.close()

    @app.get("/api/tambo/establecimientos")
    def api_tambos():
        conn = get_db()
        try:
            return listar_tambos(conn, get_empresa_activa_id())
        finally:
            conn.close()

    @app.get("/api/tambo/produccion")
    def api_prod(tambo_id: Optional[int] = None, limit: int = 60):
        conn = get_db()
        try:
            return listar_produccion_diaria(
                conn, get_empresa_activa_id(), tambo_id=tambo_id, limit=limit
            )
        finally:
            conn.close()

    @app.post("/api/tambo/produccion")
    def api_reg_prod(data: ProduccionDiariaModel, request: Request):
        conn = get_db()
        try:
            pid = registrar_produccion_diaria(
                conn,
                get_empresa_activa_id(),
                data.model_dump(),
                usuario=_firma(request),
            )
            return {"id": pid, "status": "ok"}
        finally:
            conn.close()

    @app.post("/api/tambo/control-lechero")
    def api_control(data: ControlLecheroModel):
        conn = get_db()
        try:
            cid = registrar_control_lechero(
                conn, get_empresa_activa_id(), data.model_dump()
            )
            return {"id": cid, "status": "ok"}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            conn.close()

    @app.post("/api/tambo/importar")
    def api_tambo_importar(data: ImportEventosModel, request: Request):
        if not data.eventos:
            raise HTTPException(400, "Sin filas para importar")
        conn = get_db()
        try:
            result = importar_masivo_tambo(
                conn,
                get_empresa_activa_id(),
                [x.model_dump() for x in data.eventos],
                usuario=_firma(request),
            )
            return {"status": "ok", **result}
        finally:
            conn.close()
