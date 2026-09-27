# -*- coding: utf-8 -*-
"""
Vencimientos de retenciones/percepciones para el Estado Financiero.

Retenciones IIBB y Ganancias (SICORE): liquidación quincenal
  - Quincena 1 (días 1–15) → vto. día 20 del mismo mes
  - Quincena 2 (días 16–fin) → vto. día 5 del mes siguiente

Percepciones IIBB: liquidación mensual → vto. día 5 del mes siguiente.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple


def _parse_iso(s: Any) -> Optional[date]:
    if s is None:
        return None
    raw = str(s).strip()[:10]
    if len(raw) < 10 or raw[4] != "-":
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


def vencimiento_retencion_quincenal(fecha_op: date) -> date:
    """Fecha de pago de retenciones IIBB / SICORE según quincena de la operación."""
    if fecha_op.day <= 15:
        return date(fecha_op.year, fecha_op.month, 20)
    nxt = _add_months(date(fecha_op.year, fecha_op.month, 1), 1)
    return date(nxt.year, nxt.month, 5)


def vencimiento_percepcion_mensual(fecha_op: date) -> date:
    """Percepciones IIBB: se pagan el 5 del mes siguiente al de la operación."""
    nxt = _add_months(date(fecha_op.year, fecha_op.month, 1), 1)
    return date(nxt.year, nxt.month, 5)


def _clamp_fecha(due: date, desde: date, hasta: date) -> Optional[date]:
    """Incluye vencidos anteriores al rango colocándolos en `desde`."""
    if due > hasta:
        return None
    if due < desde:
        return desde
    return due


def _linea(
    fecha: str,
    detalle: str,
    monto: float,
    fuente: str,
    forma_pago: str = "Débito fiscal",
    varios: str = "",
    vencido: bool = False,
) -> dict:
    monto = round(float(monto or 0), 2)
    return {
        "fecha": fecha,
        "detalle": detalle,
        "forma_pago": forma_pago,
        "cta_cte": "",
        "nro_cuota": "",
        "tipo_cambio": 0,
        "plazo": "",
        "capital": monto,
        "intereses": 0.0,
        "impuestos": monto,
        "cargos": 0.0,
        "subtotal": monto,
        "monto_ars": monto,
        "monto_usd": 0.0,
        "varios": varios or ("VENCIDO" if vencido else ""),
        "origen": forma_pago,
        "fuente": fuente,
        "moneda": "ARS",
        "es_disponibilidad": False,
    }


def _agrupar_retenciones(
    cur,
    tipos: Tuple[str, ...],
    label: str,
    fuente: str,
    desde: date,
    hasta: date,
) -> List[dict]:
    """Suma importe_retenido por fecha de vencimiento quincenal."""
    ph = ",".join("?" for _ in tipos)
    tipos_up = tuple(t.upper() for t in tipos)
    cur.execute(
        f"""
        SELECT fecha, SUM(COALESCE(importe_retenido, 0)) AS total
        FROM retenciones_sicore
        WHERE UPPER(COALESCE(tipo_retencion, 'SICORE')) IN ({ph})
          AND COALESCE(importe_retenido, 0) > 0.009
          AND COALESCE(fecha, '') <> ''
        GROUP BY fecha
        """,
        tipos_up,
    )
    buckets: Dict[str, float] = {}
    for r in cur.fetchall():
        d = dict(r)
        op = _parse_iso(d.get("fecha"))
        if not op:
            continue
        due = vencimiento_retencion_quincenal(op)
        key = due.isoformat()
        buckets[key] = buckets.get(key, 0.0) + float(d.get("total") or 0)

    out: List[dict] = []
    for due_s, total in sorted(buckets.items()):
        due = date.fromisoformat(due_s)
        clamped = _clamp_fecha(due, desde, hasta)
        if clamped is None or abs(total) < 0.01:
            continue
        q = "1ª quincena" if due.day == 20 else "2ª quincena"
        # Periodo liquidado aproximado a partir del vto
        if due.day == 20:
            periodo = f"{due.month:02d}/{due.year} (1–15)"
        else:
            prev = _add_months(date(due.year, due.month, 1), -1)
            last = monthrange(prev.year, prev.month)[1]
            periodo = f"{prev.month:02d}/{prev.year} (16–{last})"
        vencido = due < desde
        det = f"Pago retenciones {label} — {periodo}"
        if vencido:
            det = f"[VENCIDO {due.strftime('%d/%m/%Y')}] {det}"
        out.append(
            _linea(
                clamped.isoformat(),
                det,
                total,
                fuente,
                forma_pago=f"Retenciones {label}",
                varios=q,
                vencido=vencido,
            )
        )
    return out


def _agrupar_percepciones(cur, desde: date, hasta: date) -> List[dict]:
    cur.execute(
        """
        SELECT fecha, SUM(COALESCE(importe_retenido, 0)) AS total
        FROM retenciones_sicore
        WHERE UPPER(COALESCE(tipo_retencion, '')) IN ('PERCEPCION_IIBB', 'PERCEPCION IIBB', 'PERC_IIBB')
          AND COALESCE(importe_retenido, 0) > 0.009
          AND COALESCE(fecha, '') <> ''
        GROUP BY fecha
        """
    )
    buckets: Dict[str, float] = {}
    for r in cur.fetchall():
        d = dict(r)
        op = _parse_iso(d.get("fecha"))
        if not op:
            continue
        due = vencimiento_percepcion_mensual(op)
        key = due.isoformat()
        buckets[key] = buckets.get(key, 0.0) + float(d.get("total") or 0)

    out: List[dict] = []
    for due_s, total in sorted(buckets.items()):
        due = date.fromisoformat(due_s)
        clamped = _clamp_fecha(due, desde, hasta)
        if clamped is None or abs(total) < 0.01:
            continue
        prev = _add_months(date(due.year, due.month, 1), -1)
        periodo = f"{prev.month:02d}/{prev.year}"
        vencido = due < desde
        det = f"Pago percepciones IIBB — período {periodo}"
        if vencido:
            det = f"[VENCIDO {due.strftime('%d/%m/%Y')}] {det}"
        out.append(
            _linea(
                clamped.isoformat(),
                det,
                total,
                "percepciones_iibb",
                forma_pago="Percepciones IIBB",
                varios="Mensual — vto. día 5",
                vencido=vencido,
            )
        )
    return out


def lineas_impuestos_financiero(cur, desde_s: str, hasta_s: str) -> List[dict]:
    """Genera líneas de egreso por vencimientos de retenciones y percepciones."""
    desde = _parse_iso(desde_s) or date.today()
    hasta = _parse_iso(hasta_s) or (desde + timedelta(days=365))

    # Verificar tabla
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='retenciones_sicore'")
        if not cur.fetchone():
            return []
    except Exception:
        return []

    lineas: List[dict] = []
    lineas.extend(
        _agrupar_retenciones(
            cur, ("SICORE", "GANANCIAS", "RET_GANANCIAS"), "Ganancias (SICORE)", "retenciones_sicore", desde, hasta
        )
    )
    lineas.extend(
        _agrupar_retenciones(cur, ("IIBB", "RET_IIBB", "ARBA"), "IIBB", "retenciones_iibb", desde, hasta)
    )
    lineas.extend(_agrupar_percepciones(cur, desde, hasta))
    return lineas
