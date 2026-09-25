# -*- coding: utf-8 -*-
"""
Importa tablas/tablas.xlsx (export Access) → campoplus.db

Hojas:
- Contratos Alquileres, Alquileres Cta Cte, Alquiler modalidad
- Campos Lotes Titularidades IP2, Arca IP 1
- granos, tipo insumos, actividades y laboreos, labor cultural
- Margenes brutos Access
- productos, Stock Almacenes
- Patrimonial Rodados Maquinarias
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from agro_almacen import init_almacen_schema
from agro_campania import (
    asegurar_campania_activa,
    init_agro_schema,
    normalizar_codigo_campania,
    normalizar_cultivo,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "campoplus.db")
EXCEL_TABLAS = os.path.join(BASE_DIR, "tablas", "tablas.xlsx")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _safe_str(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "nat", "null"):
        return ""
    return s


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return default
        if isinstance(val, str):
            val = val.replace(".", "").replace(",", ".") if val.count(",") == 1 and val.count(".") > 1 else val.replace(",", ".")
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_date(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    try:
        if pd.isna(val):
            return ""
    except Exception:
        pass
    if isinstance(val, datetime):
        try:
            return val.strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            return ""
    try:
        if hasattr(val, "strftime") and not pd.isna(val):
            return val.strftime("%Y-%m-%d")
    except Exception:
        pass
    s = _safe_str(val)
    if not s:
        return ""
    try:
        ts = pd.to_datetime(val, errors="coerce")
        if pd.isna(ts):
            return ""
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return s[:10]


def _ensure_col(cursor, table: str, col: str, decl: str) -> None:
    cols = {r[1] for r in cursor.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl};")
        except Exception:
            pass


def parse_gps_dms(text: str) -> Optional[float]:
    """Convierte 34°51'30.75\"S → decimal (negativo para S/O)."""
    s = _safe_str(text)
    if not s:
        return None
    # Ya decimal?
    try:
        if re.fullmatch(r"-?\d+[.,]\d+", s.replace(" ", "")):
            return float(s.replace(",", "."))
    except ValueError:
        pass
    m = re.search(
        r"(\d+)\s*[°º]\s*(\d+)\s*['′]\s*([\d.,]+)\s*[\"″]?\s*([NnSsEeOoWw])?",
        s,
    )
    if not m:
        return None
    deg = float(m.group(1))
    minutes = float(m.group(2))
    seconds = float(m.group(3).replace(",", "."))
    hemi = (m.group(4) or "").upper()
    val = deg + minutes / 60.0 + seconds / 3600.0
    if hemi in ("S", "O", "W"):
        val = -val
    return val


