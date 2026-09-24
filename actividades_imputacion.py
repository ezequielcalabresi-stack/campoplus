# -*- coding: utf-8 -*-
"""
Actividades y cuentas de imputación (legado Access ACTIVIDADES).
Usado en ingreso de facturas de gastos.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_IMPUT = os.path.join(BASE_DIR, "tablas", "tablas1xlsx.xlsx")
EXCEL_MAESTRO = os.path.join(
    BASE_DIR,
    "tablas",
    "imputacion de ctas para GESTION de Ingresos y Egresos x actividad.xlsx",
)


def init_actividades_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS actividades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            nombre TEXT NOT NULL,
            activo INTEGER DEFAULT 1,
            orden INTEGER DEFAULT 0
        );
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_actividades_empresa_nombre
        ON actividades(empresa_id, nombre);
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS actividad_cuentas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actividad_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            tipo_cta TEXT DEFAULT 'EGRESO',
            activo INTEGER DEFAULT 1,
            FOREIGN KEY(actividad_id) REFERENCES actividades(id)
        );
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_act_cta_unique
        ON actividad_cuentas(actividad_id, nombre);
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS factura_imputaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            cc_id INTEGER,
            descripcion TEXT,
            destino_tipo TEXT,
            actividad_id INTEGER,
            cuenta_imputacion_id INTEGER,
            actividad_nombre TEXT,
            cuenta_nombre TEXT,
            neto REAL DEFAULT 0,
            alicuota_iva REAL DEFAULT 0,
            iva REAL DEFAULT 0,
            item_almacen_id INTEGER,
            item_nombre TEXT,
            unidad TEXT,
            cantidad REAL DEFAULT 0,
            precio_unitario REAL DEFAULT 0,
            precio_unitario_usd REAL DEFAULT 0,
            tipo_cambio REAL DEFAULT 0,
            campania_codigo TEXT,
            detalle_tipo TEXT
        );
        """
    )
    cols = {r[1] for r in cursor.execute("PRAGMA table_info(factura_imputaciones)")}
    for col, typ in (
        ("item_almacen_id", "INTEGER"),
        ("item_nombre", "TEXT"),
        ("unidad", "TEXT"),
        ("cantidad", "REAL DEFAULT 0"),
        ("precio_unitario", "REAL DEFAULT 0"),
        ("precio_unitario_usd", "REAL DEFAULT 0"),
        ("tipo_cambio", "REAL DEFAULT 0"),
        ("campania_codigo", "TEXT"),
        ("detalle_tipo", "TEXT"),
    ):
        if col not in cols:
            try:
                cursor.execute(f"ALTER TABLE factura_imputaciones ADD COLUMN {col} {typ};")
            except Exception:
                pass


def _pares_desde_maestro() -> List[Tuple[str, str, str]]:
    if not os.path.exists(EXCEL_MAESTRO):
        return []
    try:
        xl = pd.ExcelFile(EXCEL_MAESTRO)
        sheet = next(
            (s for s in xl.sheet_names if "cuenta" in s.lower() and "imput" in s.lower()),
            xl.sheet_names[0],
        )
        df = pd.read_excel(EXCEL_MAESTRO, sheet_name=sheet)
    except Exception:
        return []
    out: List[Tuple[str, str, str]] = []
    seen = set()
    for _, row in df.iterrows():
        act = str(row.get("ACTIVIDAD") or "").strip()
        cta = str(row.get("Cuenta a Imputar") or "").strip()
        tipo = str(row.get("Tipo de Cta") or "EGRESO").strip().upper()
        if not act or not cta or act.lower() == "nan" or cta.lower() == "nan":
            continue
        if tipo not in ("EGRESO", "INGRESO"):
            tipo = "EGRESO"
        key = (act.upper(), cta.upper(), tipo)
        if key in seen:
            continue
        seen.add(key)
        out.append((act, cta, tipo))
    return out


