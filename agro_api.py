# -*- coding: utf-8 -*-
"""Rutas FastAPI del módulo agro / márgenes brutos / campaña."""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional
import re

from fastapi import Body, HTTPException
from pydantic import BaseModel

from agro_campania import (
    ROTACION_IDEAL,
    campo_con_lotes,
    codigo_campania_actual,
    etiqueta_campania,
    generar_plan_campania,
    listar_campos,
    listar_planificacion,
    normalizar_codigo_campania,
    normalizar_cultivo,
    porcentaje_aparceria_por_rendimiento,
    sincronizar_arrendador_proveedor,
    sugerir_cultivo_rotacion,
)


class EscalaAparceriaModel(BaseModel):
    rendimiento_tn_ha: float
    porcentaje: float
    orden: int = 0


class LoteAgroModel(BaseModel):
    codigo: Optional[str] = ""
    nombre: str
    superficie_base: float = 0.0
    lat: Optional[float] = None
    lng: Optional[float] = None
    geojson: Optional[str] = ""
    orden: int = 0


class ContratoArrendamientoModel(BaseModel):
    modalidad: str = "kilos_fijos"
    grano: str = "Soja"
    kilos_por_ha: float = 0.0
    porcentaje_base: float = 0.0
    monto_fijo: float = 0.0
    vigencia_desde: Optional[str] = ""
    vigencia_hasta: Optional[str] = ""
    observaciones: Optional[str] = ""
    escalas: Optional[List[EscalaAparceriaModel]] = None
    campania_id: Optional[int] = None


class CampoAgroModel(BaseModel):
    tipo: str = "propio"
    nombre: str
    superficie_total: float = 0.0
    arrendador_cuit: Optional[str] = ""
    arrendador_razon: Optional[str] = ""
    arrendador_domicilio: Optional[str] = ""
    arrendador_telefono: Optional[str] = ""
    arrendador_email: Optional[str] = ""
    arrendador_localidad: Optional[str] = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    direccion_ref: Optional[str] = ""
    notas: Optional[str] = ""
    lotes: Optional[List[LoteAgroModel]] = None
    contrato: Optional[ContratoArrendamientoModel] = None
    sync_proveedor: bool = True


class CampaniaAgroModel(BaseModel):
    codigo: str
    nombre: Optional[str] = ""
    activa: int = 1
    fecha_inicio: Optional[str] = ""
    fecha_fin: Optional[str] = ""
    notas: Optional[str] = ""


class PlanLoteItemModel(BaseModel):
    lote_id: int
    cultivo_antecesor: Optional[str] = ""
    cultivo_planificado: Optional[str] = ""
    superficie: Optional[float] = None
    notas: Optional[str] = ""
    modificado_manual: int = 1


class PlanLoteLoteModel(BaseModel):
    items: List[PlanLoteItemModel]