def init_import_schema(cursor) -> None:
    init_agro_schema(cursor)
    init_almacen_schema(cursor)

    for col, decl in (
        ("titularidad", "TEXT"),
        ("titular", "TEXT"),
        ("localidad", "TEXT"),
        ("partido", "TEXT"),
        ("provincia", "TEXT"),
        ("gps_texto", "TEXT"),
        ("id_access", "INTEGER"),
    ):
        _ensure_col(cursor, "campos_agro", col, decl)

    for col, decl in (
        ("cultivo_actual", "TEXT"),
        ("hibrido_variedad", "TEXT"),
        ("titular", "TEXT"),
        ("titularidad", "TEXT"),
        ("localidad", "TEXT"),
        ("gps_texto", "TEXT"),
        ("id_access", "INTEGER"),
    ):
        _ensure_col(cursor, "lotes_agro", col, decl)

    for col, decl in (
        ("presentacion", "TEXT"),
        ("id_access", "INTEGER"),
        ("detalle", "TEXT"),
        ("categoria_codigo", "TEXT"),
        ("clasificacion_manual", "INTEGER DEFAULT 0"),
    ):
        _ensure_col(cursor, "almacen_items", col, decl)

    _ensure_col(cursor, "almacen_movimientos", "id_access", "INTEGER")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS catalogo_granos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            nombre TEXT NOT NULL,
            UNIQUE(empresa_id, nombre)
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS catalogo_tipo_insumos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS catalogo_laboreos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            grupo TEXT DEFAULT 'laboreo',
            UNIQUE(nombre, grupo)
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS catalogo_modalidades_alquiler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS alquileres_cta_cte (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            id_access INTEGER,
            empresa_nombre TEXT,
            campania_codigo TEXT,
            locador TEXT,
            fecha_pago TEXT,
            grano TEXT,
            fecha_pizarra TEXT,
            precio_pizarra REAL DEFAULT 0,
            debe REAL DEFAULT 0,
            haber REAL DEFAULT 0,
            varios TEXT,
            importe_total REAL DEFAULT 0,
            usuario TEXT,
            UNIQUE(empresa_id, id_access)
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS contratos_alquileres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            id_access INTEGER,
            empresa_nombre TEXT,
            campania_codigo TEXT,
            fecha_contrato TEXT,
            fecha_finalizacion TEXT,
            locador TEXT,
            hectareas REAL DEFAULT 0,
            grano TEXT,
            tn_por_ha REAL DEFAULT 0,
            tn_totales REAL DEFAULT 0,
            forma_pago TEXT,
            modalidad TEXT,
            porcentaje REAL DEFAULT 0,
            varios TEXT,
            usuario TEXT,
            campo_id INTEGER,
            observaciones TEXT,
            nombre_campo TEXT,
            propietario TEXT,
            ubicacion TEXT,
            partido TEXT,
            localidad TEXT,
            provincia TEXT,
            rubro TEXT DEFAULT 'agricultura',
            kilos_por_ha REAL DEFAULT 0,
            kilos_totales REAL DEFAULT 0,
            cant_cuotas INTEGER DEFAULT 0,
            precio_pizarra REAL DEFAULT 0,
            lat REAL,
            lng REAL,
            UNIQUE(empresa_id, id_access)
        );
        """
    )
    # Migración columnas planificación (bases ya creadas)
    cur_cols = {r[1] for r in cursor.execute("PRAGMA table_info(contratos_alquileres);").fetchall()}
    for col, ddl in [
        ("nombre_campo", "TEXT"),
        ("propietario", "TEXT"),
        ("ubicacion", "TEXT"),
        ("partido", "TEXT"),
        ("localidad", "TEXT"),
        ("provincia", "TEXT"),
        ("rubro", "TEXT DEFAULT 'agricultura'"),
        ("kilos_por_ha", "REAL DEFAULT 0"),
        ("kilos_totales", "REAL DEFAULT 0"),
        ("cant_cuotas", "INTEGER DEFAULT 0"),
        ("precio_pizarra", "REAL DEFAULT 0"),
        ("lat", "REAL"),
        ("lng", "REAL"),
    ]:
        if col not in cur_cols:
            cursor.execute(f"ALTER TABLE contratos_alquileres ADD COLUMN {col} {ddl};")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS contratos_alquileres_cuotas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            contrato_id INTEGER NOT NULL,
            nro_cuota INTEGER NOT NULL,
            fecha_vencimiento TEXT,
            kilos REAL DEFAULT 0,
            porcentaje REAL DEFAULT 0,
            valuacion_ars REAL DEFAULT 0,
            estado TEXT DEFAULT 'Pendiente',
            detalle TEXT,
            FOREIGN KEY(contrato_id) REFERENCES contratos_alquileres(id)
        );
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alq_cuotas_contrato
        ON contratos_alquileres_cuotas(contrato_id, nro_cuota);
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS arca_ip1 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            titularidad TEXT,
            establecimiento TEXT,
            partido TEXT,
            localidad TEXT,
            provincia TEXT,
            lote TEXT,
            cultivo TEXT,
            variedad TEXT,
            superficie REAL DEFAULT 0,
            pct_participacion REAL DEFAULT 0,
            has_silo_chico REAL DEFAULT 0,
            coordenadas TEXT,
            coordenadas2 TEXT,
            campania_codigo TEXT
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS arca_ip2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            titular TEXT,
            titularidad TEXT,
            campo TEXT,
            lote TEXT,
            has REAL DEFAULT 0,
            cultivo TEXT,
            hibrido_variedad TEXT,
            localidad TEXT,
            gps_lat_texto TEXT,
            gps_lng_texto TEXT,
            lat REAL,
            lng REAL,
            campo_id INTEGER,
            lote_id INTEGER
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS margenes_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            id_access INTEGER,
            campania_codigo TEXT,
            cultivo TEXT,
            nro_orden INTEGER,
            campo TEXT,
            lote TEXT,
            cantidad_has REAL DEFAULT 0,
            fecha_orden TEXT,
            laboreo TEXT,
            labor_cultural TEXT,
            contratista TEXT,
            producto TEXT,
            tipo TEXT,
            dosis_ha REAL DEFAULT 0,
            cantidad_total REAL DEFAULT 0,
            fecha_aplicacion TEXT,
            precio REAL DEFAULT 0,
            costo_ars REAL DEFAULT 0,
            tc REAL DEFAULT 0,
            costo_usd REAL DEFAULT 0,
            varios TEXT,
            fecha_compra TEXT,
            nro_remito TEXT,
            proveedor TEXT,
            cantidad_ingresada REAL DEFAULT 0,
            unidad TEXT,
            empresa_nombre TEXT,
            UNIQUE(empresa_id, id_access)
        );
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_margenes_campania
        ON margenes_access(empresa_id, campania_codigo);
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bienes_patrimoniales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            id_access INTEGER,
            tipo_rodado TEXT,
            nro_factura TEXT,
            marca TEXT,
            detalle TEXT,
            fecha_compra TEXT,
            modelo TEXT,
            anio TEXT,
            marca_chasis TEXT,
            nro_chasis TEXT,
            marca_motor TEXT,
            nro_motor TEXT,
            nro_patente TEXT,
            importe_neto REAL DEFAULT 0,
            iva REAL DEFAULT 0,
            otros_impuestos REAL DEFAULT 0,
            total REAL DEFAULT 0,
            porcentaje_iva REAL DEFAULT 0,
            total_usd REAL DEFAULT 0,
            fecha_baja TEXT,
            valor_residual REAL DEFAULT 0,
            comprador TEXT,
            UNIQUE(empresa_id, id_access)
        );
        """
    )


def _find_sheet(xl: pd.ExcelFile, *needles: str) -> Optional[str]:
    names = xl.sheet_names
    for n in names:
        low = n.strip().lower()
        if all(nd.lower() in low for nd in needles):
            return n
    for n in names:
        low = n.strip().lower()
        if any(nd.lower() in low for nd in needles):
            return n
    return None


def _read(xl: pd.ExcelFile, sheet: Optional[str]) -> pd.DataFrame:
    if not sheet:
        return pd.DataFrame()
    return pd.read_excel(xl, sheet_name=sheet)


def _upsert_campo(
    cur,
    empresa_id: int,
    nombre: str,
    titularidad: str,
    titular: str,
    localidad: str,
    lat: Optional[float],
    lng: Optional[float],
    gps_texto: str,
) -> int:
    nombre = nombre.strip()
    cur.execute(
        """
        SELECT id FROM campos_agro
        WHERE empresa_id = ? AND UPPER(TRIM(nombre)) = UPPER(TRIM(?))
        LIMIT 1;
        """,
        (empresa_id, nombre),
    )
    row = cur.fetchone()
    tipo = "propio" if "propio" in (titularidad or "").lower() or "silo" in (titularidad or "").lower() else "arrendado"
    if "tercer" in (titularidad or "").lower():
        tipo = "arrendado"
    if row:
        cid = int(row["id"])
        cur.execute(
            """
            UPDATE campos_agro SET
                titularidad=?, titular=?, localidad=?,
                lat=COALESCE(?, lat), lng=COALESCE(?, lng),
                gps_texto=COALESCE(NULLIF(?,''), gps_texto),
                tipo=COALESCE(NULLIF(tipo,''), ?),
                arrendador_razon=COALESCE(NULLIF(arrendador_razon,''), ?)
            WHERE id=?;
            """,
            (
                titularidad,
                titular,
                localidad,
                lat,
                lng,
                gps_texto,
                tipo,
                titular if tipo == "arrendado" else "",
                cid,
            ),
        )
        return cid
    cur.execute(
        """
        INSERT INTO campos_agro (
            empresa_id, tipo, nombre, superficie_total, arrendador_razon,
            lat, lng, localidad, titularidad, titular, gps_texto, fecha_alta
        ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            empresa_id,
            tipo,
            nombre,
            titular if tipo == "arrendado" else "",
            lat,
            lng,
            localidad,
            titularidad,
            titular,
            gps_texto,
            datetime.now().strftime("%Y-%m-%d"),
        ),
    )
    return int(cur.lastrowid)


def _upsert_lote(
    cur,
    campo_id: int,
    nombre: str,
    has: float,
    cultivo: str,
    hibrido: str,
    titular: str,
    titularidad: str,
    localidad: str,
    lat: Optional[float],
    lng: Optional[float],
    gps_texto: str,
) -> int:
    nombre = nombre.strip() or "Lote"
    cur.execute(
        """
        SELECT id FROM lotes_agro
        WHERE campo_id = ? AND UPPER(TRIM(nombre)) = UPPER(TRIM(?))
        LIMIT 1;
        """,
        (campo_id, nombre),
    )
    row = cur.fetchone()
    if row:
        lid = int(row["id"])
        cur.execute(
            """
            UPDATE lotes_agro SET
                superficie_base=CASE WHEN ? > 0 THEN ? ELSE superficie_base END,
                cultivo_actual=?, hibrido_variedad=?, titular=?, titularidad=?,
                localidad=?, lat=COALESCE(?, lat), lng=COALESCE(?, lng), gps_texto=?
            WHERE id=?;
            """,
            (has, has, cultivo, hibrido, titular, titularidad, localidad, lat, lng, gps_texto, lid),
        )
        return lid
    cur.execute(
        """
        INSERT INTO lotes_agro (
            campo_id, codigo, nombre, superficie_base, lat, lng,
            cultivo_actual, hibrido_variedad, titular, titularidad, localidad, gps_texto
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            campo_id,
            nombre[:20],
            nombre,
            has,
            lat,
            lng,
            cultivo,
            hibrido,
            titular,
            titularidad,
            localidad,
            gps_texto,
        ),
    )
    return int(cur.lastrowid)


