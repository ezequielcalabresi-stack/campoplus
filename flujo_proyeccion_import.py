# -*- coding: utf-8 -*-
"""
Importa filas de proyección financiera desde Access (movimientos bancarios.xlsx,
columna Proyeccion = Si) hacia SQLite: flujo_proyeccion_access.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd


def _safe_str(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _safe_float(v) -> float:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return 0.0
        if isinstance(v, str):
            s = v.strip().replace("$", "").replace(" ", "")
            if "," in s and "." in s:
                s = s.replace(".", "").replace(",", ".")
            elif "," in s:
                s = s.replace(",", ".")
            return float(s or 0)
        return float(v)
    except Exception:
        return 0.0


def _safe_date(v) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    try:
        ts = pd.to_datetime(v, errors="coerce")
        if pd.isna(ts):
            return ""
        return ts.strftime("%Y-%m-%d")
    except Exception:
        s = _safe_str(v)
        return s[:10] if len(s) >= 10 else s


def _safe_cuota(v) -> str:
    """Cuota como entero (1, 2, 3) — nunca '1.0'."""
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if abs(float(v)) < 1e-9:
            return ""
        if abs(float(v) - round(float(v))) < 1e-9:
            return str(int(round(float(v))))
        return str(v).rstrip("0").rstrip(".") if "." in str(v) else str(v)
    s = _safe_str(v)
    if not s:
        return ""
    try:
        f = float(s.replace(",", "."))
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
    except Exception:
        pass
    if s.endswith(".0"):
        return s[:-2]
    return s


def init_flujo_proyeccion_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS flujo_proyeccion_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            id_access INTEGER,
            detalle TEXT,
            forma_pago TEXT,
            cta_cte TEXT,
            fecha_debito TEXT,
            fecha_debitado TEXT,
            nro_cuota TEXT,
            tipo_cambio REAL DEFAULT 0,
            plazo TEXT,
            capital REAL DEFAULT 0,
            intereses REAL DEFAULT 0,
            impuestos REAL DEFAULT 0,
            cargos REAL DEFAULT 0,
            subtotal REAL DEFAULT 0,
            moneda TEXT DEFAULT 'ARS',
            capital_usd REAL DEFAULT 0,
            nro_cheque TEXT,
            nro_credito TEXT,
            varios TEXT,
            es_disponibilidad INTEGER DEFAULT 0,
            fuente TEXT DEFAULT 'access_proyeccion'
        );
        """
    )
    cols = {r[1] for r in cursor.execute("PRAGMA table_info(flujo_proyeccion_access);").fetchall()}
    if "fecha_debitado" not in cols:
        cursor.execute("ALTER TABLE flujo_proyeccion_access ADD COLUMN fecha_debitado TEXT;")
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_flujo_proy_fecha
        ON flujo_proyeccion_access(empresa_id, fecha_debito);
        """
    )


def _find_movimientos_xlsx(base_dir: str) -> Optional[str]:
    candidates = [
        os.path.join(base_dir, "tablas", "movimientos bancarios.xlsx"),
        os.path.join(base_dir, "tablas", "bancos y mov.xlsx"),
        os.path.join(base_dir, "movimientos bancarios.xlsx"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def importar_flujo_proyeccion_access(
    cursor, empresa_id: int = 1, xlsx_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Carga filas Proyeccion=Si (+ cheques empresa / extras / sueldos futuros)
    desde el Excel de movimientos bancarios Access.
    """
    init_flujo_proyeccion_schema(cursor)
    base = os.path.dirname(os.path.abspath(__file__))
    path = xlsx_path or _find_movimientos_xlsx(base)
    if not path:
        return {"status": "error", "message": "No se encontró movimientos bancarios.xlsx", "rows": 0}

    xl = pd.ExcelFile(path)
    sheet = None
    for s in xl.sheet_names:
        if "mov" in s.lower():
            sheet = s
            break
    sheet = sheet or xl.sheet_names[0]
    df = pd.read_excel(xl, sheet)

    # Normalizar nombres de columnas frecuentes
    colmap = {c: c for c in df.columns}
    lower = {str(c).strip().lower(): c for c in df.columns}

    def col(*names):
        for n in names:
            if n.lower() in lower:
                return lower[n.lower()]
        return None

    c_id = col("Id", "ID")
    c_prov = col("Proveedor")
    c_haber = col("HABER", "Haber")
    c_debe = col("DEBE", "Debe")
    c_fc = col("FechaCobro", "Fecha Cobro", "FECHA COBRO")
    c_debitado = col("Debitados", "Debitado", "Fecha Debito", "Fecha Débito")
    c_forma = col("Forma de Pago", "Forma de pago")
    c_cta = col("Cta Cte", "CtaCte", "CTA CTE")
    c_chq = col("Nº Cheque", "Nro Cheque", "N° Cheque", "Cheque")
    c_proy = col("Proyeccion", "Proyección")
    c_cuota = col("Nº Cuota", "Nro Cuota", "N° Cuota")
    c_tc = col("TC", "T/C")
    c_plazo = col("Plazo")
    c_int = col("Intereses")
    c_imp = col("Impuestos")
    c_cargos = col("Cargos")
    c_cap = col("Importe Monto origen $", "Capital", "CAPITAL")
    c_cap_usd = col("Capital U$S", "Capital USD")
    c_cred = col("Nro de Credito", "Nro de Crédito", "Nº de Credito")
    c_varios = col("Nota Varios", "Detalle", "Nota")
    c_moneda = col("Moneda")

    n = 0
    n_skip = 0
    n_conciliados = 0
    cursor.execute(
        "DELETE FROM flujo_proyeccion_access WHERE empresa_id=? AND fuente='access_proyeccion';",
        (empresa_id,),
    )
    for _, row in df.iterrows():
        proy = _safe_str(row.get(c_proy) if c_proy else "").lower()
        forma = _safe_str(row.get(c_forma) if c_forma else "")
        prov = _safe_str(row.get(c_prov) if c_prov else "")
        # FechaCobro = vto / fecha de pago (columna que se muestra en el financiero)
        fecha = _safe_date(row.get(c_fc) if c_fc else None)
        # Debitados = conciliado en extracto → NO entra al financiero (ya no es pendiente)
        fecha_debitado = _safe_date(row.get(c_debitado) if c_debitado else None)
        if fecha_debitado:
            n_conciliados += 1
            n_skip += 1
            continue

        haber = _safe_float(row.get(c_haber) if c_haber else 0)
        debe = _safe_float(row.get(c_debe) if c_debe else 0)
        # Access: egresos en HABER (puede ser negativo = cheques en cartera / disponibilidades)
        if abs(haber) >= 0.01:
            subtotal = haber
        elif abs(debe) >= 0.01:
            subtotal = -debe
        else:
            subtotal = 0.0

        es_proy = proy in ("si", "sí", "true", "1", "yes")
        forma_l = forma.lower()
        prov_l = prov.lower()
        es_cheque_emp = "cheques empresa" in forma_l or forma_l == "cheque empresa"
        es_extra = "pago extras" in prov_l or prov_l.startswith("extras")
        es_sueldo = any(k in prov_l for k in ("sueldo", "sindicato", "aguinaldo"))
        es_disp = (
            subtotal < -0.01
            or "cheques en cartera" in prov_l
            or prov_l.startswith("saldo ")
            or prov_l.startswith("caja")
            or "fci " in prov_l
            or prov_l.startswith("fci")
            or "plazo fijo" in prov_l
            or prov_l.startswith("fima")
        )
        if not (es_proy or es_cheque_emp or es_extra or es_sueldo):
            n_skip += 1
            continue
        if not fecha or abs(subtotal) < 0.01:
            n_skip += 1
            continue

        ida = int(_safe_float(row.get(c_id) if c_id else 0)) or None
        capital = _safe_float(row.get(c_cap) if c_cap else 0)
        intereses = _safe_float(row.get(c_int) if c_int else 0)
        impuestos = _safe_float(row.get(c_imp) if c_imp else 0)
        cargos = _safe_float(row.get(c_cargos) if c_cargos else 0)

        cursor.execute(
            """
            INSERT INTO flujo_proyeccion_access (
                empresa_id, id_access, detalle, forma_pago, cta_cte, fecha_debito, fecha_debitado,
                nro_cuota, tipo_cambio, plazo, capital, intereses, impuestos, cargos,
                subtotal, moneda, capital_usd, nro_cheque, nro_credito, varios,
                es_disponibilidad, fuente
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
            """,
            (
                empresa_id,
                ida,
                prov,
                forma or ("Cheques Empresa" if es_cheque_emp else ""),
                _safe_str(row.get(c_cta) if c_cta else ""),
                fecha,
                "",  # sin debitar = pendiente
                _safe_cuota(row.get(c_cuota) if c_cuota else None),
                _safe_float(row.get(c_tc) if c_tc else 0),
                _safe_str(row.get(c_plazo) if c_plazo else ""),
                capital,
                intereses,
                impuestos,
                cargos,
                subtotal,
                _safe_str(row.get(c_moneda) if c_moneda else "") or "ARS",
                _safe_float(row.get(c_cap_usd) if c_cap_usd else 0),
                _safe_str(row.get(c_chq) if c_chq else ""),
                _safe_str(row.get(c_cred) if c_cred else ""),
                _safe_str(row.get(c_varios) if c_varios else ""),
                1 if es_disp else 0,
                "access_proyeccion",
            ),
        )
        n += 1

    return {
        "status": "success",
        "rows": n,
        "skipped": n_skip,
        "conciliados_excluidos": n_conciliados,
        "source": path,
        "sheet": sheet,
    }


if __name__ == "__main__":
    import sqlite3

    conn = sqlite3.connect("campoplus.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    r = importar_flujo_proyeccion_access(cur, 1)
    conn.commit()
    print(r)
    cur.execute(
        "SELECT forma_pago, COUNT(*) n, ROUND(SUM(subtotal),2) t FROM flujo_proyeccion_access GROUP BY 1 ORDER BY n DESC LIMIT 15"
    )
    for row in cur.fetchall():
        print(dict(row))
    conn.close()