def _pares_desde_excel() -> List[Tuple[str, str, str]]:
    pares = _pares_desde_maestro()
    if pares:
        return pares
    if not os.path.exists(EXCEL_IMPUT):
        return []
    try:
        df = pd.read_excel(EXCEL_IMPUT, sheet_name="Imputaciones")
    except Exception:
        return []
    seen = set()
    out: List[Tuple[str, str, str]] = []
    for _, row in df.iterrows():
        act = str(row.get("Actividad") or "").strip()
        cta = str(row.get("Cta a Imputacion") or "").strip()
        if not act or not cta or act.lower() == "nan" or cta.lower() == "nan":
            continue
        eg = 0.0
        ing = 0.0
        try:
            if pd.notnull(row.get("Egresos")):
                eg = float(row.get("Egresos") or 0)
        except (TypeError, ValueError):
            eg = 0.0
        try:
            if pd.notnull(row.get("INGRESOS")):
                ing = float(row.get("INGRESOS") or 0)
        except (TypeError, ValueError):
            ing = 0.0
        tipo = "INGRESO" if abs(ing) > abs(eg) and abs(ing) > 0.01 else "EGRESO"
        key = (act.upper(), cta.upper(), tipo)
        if key in seen:
            continue
        seen.add(key)
        out.append((act, cta, tipo))
    return out