def importar_catalogos(cur, xl: pd.ExcelFile, empresa_id: int) -> Dict[str, int]:
    out = {}
    # Granos
    sh = _find_sheet(xl, "grano")
    df = _read(xl, sh)
    n = 0
    for _, row in df.iterrows():
        nombre = _safe_str(row.get("Nombre"))
        if not nombre and len(row):
            nombre = _safe_str(row.iloc[0])
        if not nombre:
            continue
        cur.execute(
            "INSERT OR IGNORE INTO catalogo_granos (empresa_id, nombre) VALUES (?, ?);",
            (empresa_id, nombre),
        )
        n += 1
    out["granos"] = n

    sh = _find_sheet(xl, "tipo", "insumo")
    df = _read(xl, sh)
    n = 0
    for _, row in df.iterrows():
        nombre = _safe_str(row.get("TIPO"))
        if not nombre and len(row):
            nombre = _safe_str(row.iloc[0])
        if not nombre:
            continue
        cur.execute("INSERT OR IGNORE INTO catalogo_tipo_insumos (nombre) VALUES (?);", (nombre,))
        n += 1
    out["tipo_insumos"] = n

    sh = _find_sheet(xl, "actividades", "laboreo")
    df = _read(xl, sh)
    n = 0
    for _, row in df.iterrows():
        nombre = _safe_str(row.iloc[0] if len(row) else "")
        if not nombre:
            continue
        cur.execute(
            "INSERT OR IGNORE INTO catalogo_laboreos (nombre, grupo) VALUES (?, 'laboreo');",
            (nombre,),
        )
        n += 1
    out["laboreos"] = n

    sh = _find_sheet(xl, "labor", "cultural")
    df = _read(xl, sh)
    n = 0
    for _, row in df.iterrows():
        nombre = _safe_str(row.iloc[0] if len(row) else "")
        if not nombre:
            continue
        cur.execute(
            "INSERT OR IGNORE INTO catalogo_laboreos (nombre, grupo) VALUES (?, 'labor_cultural');",
            (nombre,),
        )
        n += 1
    out["labor_cultural"] = n

    sh = _find_sheet(xl, "modalidad")
    df = _read(xl, sh)
    n = 0
    for _, row in df.iterrows():
        nombre = _safe_str(row.iloc[0] if len(row) else "")
        if not nombre:
            continue
        cur.execute(
            "INSERT OR IGNORE INTO catalogo_modalidades_alquiler (nombre) VALUES (?);",
            (nombre,),
        )
        n += 1
    out["modalidades"] = n
    return out


