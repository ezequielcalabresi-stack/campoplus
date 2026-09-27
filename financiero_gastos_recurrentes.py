# -*- coding: utf-8 -*-
"""
Gastos / movimientos recurrentes del Estado Financiero.

Plantillas con bucle mensual, bimestral o trimestral: se cargan una vez
y aparecen automáticamente en cada período del flujo proyectado.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Dict, List, Optional


CATEGORIAS = [
    "Vialidad",
    "Imp. Inmobiliario rural",
    "Complementario",
    "Aportes y contribuciones",
    "Tarjetas",
    "Adelanto socios / dividendos",
    "Gastos varios",
    "Otros",
]

FRECUENCIAS = {
    "mensual": 1,
    "bimestral": 2,
    "trimestral": 3,
}


def init_gastos_recurrentes_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS financiero_gastos_recurrentes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            detalle TEXT NOT NULL,
            categoria TEXT DEFAULT 'Otros',
            monto REAL DEFAULT 0,
            moneda TEXT DEFAULT 'ARS',
            dia_pago INTEGER DEFAULT 10,
            frecuencia TEXT DEFAULT 'mensual',
            fecha_inicio TEXT,
            fecha_fin TEXT,
            activo INTEGER DEFAULT 1,
            observaciones TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    # Fix typo if table was created wrong in a partial run — recreate check
    cols = {r[1] for r in cursor.execute("PRAGMA table_info(financiero_gastos_recurrentes);").fetchall()}
    if "monto" not in cols:
        cursor.execute("ALTER TABLE financiero_gastos_recurrentes ADD COLUMN monto REAL DEFAULT 0;")
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fin_gastos_emp
        ON financiero_gastos_recurrentes(empresa_id, activo);
        """
    )


def _parse_iso(s: Any) -> Optional[date]:
    if s is None:
        return None
    raw = str(s).strip()[:10]
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw)
    except Exception:
        return None


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    last = monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def _safe_dia(dia: Any, default: int = 10) -> int:
    try:
        n = int(dia or default)
    except Exception:
        n = default
    return max(1, min(28, n))


def _ocurrencias(
    inicio: date,
    fin_plantilla: Optional[date],
    dia_pago: int,
    step_meses: int,
    desde: date,
    hasta: date,
) -> List[date]:
    """Genera fechas de pago entre desde/hasta según frecuencia."""
    cur = date(
        inicio.year,
        inicio.month,
        min(dia_pago, monthrange(inicio.year, inicio.month)[1]),
    )
    if cur < inicio:
        nxt = _add_months(date(inicio.year, inicio.month, 1), 1)
        cur = date(nxt.year, nxt.month, min(dia_pago, monthrange(nxt.year, nxt.month)[1]))

    out: List[date] = []
    guard = 0
    while cur <= hasta and guard < 600:
        if fin_plantilla and cur > fin_plantilla:
            break
        if cur >= desde:
            out.append(cur)
        nxt = _add_months(date(cur.year, cur.month, 1), step_meses)
        cur = date(nxt.year, nxt.month, min(dia_pago, monthrange(nxt.year, nxt.month)[1]))
        guard += 1
    return out


def listar_gastos(conn, empresa_id: int, solo_activos: bool = False) -> List[dict]:
    cur = conn.cursor()
    init_gastos_recurrentes_schema(cur)
    sql = """
        SELECT id, empresa_id, detalle, categoria, monto, moneda, dia_pago,
               frecuencia, fecha_inicio, fecha_fin, activo, observaciones,
               created_at, updated_at
        FROM financiero_gastos_recurrentes
        WHERE COALESCE(empresa_id, 1) = ?
    """
    if solo_activos:
        sql += " AND COALESCE(activo, 1) = 1"
    sql += " ORDER BY categoria, detalle, id"
    cur.execute(sql, (empresa_id,))
    return [dict(r) for r in cur.fetchall()]