def _guardar_contrato_campo(cursor, campo_id: int, empresa_id: int, contrato: ContratoArrendamientoModel) -> int:
    cursor.execute(
        """
        INSERT INTO contratos_arrendamiento
        (campo_id, empresa_id, campania_id, modalidad, grano, kilos_por_ha, porcentaje_base,
         monto_fijo, vigencia_desde, vigencia_hasta, observaciones)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            campo_id, empresa_id, contrato.campania_id,
            contrato.modalidad, contrato.grano, contrato.kilos_por_ha, contrato.porcentaje_base,
            contrato.monto_fijo, contrato.vigencia_desde or "", contrato.vigencia_hasta or "",
            contrato.observaciones or "",
        ),
    )
    cid = cursor.lastrowid
    if contrato.modalidad == "aparceria" and contrato.escalas:
        for i, e in enumerate(contrato.escalas):
            cursor.execute(
                """
                INSERT INTO escalas_aparceria (contrato_id, rendimiento_tn_ha, porcentaje, orden)
                VALUES (?, ?, ?, ?);
                """,
                (cid, e.rendimiento_tn_ha, e.porcentaje, e.orden if e.orden else i),
            )
    return cid


def register_agro_routes(app, get_db, get_empresa_activa_id):
    """Registra endpoints /api/agro/* en la app FastAPI."""
    from agro_campania import init_agro_schema

    # Asegura schema aunque la DB exista de antes
    try:
        _c = get_db()
        init_agro_schema(_c.cursor())
        _c.commit()
        _c.close()
    except Exception as e:
        print(f"AVISO init agro schema: {e}")

    @app.get("/api/agro/campania_sugerida")
    def api_agro_campania_sugerida(fecha: Optional[str] = None):
        """Sugiere código AA-BB según fecha (junio–mayo). Ej: sep-2026 → 26-27."""
        codigo = codigo_campania_actual(fecha)
        return {
            "codigo": codigo,
            "nombre": etiqueta_campania(codigo),
            "formato": "AA-BB (últimos 2 dígitos de cada año del período)",
            "ejemplo": "Campaña junio 2026 – mayo 2027 → 26-27",
        }

    @app.get("/api/agro/rotacion")
    def api_agro_rotacion(antecesor: Optional[str] = None):
        return {
            "ciclo": ROTACION_IDEAL,
            "antecesor": normalizar_cultivo(antecesor) if antecesor else "",
            "sugerido": sugerir_cultivo_rotacion(antecesor) if antecesor else "",
            "nota": "Orientación: Trigo → Soja 2ª → Maíz → Soja 1ª. Modificable por el ingeniero.",
        }

    @app.get("/api/agro/cultivos")
    def api_agro_cultivos():
        return [{"nombre": n, "orden": i + 1} for i, n in enumerate(ROTACION_IDEAL)]

    @app.get("/api/agro/campanias")
    def api_agro_campanias():
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM campanias_agro WHERE empresa_id = ? ORDER BY codigo COLLATE NOCASE ASC, id DESC;",
            (empresa_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.post("/api/agro/campanias")
    def api_agro_campania_crear(data: CampaniaAgroModel):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        codigo = normalizar_codigo_campania(data.codigo)
        if not re.match(r"^\d{2}-\d{2}$", codigo):
            conn.close()
            raise HTTPException(
                status_code=400,
                detail="Código de campaña inválido. Usá el formato AA-AA (ej. 26-27).",
            )
        nombre = (data.nombre or "").strip() or etiqueta_campania(codigo)

        cur.execute(
            "SELECT id, nombre FROM campanias_agro WHERE empresa_id = ? AND codigo = ?;",
            (empresa_id, codigo),
        )
        existente = cur.fetchone()
        if existente:
            cid = int(existente["id"])
            if data.activa:
                cur.execute("UPDATE campanias_agro SET activa = 0 WHERE empresa_id = ?;", (empresa_id,))
            cur.execute(
                """
                UPDATE campanias_agro SET
                    nombre = COALESCE(NULLIF(?, ''), nombre),
                    activa = CASE WHEN ? THEN 1 ELSE activa END,
                    fecha_inicio = COALESCE(NULLIF(?, ''), fecha_inicio),
                    fecha_fin = COALESCE(NULLIF(?, ''), fecha_fin),
                    notas = COALESCE(NULLIF(?, ''), notas)
                WHERE id = ?;
                """,
                (
                    nombre,
                    1 if data.activa else 0,
                    data.fecha_inicio or "",
                    data.fecha_fin or "",
                    data.notas or "",
                    cid,
                ),
            )
            conn.commit()
            conn.close()
            return {
                "status": "success",
                "id": cid,
                "codigo": codigo,
                "nombre": nombre or existente["nombre"],
                "existente": True,
                "message": f"La campaña {codigo} ya existía; se activó / actualizó.",
            }

        if data.activa:
            cur.execute("UPDATE campanias_agro SET activa = 0 WHERE empresa_id = ?;", (empresa_id,))
        try:
            cur.execute(
                """
                INSERT INTO campanias_agro (empresa_id, codigo, nombre, activa, fecha_inicio, fecha_fin, notas)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    empresa_id, codigo, nombre,
                    1 if data.activa else 0, data.fecha_inicio or "", data.fecha_fin or "", data.notas or "",
                ),
            )
            cid = cur.lastrowid
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            msg = str(e)
            if "UNIQUE" in msg.upper():
                msg = f"Ya existe una campaña con código {codigo}."
            raise HTTPException(status_code=400, detail=msg)
        conn.close()
        return {
            "status": "success",
            "id": cid,
            "codigo": codigo,
            "nombre": nombre,
            "existente": False,
            "message": f"Campaña {codigo} creada.",
        }

    @app.post("/api/agro/campanias/{campania_id}/activar")
    def api_agro_campania_activar(campania_id: int):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE campanias_agro SET activa = 0 WHERE empresa_id = ?;", (empresa_id,))
        cur.execute(
            "UPDATE campanias_agro SET activa = 1 WHERE id = ? AND empresa_id = ?;",
            (campania_id, empresa_id),
        )
        conn.commit()
        conn.close()
        return {"status": "success"}

    @app.get("/api/agro/campos")
    def api_agro_campos(tipo: Optional[str] = None):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        rows = listar_campos(cur, empresa_id, tipo=tipo)
        conn.close()
        return rows

    @app.get("/api/agro/campos/{campo_id}")
    def api_agro_campo_detalle(campo_id: int, campania_id: Optional[int] = None):
        conn = get_db()
        cur = conn.cursor()
        data = campo_con_lotes(cur, campo_id, campania_id=campania_id)
        conn.close()
        if not data:
            raise HTTPException(status_code=404, detail="Campo no encontrado")
        return data

    @app.post("/api/agro/campos")
    def api_agro_campo_crear(data: CampoAgroModel):
        empresa_id = get_empresa_activa_id()
        tipo = (data.tipo or "propio").strip().lower()
        if tipo not in ("propio", "arrendado"):
            raise HTTPException(status_code=400, detail="tipo debe ser propio o arrendado")
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO campos_agro (
                    empresa_id, tipo, nombre, superficie_total,
                    arrendador_cuit, arrendador_razon, arrendador_domicilio,
                    arrendador_telefono, arrendador_email, arrendador_localidad,
                    lat, lng, direccion_ref, notas, fecha_alta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    empresa_id, tipo, data.nombre.strip(), data.superficie_total,
                    data.arrendador_cuit or "", data.arrendador_razon or "", data.arrendador_domicilio or "",
                    data.arrendador_telefono or "", data.arrendador_email or "", data.arrendador_localidad or "",
                    data.lat, data.lng, data.direccion_ref or "", data.notas or "",
                    datetime.now().strftime("%Y-%m-%d"),
                ),
            )
            campo_id = cur.lastrowid
            if data.lotes:
                for i, lote in enumerate(data.lotes):
                    cur.execute(
                        """
                        INSERT INTO lotes_agro (campo_id, codigo, nombre, superficie_base, lat, lng, geojson, orden)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            campo_id, lote.codigo or "", lote.nombre, lote.superficie_base,
                            lote.lat, lote.lng, lote.geojson or "", lote.orden if lote.orden else i,
                        ),
                    )
            if tipo == "arrendado" and data.sync_proveedor:
                sincronizar_arrendador_proveedor(cur, data.dict(), empresa_id)
            if tipo == "arrendado" and data.contrato:
                _guardar_contrato_campo(cur, campo_id, empresa_id, data.contrato)
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail=str(e))
        conn.close()
        return {"status": "success", "id": campo_id}

    @app.put("/api/agro/campos/{campo_id}")
    def api_agro_campo_actualizar(campo_id: int, data: CampoAgroModel):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM campos_agro WHERE id=? AND empresa_id=?;", (campo_id, empresa_id))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Campo no encontrado")
        tipo = (data.tipo or "propio").strip().lower()
        try:
            cur.execute(
                """
                UPDATE campos_agro SET
                    tipo=?, nombre=?, superficie_total=?,
                    arrendador_cuit=?, arrendador_razon=?, arrendador_domicilio=?,
                    arrendador_telefono=?, arrendador_email=?, arrendador_localidad=?,
                    lat=?, lng=?, direccion_ref=?, notas=?
                WHERE id=?;
                """,
                (
                    tipo, data.nombre.strip(), data.superficie_total,
                    data.arrendador_cuit or "", data.arrendador_razon or "", data.arrendador_domicilio or "",
                    data.arrendador_telefono or "", data.arrendador_email or "", data.arrendador_localidad or "",
                    data.lat, data.lng, data.direccion_ref or "", data.notas or "",
                    campo_id,
                ),
            )
            if tipo == "arrendado" and data.sync_proveedor:
                sincronizar_arrendador_proveedor(cur, data.dict(), empresa_id)
            if tipo == "arrendado" and data.contrato:
                _guardar_contrato_campo(cur, campo_id, empresa_id, data.contrato)
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail=str(e))
        conn.close()
        return {"status": "success"}

    @app.delete("/api/agro/campos/{campo_id}")
    def api_agro_campo_baja(campo_id: int):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE campos_agro SET baja=1 WHERE id=? AND empresa_id=?;", (campo_id, empresa_id))
        conn.commit()
        conn.close()
        return {"status": "success"}

    @app.post("/api/agro/campos/{campo_id}/lotes")
    def api_agro_lote_crear(campo_id: int, data: LoteAgroModel):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM campos_agro WHERE id=? AND empresa_id=? AND COALESCE(baja,0)=0;",
            (campo_id, empresa_id),
        )
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Campo no encontrado")
        cur.execute(
            """
            INSERT INTO lotes_agro (campo_id, codigo, nombre, superficie_base, lat, lng, geojson, orden)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                campo_id, data.codigo or "", data.nombre, data.superficie_base,
                data.lat, data.lng, data.geojson or "", data.orden,
            ),
        )
        lid = cur.lastrowid
        cur.execute(
            "SELECT COALESCE(SUM(superficie_base),0) AS t FROM lotes_agro WHERE campo_id=? AND COALESCE(baja,0)=0;",
            (campo_id,),
        )
        total = float(cur.fetchone()["t"] or 0)
        cur.execute("UPDATE campos_agro SET superficie_total=? WHERE id=?;", (total, campo_id))
        conn.commit()
        conn.close()
        return {"status": "success", "id": lid, "superficie_total_campo": total}

    @app.put("/api/agro/lotes/{lote_id}")
    def api_agro_lote_actualizar(lote_id: int, data: LoteAgroModel):
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT campo_id FROM lotes_agro WHERE id=?;", (lote_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Lote no encontrado")
        campo_id = row["campo_id"]
        cur.execute(
            """
            UPDATE lotes_agro SET codigo=?, nombre=?, superficie_base=?, lat=?, lng=?, geojson=?, orden=?
            WHERE id=?;
            """,
            (
                data.codigo or "", data.nombre, data.superficie_base,
                data.lat, data.lng, data.geojson or "", data.orden, lote_id,
            ),
        )
        cur.execute(
            "SELECT COALESCE(SUM(superficie_base),0) AS t FROM lotes_agro WHERE campo_id=? AND COALESCE(baja,0)=0;",
            (campo_id,),
        )
        total = float(cur.fetchone()["t"] or 0)
        cur.execute("UPDATE campos_agro SET superficie_total=? WHERE id=?;", (total, campo_id))
        conn.commit()
        conn.close()
        return {"status": "success", "superficie_total_campo": total}

    @app.put("/api/agro/lotes/{lote_id}/superficie_campania")
    def api_agro_lote_sup_campania(lote_id: int, campania_id: int, superficie: float):
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO lote_superficie_campania (lote_id, campania_id, superficie)
            VALUES (?, ?, ?)
            ON CONFLICT(lote_id, campania_id) DO UPDATE SET superficie=excluded.superficie;
            """,
            (lote_id, campania_id, superficie),
        )
        conn.commit()
        conn.close()
        return {"status": "success"}

    @app.post("/api/agro/campos/{campo_id}/contrato")
    def api_agro_contrato(campo_id: int, data: ContratoArrendamientoModel):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM campos_agro WHERE id=? AND empresa_id=?;", (campo_id, empresa_id))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Campo no encontrado")
        cid = _guardar_contrato_campo(cur, campo_id, empresa_id, data)
        conn.commit()
        conn.close()
        return {"status": "success", "contrato_id": cid}

    @app.get("/api/agro/aparceria/calcular")
    def api_agro_aparceria_calc(contrato_id: int, rendimiento_tn_ha: float):
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM contratos_arrendamiento WHERE id=?;", (contrato_id,))
        c = cur.fetchone()
        if not c:
            conn.close()
            raise HTTPException(status_code=404, detail="Contrato no encontrado")
        cur.execute(
            "SELECT * FROM escalas_aparceria WHERE contrato_id=? ORDER BY rendimiento_tn_ha ASC;",
            (contrato_id,),
        )
        escalas = [dict(r) for r in cur.fetchall()]
        conn.close()
        pct = porcentaje_aparceria_por_rendimiento(escalas, rendimiento_tn_ha, float(c["porcentaje_base"] or 0))
        return {
            "contrato_id": contrato_id,
            "rendimiento_tn_ha": rendimiento_tn_ha,
            "porcentaje_aplicado": pct,
            "escalas": escalas,
        }

    @app.post("/api/agro/campanias/{campania_id}/generar_plan")
    def api_agro_generar_plan(campania_id: int, usar_antecesor_previo: bool = True):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM campanias_agro WHERE id=? AND empresa_id=?;", (campania_id, empresa_id))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Campaña no encontrada")
        result = generar_plan_campania(cur, campania_id, empresa_id, usar_antecesor_previo=usar_antecesor_previo)
        conn.commit()
        conn.close()
        return result

    @app.get("/api/agro/campanias/{campania_id}/plan")
    def api_agro_listar_plan(campania_id: int, completar_antecesor: bool = True):
        """
        Lista el plan. Si faltan antecesores, los completa desde campaña previa /
        cultivo_actual / márgenes (sin pisar ediciones manuales).
        """
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        if completar_antecesor:
            cur.execute(
                """
                SELECT COUNT(*) AS n,
                       SUM(CASE WHEN COALESCE(cultivo_antecesor,'')='' THEN 1 ELSE 0 END) AS vacios
                FROM planificacion_lote WHERE campania_id=?;
                """,
                (campania_id,),
            )
            st = cur.fetchone()
            n = int(st["n"] or 0) if st else 0
            vacios = int(st["vacios"] or 0) if st else 0
            # sin filas o con antecesores vacíos → generar / completar
            if n == 0 or vacios > 0:
                generar_plan_campania(cur, campania_id, empresa_id, usar_antecesor_previo=True)
                conn.commit()
        rows = listar_planificacion(cur, campania_id)
        conn.close()
        return rows

    @app.put("/api/agro/campanias/{campania_id}/plan")
    def api_agro_guardar_plan(campania_id: int, data: PlanLoteLoteModel):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM campanias_agro WHERE id=? AND empresa_id=?;", (campania_id, empresa_id))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Campaña no encontrada")
        for it in data.items:
            sugerido = sugerir_cultivo_rotacion(it.cultivo_antecesor) if it.cultivo_antecesor else ""
            planif = it.cultivo_planificado or sugerido
            cur.execute(
                """
                INSERT INTO planificacion_lote
                (campania_id, lote_id, cultivo_antecesor, cultivo_sugerido, cultivo_planificado, superficie, modificado_manual, notas)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(campania_id, lote_id) DO UPDATE SET
                    cultivo_antecesor=excluded.cultivo_antecesor,
                    cultivo_sugerido=excluded.cultivo_sugerido,
                    cultivo_planificado=excluded.cultivo_planificado,
                    superficie=COALESCE(excluded.superficie, planificacion_lote.superficie),
                    modificado_manual=excluded.modificado_manual,
                    notas=excluded.notas;
                """,
                (
                    campania_id, it.lote_id,
                    normalizar_cultivo(it.cultivo_antecesor) if it.cultivo_antecesor else "",
                    sugerido, normalizar_cultivo(planif) if planif else "",
                    it.superficie, it.modificado_manual, it.notas or "",
                ),
            )
        conn.commit()
        conn.close()
        return {"status": "success", "guardados": len(data.items)}

    # ---- Almacén (productos neto + laboreos terceros) + OT ----
    from agro_almacen import (
        init_almacen_schema,
        listar_items_almacen,
        listar_categorias_almacen,
        crear_categoria_almacen,
        actualizar_item_categoria,
        actualizar_item_clasificacion,
        ingresar_almacen,
        emitir_ot_consumiendo_almacen,
        siguiente_nro_ot,
        costos_por_lote_campania,
    )

    try:
        _ca = get_db()
        init_almacen_schema(_ca.cursor())
        _ca.commit()
        _ca.close()
    except Exception as e:
        print(f"AVISO init almacen schema: {e}")

    class AlmacenIngresoModel(BaseModel):
        item_id: Optional[int] = None
        tipo: str = "producto"  # producto | laboreo
        codigo: Optional[str] = ""
        nombre: Optional[str] = ""
        categoria: Optional[str] = ""
        categoria_codigo: Optional[str] = ""
        unidad: Optional[str] = "Kg"
        fecha: Optional[str] = ""
        cantidad: float
        precio_unitario_neto: float  # SIN impuestos
        proveedor_cuit: Optional[str] = ""
        proveedor_nombre: Optional[str] = ""
        nro_comprobante: Optional[str] = ""
        observaciones: Optional[str] = ""

    class AlmacenCategoriaModel(BaseModel):
        codigo: str
        nombre: str
        aplica_ot: bool = False
        orden: int = 100

    class AlmacenItemCategoriaModel(BaseModel):
        categoria_codigo: Optional[str] = None
        tipo: Optional[str] = None  # producto | laboreo
        unidad: Optional[str] = None

    class OtConsumoModel(BaseModel):
        item_id: int
        dosis_por_ha: float = 0.0
        cantidad: float = 0.0

    class OrdenTrabajoModel(BaseModel):
        fecha: str
        tipo_labor: str
        contratista_nombre: Optional[str] = ""
        contratista_cuit: Optional[str] = ""
        campania_id: Optional[int] = None
        campo_id: Optional[int] = None
        lote_id: Optional[int] = None
        superficie_has: float = 0.0
        cultivo: Optional[str] = ""
        observaciones: Optional[str] = ""
        nro_ot: Optional[str] = ""
        consumos: Optional[List[OtConsumoModel]] = None

    @app.get("/api/agro/almacen/categorias")
    def api_almacen_categorias(solo_aplica_ot: Optional[bool] = None):
        conn = get_db()
        cur = conn.cursor()
        rows = listar_categorias_almacen(cur, solo_aplica_ot=solo_aplica_ot)
        conn.close()
        return rows

    @app.post("/api/agro/almacen/categorias")
    def api_almacen_categorias_crear(data: AlmacenCategoriaModel):
        conn = get_db()
        cur = conn.cursor()
        try:
            row = crear_categoria_almacen(
                cur,
                codigo=data.codigo,
                nombre=data.nombre,
                aplica_ot=data.aplica_ot,
                orden=data.orden,
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail=str(e))
        conn.close()
        return row

    def _patch_item_clasificacion(item_id: int, data: AlmacenItemCategoriaModel):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        try:
            row = actualizar_item_clasificacion(
                cur,
                empresa_id,
                item_id,
                categoria_codigo=data.categoria_codigo,
                tipo=data.tipo,
                unidad=data.unidad,
            )
            conn.commit()
            # respuesta JSON-safe
            return {
                "id": row.get("id"),
                "nombre": row.get("nombre"),
                "tipo": row.get("tipo"),
                "categoria": row.get("categoria"),
                "categoria_codigo": row.get("categoria_codigo"),
                "unidad": row.get("unidad"),
                "clasificacion_manual": int(row.get("clasificacion_manual") or 1),
                "aplica_ot": None,
            }
        except HTTPException:
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=400, detail=f"Clasificación: {e}")
        finally:
            conn.close()

    @app.post("/api/agro/almacen/items/{item_id}/clasificar")
    def api_almacen_item_clasificar(
        item_id: int,
        categoria_codigo: Optional[str] = Body(None),
        tipo: Optional[str] = Body(None),
        unidad: Optional[str] = Body(None),
    ):
        """Edición manual de categoría / tipo / unidad (no se pisa en reimport)."""
        data = AlmacenItemCategoriaModel(
            categoria_codigo=categoria_codigo, tipo=tipo, unidad=unidad
        )
        return _patch_item_clasificacion(item_id, data)

    @app.patch("/api/agro/almacen/items/{item_id}/categoria")
    def api_almacen_item_categoria(
        item_id: int,
        categoria_codigo: Optional[str] = Body(None),
        tipo: Optional[str] = Body(None),
        unidad: Optional[str] = Body(None),
    ):
        data = AlmacenItemCategoriaModel(
            categoria_codigo=categoria_codigo, tipo=tipo, unidad=unidad
        )
        return _patch_item_clasificacion(item_id, data)

    @app.get("/api/agro/almacen/items")
    def api_almacen_items(
        tipo: Optional[str] = None,
        solo_con_stock: bool = False,
        orden: Optional[str] = "nombre",
        categoria_codigo: Optional[str] = None,
        solo_aplica_ot: bool = False,
    ):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        rows = listar_items_almacen(
            cur,
            empresa_id,
            tipo=tipo,
            solo_con_stock=solo_con_stock,
            orden=orden or "nombre",
            categoria_codigo=categoria_codigo,
            solo_aplica_ot=solo_aplica_ot,
        )
        conn.close()
        return rows

    @app.post("/api/agro/almacen/ingreso")
    def api_almacen_ingreso(data: AlmacenIngresoModel):
        """Ingreso a almacén a costo NETO (sin IVA ni otros impuestos)."""
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        try:
            result = ingresar_almacen(
                cur,
                empresa_id=empresa_id,
                item_id=data.item_id,
                tipo=data.tipo,
                codigo=data.codigo or "",
                nombre=data.nombre or "",
                categoria=data.categoria or "",
                categoria_codigo=data.categoria_codigo or "",
                unidad=data.unidad or "Kg",
                fecha=data.fecha or "",
                cantidad=data.cantidad,
                precio_unitario_neto=data.precio_unitario_neto,
                proveedor_cuit=data.proveedor_cuit or "",
                proveedor_nombre=data.proveedor_nombre or "",
                nro_comprobante=data.nro_comprobante or "",
                observaciones=data.observaciones or "",
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail=str(e))
        conn.close()
        return result

    @app.get("/api/agro/almacen/movimientos")
    def api_almacen_movimientos(item_id: Optional[int] = None, limit: int = 100):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        q = """
            SELECT m.*, i.nombre AS item_nombre, i.tipo AS item_tipo, i.unidad
            FROM almacen_movimientos m
            JOIN almacen_items i ON i.id = m.item_id
            WHERE m.empresa_id = ?
        """
        params: List[Any] = [empresa_id]
        if item_id:
            q += " AND m.item_id = ?"
            params.append(item_id)
        q += " ORDER BY m.fecha DESC, m.id DESC LIMIT ?;"
        params.append(limit)
        cur.execute(q, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.get("/api/agro/ot/siguiente_nro")
    def api_ot_siguiente():
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        nro = siguiente_nro_ot(cur, empresa_id)
        conn.close()
        return {"nro_ot": nro}

    @app.get("/api/agro/ot")
    def api_ot_listar(campania_id: Optional[int] = None, limit: int = 50):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        q = "SELECT * FROM ordenes_trabajo WHERE empresa_id=?"
        params: List[Any] = [empresa_id]
        if campania_id:
            q += " AND campania_id=?"
            params.append(campania_id)
        q += " ORDER BY fecha DESC, id DESC LIMIT ?;"
        params.append(limit)
        cur.execute(q, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.post("/api/agro/ot")
    def api_ot_emitir(data: OrdenTrabajoModel):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        try:
            consumos = [c.dict() for c in (data.consumos or [])]
            result = emitir_ot_consumiendo_almacen(
                cur,
                empresa_id=empresa_id,
                fecha=data.fecha,
                tipo_labor=data.tipo_labor,
                contratista_nombre=data.contratista_nombre or "",
                contratista_cuit=data.contratista_cuit or "",
                campania_id=data.campania_id,
                campo_id=data.campo_id,
                lote_id=data.lote_id,
                superficie_has=data.superficie_has,
                cultivo=data.cultivo or "",
                observaciones=data.observaciones or "",
                consumos=consumos,
                nro_ot=data.nro_ot or None,
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail=str(e))
        conn.close()
        return result

    @app.get("/api/agro/campanias/{campania_id}/costos")
    def api_campania_costos(campania_id: int):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        rows = costos_por_lote_campania(cur, campania_id, empresa_id)
        conn.close()
        return rows

    @app.post("/api/agro/importar_tablas")
    def api_importar_tablas_agro(forzar: int = 0):
        """Importa tablas/tablas.xlsx (Access) a SQLite."""
        from importar_tablas_agro import importar_tablas_agro

        return importar_tablas_agro(get_empresa_activa_id(), forzar=bool(forzar))

    @app.post("/api/agro/almacen/recalcular_stock")
    def api_recalcular_stock_almacen():
        """
        Relee 'Stock Almacenes' del Excel y recalcula saldos (ENTRADA − SALIDA).
        La columna STOCK del export Access suele venir en 0.
        """
        from importar_tablas_agro import (
            EXCEL_TABLAS,
            get_db as get_agro_db,
            init_import_schema,
            importar_productos,
            importar_stock,
        )
        import os
        import pandas as pd

        if not os.path.exists(EXCEL_TABLAS):
            raise HTTPException(404, f"No se encontró {EXCEL_TABLAS}")
        empresa_id = get_empresa_activa_id()
        conn = get_agro_db()
        cur = conn.cursor()
        init_import_schema(cur)
        xl = pd.ExcelFile(EXCEL_TABLAS)
        n_prod = importar_productos(cur, xl, empresa_id)
        stock = importar_stock(cur, xl, empresa_id)
        conn.commit()
        conn.close()
        return {"status": "ok", "productos": n_prod, "stock": stock}

    @app.get("/api/agro/catalogos/granos")
    def api_catalogo_granos():
        conn = get_db()
        cur = conn.cursor()
        eid = get_empresa_activa_id()
        cur.execute(
            "SELECT id, nombre FROM catalogo_granos WHERE empresa_id=? ORDER BY nombre COLLATE NOCASE;",
            (eid,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.get("/api/agro/catalogos/laboreos")
    def api_catalogo_laboreos(grupo: Optional[str] = None):
        conn = get_db()
        cur = conn.cursor()
        if grupo:
            cur.execute(
                "SELECT * FROM catalogo_laboreos WHERE grupo=? ORDER BY nombre COLLATE NOCASE;",
                (grupo,),
            )
        else:
            cur.execute("SELECT * FROM catalogo_laboreos ORDER BY grupo, nombre COLLATE NOCASE;")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.get("/api/agro/alquileres_cc")
    def api_alquileres_cc(
        campania: Optional[str] = None,
        locador: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 500,
    ):
        conn = get_db()
        cur = conn.cursor()
        eid = get_empresa_activa_id()
        sql = "SELECT * FROM alquileres_cta_cte WHERE empresa_id=?"
        params: list = [eid]
        if campania:
            sql += " AND campania_codigo=?"
            params.append(normalizar_codigo_campania(campania))
        if locador:
            sql += " AND UPPER(TRIM(locador))=UPPER(TRIM(?))"
            params.append(locador)
        if q:
            sql += " AND (locador LIKE ? OR grano LIKE ? OR varios LIKE ? OR CAST(id_access AS TEXT) LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like, like, like])
        sql += " ORDER BY COALESCE(fecha_pago, fecha_pizarra, '') DESC, id_access DESC LIMIT ?"
        params.append(limit)
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.get("/api/agro/contratos_alquileres")
    def api_contratos_alquileres(
        campania: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 300,
    ):
        from importar_tablas_agro import init_import_schema

        conn = get_db()
        cur = conn.cursor()
        init_import_schema(cur)
        eid = get_empresa_activa_id()
        sql = "SELECT * FROM contratos_alquileres WHERE empresa_id=?"
        params: list = [eid]
        if campania:
            sql += " AND campania_codigo=?"
            params.append(normalizar_codigo_campania(campania))
        if q:
            sql += (
                " AND (locador LIKE ? OR propietario LIKE ? OR grano LIKE ? OR modalidad LIKE ?"
                " OR forma_pago LIKE ? OR nombre_campo LIKE ? OR ubicacion LIKE ?"
                " OR CAST(id_access AS TEXT) LIKE ?)"
            )
            like = f"%{q}%"
            params.extend([like, like, like, like, like, like, like, like])
        sql += " ORDER BY campania_codigo DESC, locador COLLATE NOCASE, id_access DESC LIMIT ?"
        params.append(limit)
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.get("/api/agro/contratos_alquileres/{contrato_id}")
    def api_contrato_alquiler_detalle(contrato_id: int):
        from importar_tablas_agro import init_import_schema

        conn = get_db()
        cur = conn.cursor()
        init_import_schema(cur)
        eid = get_empresa_activa_id()
        cur.execute(
            "SELECT * FROM contratos_alquileres WHERE id=? AND empresa_id=?;",
            (contrato_id, eid),
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            raise HTTPException(404, "Contrato no encontrado")
        c = dict(row)
        cur.execute(
            """
            SELECT * FROM alquileres_cta_cte
            WHERE empresa_id=? AND campania_codigo=? AND UPPER(TRIM(locador))=UPPER(TRIM(?))
            ORDER BY COALESCE(fecha_pago, fecha_pizarra, '') ASC, id_access ASC;
            """,
            (eid, c.get("campania_codigo") or "", c.get("locador") or ""),
        )
        cc = [dict(r) for r in cur.fetchall()]
        saldo_tn = sum(float(x.get("haber") or 0) - float(x.get("debe") or 0) for x in cc)
        cur.execute(
            """
            SELECT * FROM contratos_alquileres_cuotas
            WHERE contrato_id=? AND empresa_id=?
            ORDER BY nro_cuota ASC, id ASC;
            """,
            (contrato_id, eid),
        )
        cuotas = [dict(r) for r in cur.fetchall()]
        conn.close()
        c["movimientos_cc"] = cc
        c["cuotas"] = cuotas
        c["saldo_tn"] = round(saldo_tn, 4)
        c["superficie_para_margenes"] = float(c.get("hectareas") or 0)
        return c

    def _sync_campo_desde_contrato(cur, empresa_id: int, data: dict) -> Optional[int]:
        """Crea/actualiza campos_agro para que el contrato alimente la planificación."""
        from agro_campania import init_agro_schema

        init_agro_schema(cur)
        nombre = (data.get("nombre_campo") or data.get("locador") or "").strip()
        if not nombre:
            return data.get("campo_id")
        propietario = (data.get("propietario") or data.get("locador") or "").strip()
        ubicacion = (data.get("ubicacion") or "").strip()
        localidad = (data.get("localidad") or "").strip() or ubicacion
        partido = (data.get("partido") or "").strip()
        has = float(data.get("hectareas") or 0)
        campo_id = data.get("campo_id")
        if campo_id:
            cur.execute(
                "SELECT id FROM campos_agro WHERE id=? AND empresa_id=? AND COALESCE(baja,0)=0;",
                (campo_id, empresa_id),
            )
            if not cur.fetchone():
                campo_id = None
        if not campo_id:
            cur.execute(
                """
                SELECT id FROM campos_agro
                WHERE empresa_id=? AND COALESCE(baja,0)=0
                  AND UPPER(TRIM(nombre))=UPPER(TRIM(?))
                LIMIT 1;
                """,
                (empresa_id, nombre),
            )
            row = cur.fetchone()
            if row:
                campo_id = int(row["id"])
        notas = " · ".join(
            x for x in [
                f"Partido: {partido}" if partido else "",
                f"Provincia: {(data.get('provincia') or '').strip()}" if data.get("provincia") else "",
                f"Ubicación: {ubicacion}" if ubicacion and ubicacion != localidad else "",
            ] if x
        )
        if campo_id:
            cur.execute(
                """
                UPDATE campos_agro SET
                    nombre=?, tipo='arrendado', superficie_total=COALESCE(NULLIF(?,0), superficie_total),
                    arrendador_razon=COALESCE(NULLIF(?,''), arrendador_razon),
                    arrendador_localidad=COALESCE(NULLIF(?,''), arrendador_localidad),
                    direccion_ref=COALESCE(NULLIF(?,''), direccion_ref),
                    lat=COALESCE(?, lat), lng=COALESCE(?, lng),
                    notas=COALESCE(NULLIF(?,''), notas)
                WHERE id=?;
                """,
                (
                    nombre, has, propietario, localidad, ubicacion or partido,
                    data.get("lat"), data.get("lng"), notas, campo_id,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO campos_agro (
                    empresa_id, tipo, nombre, superficie_total,
                    arrendador_razon, arrendador_localidad, direccion_ref,
                    lat, lng, notas, fecha_alta
                ) VALUES (?, 'arrendado', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'));
                """,
                (
                    empresa_id, nombre, has, propietario, localidad,
                    ubicacion or partido, data.get("lat"), data.get("lng"), notas,
                ),
            )
            campo_id = cur.lastrowid
        # Lote único por superficie del contrato (planificación)
        if has > 0 and campo_id:
            cur.execute(
                "SELECT id FROM lotes_agro WHERE campo_id=? AND COALESCE(baja,0)=0 ORDER BY id LIMIT 1;",
                (campo_id,),
            )
            lote = cur.fetchone()
            if lote:
                cur.execute(
                    "UPDATE lotes_agro SET superficie_base=?, nombre=COALESCE(NULLIF(nombre,''), ?) WHERE id=?;",
                    (has, f"Lote 1 · {nombre}", lote["id"]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO lotes_agro (campo_id, codigo, nombre, superficie_base, orden)
                    VALUES (?, '1', ?, ?, 1);
                    """,
                    (campo_id, f"Lote 1 · {nombre}", has),
                )
        # Contrato operativo en campos (Márgenes)
        modalidad_map = {
            "alquiler kgs": "kilos_fijos",
            "kilos fijos": "kilos_fijos",
            "kilos fijos por hectárea": "kilos_fijos",
            "aparceria": "aparceria",
            "aparcería": "aparceria",
            "alquiler moneda": "moneda",
        }
        mod_raw = (data.get("modalidad") or "kilos_fijos").strip().lower()
        modalidad = modalidad_map.get(mod_raw, mod_raw.replace(" ", "_") if mod_raw else "kilos_fijos")
        kg_ha = float(data.get("kilos_por_ha") or 0)
        if kg_ha <= 0 and float(data.get("tn_por_ha") or 0) > 0:
            kg_ha = float(data["tn_por_ha"]) * 1000
        cur.execute(
            "SELECT id FROM contratos_arrendamiento WHERE campo_id=? ORDER BY id DESC LIMIT 1;",
            (campo_id,),
        )
        ct = cur.fetchone()
        if ct:
            cur.execute(
                """
                UPDATE contratos_arrendamiento SET
                    modalidad=?, grano=?, kilos_por_ha=?, porcentaje_base=?,
                    vigencia_desde=?, vigencia_hasta=?, observaciones=?
                WHERE id=?;
                """,
                (
                    modalidad, data.get("grano") or "Soja", kg_ha,
                    float(data.get("porcentaje") or 0),
                    data.get("fecha_contrato") or "", data.get("fecha_finalizacion") or "",
                    data.get("observaciones") or data.get("varios") or "",
                    ct["id"],
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO contratos_arrendamiento (
                    campo_id, empresa_id, modalidad, grano, kilos_por_ha,
                    porcentaje_base, vigencia_desde, vigencia_hasta, observaciones
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    campo_id, empresa_id, modalidad, data.get("grano") or "Soja", kg_ha,
                    float(data.get("porcentaje") or 0),
                    data.get("fecha_contrato") or "", data.get("fecha_finalizacion") or "",
                    data.get("observaciones") or data.get("varios") or "",
                ),
            )
        if propietario:
            try:
                sincronizar_arrendador_proveedor(cur, {
                    "arrendador_cuit": str(data.get("arrendador_cuit") or data.get("cuit_propietario") or "").strip(),
                    "arrendador_razon": propietario,
                    "arrendador_localidad": localidad,
                })
            except Exception:
                pass
        return campo_id

    def _guardar_cuotas_contrato(cur, empresa_id: int, contrato_id: int, cuotas: list, ctx: dict):
        cur.execute(
            "DELETE FROM contratos_alquileres_cuotas WHERE contrato_id=? AND empresa_id=?;",
            (contrato_id, empresa_id),
        )
        campo = (ctx.get("nombre_campo") or "").strip()
        prop = (ctx.get("propietario") or ctx.get("locador") or "").strip()
        grano = (ctx.get("grano") or "").strip()
        for i, q in enumerate(cuotas or [], start=1):
            if not isinstance(q, dict):
                continue
            nro = int(q.get("nro_cuota") or i)
            kilos = float(q.get("kilos") or 0)
            val = float(q.get("valuacion_ars") or 0)
            detalle = (q.get("detalle") or "").strip()
            if not detalle:
                detalle = f"{campo or 'Campo'} · {prop or 'Locador'} · pago en {grano or 'grano'} · cuota {nro}"
            cur.execute(
                """
                INSERT INTO contratos_alquileres_cuotas (
                    empresa_id, contrato_id, nro_cuota, fecha_vencimiento,
                    kilos, porcentaje, valuacion_ars, estado, detalle
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    empresa_id, contrato_id, nro,
                    str(q.get("fecha_vencimiento") or "")[:10],
                    kilos, float(q.get("porcentaje") or 0), val,
                    str(q.get("estado") or "Pendiente"), detalle,
                ),
            )

    @app.post("/api/agro/contratos_alquileres")
    def api_crear_contrato_alquiler(data: dict = Body(...)):
        """Alta de contrato con datos de planificación + cronograma de cuotas."""
        from importar_tablas_agro import init_import_schema
        from agro_campania import asegurar_campania_activa

        conn = get_db()
        cur = conn.cursor()
        init_import_schema(cur)
        eid = get_empresa_activa_id()
        locador = str(data.get("locador") or data.get("propietario") or "").strip()
        propietario = str(data.get("propietario") or locador).strip()
        nombre_campo = str(data.get("nombre_campo") or "").strip()
        if not locador and not nombre_campo:
            conn.close()
            raise HTTPException(400, "Indicá propietario/arrendador y/o nombre de campo.")
        camp = normalizar_codigo_campania(str(data.get("campania_codigo") or ""))
        if camp:
            asegurar_campania_activa(cur, eid, camp)
        has = float(data.get("hectareas") or 0)
        kg_ha = float(data.get("kilos_por_ha") or 0)
        kg_tot = float(data.get("kilos_totales") or 0)
        if kg_tot <= 0 and has > 0 and kg_ha > 0:
            kg_tot = round(has * kg_ha, 2)
        tn_ha = float(data.get("tn_por_ha") or 0) or (kg_ha / 1000.0 if kg_ha else 0)
        tn_tot = float(data.get("tn_totales") or 0) or (kg_tot / 1000.0 if kg_tot else 0)
        payload = {
            **data,
            "locador": locador or propietario,
            "propietario": propietario or locador,
            "nombre_campo": nombre_campo or locador,
            "hectareas": has,
            "kilos_por_ha": kg_ha,
            "kilos_totales": kg_tot,
            "tn_por_ha": tn_ha,
            "tn_totales": tn_tot,
            "grano": str(data.get("grano") or "").strip(),
        }
        campo_id = _sync_campo_desde_contrato(cur, eid, payload)
        cur.execute(
            """
            INSERT INTO contratos_alquileres (
                empresa_id, campania_codigo, fecha_contrato, fecha_finalizacion,
                locador, propietario, nombre_campo, ubicacion, partido, localidad, provincia,
                hectareas, grano, tn_por_ha, tn_totales, kilos_por_ha, kilos_totales,
                forma_pago, modalidad, porcentaje, rubro, cant_cuotas, precio_pizarra,
                varios, observaciones, campo_id, lat, lng
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
            """,
            (
                eid, camp,
                str(data.get("fecha_contrato") or "")[:10],
                str(data.get("fecha_finalizacion") or "")[:10],
                payload["locador"], payload["propietario"], payload["nombre_campo"],
                str(data.get("ubicacion") or "").strip(),
                str(data.get("partido") or "").strip(),
                str(data.get("localidad") or "").strip(),
                str(data.get("provincia") or "").strip(),
                has, payload["grano"], tn_ha, tn_tot, kg_ha, kg_tot,
                str(data.get("forma_pago") or "").strip(),
                str(data.get("modalidad") or "").strip(),
                float(data.get("porcentaje") or 0),
                str(data.get("rubro") or "agricultura").strip(),
                int(data.get("cant_cuotas") or len(data.get("cuotas") or []) or 0),
                float(data.get("precio_pizarra") or 0),
                str(data.get("varios") or "").strip(),
                str(data.get("observaciones") or data.get("varios") or "").strip(),
                campo_id,
                data.get("lat"), data.get("lng"),
            ),
        )
        cid = cur.lastrowid
        _guardar_cuotas_contrato(cur, eid, cid, data.get("cuotas") or [], payload)
        # Haber en CC por tn totales (obligación pactada) si hay kilos
        if tn_tot > 0.0001 and camp:
            cur.execute(
                """
                INSERT INTO alquileres_cta_cte (
                    empresa_id, campania_codigo, locador, fecha_pago, grano,
                    haber, varios, importe_total, precio_pizarra
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    eid, camp, payload["locador"],
                    str(data.get("fecha_contrato") or "")[:10],
                    payload["grano"], tn_tot,
                    f"Contrato #{cid} · {payload['nombre_campo']} · {int(data.get('cant_cuotas') or 0)} cuotas",
                    float(data.get("precio_pizarra") or 0) * (kg_tot / 1000.0 if float(data.get("precio_pizarra") or 0) > 10000 else kg_tot),
                    float(data.get("precio_pizarra") or 0),
                ),
            )
        conn.commit()
        conn.close()
        return {"status": "ok", "id": cid, "campo_id": campo_id, "message": "Contrato guardado"}

    @app.put("/api/agro/contratos_alquileres/{contrato_id}")
    def api_actualizar_contrato_alquiler(contrato_id: int, data: dict = Body(...)):
        from importar_tablas_agro import init_import_schema

        conn = get_db()
        cur = conn.cursor()
        init_import_schema(cur)
        eid = get_empresa_activa_id()
        cur.execute(
            "SELECT * FROM contratos_alquileres WHERE id=? AND empresa_id=?;",
            (contrato_id, eid),
        )
        prev = cur.fetchone()
        if not prev:
            conn.close()
            raise HTTPException(404, "Contrato no encontrado")
        prev_d = dict(prev)
        has = float(data.get("hectareas") if data.get("hectareas") is not None else prev_d.get("hectareas") or 0)
        kg_ha = float(data.get("kilos_por_ha") if data.get("kilos_por_ha") is not None else prev_d.get("kilos_por_ha") or 0)
        kg_tot = float(data.get("kilos_totales") if data.get("kilos_totales") is not None else prev_d.get("kilos_totales") or 0)
        if kg_tot <= 0 and has > 0 and kg_ha > 0:
            kg_tot = round(has * kg_ha, 2)
        tn_ha = float(data.get("tn_por_ha") if data.get("tn_por_ha") is not None else prev_d.get("tn_por_ha") or 0)
        if tn_ha <= 0 and kg_ha > 0:
            tn_ha = kg_ha / 1000.0
        tn_tot = float(data.get("tn_totales") if data.get("tn_totales") is not None else prev_d.get("tn_totales") or 0)
        if tn_tot <= 0 and kg_tot > 0:
            tn_tot = kg_tot / 1000.0
        if tn_tot <= 0 and has > 0 and tn_ha > 0:
            tn_tot = round(has * tn_ha, 4)
        campos = {
            "locador": str(data.get("locador") or prev_d.get("locador") or "").strip(),
            "propietario": str(data.get("propietario") or data.get("locador") or prev_d.get("propietario") or "").strip(),
            "nombre_campo": str(data.get("nombre_campo") or prev_d.get("nombre_campo") or "").strip(),
            "ubicacion": str(data.get("ubicacion") if "ubicacion" in data else prev_d.get("ubicacion") or "").strip(),
            "partido": str(data.get("partido") if "partido" in data else prev_d.get("partido") or "").strip(),
            "localidad": str(data.get("localidad") if "localidad" in data else prev_d.get("localidad") or "").strip(),
            "provincia": str(data.get("provincia") if "provincia" in data else prev_d.get("provincia") or "").strip(),
            "campania_codigo": normalizar_codigo_campania(
                str(data.get("campania_codigo") or prev_d.get("campania_codigo") or "")
            ),
            "fecha_contrato": str(data.get("fecha_contrato") if "fecha_contrato" in data else prev_d.get("fecha_contrato") or "").strip(),
            "fecha_finalizacion": str(data.get("fecha_finalizacion") if "fecha_finalizacion" in data else prev_d.get("fecha_finalizacion") or "").strip(),
            "hectareas": has,
            "grano": str(data.get("grano") if "grano" in data else prev_d.get("grano") or "").strip(),
            "tn_por_ha": tn_ha,
            "tn_totales": tn_tot,
            "kilos_por_ha": kg_ha,
            "kilos_totales": kg_tot,
            "forma_pago": str(data.get("forma_pago") if "forma_pago" in data else prev_d.get("forma_pago") or "").strip(),
            "modalidad": str(data.get("modalidad") if "modalidad" in data else prev_d.get("modalidad") or "").strip(),
            "porcentaje": float(data.get("porcentaje") if data.get("porcentaje") is not None else prev_d.get("porcentaje") or 0),
            "rubro": str(data.get("rubro") if "rubro" in data else prev_d.get("rubro") or "agricultura").strip(),
            "cant_cuotas": int(data.get("cant_cuotas") if data.get("cant_cuotas") is not None else prev_d.get("cant_cuotas") or 0),
            "precio_pizarra": float(data.get("precio_pizarra") if data.get("precio_pizarra") is not None else prev_d.get("precio_pizarra") or 0),
            "varios": str(data.get("varios") if "varios" in data else prev_d.get("varios") or "").strip(),
            "observaciones": str(data.get("observaciones") if "observaciones" in data else prev_d.get("observaciones") or "").strip(),
            "campo_id": prev_d.get("campo_id"),
            "lat": data.get("lat") if "lat" in data else prev_d.get("lat"),
            "lng": data.get("lng") if "lng" in data else prev_d.get("lng"),
        }
        campo_id = _sync_campo_desde_contrato(cur, eid, campos)
        cur.execute(
            """
            UPDATE contratos_alquileres SET
                locador=?, propietario=?, nombre_campo=?, ubicacion=?, partido=?, localidad=?, provincia=?,
                campania_codigo=?, fecha_contrato=?, fecha_finalizacion=?,
                hectareas=?, grano=?, tn_por_ha=?, tn_totales=?, kilos_por_ha=?, kilos_totales=?,
                forma_pago=?, modalidad=?, porcentaje=?, rubro=?, cant_cuotas=?, precio_pizarra=?,
                varios=?, observaciones=?, campo_id=?, lat=?, lng=?
            WHERE id=?;
            """,
            (
                campos["locador"], campos["propietario"], campos["nombre_campo"],
                campos["ubicacion"], campos["partido"], campos["localidad"], campos["provincia"],
                campos["campania_codigo"], campos["fecha_contrato"], campos["fecha_finalizacion"],
                campos["hectareas"], campos["grano"], campos["tn_por_ha"], campos["tn_totales"],
                campos["kilos_por_ha"], campos["kilos_totales"],
                campos["forma_pago"], campos["modalidad"], campos["porcentaje"], campos["rubro"],
                campos["cant_cuotas"], campos["precio_pizarra"],
                campos["varios"], campos["observaciones"], campo_id,
                campos["lat"], campos["lng"], contrato_id,
            ),
        )
        if "cuotas" in data and isinstance(data.get("cuotas"), list):
            _guardar_cuotas_contrato(cur, eid, contrato_id, data["cuotas"], campos)
            cur.execute(
                "UPDATE contratos_alquileres SET cant_cuotas=? WHERE id=?;",
                (len(data["cuotas"]), contrato_id),
            )
        conn.commit()
        conn.close()
        return {"status": "ok", "id": contrato_id, "campo_id": campo_id, "message": "Contrato actualizado"}

    @app.post("/api/agro/importar_alquileres")
    def api_importar_alquileres_solo():
        """Reimporta Contratos Alquileres + Alquileres Cta Cte desde tablas.xlsx."""
        from importar_tablas_agro import (
            EXCEL_TABLAS,
            get_db as get_agro_db,
            init_import_schema,
            importar_alquileres,
            importar_contratos_alquileres,
            importar_catalogos,
        )
        import os
        import pandas as pd

        if not os.path.exists(EXCEL_TABLAS):
            raise HTTPException(404, f"No se encontró {EXCEL_TABLAS}")
        empresa_id = get_empresa_activa_id()
        conn = get_agro_db()
        cur = conn.cursor()
        init_import_schema(cur)
        xl = pd.ExcelFile(EXCEL_TABLAS)
        cats = importar_catalogos(cur, xl, empresa_id)
        n_cc = importar_alquileres(cur, xl, empresa_id)
        n_ct = importar_contratos_alquileres(cur, xl, empresa_id)
        conn.commit()
        conn.close()
        return {
            "status": "ok",
            "alquileres_cc": n_cc,
            "contratos_alquileres": n_ct,
            "modalidades": cats.get("modalidades", 0),
        }

    @app.get("/api/agro/margenes_historicos")
    def api_margenes_hist(campania: Optional[str] = None, campo: Optional[str] = None, limit: int = 500):
        conn = get_db()
        cur = conn.cursor()
        eid = get_empresa_activa_id()
        sql = "SELECT * FROM margenes_access WHERE empresa_id=?"
        params: list = [eid]
        if campania:
            sql += " AND campania_codigo=?"
            params.append(normalizar_codigo_campania(campania))
        if campo:
            sql += " AND UPPER(TRIM(campo))=UPPER(TRIM(?))"
            params.append(campo)
        sql += " ORDER BY id_access DESC LIMIT ?"
        params.append(limit)
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.get("/api/agro/arca/ip1")
    def api_arca_ip1():
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM arca_ip1 WHERE empresa_id=? ORDER BY establecimiento, lote;",
            (get_empresa_activa_id(),),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.get("/api/agro/arca/ip2")
    def api_arca_ip2():
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM arca_ip2 WHERE empresa_id=? ORDER BY campo, lote;",
            (get_empresa_activa_id(),),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.get("/api/agro/patrimonial")
    def api_patrimonial():
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM bienes_patrimoniales WHERE empresa_id=? ORDER BY COALESCE(fecha_compra,''), id;",
            (get_empresa_activa_id(),),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