def importar_campos_ip2(cur, xl: pd.ExcelFile, empresa_id: int) -> Dict[str, int]:
    sh = _find_sheet(xl, "campos", "lotes") or _find_sheet(xl, "IP2")
    df = _read(xl, sh)
    if df.empty:
        return {"campos": 0, "lotes": 0, "ip2": 0}

    # Limpiar snapshot IP2 de esta empresa y rearmar
    cur.execute("DELETE FROM arca_ip2 WHERE empresa_id = ?;", (empresa_id,))
    campos = set()
    lotes = 0
    campania = normalizar_codigo_campania("25-26")
    campania_id = asegurar_campania_activa(cur, empresa_id, campania)

    for _, row in df.iterrows():
        campo = _safe_str(row.get("campo"))
        lote = _safe_str(row.get("lote"))
        if not campo:
            continue
        titular = _safe_str(row.get("Titular"))
        titularidad = _safe_str(row.get("Titularidad"))
        has = _safe_float(row.get("has"))
        cultivo = normalizar_cultivo(_safe_str(row.get("cultivo 25 26")))
        hibrido = _safe_str(row.get("hibrido/variedad"))
        localidad = _safe_str(row.get("localidad"))
        gps_lat_t = _safe_str(row.get("gps"))
        # longitud suele estar en columna sin nombre
        gps_lng_t = ""
        for c in df.columns:
            if str(c).startswith("Unnamed"):
                gps_lng_t = _safe_str(row.get(c))
                if gps_lng_t:
                    break
        lat = parse_gps_dms(gps_lat_t)
        lng = parse_gps_dms(gps_lng_t)
        gps_texto = " ".join(x for x in (gps_lat_t, gps_lng_t) if x)

        cid = _upsert_campo(
            cur, empresa_id, campo, titularidad, titular, localidad, lat, lng, gps_texto
        )
        campos.add(cid)
        lid = _upsert_lote(
            cur,
            cid,
            lote or "General",
            has,
            cultivo,
            hibrido,
            titular,
            titularidad,
            localidad,
            lat,
            lng,
            gps_texto,
        )
        lotes += 1
        if has > 0:
            cur.execute(
                """
                INSERT INTO lote_superficie_campania (lote_id, campania_id, superficie)
                VALUES (?, ?, ?)
                ON CONFLICT(lote_id, campania_id) DO UPDATE SET superficie=excluded.superficie;
                """,
                (lid, campania_id, has),
            )
        if cultivo:
            cur.execute(
                """
                INSERT INTO planificacion_lote (
                    campania_id, lote_id, cultivo_planificado, superficie, modificado_manual
                ) VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(campania_id, lote_id) DO UPDATE SET
                    cultivo_planificado=excluded.cultivo_planificado,
                    superficie=excluded.superficie;
                """,
                (campania_id, lid, cultivo, has),
            )
        cur.execute(
            """
            INSERT INTO arca_ip2 (
                empresa_id, titular, titularidad, campo, lote, has, cultivo,
                hibrido_variedad, localidad, gps_lat_texto, gps_lng_texto, lat, lng,
                campo_id, lote_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                empresa_id,
                titular,
                titularidad,
                campo,
                lote,
                has,
                cultivo,
                hibrido,
                localidad,
                gps_lat_t,
                gps_lng_t,
                lat,
                lng,
                cid,
                lid,
            ),
        )

    # Recalcular superficie campos
    for cid in campos:
        cur.execute(
            """
            UPDATE campos_agro SET superficie_total = (
                SELECT COALESCE(SUM(superficie_base),0) FROM lotes_agro
                WHERE campo_id = ? AND COALESCE(baja,0)=0
            ) WHERE id = ?;
            """,
            (cid, cid),
        )
    return {"campos": len(campos), "lotes": lotes, "ip2": lotes}


def importar_arca_ip1(cur, xl: pd.ExcelFile, empresa_id: int) -> int:
    sh = _find_sheet(xl, "IP", "1") or _find_sheet(xl, "Arca")
    df = _read(xl, sh)
    if df.empty:
        return 0
    cur.execute("DELETE FROM arca_ip1 WHERE empresa_id = ?;", (empresa_id,))
    n = 0
    for _, row in df.iterrows():
        est = _safe_str(row.get("Establecim.") or row.get("Establecimiento"))
        if not est and not _safe_str(row.get("Lote")):
            continue
        coords = _safe_str(row.get("Coordenadas"))
        coords2 = ""
        for c in df.columns:
            if str(c).startswith("Unnamed"):
                coords2 = _safe_str(row.get(c))
                if coords2:
                    break
        cur.execute(
            """
            INSERT INTO arca_ip1 (
                empresa_id, titularidad, establecimiento, partido, localidad, provincia,
                lote, cultivo, variedad, superficie, pct_participacion, has_silo_chico,
                coordenadas, coordenadas2, campania_codigo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                empresa_id,
                _safe_str(row.get("Titularidad")),
                est,
                _safe_str(row.get("Partido")),
                _safe_str(row.get("Localidad")),
                _safe_str(row.get("Pcia.")),
                _safe_str(row.get("Lote")),
                _safe_str(row.get("Cultivo")),
                _safe_str(row.get("Variedad")),
                _safe_float(row.get("Sup.")),
                _safe_float(row.get("% Part.")),
                _safe_float(row.get("Has Sembradas SILO CHICO SA")),
                coords,
                coords2,
                "25-26",
            ),
        )
        n += 1
    return n


def importar_alquileres(cur, xl: pd.ExcelFile, empresa_id: int) -> int:
    sh = _find_sheet(xl, "Alquileres", "Cta")
    df = _read(xl, sh)
    if df.empty:
        return 0
    n = 0
    for _, row in df.iterrows():
        ida = int(_safe_float(row.get("Id")))
        if not ida:
            continue
        camp = normalizar_codigo_campania(_safe_str(row.get("Campaña")))
        if camp:
            asegurar_campania_activa(cur, empresa_id, camp)
        cur.execute(
            """
            INSERT INTO alquileres_cta_cte (
                empresa_id, id_access, empresa_nombre, campania_codigo, locador,
                fecha_pago, grano, fecha_pizarra, precio_pizarra, debe, haber,
                varios, importe_total, usuario
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(empresa_id, id_access) DO UPDATE SET
                empresa_nombre=excluded.empresa_nombre,
                campania_codigo=excluded.campania_codigo,
                locador=excluded.locador,
                fecha_pago=excluded.fecha_pago,
                grano=excluded.grano,
                fecha_pizarra=excluded.fecha_pizarra,
                precio_pizarra=excluded.precio_pizarra,
                debe=excluded.debe,
                haber=excluded.haber,
                varios=excluded.varios,
                importe_total=excluded.importe_total,
                usuario=excluded.usuario;
            """,
            (
                empresa_id,
                ida,
                _safe_str(row.get("Empresa")),
                camp,
                _safe_str(row.get("Locador")),
                _safe_date(row.get("Fecha Pago")),
                _safe_str(row.get("Grano")),
                _safe_date(row.get("Fecha Pizarra")),
                _safe_float(row.get("Precio Pizarra")),
                _safe_float(row.get("DEBE")),
                _safe_float(row.get("HABER")),
                _safe_str(row.get("Varios")),
                _safe_float(row.get("Importe Total")),
                _safe_str(row.get("Usuario")),
            ),
        )
        n += 1
    return n


def _normalizar_modalidad_alquiler(raw: str) -> str:
    s = (raw or "").strip().lower()
    if "aparcer" in s:
        return "Aparceria"
    if "moneda" in s or "monto" in s or "fijo $" in s:
        return "Alquiler Moneda"
    if "kg" in s or "kilo" in s or "tn" in s:
        return "Alquiler Kgs"
    return raw.strip() or "Alquiler Kgs"


