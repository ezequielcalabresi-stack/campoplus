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
    id: Optional[int] = None
    codigo: Optional[str] = ""
    nombre: str
    superficie_base: float = 0.0
    # Hectáreas del lote al 100% cuando se trabaja con socios (superficie_base = parte de la empresa).
    superficie_total_lote: Optional[float] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    # None = conservar el contorno guardado; "" = borrarlo.
    geojson: Optional[str] = None
    orden: int = 0


def _asegurar_cols_lotes(cur) -> None:
    cur.execute("PRAGMA table_info(lotes_agro);")
    cols = {r[1] for r in cur.fetchall()}
    if cols and "superficie_total_lote" not in cols:
        cur.execute("ALTER TABLE lotes_agro ADD COLUMN superficie_total_lote REAL DEFAULT 0;")


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


def _recalcular_superficie_campo(cur, campo_id: int) -> float:
    """El total del campo es la suma de sus lotes activos (si tiene lotes con hectáreas)."""
    cur.execute(
        "SELECT COALESCE(SUM(superficie_base),0) AS t FROM lotes_agro WHERE campo_id=? AND COALESCE(baja,0)=0;",
        (campo_id,),
    )
    total = round(float(cur.fetchone()[0] or 0), 4)
    if total > 0:
        cur.execute("UPDATE campos_agro SET superficie_total=? WHERE id=?;", (total, campo_id))
    return total


def _campania_activa_lote(cur, lote_id: int):
    cur.execute(
        """
        SELECT a.id, a.codigo, c.empresa_id
        FROM lotes_agro l
        JOIN campos_agro c ON c.id = l.campo_id
        JOIN campanias_agro a ON a.empresa_id = c.empresa_id AND COALESCE(a.activa,0) = 1
        WHERE l.id = ?
        LIMIT 1;
        """,
        (lote_id,),
    )
    return cur.fetchone()


def _anio_campania(codigo: str) -> int:
    m = re.match(r"^(\d{2})-(\d{2})$", normalizar_codigo_campania(codigo or ""))
    return int(m.group(1)) if m else -1


def _registrar_superficie_lote(cur, lote_id: int, anterior: float, nueva: float) -> None:
    """Cambio de hectáreas de un lote: rige desde la campaña activa en adelante.
    Las campañas anteriores quedan congeladas con la superficie que tenían."""
    activa = _campania_activa_lote(cur, lote_id)
    if not activa:
        return
    anio_act = _anio_campania(activa["codigo"])
    cur.execute(
        """
        SELECT a.id, a.codigo,
               (SELECT p.superficie FROM planificacion_lote p WHERE p.campania_id=a.id AND p.lote_id=?) AS sup_plan,
               (SELECT s.superficie FROM lote_superficie_campania s WHERE s.campania_id=a.id AND s.lote_id=?) AS sup_camp
        FROM campanias_agro a
        WHERE a.empresa_id = ?;
        """,
        (lote_id, lote_id, activa["empresa_id"]),
    )
    for a in cur.fetchall():
        anio = _anio_campania(a["codigo"])
        if anio < 0:
            continue
        if a["id"] == activa["id"] or anio_act <= anio <= anio_act + 3:
            cur.execute(
                """
                INSERT INTO lote_superficie_campania (lote_id, campania_id, superficie)
                VALUES (?, ?, ?)
                ON CONFLICT(lote_id, campania_id) DO UPDATE SET superficie=excluded.superficie;
                """,
                (lote_id, a["id"], nueva),
            )
            cur.execute(
                "UPDATE planificacion_lote SET superficie=? WHERE campania_id=? AND lote_id=?;",
                (nueva, a["id"], lote_id),
            )
        elif anio < anio_act and a["sup_camp"] is None and a["sup_plan"] is not None:
            congelada = float(a["sup_plan"] or 0) or float(anterior or 0)
            cur.execute(
                "INSERT INTO lote_superficie_campania (lote_id, campania_id, superficie) VALUES (?, ?, ?);",
                (lote_id, a["id"], congelada),
            )


def _aplicar_superficie_campania(cur, lote_id: int, campania_id: int, superficie: float) -> None:
    """Hectáreas de un lote en una campaña. Si es la campaña activa, pasan a ser las
    hectáreas actuales del lote y se recalcula el total del campo."""
    superficie = float(superficie or 0)
    if superficie <= 0:
        return
    cur.execute(
        """
        INSERT INTO lote_superficie_campania (lote_id, campania_id, superficie)
        VALUES (?, ?, ?)
        ON CONFLICT(lote_id, campania_id) DO UPDATE SET superficie=excluded.superficie;
        """,
        (lote_id, campania_id, superficie),
    )
    activa = _campania_activa_lote(cur, lote_id)
    if not activa or int(activa["id"]) != int(campania_id):
        return
    cur.execute("SELECT campo_id, superficie_base FROM lotes_agro WHERE id=?;", (lote_id,))
    row = cur.fetchone()
    if not row:
        return
    anterior = float(row["superficie_base"] or 0)
    if abs(superficie - anterior) <= 1e-6:
        return
    _registrar_superficie_lote(cur, lote_id, anterior, superficie)
    cur.execute("UPDATE lotes_agro SET superficie_base=? WHERE id=?;", (superficie, lote_id))
    _recalcular_superficie_campo(cur, int(row["campo_id"]))