def upsert_gasto(conn, empresa_id: int, data: dict, gasto_id: Optional[int] = None) -> dict:
    cur = conn.cursor()
    init_gastos_recurrentes_schema(cur)
    ahora = date.today().isoformat()
    detalle = (data.get("detalle") or "").strip()
    if not detalle:
        raise ValueError("El detalle es obligatorio.")
    monto = round(float(data.get("monto") or 0), 2)
    if abs(monto) < 0.01:
        raise ValueError("El importe debe ser distinto de cero.")
    freq = (data.get("frecuencia") or "mensual").strip().lower()
    if freq not in FRECUENCIAS:
        raise ValueError("Frecuencia inválida (mensual / bimestral / trimestral).")
    categoria = (data.get("categoria") or "Otros").strip() or "Otros"
    dia = _safe_dia(data.get("dia_pago"), 10)
    moneda = (data.get("moneda") or "ARS").strip().upper() or "ARS"
    fecha_inicio = (data.get("fecha_inicio") or ahora)[:10]
    fecha_fin = (data.get("fecha_fin") or "").strip()[:10] or None
    activo = 1 if int(data.get("activo", 1) or 0) else 0
    obs = (data.get("observaciones") or "").strip() or None

    if gasto_id:
        cur.execute(
            """
            UPDATE financiero_gastos_recurrentes SET
                detalle=?, categoria=?, monto=?, moneda=?, dia_pago=?,
                frecuencia=?, fecha_inicio=?, fecha_fin=?, activo=?,
                observaciones=?, updated_at=?
            WHERE id=? AND COALESCE(empresa_id,1)=?
            """,
            (
                detalle, categoria, monto, moneda, dia, freq,
                fecha_inicio, fecha_fin, activo, obs, ahora,
                gasto_id, empresa_id,
            ),
        )
        if cur.rowcount == 0:
            raise ValueError("Gasto no encontrado.")
        rid = gasto_id
    else:
        cur.execute(
            """
            INSERT INTO financiero_gastos_recurrentes (
                empresa_id, detalle, categoria, monto, moneda, dia_pago,
                frecuencia, fecha_inicio, fecha_fin, activo, observaciones,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                empresa_id, detalle, categoria, monto, moneda, dia, freq,
                fecha_inicio, fecha_fin, activo, obs, ahora, ahora,
            ),
        )
        rid = cur.lastrowid
    conn.commit()
    cur.execute("SELECT * FROM financiero_gastos_recurrentes WHERE id=?", (rid,))
    return dict(cur.fetchone())


def borrar_gasto(conn, empresa_id: int, gasto_id: int) -> None:
    cur = conn.cursor()
    init_gastos_recurrentes_schema(cur)
    cur.execute(
        "DELETE FROM financiero_gastos_recurrentes WHERE id=? AND COALESCE(empresa_id,1)=?",
        (gasto_id, empresa_id),
    )
    if cur.rowcount == 0:
        raise ValueError("Gasto no encontrado.")
    conn.commit()


def lineas_gastos_recurrentes(cur, empresa_id: int, desde_s: str, hasta_s: str) -> List[dict]:
    init_gastos_recurrentes_schema(cur)
    desde = _parse_iso(desde_s) or date.today()
    hasta = _parse_iso(hasta_s) or (desde + timedelta(days=365))

    cur.execute(
        """
        SELECT id, detalle, categoria, monto, moneda, dia_pago, frecuencia,
               fecha_inicio, fecha_fin, observaciones
        FROM financiero_gastos_recurrentes
        WHERE COALESCE(empresa_id, 1) = ?
          AND COALESCE(activo, 1) = 1
          AND ABS(COALESCE(monto, 0)) > 0.009
        """,
        (empresa_id,),
    )
    lineas: List[dict] = []
    for r in cur.fetchall():
        d = dict(r)
        inicio = _parse_iso(d.get("fecha_inicio")) or desde
        fin_p = _parse_iso(d.get("fecha_fin"))
        step = FRECUENCIAS.get((d.get("frecuencia") or "mensual").lower(), 1)
        dia = _safe_dia(d.get("dia_pago"), 10)
        monto = round(float(d.get("monto") or 0), 2)
        cat = (d.get("categoria") or "").strip()
        det_base = (d.get("detalle") or "").strip()
        freq = (d.get("frecuencia") or "mensual").lower()
        for fec in _ocurrencias(inicio, fin_p, dia, step, desde, hasta):
            det = det_base
            if cat and cat.lower() not in det.lower():
                det = f"{cat}: {det}"
            lineas.append({
                "fecha": fec.isoformat(),
                "detalle": det,
                "forma_pago": "Gasto fijo / recurrente",
                "cta_cte": "",
                "nro_cuota": "",
                "tipo_cambio": 0,
                "plazo": freq,
                "capital": monto,
                "intereses": 0.0,
                "impuestos": 0.0,
                "cargos": 0.0,
                "subtotal": monto,
                "monto_ars": monto,
                "monto_usd": 0.0,
                "varios": (d.get("observaciones") or "").strip() or f"Bucle {freq}",
                "origen": "Gasto recurrente",
                "fuente": "gasto_recurrente",
                "moneda": (d.get("moneda") or "ARS"),
                "es_disponibilidad": False,
                "gasto_id": d.get("id"),
            })
    return lineas