def importar_contratos_alquileres(cur, xl: pd.ExcelFile, empresa_id: int) -> int:
    """
    Solapa 'Contratos Alquileres': cabecera de cada contrato por campaña.
    En Access, 'kgs x Has' / 'Kgs Totales' suelen ser toneladas (tn/ha y tn).
    La superficie (hectáreas) es la base para planificar Márgenes Brutos.
    """
    sh = _find_sheet(xl, "Contratos", "Alquiler") or _find_sheet(xl, "Contratos Alquileres")
    df = _read(xl, sh)
    if df.empty:
        return 0
    n = 0
    for _, row in df.iterrows():
        ida = int(_safe_float(row.get("Id")))
        if not ida:
            continue
        camp = normalizar_codigo_campania(_safe_str(row.get("Campaña")))
        if camp:
            asegurar_campania_activa(cur, empresa_id, camp)
        locador = _safe_str(row.get("Locador"))
        has_tot = _safe_float(row.get("Hectareas Totales"))
        tn_ha = _safe_float(row.get("kgs x Has") or row.get("Kgs x Has") or row.get("tn x Has"))
        tn_tot = _safe_float(row.get("Kgs Totales") or row.get("Tn Totales"))
        if tn_tot <= 0 and has_tot > 0 and tn_ha > 0:
            tn_tot = round(has_tot * tn_ha, 4)
        modalidad = _normalizar_modalidad_alquiler(_safe_str(row.get("Modalidad")))
        pct = _safe_float(row.get("Porcentaje"))

        # Intentar vincular a campo arrendado por razón social del arrendador
        campo_id = None
        if locador:
            cur.execute(
                """
                SELECT id FROM campos_agro
                WHERE empresa_id=? AND tipo='arrendado'
                  AND (
                    UPPER(TRIM(COALESCE(arrendador_razon,''))) = UPPER(TRIM(?))
                    OR UPPER(TRIM(COALESCE(nombre,''))) = UPPER(TRIM(?))
                  )
                LIMIT 1;
                """,
                (empresa_id, locador, locador),
            )
            row_c = cur.fetchone()
            if row_c:
                campo_id = int(row_c["id"])

        cur.execute(
            """
            INSERT INTO contratos_alquileres (
                empresa_id, id_access, empresa_nombre, campania_codigo,
                fecha_contrato, fecha_finalizacion, locador, hectareas, grano,
                tn_por_ha, tn_totales, forma_pago, modalidad, porcentaje,
                varios, usuario, campo_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(empresa_id, id_access) DO UPDATE SET
                empresa_nombre=excluded.empresa_nombre,
                campania_codigo=excluded.campania_codigo,
                fecha_contrato=excluded.fecha_contrato,
                fecha_finalizacion=excluded.fecha_finalizacion,
                locador=excluded.locador,
                hectareas=excluded.hectareas,
                grano=excluded.grano,
                tn_por_ha=excluded.tn_por_ha,
                tn_totales=excluded.tn_totales,
                forma_pago=excluded.forma_pago,
                modalidad=excluded.modalidad,
                porcentaje=excluded.porcentaje,
                varios=excluded.varios,
                usuario=excluded.usuario,
                campo_id=COALESCE(excluded.campo_id, contratos_alquileres.campo_id);
            """,
            (
                empresa_id,
                ida,
                _safe_str(row.get("Empresa")),
                camp,
                _safe_date(row.get("Fecha Contrato")),
                _safe_date(row.get("Fecha Finalizacion")),
                locador,
                has_tot,
                _safe_str(row.get("Grano")),
                tn_ha,
                tn_tot,
                _safe_str(row.get("Forma de Pago")),
                modalidad,
                pct,
                _safe_str(row.get("Varios")),
                _safe_str(row.get("Usuario")),
                campo_id,
            ),
        )
        n += 1
    return n


def importar_productos(cur, xl: pd.ExcelFile, empresa_id: int) -> int:
    sh = _find_sheet(xl, "producto")
    df = _read(xl, sh)
    if df.empty:
        return 0
    n = 0
    for _, row in df.iterrows():
        nombre = _safe_str(row.get("NOMBRE PRODUCTO"))
        if not nombre:
            continue
        ida = int(_safe_float(row.get("Id"))) or None
        presentacion = _safe_str(row.get("Presentacion"))
        tipo_xls = _safe_str(row.get("Tipo")) or "Insumo"
        detalle = _safe_str(row.get("Detalle"))
        es_laboreo = tipo_xls.strip().lower() in (
            "servicio", "servicios", "laboreo", "laboreos", "labor"
        )
        tipo_item = "laboreo" if es_laboreo else "producto"
        unidad = "Has" if es_laboreo else "Un"
        pl = presentacion.lower()
        if not es_laboreo:
            if "lt" in pl:
                unidad = "Lt"
            elif "kg" in pl:
                unidad = "Kg"
            elif "ha" in pl:
                unidad = "Has"
        codigo = f"P{ida}" if ida else None
        cat_cod = "laboreos" if es_laboreo else None
        cat_nombre = "Laboreos / Servicios" if es_laboreo else tipo_xls
        if ida:
            cur.execute(
                "SELECT id, COALESCE(clasificacion_manual,0) AS clasificacion_manual FROM almacen_items WHERE empresa_id=? AND id_access=?;",
                (empresa_id, ida),
            )
            existing = cur.fetchone()
        else:
            cur.execute(
                """
                SELECT id, COALESCE(clasificacion_manual,0) AS clasificacion_manual FROM almacen_items
                WHERE empresa_id=? AND UPPER(TRIM(nombre))=UPPER(TRIM(?))
                LIMIT 1;
                """,
                (empresa_id, nombre),
            )
            existing = cur.fetchone()
        if existing:
            manual = int(existing["clasificacion_manual"] or 0)
            if manual:
                cur.execute(
                    """
                    UPDATE almacen_items SET
                        nombre=?, presentacion=?, detalle=?,
                        codigo=COALESCE(codigo, ?), activo=1
                    WHERE id=?;
                    """,
                    (nombre, presentacion, detalle, codigo, int(existing["id"])),
                )
            else:
                cur.execute(
                    """
                    UPDATE almacen_items SET
                        nombre=?, categoria=?, categoria_codigo=COALESCE(?, categoria_codigo),
                        unidad=?, presentacion=?, detalle=?,
                        codigo=COALESCE(codigo, ?), activo=1, tipo=?
                    WHERE id=?;
                    """,
                    (
                        nombre, cat_nombre, cat_cod, unidad, presentacion, detalle,
                        codigo, tipo_item, int(existing["id"]),
                    ),
                )
        else:
            cur.execute(
                """
                INSERT INTO almacen_items (
                    empresa_id, tipo, codigo, nombre, categoria, categoria_codigo, unidad,
                    stock_cantidad, costo_promedio_neto, activo, presentacion, id_access, detalle
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 1, ?, ?, ?);
                """,
                (
                    empresa_id, tipo_item, codigo, nombre, cat_nombre, cat_cod or "",
                    unidad, presentacion, ida, detalle,
                ),
            )
        n += 1
    return n


def _es_laboreo_xls(tipo_val: str) -> bool:
    t = (tipo_val or "").strip().lower()
    return t in ("laboreo", "laboreos", "servicio", "servicios", "labor")