def _sincronizar_lotes_campo(cur, campo_id: int, lotes: List["LoteAgroModel"]) -> None:
    """Actualiza los lotes existentes, agrega los nuevos y da de baja (no borra) los quitados."""
    cur.execute("SELECT id, superficie_base FROM lotes_agro WHERE campo_id=? AND COALESCE(baja,0)=0;", (campo_id,))
    sup_previa = {int(r[0]): float(r[1] or 0) for r in cur.fetchall()}
    existentes = set(sup_previa)
    enviados = set()
    for i, lote in enumerate(lotes):
        nombre = (lote.nombre or "").strip()
        if not nombre:
            continue
        orden = lote.orden if lote.orden else i
        if lote.id and int(lote.id) in existentes:
            enviados.add(int(lote.id))
            nueva = float(lote.superficie_base or 0)
            if abs(nueva - sup_previa[int(lote.id)]) > 1e-6:
                _registrar_superficie_lote(cur, int(lote.id), sup_previa[int(lote.id)], nueva)
            cur.execute(
                """
                UPDATE lotes_agro SET codigo=?, nombre=?, superficie_base=?, lat=?, lng=?, orden=?,
                       geojson=CASE WHEN ? IS NULL THEN geojson ELSE ? END,
                       superficie_total_lote=COALESCE(?, superficie_total_lote)
                WHERE id=?;
                """,
                (lote.codigo or "", nombre, lote.superficie_base, lote.lat, lote.lng, orden,
                 lote.geojson, lote.geojson, lote.superficie_total_lote, int(lote.id)),
            )
        else:
            cur.execute(
                """
                INSERT INTO lotes_agro (campo_id, codigo, nombre, superficie_base, lat, lng, geojson, orden, superficie_total_lote)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (campo_id, lote.codigo or "", nombre, lote.superficie_base, lote.lat, lote.lng, lote.geojson or "", orden,
                 lote.superficie_total_lote or 0),
            )
    for lote_id in existentes - enviados:
        cur.execute("UPDATE lotes_agro SET baja=1 WHERE id=?;", (lote_id,))


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


# Con `from __future__ import annotations` los modelos del body tienen que estar
# a nivel de módulo; definidos dentro de register_agro_routes FastAPI los toma como query.
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
    aporte_pct: Optional[float] = None
    aporta: Optional[str] = ""


class SocioParticipacionModel(BaseModel):
    socio_id: Optional[int] = None
    socio_nombre: str = ""
    socio_cuit: Optional[str] = ""
    porcentaje: float = 0.0
    aporta_labores: Optional[bool] = None
    aporta_insumos: Optional[bool] = None


class SocioAgroModel(BaseModel):
    nombre: str
    cuit: Optional[str] = ""
    contacto: Optional[str] = ""
    telefono: Optional[str] = ""
    email: Optional[str] = ""
    observaciones: Optional[str] = ""
    activo: bool = True


class ParticipacionCampoModel(BaseModel):
    campania_id: int
    socios: List[SocioParticipacionModel] = []


class OtDestinoModel(BaseModel):
    campo_id: Optional[int] = None
    lote_id: Optional[int] = None
    superficie_has: float = 0.0
    cultivo: Optional[str] = ""


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
    fecha_aplicacion: Optional[str] = ""
    labor_cultural: Optional[str] = ""
    consumos: Optional[List[OtConsumoModel]] = None
    destinos: Optional[List[OtDestinoModel]] = None


class CosteoLineaModel(BaseModel):
    precio: float
    moneda: str = "USD"
    tc: float = 0.0
    origen: str = "manual"
    mov_ingreso_id: Optional[int] = None
    proveedor: Optional[str] = ""
    nro_comprobante: Optional[str] = ""
    fecha_compra: Optional[str] = ""


class AlquilerVentaModel(BaseModel):
    locador: str
    campania_codigo: str
    grano: str
    tns_vendidas: float
    precio_pizarra: float = 0
    fecha_pizarra: Optional[str] = ""
    dias_pago: int = 12
    fecha_pago: Optional[str] = ""
    detalle: Optional[str] = "Venta"
    actividad: Optional[str] = ""
    cuenta: Optional[str] = ""
    forzar: bool = False  # permite vender por encima del saldo
    confirmar_duplicado: bool = False


class AlquilerVentaEditModel(BaseModel):
    tns_vendidas: float
    precio_pizarra: float = 0
    fecha_pizarra: Optional[str] = ""
    fecha_pago: Optional[str] = ""
    detalle: Optional[str] = ""


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
                _asegurar_cols_lotes(cur)
                for i, lote in enumerate(data.lotes):
                    cur.execute(
                        """
                        INSERT INTO lotes_agro (campo_id, codigo, nombre, superficie_base, lat, lng, geojson, orden, superficie_total_lote)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            campo_id, lote.codigo or "", lote.nombre, lote.superficie_base,
                            lote.lat, lote.lng, lote.geojson or "", lote.orden if lote.orden else i,
                            lote.superficie_total_lote or 0,
                        ),
                    )
                _recalcular_superficie_campo(cur, campo_id)
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
            if data.lotes is not None:
                _asegurar_cols_lotes(cur)
                _sincronizar_lotes_campo(cur, campo_id, data.lotes)
            _recalcular_superficie_campo(cur, campo_id)
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
        _asegurar_cols_lotes(cur)
        cur.execute(
            """
            INSERT INTO lotes_agro (campo_id, codigo, nombre, superficie_base, lat, lng, geojson, orden, superficie_total_lote)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                campo_id, data.codigo or "", data.nombre, data.superficie_base,
                data.lat, data.lng, data.geojson or "", data.orden, data.superficie_total_lote or 0,
            ),
        )
        lid = cur.lastrowid
        total = _recalcular_superficie_campo(cur, campo_id)
        conn.commit()
        conn.close()
        return {"status": "success", "id": lid, "superficie_total_campo": total}

    @app.put("/api/agro/lotes/{lote_id}")
    def api_agro_lote_actualizar(lote_id: int, data: LoteAgroModel):
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT campo_id, superficie_base FROM lotes_agro WHERE id=?;", (lote_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Lote no encontrado")
        campo_id = row["campo_id"]
        anterior = float(row["superficie_base"] or 0)
        if abs(float(data.superficie_base or 0) - anterior) > 1e-6:
            _registrar_superficie_lote(cur, lote_id, anterior, float(data.superficie_base or 0))
        _asegurar_cols_lotes(cur)
        cur.execute(
            """
            UPDATE lotes_agro SET codigo=?, nombre=?, superficie_base=?, lat=?, lng=?,
                   geojson=CASE WHEN ? IS NULL THEN geojson ELSE ? END, orden=?,
                   superficie_total_lote=COALESCE(?, superficie_total_lote)
            WHERE id=?;
            """,
            (
                data.codigo or "", data.nombre, data.superficie_base,
                data.lat, data.lng, data.geojson, data.geojson, data.orden,
                data.superficie_total_lote, lote_id,
            ),
        )
        total = _recalcular_superficie_campo(cur, campo_id)
        conn.commit()
        conn.close()
        return {"status": "success", "superficie_total_campo": total}

    @app.put("/api/agro/lotes/{lote_id}/superficie_campania")
    def api_agro_lote_sup_campania(lote_id: int, campania_id: int, superficie: float):
        conn = get_db()
        cur = conn.cursor()
        _aplicar_superficie_campania(cur, lote_id, campania_id, superficie)
        cur.execute(
            "UPDATE planificacion_lote SET superficie=? WHERE campania_id=? AND lote_id=?;",
            (superficie, campania_id, lote_id),
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
            if it.superficie:
                _aplicar_superficie_campania(cur, it.lote_id, campania_id, it.superficie)
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
        guardar_ficha_item,
        ingresar_almacen,
        emitir_ot_consumiendo_almacen,
        valuar_salida_insumo,
        siguiente_nro_ot,
        costos_por_lote_campania,
        listar_lineas_ot,
        opciones_costeo_linea,
        costear_linea_ot,
        anular_ot,
        editar_ot,
        confirmar_ot,
        detalle_ot,
        asegurar_schema_participaciones,
        participacion_campo,
        guardar_participacion_campo,
        participaciones_campania,
        resumen_participacion_campania,
        excel_participacion_campania,
        listar_socios_agro,
        guardar_socio_agro,
        borrar_socio_agro,
    )

    try:
        _ca = get_db()
        init_almacen_schema(_ca.cursor())
        _ca.commit()
        _ca.close()
    except Exception as e:
        print(f"AVISO init almacen schema: {e}")

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

    @app.put("/api/agro/almacen/items/{item_id}")
    def api_almacen_ficha(item_id: int, data: dict = Body(...)):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        try:
            row = guardar_ficha_item(
                cur, empresa_id, item_id,
                nombre=data.get("nombre") or "",
                presentacion=data.get("presentacion") or "",
                detalle=data.get("detalle") or "",
                tipo=data.get("tipo"),
                categoria_codigo=data.get("categoria_codigo"),
                unidad=data.get("unidad"),
                stock_cantidad=float(data.get("stock_cantidad") or 0),
                costo_usd=float(data.get("costo_usd") or 0),
                costo_ars=float(data.get("costo_ars") or 0),
                tipo_cambio=float(data.get("tipo_cambio") or 0),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            raise HTTPException(400, str(e))
        conn.close()
        return {"status": "ok", "id": row.get("id"), "stock_cantidad": row.get("stock_cantidad")}

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

    @app.get("/api/agro/almacen/valuacion")
    def api_almacen_valuacion(item_id: int, cantidad: float = 1):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        try:
            data = valuar_salida_insumo(cur, empresa_id, item_id, cantidad)
        except ValueError as e:
            conn.close()
            raise HTTPException(400, str(e))
        conn.close()
        return data

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
        q = """
            SELECT o.*, c.nombre AS campo_nombre, l.nombre AS lote_nombre,
                   (SELECT COUNT(*) FROM ot_destinos d WHERE d.ot_id = o.id) AS n_destinos,
                   (SELECT GROUP_CONCAT(COALESCE(c2.nombre,'') || ' | ' || COALESCE(l2.nombre,''), '; ')
                      FROM ot_destinos d2
                      LEFT JOIN campos_agro c2 ON c2.id = d2.campo_id
                      LEFT JOIN lotes_agro l2 ON l2.id = d2.lote_id
                     WHERE d2.ot_id = o.id) AS destinos_txt
            FROM ordenes_trabajo o
            LEFT JOIN campos_agro c ON c.id = o.campo_id
            LEFT JOIN lotes_agro l ON l.id = o.lote_id
            WHERE o.empresa_id=?
        """
        params: List[Any] = [empresa_id]
        if campania_id:
            q += " AND o.campania_id=?"
            params.append(campania_id)
        q += " ORDER BY o.id DESC LIMIT ?;"
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
                fecha_aplicacion=data.fecha_aplicacion or "",
                labor_cultural=data.labor_cultural or "",
                destinos=[d.dict() for d in (data.destinos or [])],
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail=str(e))
        conn.close()
        return result

    @app.get("/api/agro/ot/{ot_id:int}")
    def api_ot_detalle(ot_id: int):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        try:
            data = detalle_ot(cur, empresa_id, ot_id)
        except ValueError as e:
            conn.close()
            raise HTTPException(404, str(e))
        conn.close()
        return data

    @app.put("/api/agro/ot/{ot_id:int}")
    def api_ot_editar(ot_id: int, data: OrdenTrabajoModel):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        try:
            result = editar_ot(
                cur, empresa_id, ot_id,
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
                consumos=[c.dict() for c in (data.consumos or [])],
                nro_ot=data.nro_ot or None,
                fecha_aplicacion=data.fecha_aplicacion or "",
                labor_cultural=data.labor_cultural or "",
                destinos=[d.dict() for d in (data.destinos or [])],
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail=str(e))
        conn.close()
        return result

    @app.post("/api/agro/ot/{ot_id:int}/confirmar")
    def api_ot_confirmar(ot_id: int):
        return _cambiar_confirmacion_ot(ot_id, True)

    @app.post("/api/agro/ot/{ot_id:int}/reabrir")
    def api_ot_reabrir(ot_id: int):
        return _cambiar_confirmacion_ot(ot_id, False)

    def _cambiar_confirmacion_ot(ot_id: int, confirmar: bool):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        try:
            result = confirmar_ot(cur, empresa_id, ot_id, confirmar)
            conn.commit()
        except ValueError as e:
            conn.rollback()
            conn.close()
            raise HTTPException(400, str(e))
        conn.close()
        return result

    @app.get("/api/agro/ot/lineas")
    def api_ot_lineas(
        solo_pendientes: bool = True,
        nro_orden: Optional[int] = None,
        campania: Optional[str] = None,
        ultimas_ot: int = 40,
    ):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        rows = listar_lineas_ot(
            cur, empresa_id,
            solo_pendientes=solo_pendientes,
            nro_orden=nro_orden,
            campania_codigo=normalizar_codigo_campania(campania) if campania else "",
            ultimas_ot=ultimas_ot,
        )
        conn.close()
        return rows

    @app.get("/api/agro/ot/lineas/{linea_id}/opciones")
    def api_ot_linea_opciones(linea_id: int):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        try:
            data = opciones_costeo_linea(cur, empresa_id, linea_id)
        except ValueError as e:
            conn.close()
            raise HTTPException(404, str(e))
        conn.close()
        return data

    @app.put("/api/agro/ot/lineas/{linea_id}/costo")
    def api_ot_linea_costear(linea_id: int, data: CosteoLineaModel):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        try:
            result = costear_linea_ot(
                cur, empresa_id, linea_id,
                precio=data.precio,
                moneda=data.moneda,
                tc=data.tc,
                origen=data.origen,
                mov_ingreso_id=data.mov_ingreso_id,
                proveedor=data.proveedor or "",
                nro_comprobante=data.nro_comprobante or "",
                fecha_compra=data.fecha_compra or "",
            )
            conn.commit()
        except ValueError as e:
            conn.rollback()
            conn.close()
            raise HTTPException(400, str(e))
        conn.close()
        return result

    @app.post("/api/agro/ot/{ot_id}/anular")
    def api_ot_anular(ot_id: int):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        try:
            result = anular_ot(cur, empresa_id, ot_id)
            conn.commit()
        except ValueError as e:
            conn.rollback()
            conn.close()
            raise HTTPException(400, str(e))
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

    @app.get("/api/agro/campos/{campo_id}/participacion")
    def api_campo_participacion(campo_id: int, campania_id: int):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        asegurar_schema_participaciones(cur)
        data = participacion_campo(cur, empresa_id, campo_id, campania_id)
        conn.close()
        return data

    @app.put("/api/agro/campos/{campo_id}/participacion")
    def api_campo_participacion_guardar(campo_id: int, data: ParticipacionCampoModel):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        asegurar_schema_participaciones(cur)
        try:
            result = guardar_participacion_campo(
                cur, empresa_id, campo_id, data.campania_id, [s.dict() for s in data.socios]
            )
            conn.commit()
        except ValueError as e:
            conn.rollback()
            conn.close()
            raise HTTPException(400, str(e))
        conn.close()
        return result

    @app.get("/api/agro/socios")
    def api_socios(incluir_inactivos: bool = False):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        asegurar_schema_participaciones(cur)
        data = listar_socios_agro(cur, empresa_id, incluir_inactivos)
        conn.close()
        return data

    def _guardar_socio(data: SocioAgroModel, socio_id: Optional[int] = None):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        asegurar_schema_participaciones(cur)
        try:
            result = guardar_socio_agro(cur, empresa_id, data.dict(), socio_id)
            conn.commit()
        except ValueError as e:
            conn.rollback()
            conn.close()
            raise HTTPException(400, str(e))
        conn.close()
        return result

    @app.post("/api/agro/socios")
    def api_socio_nuevo(data: SocioAgroModel):
        return _guardar_socio(data)

    @app.put("/api/agro/socios/{socio_id}")
    def api_socio_editar(socio_id: int, data: SocioAgroModel):
        return _guardar_socio(data, socio_id)

    @app.delete("/api/agro/socios/{socio_id}")
    def api_socio_borrar(socio_id: int):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        asegurar_schema_participaciones(cur)
        try:
            res = borrar_socio_agro(cur, empresa_id, socio_id)
            conn.commit()
        except ValueError as e:
            conn.close()
            raise HTTPException(404, str(e))
        conn.close()
        return {"status": res}

    @app.get("/api/agro/campanias/{campania_id}/participaciones")
    def api_campania_participaciones(campania_id: int):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        asegurar_schema_participaciones(cur)
        data = participaciones_campania(cur, empresa_id, campania_id)
        conn.close()
        return {str(k): v for k, v in data.items()}

    @app.get("/api/agro/campanias/{campania_id}/participacion_resumen")
    def api_campania_participacion_resumen(campania_id: int):
        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        data = resumen_participacion_campania(cur, empresa_id, campania_id)
        conn.close()
        return data

    @app.get("/api/agro/campanias/{campania_id}/participacion_resumen.xlsx")
    def api_campania_participacion_excel(campania_id: int):
        from fastapi.responses import Response

        empresa_id = get_empresa_activa_id()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT codigo FROM campanias_agro WHERE id=?;", (campania_id,))
        r = cur.fetchone()
        codigo = (r["codigo"] if r else "") or str(campania_id)
        contenido = excel_participacion_campania(cur, empresa_id, campania_id)
        conn.close()
        return Response(
            content=contenido,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="participacion_{codigo}.xlsx"'},
        )

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

    @app.get("/api/agro/alquileres_cc/locadores")
    def api_alquileres_cc_locadores(
        campania: Optional[str] = None,
        grano: Optional[str] = None,
        q: Optional[str] = None,
        solo_arrendadores: bool = False,
    ):
        """Arrendadores: padrón de proveedores + contratos + cuenta corriente."""
        conn = get_db()
        cur = conn.cursor()
        eid = get_empresa_activa_id()
        texto = (q or "").strip()
        sql = """
            SELECT DISTINCT TRIM(locador) AS locador FROM (
                SELECT COALESCE(NULLIF(TRIM(nombre_fantasia),''), razon_social) AS locador
                FROM entidades
                WHERE COALESCE(es_proveedor, 0) = 1
                  AND TRIM(COALESCE(razon_social,'')||COALESCE(nombre_fantasia,'')) <> ''
                UNION
                SELECT locador FROM alquileres_cta_cte
                WHERE empresa_id=? AND TRIM(COALESCE(locador,''))<>''
                UNION
                SELECT COALESCE(NULLIF(TRIM(propietario),''), locador) FROM contratos_alquileres
                WHERE empresa_id=? AND TRIM(COALESCE(locador,'')||COALESCE(propietario,''))<>''
            ) t
            WHERE locador IS NOT NULL AND TRIM(locador)<>''
        """
        params: list = [eid, eid]
        if texto:
            sql += " AND UPPER(locador) LIKE ?"
            params.append(f"%{texto.upper()}%")
        elif campania or grano:
            camp = normalizar_codigo_campania(campania) if campania else ""
            grano_n = (grano or "").strip().upper()
            sql += """
              AND UPPER(TRIM(locador)) IN (
                SELECT UPPER(TRIM(locador)) FROM alquileres_cta_cte
                WHERE empresa_id=? AND TRIM(COALESCE(locador,''))<>''
            """
            params.append(eid)
            if camp:
                sql += " AND campania_codigo=?"
                params.append(camp)
            if grano_n:
                sql += " AND UPPER(TRIM(grano))=?"
                params.append(grano_n)
            sql += ")"
        if solo_arrendadores:
            sql += """
              AND UPPER(TRIM(locador)) IN (
                SELECT UPPER(TRIM(COALESCE(NULLIF(TRIM(nombre_fantasia),''), razon_social)))
                FROM entidades WHERE COALESCE(es_propietario_inmueble, 0) = 1
                UNION
                SELECT UPPER(TRIM(razon_social)) FROM entidades
                WHERE COALESCE(es_propietario_inmueble, 0) = 1
              )
            """
        sql += " ORDER BY locador COLLATE NOCASE LIMIT 80"
        cur.execute(sql, params)
        rows = [r["locador"] for r in cur.fetchall() if r["locador"]]
        conn.close()
        return rows

    @app.get("/api/agro/arrendadores/tn_a_favor")
    def api_tn_a_favor_arrendadores(solo_con_saldo: bool = False):
        """Toneladas a favor de proveedores marcados como arrendadores (oficial y S/P).
        Deduplica por nombre+oficial/SP (prioriza CUIT fiscal sobre provisionales 99…/00…).
        """
        from agro_campania import alta_locadores_faltantes
        conn = get_db()
        cur = conn.cursor()
        alta_locadores_faltantes(cur)
        conn.commit()
        eid = get_empresa_activa_id()
        cur.execute(
            """
            SELECT
                COALESCE(NULLIF(TRIM(e.nombre_fantasia),''), e.razon_social) AS nombre,
                e.cuit,
                COALESCE(e.centro_costo, '1') AS centro_costo,
                ROUND(SUM(COALESCE(cc.haber, 0) - COALESCE(cc.debe, 0)), 4) AS saldo_tn
            FROM entidades e
            LEFT JOIN alquileres_cta_cte cc
              ON cc.empresa_id = ?
             AND UPPER(TRIM(cc.locador)) = UPPER(TRIM(COALESCE(NULLIF(TRIM(e.nombre_fantasia),''), e.razon_social)))
            WHERE COALESCE(e.es_propietario_inmueble, 0) = 1
            GROUP BY nombre, e.cuit, e.centro_costo
            ORDER BY saldo_tn DESC, nombre COLLATE NOCASE;
            """,
            (eid,),
        )

        def _prio_cuit(cuit: str) -> int:
            d = "".join(ch for ch in str(cuit or "") if ch.isdigit())
            if len(d) == 11 and d[:2] in ("20", "23", "24", "27", "30", "33", "34") and not d.startswith("00"):
                return 0  # fiscal
            if d.startswith("99"):
                return 2  # provisorio
            return 1  # placeholder Access / otros

        # Agrupar por (nombre_norm, es_sp)
        buckets: dict = {}
        for r in cur.fetchall():
            d = dict(r)
            nombre = (d.get("nombre") or "").strip()
            if not nombre:
                continue
            nombre_u = nombre.upper()
            es_sp = str(d.get("centro_costo") or "").upper() in ("SP", "S/P") or "(S/P)" in nombre_u or nombre_u.endswith("S/P")
            key = (nombre_u.replace(".", " ").replace("  ", " ").strip(), es_sp)
            saldo = float(d.get("saldo_tn") or 0)
            cuit = str(d.get("cuit") or "")
            prev = buckets.get(key)
            if not prev:
                buckets[key] = {
                    "nombre": nombre,
                    "cuit": cuit,
                    "centro_costo": "SP" if es_sp else "1",
                    "es_sp": es_sp,
                    "saldo_tn": saldo,
                    "_prio": _prio_cuit(cuit),
                }
            else:
                # sumar saldo solo si es otro CUIT distinto (evitar doble conteo del mismo movimiento)
                if cuit != prev["cuit"]:
                    # Preferir CUIT fiscal; el saldo de CC se toma del mejor match de nombre
                    # (ya viene agregado por nombre en el JOIN). Si hay duplicados de entidad,
                    # nos quedamos con el mayor saldo absoluto y el CUIT de mejor prioridad.
                    if abs(saldo) > abs(prev["saldo_tn"]):
                        prev["saldo_tn"] = saldo
                    if _prio_cuit(cuit) < prev["_prio"]:
                        prev["cuit"] = cuit
                        prev["_prio"] = _prio_cuit(cuit)
                        prev["nombre"] = nombre
                else:
                    prev["saldo_tn"] = saldo

        rows = []
        for v in buckets.values():
            v.pop("_prio", None)
            if solo_con_saldo and abs(float(v.get("saldo_tn") or 0)) < 0.0001:
                continue
            rows.append(v)
        rows.sort(key=lambda x: (-float(x.get("saldo_tn") or 0), (x.get("nombre") or "").upper()))
        conn.close()
        return rows

    @app.get("/api/agro/alquileres_cc/saldos")
    def api_alquileres_cc_saldos(
        locador: Optional[str] = None,
        campania: Optional[str] = None,
        grano: Optional[str] = None,
        solo_con_saldo: bool = True,
    ):
        """
        Saldos disponibles por locador / campaña / grano.
        Saldo tn = SUM(haber) - SUM(debe)  (pactado − entregado/vendido).
        """
        conn = get_db()
        cur = conn.cursor()
        eid = get_empresa_activa_id()
        sql = """
            SELECT
                campania_codigo,
                TRIM(locador) AS locador,
                UPPER(TRIM(COALESCE(grano,''))) AS grano,
                ROUND(SUM(COALESCE(haber,0)), 4) AS haber_tn,
                ROUND(SUM(COALESCE(debe,0)), 4) AS debe_tn,
                ROUND(SUM(COALESCE(haber,0)) - SUM(COALESCE(debe,0)), 4) AS saldo_tn
            FROM alquileres_cta_cte
            WHERE empresa_id=? AND TRIM(COALESCE(locador,''))<>''
        """
        params: list = [eid]
        if locador:
            sql += " AND UPPER(TRIM(locador)) LIKE ?"
            params.append("%" + locador.strip().upper() + "%")
        if campania:
            sql += " AND campania_codigo=?"
            params.append(normalizar_codigo_campania(campania))
        if grano:
            sql += " AND UPPER(TRIM(grano))=UPPER(TRIM(?))"
            params.append(grano)
        sql += " GROUP BY campania_codigo, TRIM(locador), UPPER(TRIM(COALESCE(grano,'')))"
        if solo_con_saldo:
            sql += " HAVING ABS(SUM(COALESCE(haber,0)) - SUM(COALESCE(debe,0))) > 0.0001"
        sql += " ORDER BY campania_codigo DESC, locador COLLATE NOCASE, grano"
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.post("/api/agro/alquileres_cc/venta")
    def api_alquileres_cc_venta(data: AlquilerVentaModel):
        """
        Alta de venta (entrega) contra saldo del arrendador.
        Impacta DEBE en alquileres_cta_cte (como Access «Alquileres Alta Vtas»).
        """
        from importar_tablas_agro import init_import_schema
        from datetime import datetime, timedelta

        locador = (data.locador or "").strip()
        grano = (data.grano or "").strip().upper()
        camp = normalizar_codigo_campania(data.campania_codigo)
        tns = float(data.tns_vendidas or 0)
        precio = float(data.precio_pizarra or 0)
        if not locador:
            raise HTTPException(400, "Indicá el locador / arrendador.")
        if not camp:
            raise HTTPException(400, "Indicá la campaña.")
        if not grano:
            raise HTTPException(400, "Indicá el grano.")
        if tns <= 0:
            raise HTTPException(400, "Las toneladas vendidas deben ser mayores a 0.")

        conn = get_db()
        cur = conn.cursor()
        init_import_schema(cur)
        eid = get_empresa_activa_id()

        cur.execute(
            """
            SELECT
                ROUND(SUM(COALESCE(haber,0)) - SUM(COALESCE(debe,0)), 4) AS saldo_tn
            FROM alquileres_cta_cte
            WHERE empresa_id=? AND campania_codigo=?
              AND UPPER(TRIM(locador))=UPPER(TRIM(?))
              AND UPPER(TRIM(COALESCE(grano,'')))=UPPER(TRIM(?));
            """,
            (eid, camp, locador, grano),
        )
        row = cur.fetchone()
        saldo = float(row["saldo_tn"] or 0) if row else 0.0
        if tns > saldo + 0.0001 and not data.forzar:
            conn.close()
            raise HTTPException(
                400,
                f"Saldo insuficiente: disponible {saldo:.4f} tn · venta {tns:.4f} tn "
                f"({locador} · {camp} · {grano}). Marcá «forzar» solo si corresponde.",
            )

        fecha_pizarra = (data.fecha_pizarra or "").strip()[:10]
        if not fecha_pizarra:
            fecha_pizarra = datetime.now().strftime("%Y-%m-%d")
        fecha_pago = (data.fecha_pago or "").strip()[:10]
        if not fecha_pago:
            try:
                base = datetime.strptime(fecha_pizarra, "%Y-%m-%d")
                fecha_pago = (base + timedelta(days=int(data.dias_pago or 0))).strftime("%Y-%m-%d")
            except ValueError:
                fecha_pago = fecha_pizarra

        if not data.confirmar_duplicado:
            cur.execute(
                """
                SELECT id, fecha_pago FROM alquileres_cta_cte
                WHERE empresa_id=? AND campania_codigo=?
                  AND UPPER(TRIM(locador))=UPPER(TRIM(?))
                  AND UPPER(TRIM(COALESCE(grano,'')))=UPPER(TRIM(?))
                  AND COALESCE(fecha_pizarra,'')=? AND ABS(COALESCE(debe,0)-?)<0.0001
                  AND ABS(COALESCE(precio_pizarra,0)-?)<0.01
                ORDER BY id DESC LIMIT 1;
                """,
                (eid, camp, locador, grano, fecha_pizarra, tns, precio),
            )
            dup = cur.fetchone()
            if dup:
                conn.close()
                raise HTTPException(
                    409,
                    f"Ya hay una venta igual cargada ({tns:g} tn · pizarra {fecha_pizarra} · "
                    f"$ {precio:,.2f}/tn · pago {dup['fecha_pago'] or '-'}). ¿La querés cargar de nuevo?",
                )

        importe = round(tns * precio, 2)
        detalle = (data.detalle or "Venta").strip() or "Venta"
        actividad = (data.actividad or "").strip()
        cuenta = (data.cuenta or "").strip()
        if actividad or cuenta:
            gestion = " - ".join(p for p in (actividad, cuenta) if p)
            if gestion.lower() not in detalle.lower():
                detalle = f"{gestion} · {detalle}" if detalle else gestion
        cols_cc = {r[1] for r in cur.execute("PRAGMA table_info(alquileres_cta_cte)")}
        if "actividad_gestion" not in cols_cc:
            cur.execute("ALTER TABLE alquileres_cta_cte ADD COLUMN actividad_gestion TEXT;")
        if "cuenta_gestion" not in cols_cc:
            cur.execute("ALTER TABLE alquileres_cta_cte ADD COLUMN cuenta_gestion TEXT;")

        cur.execute(
            """
            INSERT INTO alquileres_cta_cte (
                empresa_id, campania_codigo, locador, fecha_pago, grano,
                fecha_pizarra, precio_pizarra, debe, haber, varios, importe_total,
                actividad_gestion, cuenta_gestion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?);
            """,
            (
                eid, camp, locador, fecha_pago, grano,
                fecha_pizarra, precio, tns, detalle, importe,
                actividad, cuenta,
            ),
        )
        nuevo_id = cur.lastrowid
        from motor_contable import asiento_para_alquiler, proveedor_omite_asiento_oficial

        es_sp = proveedor_omite_asiento_oficial(cur, locador)
        asiento_id = None
        if not es_sp and importe > 0:
            asiento_id = asiento_para_alquiler(
                cur,
                fecha=fecha_pago or fecha_pizarra,
                locador=locador,
                importe=importe,
                detalle=detalle,
                empresa_id=eid,
                origen_id=nuevo_id,
            )
            cols_cc = {r[1] for r in cur.execute("PRAGMA table_info(alquileres_cta_cte)")}
            if "asiento_id" not in cols_cc:
                cur.execute("ALTER TABLE alquileres_cta_cte ADD COLUMN asiento_id INTEGER;")
            if asiento_id:
                cur.execute(
                    "UPDATE alquileres_cta_cte SET asiento_id=? WHERE id=?;",
                    (asiento_id, nuevo_id),
                )
        saldo_post = round(saldo - tns, 4)
        conn.commit()
        conn.close()
        if es_sp:
            msg_asiento = "Arrendador S/P: sin asiento contable."
        elif asiento_id:
            msg_asiento = f"Asiento #{asiento_id} (Debe Alquileres / Haber Proveedores)."
        elif importe <= 0:
            msg_asiento = "Sin precio: no se generó asiento."
        else:
            msg_asiento = "Sin asiento."
        return {
            "status": "ok",
            "id": nuevo_id,
            "asiento_id": asiento_id,
            "sin_asiento_sp": es_sp,
            "saldo_antes": saldo,
            "saldo_despues": saldo_post,
            "importe_total": importe,
            "fecha_pago": fecha_pago,
            "message": f"Venta registrada · {tns} tn · saldo restante {saldo_post} tn. {msg_asiento}",
        }

    @app.put("/api/agro/alquileres_cc/{mov_id}")
    def api_alquileres_cc_editar_venta(mov_id: int, data: AlquilerVentaEditModel):
        """Edita una venta (movimiento DEBE) y rehace su asiento si corresponde."""
        tns = float(data.tns_vendidas or 0)
        precio = float(data.precio_pizarra or 0)
        if tns <= 0:
            raise HTTPException(400, "Las toneladas vendidas deben ser mayores a 0.")
        conn = get_db()
        cur = conn.cursor()
        eid = get_empresa_activa_id()
        cur.execute("SELECT * FROM alquileres_cta_cte WHERE id=? AND empresa_id=?;", (mov_id, eid))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise HTTPException(404, "Movimiento no encontrado.")
        row = dict(row)
        if float(row.get("haber") or 0) > 0 or float(row.get("debe") or 0) <= 0:
            conn.close()
            raise HTTPException(400, "Sólo se pueden editar ventas (movimientos al DEBE); las tn de contrato se editan desde el contrato.")
        fecha_pizarra = (data.fecha_pizarra or "").strip()[:10] or (row.get("fecha_pizarra") or "")
        fecha_pago = (data.fecha_pago or "").strip()[:10] or fecha_pizarra
        detalle = (data.detalle or "").strip() or (row.get("varios") or "Venta")
        importe = round(tns * precio, 2)
        cur.execute(
            """UPDATE alquileres_cta_cte SET debe=?, precio_pizarra=?, fecha_pizarra=?, fecha_pago=?,
                   varios=?, importe_total=? WHERE id=?;""",
            (tns, precio, fecha_pizarra, fecha_pago, detalle, importe, mov_id),
        )
        from motor_contable import asiento_para_alquiler, eliminar_asiento, proveedor_omite_asiento_oficial
        asiento_id = row.get("asiento_id")
        if asiento_id:
            eliminar_asiento(cur, int(asiento_id))
            asiento_id = None
        if importe > 0 and not proveedor_omite_asiento_oficial(cur, row["locador"]):
            asiento_id = asiento_para_alquiler(
                cur, fecha=fecha_pago or fecha_pizarra, locador=row["locador"], importe=importe,
                detalle=detalle, empresa_id=eid, origen_id=mov_id,
            )
        if "asiento_id" in row or asiento_id:
            cols_cc = {r[1] for r in cur.execute("PRAGMA table_info(alquileres_cta_cte)")}
            if "asiento_id" not in cols_cc:
                cur.execute("ALTER TABLE alquileres_cta_cte ADD COLUMN asiento_id INTEGER;")
            cur.execute("UPDATE alquileres_cta_cte SET asiento_id=? WHERE id=?;", (asiento_id, mov_id))
        conn.commit()
        conn.close()
        return {"status": "ok", "asiento_id": asiento_id, "importe_total": importe}

    @app.delete("/api/agro/alquileres_cc/{mov_id}")
    def api_alquileres_cc_borrar_venta(mov_id: int):
        """Borra una venta (movimiento DEBE) de la cta cte de alquileres y su asiento, si tiene."""
        conn = get_db()
        cur = conn.cursor()
        eid = get_empresa_activa_id()
        cur.execute("SELECT * FROM alquileres_cta_cte WHERE id=? AND empresa_id=?;", (mov_id, eid))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise HTTPException(404, "Movimiento no encontrado.")
        row = dict(row)
        if float(row.get("haber") or 0) > 0 or float(row.get("debe") or 0) <= 0:
            conn.close()
            raise HTTPException(400, "Sólo se pueden borrar ventas (movimientos al DEBE); las tn de contrato se editan desde el contrato.")
        if row.get("asiento_id"):
            from motor_contable import eliminar_asiento
            eliminar_asiento(cur, int(row["asiento_id"]))
        cur.execute("DELETE FROM alquileres_cta_cte WHERE id=?;", (mov_id,))
        conn.commit()
        conn.close()
        return {"status": "ok", "asiento_borrado": row.get("asiento_id")}

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

    def _asegurar_margenes(cur):
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='margenes_access'")
        if not cur.fetchone():
            raise HTTPException(404, "En esta base todavía no están los costos importados de Access.")
        cols = [r[1] for r in cur.execute("PRAGMA table_info(margenes_access)").fetchall()]
        if "almacen_item_id" not in cols:
            cur.execute("ALTER TABLE margenes_access ADD COLUMN almacen_item_id INTEGER;")
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_margenes_filtro
            ON margenes_access(empresa_id, campania_codigo, campo);
            """
        )

    def _where_costos(eid, campania, campo, cultivo, lote):
        sql = " WHERE m.empresa_id=? "
        params: list = [eid]
        if campania == "__sin__":
            sql += " AND TRIM(COALESCE(m.campania_codigo,''))='' "
        elif campania:
            sql += " AND m.campania_codigo=? "
            params.append(normalizar_codigo_campania(campania))
        if campo:
            sql += " AND UPPER(TRIM(m.campo))=UPPER(TRIM(?)) "
            params.append(campo)
        if cultivo:
            sql += " AND UPPER(TRIM(m.cultivo))=UPPER(TRIM(?)) "
            params.append(cultivo)
        if lote:
            sql += " AND UPPER(TRIM(m.lote))=UPPER(TRIM(?)) "
            params.append(lote)
        return sql, params

    def _costo_linea(has_val, dosis, precio, tc, moneda):
        """Costo de una línea manual: hectáreas × dosis × precio del almacén."""
        has_val = float(has_val or 0)
        dosis = float(dosis or 0)
        precio = float(precio or 0)
        tc = float(tc or 0)
        cantidad = round(has_val * dosis, 4)
        total = round(cantidad * precio, 2)
        if (moneda or "ARS") == "USD":
            costo_usd = total
            costo_ars = round(total * tc, 2) if tc else 0.0
        else:
            costo_ars = total
            costo_usd = round(total / tc, 2) if tc else 0.0
        return cantidad, round(precio, 4), round(tc, 4), costo_ars, costo_usd

    _PALABRAS_VACIAS = {"el", "la", "los", "las", "de", "del", "y", "lote", "lotes"}
    _ABREVIATURAS = {"sta": "santa", "sto": "santo", "sn": "san", "gral": "general"}

    def _tokens_nombre(txt) -> frozenset:
        import unicodedata

        s = unicodedata.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode().lower()
        s = re.sub(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)", " ", s)
        partes = re.split(r"[^a-z0-9]+", s)
        return frozenset(_ABREVIATURAS.get(p, p) for p in partes if p and p not in _PALABRAS_VACIAS)

    class _SuperficiesLotes:
        """Cruza el texto de lote de los costos (Access) con los lotes del alta del campo.
        Un lote de costos puede juntar varios del alta ("san jose el monte" = "san jose" + "el monte")."""

        def __init__(self, cur, eid: int, campania: Optional[str]):
            self.cur = cur
            self.eid = eid
            self.campania_id = None
            if campania and campania != "__sin__":
                r = cur.execute(
                    "SELECT id FROM campanias_agro WHERE COALESCE(empresa_id, 1)=? AND codigo=?;",
                    (eid, normalizar_codigo_campania(campania)),
                ).fetchone()
                self.campania_id = r["id"] if r else None
            self._campos = None
            self._lotes = {}

        def _campo_id(self, campo: str):
            if self._campos is None:
                self._campos = [
                    (r["id"], _tokens_nombre(r["nombre"]))
                    for r in self.cur.execute(
                        "SELECT id, nombre FROM campos_agro WHERE COALESCE(empresa_id, 1)=? AND COALESCE(baja, 0)=0;",
                        (self.eid,),
                    ).fetchall()
                ]
            t = _tokens_nombre(campo)
            exacto = next((cid for cid, tok in self._campos if tok and tok == t), None)
            if exacto or not t:
                return exacto
            from difflib import SequenceMatcher

            texto = " ".join(sorted(t))
            puntajes = sorted(
                ((SequenceMatcher(None, texto, " ".join(sorted(tok))).ratio(), cid) for cid, tok in self._campos if tok),
                reverse=True,
            )
            # Solo errores de tipeo ("albiom"/"albion"): parecido alto y sin otro campo casi igual.
            if puntajes and puntajes[0][0] >= 0.8 and (len(puntajes) == 1 or puntajes[1][0] < puntajes[0][0] - 0.1):
                return puntajes[0][1]
            return None

        def _lotes_campo(self, campo_id: int):
            if campo_id not in self._lotes:
                _asegurar_cols_lotes(self.cur)
                filas = self.cur.execute(
                    """
                    SELECT l.id, l.nombre,
                           CASE WHEN COALESCE(l.superficie_total_lote, 0) > 0 THEN l.superficie_total_lote
                                ELSE COALESCE(
                                    (SELECT s.superficie FROM lote_superficie_campania s
                                     WHERE s.lote_id = l.id AND s.campania_id = ?),
                                    l.superficie_base, 0)
                           END AS sup
                    FROM lotes_agro l
                    WHERE l.campo_id = ? AND COALESCE(l.baja, 0) = 0;
                    """,
                    (self.campania_id or -1, campo_id),
                ).fetchall()
                lotes = [(r["id"], _tokens_nombre(r["nombre"]), float(r["sup"] or 0)) for r in filas]
                self._lotes[campo_id] = sorted([x for x in lotes if x[1]], key=lambda x: -len(x[1]))
            return self._lotes[campo_id]

        def resolver(self, campo: str, lote: str):
            """(has sembradas, ids de lotes) o (None, []) si no se puede cruzar con el alta."""
            cid = self._campo_id(campo)
            if not cid:
                return None, []
            pendientes = set(_tokens_nombre(lote))
            if not pendientes:
                return None, []
            elegidos = []
            for lid, tok, sup in self._lotes_campo(cid):
                if tok <= pendientes:
                    elegidos.append((lid, sup))
                    pendientes -= tok
                    if not pendientes:
                        break
            if pendientes or not elegidos:
                return None, []
            return round(sum(s for _, s in elegidos), 2), [lid for lid, _ in elegidos]

    def _por_ha(monto, has_val):
        return round(float(monto or 0) / has_val, 2) if has_val else None

    def _datos_costos_detalle(cur, eid, campania, campo, cultivo, lote, limit):
        where, params = _where_costos(eid, campania, campo, cultivo, lote)
        cur.execute(f"SELECT COUNT(*) AS n, COALESCE(SUM(costo_ars),0) AS ars, COALESCE(SUM(costo_usd),0) AS usd FROM margenes_access m {where};", params)
        tot = dict(cur.fetchone())
        cur.execute(
            f"""
            SELECT m.id, m.id_access, m.campania_codigo, m.campo, m.lote, m.cultivo,
                   m.fecha_orden, m.fecha_aplicacion, m.cantidad_has, m.laboreo, m.producto,
                   m.dosis_ha, m.cantidad_total, m.precio, m.tc, m.costo_ars, m.costo_usd,
                   m.unidad, m.contratista, m.almacen_item_id,
                   i.id AS item_id, i.costo_promedio_neto AS costo_almacen, i.stock_cantidad, i.unidad AS unidad_almacen
            FROM margenes_access m
            LEFT JOIN almacen_items i
              ON i.id = (
                SELECT ii.id FROM almacen_items ii
                WHERE ii.empresa_id = m.empresa_id
                  AND TRIM(COALESCE(m.producto,'')) != ''
                  AND UPPER(TRIM(ii.nombre)) = UPPER(TRIM(m.producto))
                ORDER BY ii.id LIMIT 1
              )
            {where}
            ORDER BY m.fecha_aplicacion, m.lote, m.id
            LIMIT ?;
            """,
            params + [max(1, min(limit, 20000))],
        )
        rows = [dict(r) for r in cur.fetchall()]
        sup = _SuperficiesLotes(cur, eid, campania)
        for r in rows:
            has_s, _ = sup.resolver(r["campo"], r["lote"])
            r["has_sembradas"] = has_s
            r["costo_ars_ha"] = _por_ha(r["costo_ars"], has_s)
            r["costo_usd_ha"] = _por_ha(r["costo_usd"], has_s)
        cur.execute(
            f"""
            SELECT TRIM(m.campo) AS campo, TRIM(m.lote) AS lote,
                   COALESCE(SUM(m.costo_ars), 0) AS costo_ars, COALESCE(SUM(m.costo_usd), 0) AS costo_usd
            FROM margenes_access m {where} GROUP BY 1, 2;
            """,
            params,
        )
        has_total, sin_sup, ars_ok, usd_ok = _sumar_superficies(sup, cur.fetchall())
        return {
            "total_filas": tot["n"],
            "costo_ars": tot["ars"],
            "costo_usd": tot["usd"],
            "has_sembradas": has_total,
            "ars_por_ha": _por_ha(ars_ok, has_total),
            "usd_por_ha": _por_ha(usd_ok, has_total),
            "lotes_sin_superficie": sin_sup,
            "filas": rows,
        }

    def _sumar_superficies(sup, pares):
        """Suma cada lote del alta una sola vez. El costo por ha usa solo el costo de los lotes con superficie:
        devuelve (has, lotes sin cruzar, costo $ con superficie, costo U$S con superficie)."""
        vistos = {}
        sin_sup = []
        ars_ok = usd_ok = 0.0
        for p in pares:
            has_s, ids = sup.resolver(p["campo"], p["lote"])
            if has_s is None:
                if (p["lote"] or "").strip():
                    sin_sup.append(f'{p["campo"]} / {p["lote"]}')
                continue
            ars_ok += float(p["costo_ars"] or 0)
            usd_ok += float(p["costo_usd"] or 0)
            for lid in ids:
                vistos[lid] = True
        total = 0.0
        for cid_lotes in sup._lotes.values():
            total += sum(s for lid, _, s in cid_lotes if lid in vistos)
        return round(total, 2), sorted(set(sin_sup)), round(ars_ok, 2), round(usd_ok, 2)

    def _datos_costos_estructura(cur, eid, campania, campo, cultivo, lote):
        where, params = _where_costos(eid, campania, campo, cultivo, lote)
        cur.execute(
            f"""
            SELECT
                TRIM(lote) AS lote,
                CASE WHEN TRIM(COALESCE(producto,'')) != '' THEN TRIM(producto) ELSE '' END AS producto,
                CASE WHEN TRIM(COALESCE(producto,'')) != '' THEN '' ELSE TRIM(COALESCE(laboreo,'')) END AS laboreo,
                MAX(cantidad_has) AS has,
                AVG(NULLIF(dosis_ha, 0)) AS dosis_ha,
                SUM(cantidad_total) AS und_total,
                SUM(costo_ars) AS costo_ars,
                SUM(costo_usd) AS costo_usd,
                COUNT(*) AS lineas
            FROM margenes_access m
            {where}
            GROUP BY 1, 2, 3
            ORDER BY lote, CASE WHEN producto = '' THEN 1 ELSE 0 END, producto, laboreo;
            """,
            params,
        )
        agrupadas = cur.fetchall()
        sup = _SuperficiesLotes(cur, eid, campania)
        lineas = []
        for r in agrupadas:
            d = dict(r)
            und = float(d["und_total"] or 0)
            has_val = float(d["has"] or 0)
            ars = float(d["costo_ars"] or 0)
            usd = float(d["costo_usd"] or 0)
            if und > 0:
                d["precio_prom"] = round(ars / und, 2)
            elif has_val > 0:
                d["precio_prom"] = round(ars / has_val, 2)
            else:
                d["precio_prom"] = 0
            d["tc"] = round(ars / usd, 2) if usd else 0
            d["costo_ars"] = round(ars, 2)
            d["costo_usd"] = round(usd, 2)
            d["dosis_ha"] = round(float(d["dosis_ha"] or 0), 4)
            has_s, _ = sup.resolver(campo, d["lote"])
            d["has_sembradas"] = has_s
            d["costo_ars_ha"] = _por_ha(ars, has_s)
            d["costo_usd_ha"] = _por_ha(usd, has_s)
            lineas.append(d)
        cur.execute(
            f"""
            SELECT COALESCE(SUM(has), 0) AS has_siembra FROM (
                SELECT MAX(cantidad_has) AS has
                FROM margenes_access m
                {where} AND UPPER(TRIM(laboreo))='SIEMBRA'
                GROUP BY TRIM(lote)
            );
            """,
            params,
        )
        has_siembra_ot = float(cur.fetchone()["has_siembra"] or 0)
        has_total, sin_sup, ars_ok, usd_ok = _sumar_superficies(
            sup, [{"campo": campo, "lote": x["lote"], "costo_ars": x["costo_ars"], "costo_usd": x["costo_usd"]} for x in lineas]
        )
        ars = round(sum(x["costo_ars"] for x in lineas), 2)
        usd = round(sum(x["costo_usd"] for x in lineas), 2)
        if not has_total:
            ars_ok, usd_ok = ars, usd
        has_ref = has_total or has_siembra_ot
        return {
            "campania": normalizar_codigo_campania(campania) if campania != "__sin__" else "",
            "campo": campo,
            "cultivo": cultivo or "",
            "lineas": lineas,
            "lotes_sin_superficie": sin_sup,
            "totales": {
                "costo_ars": ars,
                "costo_usd": usd,
                "tc": round(ars / usd, 2) if usd else 0,
                "has_sembradas": has_total,
                "has_siembra": round(has_ref, 2),
                "has_siembra_ot": round(has_siembra_ot, 2),
                "ars_por_ha": _por_ha(ars_ok, has_ref) or 0,
                "usd_por_ha": _por_ha(usd_ok, has_ref) or 0,
            },
        }

    def _excel_costos(cur, eid, campania, campo, cultivo, lote) -> bytes:
        from io import BytesIO
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        negrita = Font(bold=True)
        fondo = PatternFill("solid", fgColor="E2E8F0")
        money = "#,##0.00"
        filtro = " · ".join(x for x in [
            f"Campaña {campania}" if campania and campania != "__sin__" else "",
            campo or "", cultivo or "", f"lote {lote}" if lote else "",
        ] if x)

        def hoja(ws, titulo, sub, cols, filas, monedas_desde, anchos):
            ws["A1"] = titulo
            ws["A1"].font = Font(bold=True, size=13)
            ws["A2"] = sub
            for col, t in enumerate(cols, 1):
                c = ws.cell(row=4, column=col, value=t)
                c.font = negrita
                c.fill = fondo
                c.alignment = Alignment(wrap_text=True, vertical="center")
            for i, valores in enumerate(filas, 5):
                for col, v in enumerate(valores, 1):
                    c = ws.cell(row=i, column=col, value=v)
                    if col >= monedas_desde and isinstance(v, (int, float)):
                        c.number_format = money
            for i, w in enumerate(anchos, 1):
                ws.column_dimensions[get_column_letter(i)].width = w
            ws.freeze_panes = "A5"
            return 5 + len(filas)

        wb = Workbook()
        det = _datos_costos_detalle(cur, eid, campania, campo, cultivo, lote, 20000)
        ws = wb.active
        ws.title = "Detalle"
        cols = ["Fecha", "Campo", "Lote", "Cultivo", "Producto", "Laboreo", "Has", "Dosis/ha", "Cant.",
                "Precio", "Costo $", "Costo U$S", "Has sembradas", "$/ha sembrada", "U$S/ha sembrada",
                "Precio almacén", "Stock almacén"]
        filas = [[
            r.get("fecha_aplicacion") or r.get("fecha_orden") or "", r["campo"], r["lote"], r["cultivo"],
            r["producto"] or "", r["laboreo"] or "", r["cantidad_has"], r["dosis_ha"], r["cantidad_total"],
            r["precio"], r["costo_ars"], r["costo_usd"], r["has_sembradas"], r["costo_ars_ha"], r["costo_usd_ha"],
            r["costo_almacen"] if r.get("item_id") else None, r["stock_cantidad"] if r.get("item_id") else None,
        ] for r in det["filas"]]
        fin = hoja(ws, f"Costos - detalle | {filtro}",
                   f"{det['total_filas']} líneas · Has sembradas (alta del campo) {det['has_sembradas'] or 0}",
                   cols, filas, 7, [11, 16, 26, 12, 30, 22, 8, 9, 10, 13, 15, 13, 11, 13, 13, 14, 11])
        ws.cell(row=fin, column=1, value="Total").font = negrita
        for col, v in ((11, det["costo_ars"]), (12, det["costo_usd"]), (13, det["has_sembradas"]),
                       (14, det["ars_por_ha"]), (15, det["usd_por_ha"])):
            c = ws.cell(row=fin, column=col, value=v)
            c.font = negrita
            c.number_format = money
        if det["lotes_sin_superficie"]:
            ws.cell(row=fin + 2, column=1, value="Lotes sin superficie en el alta del campo: " + ", ".join(det["lotes_sin_superficie"]))

        if campania and campo:
            est = _datos_costos_estructura(cur, eid, campania, campo, cultivo, lote)
            ws2 = wb.create_sheet("Estructura")
            t = est["totales"]
            cols2 = ["Producto", "Laboreo", "Lote", "Has", "Dosis/ha", "Und. total", "Precio prom.",
                     "Costo $", "Costo U$S", "T/C", "Has sembradas", "$/ha sembrada", "U$S/ha sembrada"]
            filas2 = [[x["producto"], x["laboreo"], x["lote"], x["has"], x["dosis_ha"], x["und_total"],
                       x["precio_prom"], x["costo_ars"], x["costo_usd"], x["tc"], x["has_sembradas"],
                       x["costo_ars_ha"], x["costo_usd_ha"]] for x in est["lineas"]]
            fin2 = hoja(ws2, f"Estructura de costos | {filtro}",
                        f"Has sembradas {t['has_siembra']} · $/ha {t['ars_por_ha']} · U$S/ha {t['usd_por_ha']}",
                        cols2, filas2, 4, [30, 22, 26, 8, 9, 11, 13, 15, 13, 9, 11, 13, 13])
            ws2.cell(row=fin2, column=1, value="Suma total").font = negrita
            for col, v in ((8, t["costo_ars"]), (9, t["costo_usd"]), (10, t["tc"]), (11, t["has_siembra"]),
                           (12, t["ars_por_ha"]), (13, t["usd_por_ha"])):
                c = ws2.cell(row=fin2, column=col, value=v)
                c.font = negrita
                c.number_format = money

        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

    @app.get("/api/agro/costos/opciones")
    def api_costos_opciones(
        campania: Optional[str] = None,
        campo: Optional[str] = None,
        cultivo: Optional[str] = None,
    ):
        conn = get_db()
        cur = conn.cursor()
        _asegurar_margenes(cur)
        conn.commit()
        eid = get_empresa_activa_id()
        cur.execute(
            """
            SELECT campania_codigo, COUNT(*) n
            FROM margenes_access
            WHERE empresa_id=?
            GROUP BY campania_codigo
            ORDER BY campania_codigo DESC;
            """,
            (eid,),
        )
        campanias = [{"codigo": r["campania_codigo"] or "", "n": r["n"]} for r in cur.fetchall()]
        where, params = _where_costos(eid, campania, None, None, None)
        cur.execute(
            f"SELECT DISTINCT TRIM(campo) AS v FROM margenes_access m {where} AND TRIM(COALESCE(campo,''))!='' ORDER BY 1;",
            params,
        )
        campos = [r["v"] for r in cur.fetchall()]
        where, params = _where_costos(eid, campania, campo, None, None)
        cur.execute(
            f"SELECT DISTINCT TRIM(cultivo) AS v FROM margenes_access m {where} AND TRIM(COALESCE(cultivo,''))!='' ORDER BY 1;",
            params,
        )
        cultivos = [r["v"] for r in cur.fetchall()]
        where, params = _where_costos(eid, campania, campo, cultivo, None)
        cur.execute(
            f"SELECT DISTINCT TRIM(lote) AS v FROM margenes_access m {where} AND TRIM(COALESCE(lote,''))!='' ORDER BY 1;",
            params,
        )
        lotes = [r["v"] for r in cur.fetchall()]
        conn.close()
        return {"campanias": campanias, "campos": campos, "cultivos": cultivos, "lotes": lotes}

    @app.get("/api/agro/costos")
    def api_costos_detalle(
        campania: Optional[str] = None,
        campo: Optional[str] = None,
        cultivo: Optional[str] = None,
        lote: Optional[str] = None,
        limit: int = 800,
    ):
        if not campania and not campo:
            raise HTTPException(400, "Elegí al menos una campaña o un campo.")
        conn = get_db()
        try:
            cur = conn.cursor()
            _asegurar_margenes(cur)
            return _datos_costos_detalle(cur, get_empresa_activa_id(), campania, campo, cultivo, lote, min(limit, 2000))
        finally:
            conn.close()

    @app.get("/api/agro/costos/estructura")
    def api_costos_estructura(
        campania: str,
        campo: str,
        cultivo: Optional[str] = None,
        lote: Optional[str] = None,
    ):
        conn = get_db()
        try:
            cur = conn.cursor()
            _asegurar_margenes(cur)
            return _datos_costos_estructura(cur, get_empresa_activa_id(), campania, campo, cultivo, lote)
        finally:
            conn.close()

    @app.get("/api/agro/costos/excel")
    def api_costos_excel(
        campania: Optional[str] = None,
        campo: Optional[str] = None,
        cultivo: Optional[str] = None,
        lote: Optional[str] = None,
    ):
        from fastapi.responses import Response

        if not campania and not campo:
            raise HTTPException(400, "Elegí al menos una campaña o un campo.")
        conn = get_db()
        try:
            cur = conn.cursor()
            _asegurar_margenes(cur)
            contenido = _excel_costos(cur, get_empresa_activa_id(), campania, campo, cultivo, lote)
        finally:
            conn.close()
        nombre = re.sub(r"[^A-Za-z0-9]+", "_", "_".join(x for x in [campania, campo, cultivo, lote] if x)).strip("_")
        return Response(
            content=contenido,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="costos_{nombre or "campania"}.xlsx"'},
        )

    @app.post("/api/agro/costos")
    def api_costos_alta(data: dict = Body(...)):
        conn = get_db()
        cur = conn.cursor()
        _asegurar_margenes(cur)
        eid = get_empresa_activa_id()
        camp = (data.get("campania") or "").strip()
        campo = (data.get("campo") or "").strip()
        if not camp or not campo:
            conn.close()
            raise HTTPException(400, "Campaña y campo son obligatorios.")
        camp = normalizar_codigo_campania(camp)
        item_id = data.get("almacen_item_id") or None
        if not item_id:
            conn.close()
            raise HTTPException(400, "Elegí un producto o un laboreo del almacén.")
        cur.execute(
            """
            SELECT id, nombre, unidad, tipo, costo_promedio_neto, costo_promedio_usd
            FROM almacen_items WHERE id=? AND empresa_id=?;
            """,
            (int(item_id), eid),
        )
        item = cur.fetchone()
        if not item:
            conn.close()
            raise HTTPException(404, "Ese ítem no está en el almacén.")
        es_laboreo = (item["tipo"] or "") == "laboreo"
        usd = float(item["costo_promedio_usd"] or 0)
        ars = float(item["costo_promedio_neto"] or 0)
        if not es_laboreo and usd > 0:
            precio, moneda = usd, "USD"
        else:
            precio, moneda = ars, "ARS"
        if precio <= 0:
            conn.close()
            raise HTTPException(400, "Ese ítem no tiene precio cargado en el almacén.")
        producto = "" if es_laboreo else (item["nombre"] or "")
        laboreo = (item["nombre"] or "") if es_laboreo else ""
        unidad = item["unidad"] or ""
        has_val = float(data.get("cantidad_has") or 0)
        dosis = float(data.get("dosis_ha") or 0)
        if has_val <= 0 or dosis <= 0:
            conn.close()
            raise HTTPException(400, "Indicá las hectáreas y la dosis. El costo es hectáreas × dosis × precio.")
        cantidad, precio, tc, costo_ars, costo_usd = _costo_linea(
            has_val, dosis, precio, data.get("tc"), moneda,
        )
        fecha = (data.get("fecha") or "")[:10]
        cur.execute(
            """
            INSERT INTO margenes_access (
                empresa_id, id_access, campania_codigo, cultivo, campo, lote, cantidad_has,
                fecha_orden, fecha_aplicacion, laboreo, producto, dosis_ha, cantidad_total,
                precio, costo_ars, tc, costo_usd, unidad, contratista, almacen_item_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
            """,
            (
                eid, None, camp, (data.get("cultivo") or "").strip(), campo,
                (data.get("lote") or "").strip(), float(data.get("cantidad_has") or 0),
                fecha, fecha, laboreo, producto, float(data.get("dosis_ha") or 0), cantidad,
                precio, costo_ars, tc, costo_usd, unidad,
                (data.get("contratista") or "").strip(), int(item_id) if item_id else None,
            ),
        )
        nuevo = cur.lastrowid
        conn.commit()
        conn.close()
        return {
            "id": nuevo,
            "moneda": moneda,
            "precio": precio,
            "costo_ars": costo_ars,
            "costo_usd": costo_usd,
            "cantidad_total": cantidad,
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
