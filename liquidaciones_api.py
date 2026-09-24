# -*- coding: utf-8 -*-
"""
Liquidaciones (hacienda / LPG) + imputaciones de ingresos para Gestión.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from actividades_imputacion import ensure_ingresos_default, init_actividades_schema


def init_liquidaciones_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS liquidaciones_hacienda (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            entidad_cuit TEXT,
            entidad_nombre TEXT,
            nro_liquidacion TEXT,
            fecha TEXT,
            plazo_dias INTEGER DEFAULT 0,
            comision REAL DEFAULT 0,
            fondo_garantia REAL DEFAULT 0,
            iva REAL DEFAULT 0,
            ret_iibb REAL DEFAULT 0,
            ret_ganancias REAL DEFAULT 0,
            guia_ref TEXT,
            bruto REAL DEFAULT 0,
            neto_final REAL DEFAULT 0,
            cabezas INTEGER DEFAULT 0,
            kilos REAL DEFAULT 0,
            usuario_registro TEXT,
            created_at TEXT
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS liquidaciones_hacienda_tropas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            liquidacion_id INTEGER NOT NULL,
            nro_tropa TEXT,
            comprador TEXT,
            cabezas REAL DEFAULT 0,
            categoria TEXT,
            kilos REAL DEFAULT 0,
            precio_kg REAL DEFAULT 0,
            subtotal REAL DEFAULT 0,
            FOREIGN KEY(liquidacion_id) REFERENCES liquidaciones_hacienda(id)
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS liquidaciones_granos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            entidad_cuit TEXT,
            entidad_nombre TEXT,
            grano TEXT,
            campania TEXT,
            nro_lpg TEXT,
            grado TEXT,
            factor REAL DEFAULT 1,
            precio_tn REAL DEFAULT 0,
            fecha_pago TEXT,
            kilos_netos REAL DEFAULT 0,
            bruto REAL DEFAULT 0,
            neto REAL DEFAULT 0,
            usuario_registro TEXT,
            created_at TEXT
        );
        """
    )
    cursor.execute("PRAGMA table_info(liquidaciones_granos);")
    cols_lg = {r[1] for r in cursor.fetchall()}
    for col, ddl in [
        ("fecha", "TEXT"),
        ("mes_iva", "TEXT"),
        ("precio_referencia", "REAL DEFAULT 0"),
        ("cert_dep_ref", "TEXT"),
        ("subtotal", "REAL DEFAULT 0"),
        ("iva_operacion", "REAL DEFAULT 0"),
        ("operacion_c_iva", "REAL DEFAULT 0"),
        ("pct_comision", "REAL DEFAULT 2.5"),
        ("gtos_adm", "REAL DEFAULT 0"),
        ("fletes", "REAL DEFAULT 0"),
        ("gtos_almacenamiento", "REAL DEFAULT 0"),
        ("pct_iva_servicios", "REAL DEFAULT 10.5"),
        ("iva_servicios", "REAL DEFAULT 0"),
        ("total_deducciones", "REAL DEFAULT 0"),
        ("pct_iibb", "REAL DEFAULT 0.8"),
        ("ret_iibb", "REAL DEFAULT 0"),
        ("pct_sellados", "REAL DEFAULT 0.9"),
        ("sellados", "REAL DEFAULT 0"),
        ("pct_ganancias", "REAL DEFAULT 0"),
        ("ret_ganancias", "REAL DEFAULT 0"),
        ("pct_ret_iva", "REAL DEFAULT 5"),
        ("ret_iva", "REAL DEFAULT 0"),
        ("otros_ret", "REAL DEFAULT 0"),
        ("total_retenciones", "REAL DEFAULT 0"),
        ("iva_rg2300", "REAL DEFAULT 0"),
        ("neto_a_pagar", "REAL DEFAULT 0"),
        ("pago_condiciones", "REAL DEFAULT 0"),
        ("nota", "TEXT"),
    ]:
        if col not in cols_lg:
            cursor.execute(f"ALTER TABLE liquidaciones_granos ADD COLUMN {col} {ddl};")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS liquidaciones_granos_certs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            liquidacion_id INTEGER NOT NULL,
            nro_certificado TEXT,
            procedencia TEXT,
            kilos REAL DEFAULT 0,
            FOREIGN KEY(liquidacion_id) REFERENCES liquidaciones_granos(id)
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS liquidacion_imputaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            origen_tipo TEXT NOT NULL,
            origen_id INTEGER NOT NULL,
            fecha TEXT,
            actividad_id INTEGER,
            cuenta_imputacion_id INTEGER,
            actividad_nombre TEXT,
            cuenta_nombre TEXT,
            neto REAL DEFAULT 0,
            sentido TEXT DEFAULT 'INGRESO'
        );
        """
    )


class TropaIn(BaseModel):
    nro_tropa: Optional[str] = ""
    comprador: Optional[str] = ""
    cabezas: float = 0
    categoria: Optional[str] = ""
    kilos: float = 0
    precio_kg: float = 0
    subtotal: float = 0


class ImputacionIn(BaseModel):
    actividad_id: Optional[int] = None
    cuenta_imputacion_id: Optional[int] = None
    actividad_nombre: Optional[str] = ""
    cuenta_nombre: Optional[str] = ""
    neto: float = 0


class HaciendaIn(BaseModel):
    entidad_cuit: Optional[str] = ""
    entidad_nombre: Optional[str] = ""
    nro_liquidacion: Optional[str] = ""
    fecha: Optional[str] = ""
    plazo_dias: int = 0
    comision: float = 0
    fondo_garantia: float = 0
    iva: float = 0
    ret_iibb: float = 0
    ret_ganancias: float = 0
    guia_ref: Optional[str] = ""
    bruto: float = 0
    neto_final: float = 0
    cabezas: float = 0
    kilos: float = 0
    tropas: List[TropaIn] = Field(default_factory=list)
    imputaciones: List[ImputacionIn] = Field(default_factory=list)
    usuario_registro: Optional[str] = ""


class CertIn(BaseModel):
    nro_certificado: Optional[str] = ""
    procedencia: Optional[str] = ""
    kilos: float = 0


class GranoIn(BaseModel):
    entidad_cuit: Optional[str] = ""
    entidad_nombre: Optional[str] = ""
    grano: Optional[str] = ""
    campania: Optional[str] = ""
    nro_lpg: Optional[str] = ""
    grado: Optional[str] = ""
    factor: float = 1
    precio_tn: float = 0
    fecha_pago: Optional[str] = ""
    fecha: Optional[str] = ""
    mes_iva: Optional[str] = ""
    precio_referencia: float = 0
    cert_dep_ref: Optional[str] = ""
    kilos_netos: float = 0
    bruto: float = 0
    neto: float = 0
    subtotal: float = 0
    iva_operacion: float = 0
    operacion_c_iva: float = 0
    pct_comision: float = 2.5
    gtos_adm: float = 0
    fletes: float = 0
    gtos_almacenamiento: float = 0
    pct_iva_servicios: float = 10.5
    iva_servicios: float = 0
    total_deducciones: float = 0
    pct_iibb: float = 0.8
    ret_iibb: float = 0
    pct_sellados: float = 0.9
    sellados: float = 0
    pct_ganancias: float = 0
    ret_ganancias: float = 0
    pct_ret_iva: float = 5
    ret_iva: float = 0
    otros_ret: float = 0
    total_retenciones: float = 0
    iva_rg2300: float = 0
    neto_a_pagar: float = 0
    pago_condiciones: float = 0
    nota: Optional[str] = ""
    certificados: List[CertIn] = Field(default_factory=list)
    imputaciones: List[ImputacionIn] = Field(default_factory=list)
    usuario_registro: Optional[str] = ""


def _guardar_imputaciones(
    cur,
    empresa_id: int,
    origen_tipo: str,
    origen_id: int,
    fecha: str,
    imputaciones: List[ImputacionIn],
) -> None:
    cur.execute(
        "DELETE FROM liquidacion_imputaciones WHERE origen_tipo = ? AND origen_id = ?;",
        (origen_tipo, origen_id),
    )
    for row in imputaciones or []:
        neto = float(row.neto or 0)
        if neto == 0 and not (row.actividad_id or row.cuenta_imputacion_id or row.cuenta_nombre):
            continue
        cur.execute(
            """
            INSERT INTO liquidacion_imputaciones (
                empresa_id, origen_tipo, origen_id, fecha,
                actividad_id, cuenta_imputacion_id, actividad_nombre, cuenta_nombre,
                neto, sentido
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'INGRESO');
            """,
            (
                empresa_id,
                origen_tipo,
                origen_id,
                fecha or "",
                row.actividad_id,
                row.cuenta_imputacion_id,
                (row.actividad_nombre or "").strip(),
                (row.cuenta_nombre or "").strip(),
                neto,
            ),
        )


def register_liquidaciones_routes(
    app: FastAPI, get_db: Callable, get_empresa_activa_id: Callable
) -> None:
    try:
        conn = get_db()
        cur = conn.cursor()
        init_actividades_schema(cur)
        init_liquidaciones_schema(cur)
        conn.commit()
        ensure_ingresos_default(conn, get_empresa_activa_id())
        conn.close()
    except Exception as exc:
        print(f"AVISO init liquidaciones: {exc}")

    @app.get("/api/liquidaciones/hacienda")
    def list_hacienda(limit: int = 50):
        conn = get_db()
        cur = conn.cursor()
        eid = get_empresa_activa_id()
        cur.execute(
            """
            SELECT * FROM liquidaciones_hacienda
            WHERE empresa_id = ?
            ORDER BY COALESCE(fecha,'') DESC, id DESC
            LIMIT ?;
            """,
            (eid, limit),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.post("/api/liquidaciones/hacienda")
    def save_hacienda(data: HaciendaIn):
        if not (data.nro_liquidacion or "").strip() and not data.tropas and not data.imputaciones:
            raise HTTPException(400, "Completá al menos nro de liquidación o tropas/imputaciones")
        conn = get_db()
        cur = conn.cursor()
        eid = get_empresa_activa_id()
        ahora = datetime.now().isoformat(timespec="seconds")
        cur.execute(
            """
            INSERT INTO liquidaciones_hacienda (
                empresa_id, entidad_cuit, entidad_nombre, nro_liquidacion, fecha, plazo_dias,
                comision, fondo_garantia, iva, ret_iibb, ret_ganancias, guia_ref,
                bruto, neto_final, cabezas, kilos, usuario_registro, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                eid,
                (data.entidad_cuit or "").strip(),
                (data.entidad_nombre or "").strip(),
                (data.nro_liquidacion or "").strip(),
                data.fecha or "",
                int(data.plazo_dias or 0),
                float(data.comision or 0),
                float(data.fondo_garantia or 0),
                float(data.iva or 0),
                float(data.ret_iibb or 0),
                float(data.ret_ganancias or 0),
                (data.guia_ref or "").strip(),
                float(data.bruto or 0),
                float(data.neto_final or 0),
                int(data.cabezas or 0),
                float(data.kilos or 0),
                (data.usuario_registro or "").strip(),
                ahora,
            ),
        )
        lid = int(cur.lastrowid)
        for t in data.tropas or []:
            if not any([t.nro_tropa, t.comprador, t.categoria, t.cabezas, t.kilos, t.subtotal]):
                continue
            cur.execute(
                """
                INSERT INTO liquidaciones_hacienda_tropas (
                    liquidacion_id, nro_tropa, comprador, cabezas, categoria, kilos, precio_kg, subtotal
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    lid,
                    t.nro_tropa or "",
                    t.comprador or "",
                    float(t.cabezas or 0),
                    t.categoria or "",
                    float(t.kilos or 0),
                    float(t.precio_kg or 0),
                    float(t.subtotal or 0),
                ),
            )
        _guardar_imputaciones(cur, eid, "HACIENDA", lid, data.fecha or "", data.imputaciones)
        conn.commit()
        conn.close()
        return {"status": "ok", "id": lid}

    @app.get("/api/liquidaciones/granos")
    def list_granos(limit: int = 50):
        conn = get_db()
        cur = conn.cursor()
        eid = get_empresa_activa_id()
        cur.execute(
            """
            SELECT * FROM liquidaciones_granos
            WHERE empresa_id = ?
            ORDER BY COALESCE(fecha_pago,'') DESC, id DESC
            LIMIT ?;
            """,
            (eid, limit),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.post("/api/liquidaciones/granos")
    def save_granos(data: GranoIn):
        if not (data.nro_lpg or "").strip() and not data.certificados and not data.imputaciones:
            raise HTTPException(400, "Completá nro LPG, certificados o imputaciones")
        conn = get_db()
        cur = conn.cursor()
        eid = get_empresa_activa_id()
        ahora = datetime.now().isoformat(timespec="seconds")
        fecha = (data.fecha or data.fecha_pago or "").strip()
        subtotal = float(data.subtotal or data.bruto or 0)
        neto = float(data.neto_a_pagar or data.neto or 0)
        cur.execute(
            """
            INSERT INTO liquidaciones_granos (
                empresa_id, entidad_cuit, entidad_nombre, grano, campania, nro_lpg,
                grado, factor, precio_tn, fecha_pago, kilos_netos, bruto, neto,
                usuario_registro, created_at,
                fecha, mes_iva, precio_referencia, cert_dep_ref,
                subtotal, iva_operacion, operacion_c_iva,
                pct_comision, gtos_adm, fletes, gtos_almacenamiento,
                pct_iva_servicios, iva_servicios, total_deducciones,
                pct_iibb, ret_iibb, pct_sellados, sellados,
                pct_ganancias, ret_ganancias, pct_ret_iva, ret_iva,
                otros_ret, total_retenciones, iva_rg2300, neto_a_pagar,
                pago_condiciones, nota
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?
            );
            """,
            (
                eid,
                (data.entidad_cuit or "").strip(),
                (data.entidad_nombre or "").strip(),
                (data.grano or "").strip(),
                (data.campania or "").strip(),
                (data.nro_lpg or "").strip(),
                (data.grado or "").strip(),
                float(data.factor or 1),
                float(data.precio_tn or 0),
                data.fecha_pago or fecha,
                float(data.kilos_netos or 0),
                subtotal,
                neto,
                (data.usuario_registro or "").strip(),
                ahora,
                fecha,
                (data.mes_iva or "").strip(),
                float(data.precio_referencia or 0),
                (data.cert_dep_ref or "").strip(),
                subtotal,
                float(data.iva_operacion or 0),
                float(data.operacion_c_iva or 0),
                float(data.pct_comision or 0),
                float(data.gtos_adm or 0),
                float(data.fletes or 0),
                float(data.gtos_almacenamiento or 0),
                float(data.pct_iva_servicios or 0),
                float(data.iva_servicios or 0),
                float(data.total_deducciones or 0),
                float(data.pct_iibb or 0),
                float(data.ret_iibb or 0),
                float(data.pct_sellados or 0),
                float(data.sellados or 0),
                float(data.pct_ganancias or 0),
                float(data.ret_ganancias or 0),
                float(data.pct_ret_iva or 0),
                float(data.ret_iva or 0),
                float(data.otros_ret or 0),
                float(data.total_retenciones or 0),
                float(data.iva_rg2300 or 0),
                neto,
                float(data.pago_condiciones or neto),
                (data.nota or "").strip(),
            ),
        )
        lid = int(cur.lastrowid)
        for c in data.certificados or []:
            if not any([c.nro_certificado, c.procedencia, c.kilos]):
                continue
            cur.execute(
                """
                INSERT INTO liquidaciones_granos_certs (
                    liquidacion_id, nro_certificado, procedencia, kilos
                ) VALUES (?, ?, ?, ?);
                """,
                (lid, c.nro_certificado or "", c.procedencia or "", float(c.kilos or 0)),
            )
        _guardar_imputaciones(cur, eid, "GRANO", lid, fecha, data.imputaciones)
        conn.commit()
        conn.close()
        return {"status": "ok", "id": lid, "neto_a_pagar": neto}

    @app.get("/api/gestion/resumen")
    def gestion_resumen(
        actividad_id: Optional[int] = None,
        actividad: Optional[str] = None,
        desde: Optional[str] = None,
        hasta: Optional[str] = None,
    ):
        """Ingresos (liquidaciones) vs egresos (facturas) por actividad y rango de fechas."""
        conn = get_db()
        cur = conn.cursor()
        eid = get_empresa_activa_id()
        desde = (desde or "").strip() or "1900-01-01"
        hasta = (hasta or "").strip() or "2999-12-31"

        egresos_sql = """
            SELECT
                COALESCE(NULLIF(TRIM(fi.actividad_nombre),''), 'Sin actividad') AS actividad,
                fi.actividad_id,
                COALESCE(NULLIF(TRIM(fi.cuenta_nombre),''), 'Sin cuenta') AS cuenta,
                SUM(COALESCE(fi.neto,0)) AS total
            FROM factura_imputaciones fi
            LEFT JOIN cuentas_corrientes cc ON cc.id = fi.cc_id
            WHERE fi.empresa_id = ?
              AND COALESCE(cc.fecha, '') BETWEEN ? AND ?
        """
        params_eg: list = [eid, desde, hasta]
        if actividad_id:
            egresos_sql += " AND fi.actividad_id = ?"
            params_eg.append(actividad_id)
        elif actividad:
            egresos_sql += " AND UPPER(TRIM(fi.actividad_nombre)) = UPPER(TRIM(?))"
            params_eg.append(actividad)
        egresos_sql += " GROUP BY actividad, fi.actividad_id, cuenta ORDER BY total DESC;"
        cur.execute(egresos_sql, params_eg)
        egresos = [dict(r) for r in cur.fetchall()]

        ingresos_sql = """
            SELECT
                COALESCE(NULLIF(TRIM(li.actividad_nombre),''), 'Sin actividad') AS actividad,
                li.actividad_id,
                COALESCE(NULLIF(TRIM(li.cuenta_nombre),''), 'Sin cuenta') AS cuenta,
                SUM(COALESCE(li.neto,0)) AS total
            FROM liquidacion_imputaciones li
            WHERE li.empresa_id = ?
              AND COALESCE(li.fecha, '') BETWEEN ? AND ?
              AND UPPER(COALESCE(li.sentido,'INGRESO')) = 'INGRESO'
        """
        params_in: list = [eid, desde, hasta]
        if actividad_id:
            ingresos_sql += " AND li.actividad_id = ?"
            params_in.append(actividad_id)
        elif actividad:
            ingresos_sql += " AND UPPER(TRIM(li.actividad_nombre)) = UPPER(TRIM(?))"
            params_in.append(actividad)
        ingresos_sql += " GROUP BY actividad, li.actividad_id, cuenta ORDER BY total DESC;"
        cur.execute(ingresos_sql, params_in)
        ingresos = [dict(r) for r in cur.fetchall()]

        tot_ing = sum(float(r["total"] or 0) for r in ingresos)
        tot_eg = sum(float(r["total"] or 0) for r in egresos)
        conn.close()
        return {
            "desde": desde,
            "hasta": hasta,
            "ingresos": ingresos,
            "egresos": egresos,
            "totales": {
                "ingresos": tot_ing,
                "egresos": tot_eg,
                "resultado": tot_ing - tot_eg,
            },
        }