def resync_actividades_desde_excel(conn, empresa_id: int = 1) -> dict:
    pares = _pares_desde_excel()
    if not pares:
        return {"status": "error", "message": "No se pudo leer el Excel de imputaciones"}

    cur = conn.cursor()
    cur.execute("UPDATE actividades SET activo = 0 WHERE empresa_id = ?;", (empresa_id,))
    cur.execute(
        """
        UPDATE actividad_cuentas SET activo = 0
        WHERE actividad_id IN (SELECT id FROM actividades WHERE empresa_id = ?);
        """,
        (empresa_id,),
    )

    acts_map: Dict[str, List[Tuple[str, str]]] = {}
    for act, cta, tipo in pares:
        acts_map.setdefault(act, []).append((cta, tipo))

    n_act = 0
    n_cta = 0
    for orden, (nombre, ctas) in enumerate(sorted(acts_map.items(), key=lambda x: x[0].upper())):
        cur.execute(
            """
            SELECT id FROM actividades
            WHERE empresa_id = ? AND UPPER(TRIM(nombre)) = UPPER(TRIM(?))
            LIMIT 1;
            """,
            (empresa_id, nombre),
        )
        row = cur.fetchone()
        if row:
            aid = int(row["id"])
            cur.execute(
                "UPDATE actividades SET activo=1, nombre=?, orden=? WHERE id=?;",
                (nombre, orden, aid),
            )
        else:
            cur.execute(
                """
                INSERT INTO actividades (empresa_id, nombre, activo, orden)
                VALUES (?, ?, 1, ?);
                """,
                (empresa_id, nombre, orden),
            )
            aid = int(cur.lastrowid)
        n_act += 1
        for cta_nombre, tipo in ctas:
            cur.execute(
                """
                SELECT id FROM actividad_cuentas
                WHERE actividad_id = ? AND UPPER(TRIM(nombre)) = UPPER(TRIM(?))
                LIMIT 1;
                """,
                (aid, cta_nombre),
            )
            crow = cur.fetchone()
            if crow:
                cur.execute(
                    """
                    UPDATE actividad_cuentas SET activo=1, nombre=?, tipo_cta=? WHERE id=?;
                    """,
                    (cta_nombre, tipo or "EGRESO", crow["id"]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO actividad_cuentas (actividad_id, nombre, tipo_cta, activo)
                    VALUES (?, ?, ?, 1);
                    """,
                    (aid, cta_nombre, tipo or "EGRESO"),
                )
            n_cta += 1
    conn.commit()
    return {"status": "ok", "actividades": n_act, "cuentas": n_cta, "fuente": "maestro"}


_SEED_MINIMO = {
    "ADMINISTRACION": [("Gastos Oficina", "EGRESO")],
    "ALMACENES": [
        ("Agroquimicos", "EGRESO"),
        ("Fertilizantes", "EGRESO"),
        ("Semillas", "EGRESO"),
        ("Silo Bolsas", "EGRESO"),
    ],
    "CAMION": [("Combustibles", "EGRESO"), ("Repuestos", "EGRESO")],
    "ESTRUCTURA": [("Movilidad", "EGRESO")],
}

# Defaults de ingresos (estilo Access IMPUTACION DE CTAS VENTAS)
_SEED_INGRESOS = {
    "Ganaderia": [
        ("Ingreso Vtas Terneros", "INGRESO"),
        ("Ingreso por Vtas Descarte", "INGRESO"),
        ("Ingreso por Vtas Invernada", "INGRESO"),
        ("Ingreso por Vtas de Vaquillonas", "INGRESO"),
    ],
    "Agricultura": [
        ("Ingreso por Vtas de Soja", "INGRESO"),
        ("Ingreso Por Vtas de Maiz", "INGRESO"),
        ("Ingreso Por Vtas de Trigo", "INGRESO"),
        ("Ingreso Por Vtas de Cebada", "INGRESO"),
        ("Ingreso por Vtas de Sorgo", "INGRESO"),
        ("Ingreso Por Vtas de Pisingallo", "INGRESO"),
        ("ingreso por vtas de poroto", "INGRESO"),
        ("Ingreso Por Vtas de Girasol", "INGRESO"),
    ],
}


def ensure_ingresos_default(conn, empresa_id: int = 1) -> dict:
    """Asegura actividades/cuentas de ingreso típicas sin borrar el maestro."""
    cur = conn.cursor()
    creadas_act = 0
    creadas_cta = 0
    for act_nombre, ctas in _SEED_INGRESOS.items():
        cur.execute(
            """
            SELECT id FROM actividades
            WHERE empresa_id = ? AND UPPER(TRIM(nombre)) = UPPER(TRIM(?))
            LIMIT 1;
            """,
            (empresa_id, act_nombre),
        )
        row = cur.fetchone()
        if row:
            aid = int(row["id"] if hasattr(row, "keys") else row[0])
            cur.execute("UPDATE actividades SET activo = 1 WHERE id = ?;", (aid,))
        else:
            cur.execute(
                """
                INSERT INTO actividades (empresa_id, nombre, activo, orden)
                VALUES (?, ?, 1, 0);
                """,
                (empresa_id, act_nombre),
            )
            aid = int(cur.lastrowid)
            creadas_act += 1
        for cta_nombre, tipo in ctas:
            cur.execute(
                """
                SELECT id FROM actividad_cuentas
                WHERE actividad_id = ? AND UPPER(TRIM(nombre)) = UPPER(TRIM(?))
                LIMIT 1;
                """,
                (aid, cta_nombre),
            )
            crow = cur.fetchone()
            if crow:
                cid = int(crow["id"] if hasattr(crow, "keys") else crow[0])
                cur.execute(
                    "UPDATE actividad_cuentas SET activo=1, tipo_cta=? WHERE id=?;",
                    (tipo, cid),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO actividad_cuentas (actividad_id, nombre, tipo_cta, activo)
                    VALUES (?, ?, ?, 1);
                    """,
                    (aid, cta_nombre, tipo),
                )
                creadas_cta += 1
    conn.commit()
    return {"status": "ok", "actividades_nuevas": creadas_act, "cuentas_nuevas": creadas_cta}


def seed_actividades_si_vacio(conn, empresa_id: int = 1) -> dict:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS n FROM actividades WHERE empresa_id = ? AND activo = 1;",
        (empresa_id,),
    )
    if int(cur.fetchone()["n"] or 0) > 0:
        return {"status": "skip", "message": "Ya hay actividades"}
    return resync_actividades_desde_excel(conn, empresa_id)