def importar_stock(cur, xl: pd.ExcelFile, empresa_id: int) -> Dict[str, int]:
    """
    Importa 'Stock Almacenes'. Access exporta STOCK siempre en 0:
    el saldo real se recalcula por producto = Σ ENTRADA − Σ SALIDA (cronológico).

    Columna Tipo=Laboreos: ENTRADA = hectáreas trabajadas, VALOR UNITARIO = $ neto/ha.
    No pisa tipo/categoría si el ítem tiene clasificacion_manual=1.
    """
    sh = _find_sheet(xl, "Stock Almacenes", "Stock")
    df = _read(xl, sh)
    if df.empty:
        return {"movimientos": 0, "items_stock": 0, "items_con_stock": 0}

    cur.execute("PRAGMA table_info(almacen_items);")
    cols = {r[1] for r in cur.fetchall()}
    if "clasificacion_manual" not in cols:
        cur.execute("ALTER TABLE almacen_items ADD COLUMN clasificacion_manual INTEGER DEFAULT 0;")
    if "categoria_codigo" not in cols:
        cur.execute("ALTER TABLE almacen_items ADD COLUMN categoria_codigo TEXT;")

    cur.execute(
        """
        SELECT id, nombre, id_access, COALESCE(clasificacion_manual,0) AS clasificacion_manual
        FROM almacen_items WHERE empresa_id=?;
        """,
        (empresa_id,),
    )
    by_name: Dict[str, int] = {}
    manual_ids = set()
    for r in cur.fetchall():
        by_name[_safe_str(r["nombre"]).upper()] = int(r["id"])
        if int(r["clasificacion_manual"] or 0):
            manual_ids.add(int(r["id"]))

    # Orden cronológico para saldo corrido
    rows = []
    for _, row in df.iterrows():
        ida = int(_safe_float(row.get("ID"))) or 0
        producto = _safe_str(row.get("PRODUCTO") or row.get("PRODUCTO1"))
        if not producto and not ida:
            continue
        fecha = _safe_date(row.get("FECHA")) or "1900-01-01"
        rows.append((fecha, ida, row, producto))
    rows.sort(key=lambda x: (x[0], x[1]))

    n_mov = 0
    n_upd = 0
    n_lab = 0
    running: Dict[int, float] = {}
    costo_prom: Dict[int, float] = {}
    costo_prom_usd: Dict[int, float] = {}
    stock_last: Dict[int, Tuple[float, float, float]] = {}

    for fecha, ida, row, producto in rows:
        key = producto.upper()
        item_id = by_name.get(key)
        es_lab = _es_laboreo_xls(_safe_str(row.get("Tipo")))
        if not item_id:
            unidad = "Has" if es_lab else (_safe_str(row.get("UNIDAD") or row.get("UNIDAD1")) or "Un")
            tipo_item = "laboreo" if es_lab else "producto"
            cat_cod = "laboreos" if es_lab else ""
            cat_nom = "Laboreos / Servicios" if es_lab else "Insumo"
            cur.execute(
                """
                INSERT INTO almacen_items (
                    empresa_id, tipo, nombre, categoria, categoria_codigo, unidad,
                    stock_cantidad, costo_promedio_neto, activo
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 1);
                """,
                (empresa_id, tipo_item, producto, cat_nom, cat_cod, unidad),
            )
            item_id = int(cur.lastrowid)
            by_name[key] = item_id
            if es_lab:
                n_lab += 1
        elif es_lab and item_id not in manual_ids:
            cur.execute(
                """
                UPDATE almacen_items SET
                    tipo='laboreo',
                    unidad=CASE WHEN COALESCE(unidad,'') IN ('','Un','Unidad','UND','und') THEN 'Has' ELSE unidad END,
                    categoria_codigo='laboreos',
                    categoria='Laboreos / Servicios'
                WHERE id=?;
                """,
                (item_id,),
            )
            n_lab += 1

        entrada = _safe_float(row.get("ENTRADA"))
        salida = _safe_float(row.get("SALIDA"))
        stock_xls = _safe_float(row.get("STOCK"))
        vu = _safe_float(row.get("VALOR UNITARIO"))
        vu_usd = _safe_float(row.get("VALOR UNITARIO U$S"))
        camp = normalizar_codigo_campania(_safe_str(row.get("CAMPAÑA")))
        campania_id = asegurar_campania_activa(cur, empresa_id, camp) if camp else None

        tipo_mov = "ingreso" if entrada > 0 else ("egreso_ot" if salida > 0 else "ajuste")
        cantidad = entrada if entrada > 0 else (-salida if salida > 0 else 0)
        if cantidad == 0 and stock_xls == 0 and not ida:
            continue

        prev = running.get(item_id, 0.0)
        saldo = prev + cantidad
        # Si Access trae STOCK > 0, priorizarlo; si no, usar saldo corrido
        if abs(stock_xls) > 1e-9:
            saldo = stock_xls
        running[item_id] = saldo

        # Costo promedio ponderado en ingresos
        avg = costo_prom.get(item_id, 0.0)
        if entrada > 0 and vu > 0:
            if prev > 0 and avg > 0:
                avg = (prev * avg + entrada * vu) / (prev + entrada) if (prev + entrada) > 0 else vu
            else:
                avg = vu
            costo_prom[item_id] = avg
        elif vu > 0 and item_id not in costo_prom:
            costo_prom[item_id] = vu

        if entrada > 0 and vu_usd > 0:
            avg_usd = costo_prom_usd.get(item_id, 0.0)
            if prev > 0 and avg_usd > 0:
                avg_usd = (prev * avg_usd + entrada * vu_usd) / (prev + entrada) if (prev + entrada) > 0 else vu_usd
            else:
                avg_usd = vu_usd
            costo_prom_usd[item_id] = avg_usd
        elif vu_usd > 0 and item_id not in costo_prom_usd:
            costo_prom_usd[item_id] = vu_usd

        avg_out = costo_prom.get(item_id, vu)
        avg_usd_out = costo_prom_usd.get(item_id, 0.0)

        mov_id = None
        if ida:
            cur.execute(
                "SELECT id FROM almacen_movimientos WHERE empresa_id=? AND id_access=?;",
                (empresa_id, ida),
            )
            ex = cur.fetchone()
            if ex:
                mov_id = int(ex["id"])
                cur.execute(
                    """
                    UPDATE almacen_movimientos SET
                        fecha=?, tipo_mov=?, cantidad=?, precio_unitario_neto=?, precio_unitario_usd=?,
                        importe_neto=?, stock_resultante=?, costo_prom_resultante=?,
                        proveedor_nombre=?, nro_comprobante=?, campania_id=?, observaciones=?
                    WHERE id=?;
                    """,
                    (
                        fecha if fecha != "1900-01-01" else datetime.now().strftime("%Y-%m-%d"),
                        tipo_mov,
                        cantidad,
                        vu,
                        vu_usd,
                        abs(cantidad) * vu,
                        saldo,
                        avg_out,
                        _safe_str(row.get("PROVEEDOR")),
                        _safe_str(row.get("Factura Nº")),
                        campania_id,
                        _safe_str(row.get("ACTIVIDAD")),
                        mov_id,
                    ),
                )
                n_upd += 1

        if mov_id is None:
            cur.execute(
                """
                INSERT INTO almacen_movimientos (
                    empresa_id, item_id, fecha, tipo_mov, cantidad, precio_unitario_neto, precio_unitario_usd,
                    importe_neto, stock_resultante, costo_prom_resultante,
                    proveedor_nombre, nro_comprobante, campania_id, observaciones, id_access
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    empresa_id,
                    item_id,
                    fecha if fecha != "1900-01-01" else datetime.now().strftime("%Y-%m-%d"),
                    tipo_mov,
                    cantidad,
                    vu,
                    vu_usd,
                    abs(cantidad) * vu,
                    saldo,
                    avg_out,
                    _safe_str(row.get("PROVEEDOR")),
                    _safe_str(row.get("Factura Nº")),
                    campania_id,
                    _safe_str(row.get("ACTIVIDAD")),
                    ida or None,
                ),
            )
            n_mov += 1

        stock_last[item_id] = (saldo, avg_out, avg_usd_out)

    cur.execute("PRAGMA table_info(almacen_items);")
    if "costo_promedio_usd" not in {r[1] for r in cur.fetchall()}:
        cur.execute("ALTER TABLE almacen_items ADD COLUMN costo_promedio_usd REAL DEFAULT 0;")

    for item_id, (stock, vu, vu_usd) in stock_last.items():
        cur.execute(
            """
            UPDATE almacen_items SET
                stock_cantidad = ?,
                costo_promedio_neto = CASE WHEN ? > 0 THEN ? ELSE costo_promedio_neto END,
                costo_promedio_usd = CASE WHEN ? > 0 THEN ? ELSE costo_promedio_usd END
            WHERE id = ?;
            """,
            (stock, vu, vu, vu_usd, vu_usd, item_id),
        )

    con_stock = sum(1 for s, _, _u in stock_last.values() if abs(s) > 1e-6)
    return {
        "movimientos": n_mov,
        "movimientos_actualizados": n_upd,
        "items_stock": len(stock_last),
        "items_con_stock": con_stock,
        "laboreos_marcados": n_lab,
    }


def importar_margenes(cur, xl: pd.ExcelFile, empresa_id: int) -> int:
    sh = _find_sheet(xl, "Margenes") or _find_sheet(xl, "Márgenes")
    df = _read(xl, sh)
    if df.empty:
        return 0
    n = 0
    batch: List[tuple] = []
    for _, row in df.iterrows():
        ida = int(_safe_float(row.get("Id")))
        if not ida:
            continue
        camp = normalizar_codigo_campania(_safe_str(row.get("Campaña")))
        if camp:
            asegurar_campania_activa(cur, empresa_id, camp)
        batch.append(
            (
                empresa_id,
                ida,
                camp,
                _safe_str(row.get("CULTIVO")),
                int(_safe_float(row.get("Nro Orden"))) or None,
                _safe_str(row.get("CAMPO")),
                _safe_str(row.get("Lote")),
                _safe_float(row.get("Cantidad Has")),
                _safe_date(row.get("FECHA ORDEN")),
                _safe_str(row.get("LABOREO")),
                _safe_str(row.get("Labor Cultural")),
                _safe_str(row.get("Contratista")),
                _safe_str(row.get("PRODUCTO")),
                _safe_str(row.get("Tipo")),
                _safe_float(row.get("Dosis  x Ha")),
                _safe_float(row.get("Cantidad Utilizada Total")),
                _safe_date(row.get("Fecha Aplicacion")),
                _safe_float(row.get("Precio")),
                _safe_float(row.get("Costo $")),
                _safe_float(row.get("TC")),
                _safe_float(row.get("Costo U$S")),
                _safe_str(row.get("VARIOS")),
                _safe_date(row.get("Fecha compra")),
                _safe_str(row.get("Nro Remito")),
                _safe_str(row.get("Proveedor")),
                _safe_float(row.get("Cantidad Ingresada")),
                _safe_str(row.get("Unidad")),
                _safe_str(row.get("Empresa")),
            )
        )
        if len(batch) >= 500:
            cur.executemany(
                """
                INSERT INTO margenes_access (
                    empresa_id, id_access, campania_codigo, cultivo, nro_orden, campo, lote,
                    cantidad_has, fecha_orden, laboreo, labor_cultural, contratista,
                    producto, tipo, dosis_ha, cantidad_total, fecha_aplicacion,
                    precio, costo_ars, tc, costo_usd, varios,
                    fecha_compra, nro_remito, proveedor, cantidad_ingresada, unidad, empresa_nombre
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(empresa_id, id_access) DO UPDATE SET
                    campania_codigo=excluded.campania_codigo,
                    cultivo=excluded.cultivo,
                    nro_orden=excluded.nro_orden,
                    campo=excluded.campo,
                    lote=excluded.lote,
                    cantidad_has=excluded.cantidad_has,
                    fecha_orden=excluded.fecha_orden,
                    laboreo=excluded.laboreo,
                    labor_cultural=excluded.labor_cultural,
                    contratista=excluded.contratista,
                    producto=excluded.producto,
                    tipo=excluded.tipo,
                    dosis_ha=excluded.dosis_ha,
                    cantidad_total=excluded.cantidad_total,
                    fecha_aplicacion=excluded.fecha_aplicacion,
                    precio=excluded.precio,
                    costo_ars=excluded.costo_ars,
                    tc=excluded.tc,
                    costo_usd=excluded.costo_usd,
                    varios=excluded.varios,
                    fecha_compra=excluded.fecha_compra,
                    nro_remito=excluded.nro_remito,
                    proveedor=excluded.proveedor,
                    cantidad_ingresada=excluded.cantidad_ingresada,
                    unidad=excluded.unidad,
                    empresa_nombre=excluded.empresa_nombre;
                """,
                batch,
            )
            n += len(batch)
            batch = []
    if batch:
        cur.executemany(
            """
            INSERT INTO margenes_access (
                empresa_id, id_access, campania_codigo, cultivo, nro_orden, campo, lote,
                cantidad_has, fecha_orden, laboreo, labor_cultural, contratista,
                producto, tipo, dosis_ha, cantidad_total, fecha_aplicacion,
                precio, costo_ars, tc, costo_usd, varios,
                fecha_compra, nro_remito, proveedor, cantidad_ingresada, unidad, empresa_nombre
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(empresa_id, id_access) DO UPDATE SET
                campania_codigo=excluded.campania_codigo,
                cultivo=excluded.cultivo,
                nro_orden=excluded.nro_orden,
                campo=excluded.campo,
                lote=excluded.lote,
                cantidad_has=excluded.cantidad_has,
                fecha_orden=excluded.fecha_orden,
                laboreo=excluded.laboreo,
                labor_cultural=excluded.labor_cultural,
                contratista=excluded.contratista,
                producto=excluded.producto,
                tipo=excluded.tipo,
                dosis_ha=excluded.dosis_ha,
                cantidad_total=excluded.cantidad_total,
                fecha_aplicacion=excluded.fecha_aplicacion,
                precio=excluded.precio,
                costo_ars=excluded.costo_ars,
                tc=excluded.tc,
                costo_usd=excluded.costo_usd,
                varios=excluded.varios,
                fecha_compra=excluded.fecha_compra,
                nro_remito=excluded.nro_remito,
                proveedor=excluded.proveedor,
                cantidad_ingresada=excluded.cantidad_ingresada,
                unidad=excluded.unidad,
                empresa_nombre=excluded.empresa_nombre;
            """,
            batch,
        )
        n += len(batch)
    return n


def importar_patrimonial(cur, xl: pd.ExcelFile, empresa_id: int) -> int:
    sh = _find_sheet(xl, "Patrimonial") or _find_sheet(xl, "Rodados")
    df = _read(xl, sh)
    if df.empty:
        return 0
    n = 0
    for _, row in df.iterrows():
        ida = int(_safe_float(row.get("Id")))
        if not ida:
            continue
        cur.execute(
            """
            INSERT INTO bienes_patrimoniales (
                empresa_id, id_access, tipo_rodado, nro_factura, marca, detalle,
                fecha_compra, modelo, anio, marca_chasis, nro_chasis, marca_motor,
                nro_motor, nro_patente, importe_neto, iva, otros_impuestos, total,
                porcentaje_iva, total_usd, fecha_baja, valor_residual, comprador
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(empresa_id, id_access) DO UPDATE SET
                tipo_rodado=excluded.tipo_rodado,
                marca=excluded.marca,
                detalle=excluded.detalle,
                fecha_compra=excluded.fecha_compra,
                total=excluded.total,
                fecha_baja=excluded.fecha_baja,
                valor_residual=excluded.valor_residual;
            """,
            (
                empresa_id,
                ida,
                _safe_str(row.get("Tipo Rodado")),
                _safe_str(row.get("Nro de Factura")),
                _safe_str(row.get("MARCA")),
                _safe_str(row.get("DETALLE")),
                _safe_date(row.get("FECHA COMPRA")),
                _safe_str(row.get("MODELO")),
                _safe_str(row.get("AÑO")),
                _safe_str(row.get("Marca Chasis")),
                _safe_str(row.get("Nº CHASIS")),
                _safe_str(row.get("Marca Motor")),
                _safe_str(row.get("Nª MOTOR")),
                _safe_str(row.get("Nº PATENTE")),
                _safe_float(row.get("IMPORTE NETO")),
                _safe_float(row.get("IVA")),
                _safe_float(row.get("Otros Impuestos")),
                _safe_float(row.get("TOTAL")),
                _safe_float(row.get("PORCENTAJE IVA")),
                _safe_float(row.get("TOTAL U$S")),
                _safe_date(row.get("FECHA BAJA")),
                _safe_float(row.get("VALOR RESIDUAL")),
                _safe_str(row.get("COMPRADOR")),
            ),
        )
        n += 1
    return n


def importar_tablas_agro(empresa_id: int = 1, forzar: bool = False) -> Dict[str, Any]:
    if not os.path.exists(EXCEL_TABLAS):
        return {"status": "error", "message": f"No se encontró {EXCEL_TABLAS}"}

    conn = get_db()
    cur = conn.cursor()
    init_import_schema(cur)
    conn.commit()

    if not forzar:
        cur.execute("SELECT COUNT(*) AS n FROM margenes_access WHERE empresa_id=?;", (empresa_id,))
        if int(cur.fetchone()["n"] or 0) > 1000:
            conn.close()
            return {
                "status": "skip",
                "message": "Ya hay márgenes históricos importados. Usá forzar=true para reimportar.",
            }

    print(f"Importando agro desde {EXCEL_TABLAS} ...")
    xl = pd.ExcelFile(EXCEL_TABLAS)
    result: Dict[str, Any] = {"status": "ok", "archivo": EXCEL_TABLAS, "hojas": xl.sheet_names}

    result["catalogos"] = importar_catalogos(cur, xl, empresa_id)
    conn.commit()
    result["campos_ip2"] = importar_campos_ip2(cur, xl, empresa_id)
    conn.commit()
    result["arca_ip1"] = importar_arca_ip1(cur, xl, empresa_id)
    conn.commit()
    result["alquileres_cc"] = importar_alquileres(cur, xl, empresa_id)
    conn.commit()
    result["contratos_alquileres"] = importar_contratos_alquileres(cur, xl, empresa_id)
    conn.commit()
    result["productos"] = importar_productos(cur, xl, empresa_id)
    conn.commit()
    result["stock"] = importar_stock(cur, xl, empresa_id)
    conn.commit()
    result["margenes"] = importar_margenes(cur, xl, empresa_id)
    conn.commit()
    result["patrimonial"] = importar_patrimonial(cur, xl, empresa_id)
    conn.commit()
    conn.close()
    print("Import agro OK:", result)
    return result


if __name__ == "__main__":
    import json

    print(json.dumps(importar_tablas_agro(1, forzar=True), indent=2, ensure_ascii=False))