def listar_actividades(conn, empresa_id: int, solo_activas: bool = True) -> List[dict]:
    cur = conn.cursor()
    sql = """
        SELECT a.*,
               (SELECT COUNT(*) FROM actividad_cuentas c
                WHERE c.actividad_id = a.id AND c.activo = 1) AS n_cuentas
        FROM actividades a
        WHERE a.empresa_id = ?
    """
    if solo_activas:
        sql += " AND a.activo = 1"
    sql += " ORDER BY a.nombre COLLATE NOCASE;"
    cur.execute(sql, (empresa_id,))
    return [dict(r) for r in cur.fetchall()]


def listar_cuentas(conn, actividad_id: int, solo_activas: bool = True, tipo: Optional[str] = None) -> List[dict]:
    cur = conn.cursor()
    where = ["actividad_id = ?"]
    params: list = [actividad_id]
    if solo_activas:
        where.append("activo = 1")
    if tipo:
        where.append("UPPER(tipo_cta) = UPPER(?)")
        params.append(tipo)
    cur.execute(
        f"""
        SELECT * FROM actividad_cuentas
        WHERE {' AND '.join(where)}
        ORDER BY nombre COLLATE NOCASE;
        """,
        params,
    )
    return [dict(r) for r in cur.fetchall()]


def arbol_actividades(conn, empresa_id: int) -> List[dict]:
    acts = listar_actividades(conn, empresa_id, solo_activas=True)
    return [
        {**a, "cuentas": listar_cuentas(conn, int(a["id"]), solo_activas=True)}
        for a in acts
    ]


def crear_actividad(conn, empresa_id: int, nombre: str, orden: int = 0) -> int:
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("Nombre de actividad obligatorio")
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO actividades (empresa_id, nombre, activo, orden)
        VALUES (?, ?, 1, ?);
        """,
        (empresa_id, nombre, orden),
    )
    conn.commit()
    return int(cur.lastrowid)


def actualizar_actividad(conn, empresa_id: int, act_id: int, data: dict) -> None:
    cur = conn.cursor()
    fields, vals = [], []
    for k in ("nombre", "activo", "orden"):
        if k in data and data[k] is not None:
            fields.append(f"{k} = ?")
            vals.append(data[k].strip() if k == "nombre" else data[k])
    if not fields:
        return
    vals.extend([empresa_id, act_id])
    cur.execute(
        f"UPDATE actividades SET {', '.join(fields)} WHERE empresa_id = ? AND id = ?;",
        vals,
    )
    conn.commit()


def crear_cuenta(conn, actividad_id: int, nombre: str, tipo_cta: str = "EGRESO") -> int:
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("Nombre de cuenta obligatorio")
    tipo = (tipo_cta or "EGRESO").strip().upper()
    if tipo not in ("EGRESO", "INGRESO"):
        tipo = "EGRESO"
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO actividad_cuentas (actividad_id, nombre, tipo_cta, activo)
        VALUES (?, ?, ?, 1);
        """,
        (actividad_id, nombre, tipo),
    )
    conn.commit()
    return int(cur.lastrowid)


def actualizar_cuenta(conn, cta_id: int, data: dict) -> None:
    cur = conn.cursor()
    fields, vals = [], []
    for k in ("nombre", "tipo_cta", "activo", "actividad_id"):
        if k in data and data[k] is not None:
            v = data[k]
            if k == "nombre":
                v = str(v).strip()
            if k == "tipo_cta":
                v = str(v).strip().upper()
            fields.append(f"{k} = ?")
            vals.append(v)
    if not fields:
        return
    vals.append(cta_id)
    cur.execute(f"UPDATE actividad_cuentas SET {', '.join(fields)} WHERE id = ?;", vals)
    conn.commit()


def soft_delete_actividad(conn, empresa_id: int, act_id: int) -> None:
    cur = conn.cursor()
    cur.execute(
        "UPDATE actividades SET activo = 0 WHERE empresa_id = ? AND id = ?;",
        (empresa_id, act_id),
    )
    conn.commit()


def soft_delete_cuenta(conn, cta_id: int) -> None:
    cur = conn.cursor()
    cur.execute("UPDATE actividad_cuentas SET activo = 0 WHERE id = ?;", (cta_id,))
    conn.commit()
