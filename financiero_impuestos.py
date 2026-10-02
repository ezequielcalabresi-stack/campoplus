# -*- coding: utf-8 -*-
"""
Vencimientos de retenciones/percepciones para el Estado Financiero.

Retenciones Ganancias (SICORE) e IIBB (ARBA): liquidación quincenal
  - Quincena 1 (días 1–15) → vto. día 20 del mismo mes
  - Quincena 2 (días 16–fin) → vto. día 5 del mes siguiente

Percepciones IIBB: liquidación mensual → vto. día 5 del mes siguiente (junto con la quincena 2).

En el financiero va una sola línea global por vencimiento (SICORE + ARBA). Las quincenas que todavía
no terminaron se proyectan con la última quincena cerrada del mismo tipo que tenga datos (o lo ya cargado, si es más).
Los vencimientos anteriores al rango consultado se consideran pagados y no se muestran.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

_TIPOS_SICORE = {"SICORE", "GANANCIAS", "RET_GANANCIAS"}
_TIPOS_ARBA = {"IIBB", "RET_IIBB", "ARBA"}
_TIPOS_PERC = {"PERCEPCION_IIBB", "PERCEPCION IIBB", "PERC_IIBB"}
_COMPONENTES = (("sicore", "SICORE"), ("arba", "ARBA"), ("perc", "Perc. IIBB"))

Periodo = Tuple[int, int, int]  # (año, mes, quincena)


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


def _vencimiento(p: Periodo) -> date:
    y, m, q = p
    return vencimiento_retencion_quincenal(date(y, m, 1 if q == 1 else 16))


def _fin_periodo(p: Periodo) -> date:
    y, m, q = p
    return date(y, m, 15) if q == 1 else date(y, m, monthrange(y, m)[1])


def _referencia(q: int, hoy: date, montos: Dict[Periodo, Dict[str, float]]) -> Periodo:
    """Última quincena `q` terminada antes de hoy que tenga retenciones cargadas (hasta un año atrás)."""
    mes = date(hoy.year, hoy.month, 1) if q == 1 and hoy.day > 15 else _add_months(date(hoy.year, hoy.month, 1), -1)
    primero = (mes.year, mes.month, q)
    for _ in range(12):
        p = (mes.year, mes.month, q)
        if sum(montos.get(p, {}).values()) > 0.009:
            return p
        mes = _add_months(mes, -1)
    return primero


def _nombre_periodo(p: Periodo) -> str:
    y, m, q = p
    if q == 1:
        return f"1ª quincena {m:02d}/{y} (1–15)"
    return f"2ª quincena {m:02d}/{y} (16–{monthrange(y, m)[1]})"


def _pesos(v: float) -> str:
    return "$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _tabla_existe(cur, nombre: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?;", (nombre,))
    return cur.fetchone() is not None


def _digitos(s: Any) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _montos_por_periodo(cur) -> Dict[Periodo, Dict[str, float]]:
    """Retenido por quincena: SICORE, ARBA (historial de Access + emitidas en el sistema) y percepciones."""
    out: Dict[Periodo, Dict[str, float]] = {}

    def sumar(fecha: Any, clave: str, monto: float) -> None:
        op = _parse_iso(fecha)
        if not op or monto <= 0.009:
            return
        q = 2 if clave == "perc" or op.day > 15 else 1
        d = out.setdefault((op.year, op.month, q), {"sicore": 0.0, "arba": 0.0, "perc": 0.0})
        d[clave] += monto

    arba_sistema = set()
    if _tabla_existe(cur, "retenciones_sicore"):
        cur.execute(
            "SELECT fecha, cuit, UPPER(COALESCE(tipo_retencion, 'SICORE')), COALESCE(importe_retenido, 0) FROM retenciones_sicore;"
        )
        for fecha, cuit, tipo, monto in cur.fetchall():
            monto = float(monto or 0)
            tipo = (tipo or "").strip()
            if tipo in _TIPOS_SICORE:
                sumar(fecha, "sicore", monto)
            elif tipo in _TIPOS_ARBA:
                arba_sistema.add((_digitos(cuit), str(fecha or "")[:10], round(monto, 2)))
                sumar(fecha, "arba", monto)
            elif tipo in _TIPOS_PERC:
                sumar(fecha, "perc", monto)
    if _tabla_existe(cur, "retenciones_iibb"):
        cur.execute("SELECT fecha, cuit_sujeto, COALESCE(importe_retenido, 0) FROM retenciones_iibb;")
        for fecha, cuit, monto in cur.fetchall():
            monto = float(monto or 0)
            if (_digitos(cuit), str(fecha or "")[:10], round(monto, 2)) in arba_sistema:
                continue
            sumar(fecha, "arba", monto)
    return out


def _linea(fecha: str, detalle: str, monto: float, varios: str) -> dict:
    monto = round(float(monto or 0), 2)
    return {
        "fecha": fecha,
        "detalle": detalle,
        "forma_pago": "Retenciones SICORE + ARBA",
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
        "varios": varios,
        "origen": "Retenciones SICORE + ARBA",
        "fuente": "retenciones",
        "moneda": "ARS",
        "es_disponibilidad": False,
    }


def _hoy_argentina() -> date:
    return (datetime.utcnow() - timedelta(hours=3)).date()


def lineas_impuestos_financiero(cur, desde_s: str, hasta_s: str, hoy: Optional[date] = None) -> List[dict]:
    """Una línea por vencimiento (20 y 5) con el total de retenciones SICORE + ARBA (+ percepciones el 5)."""
    desde = _parse_iso(desde_s) or date.today()
    hasta = _parse_iso(hasta_s) or (desde + timedelta(days=365))
    hoy = hoy or _hoy_argentina()
    try:
        montos = _montos_por_periodo(cur)
    except Exception:
        return []
    vacio = {"sicore": 0.0, "arba": 0.0, "perc": 0.0}

    lineas: List[dict] = []
    mes = _add_months(date(desde.year, desde.month, 1), -1)
    while mes <= hasta:
        for q in (1, 2):
            p: Periodo = (mes.year, mes.month, q)
            vto = _vencimiento(p)
            if not (desde <= vto <= hasta):
                continue
            real = montos.get(p, vacio)
            ref_p = None
            if hoy <= _fin_periodo(p):
                ref_p = _referencia(q, hoy, montos)
                ref = montos.get(ref_p, vacio)
                valores = {k: max(real[k], ref[k]) for k, _ in _COMPONENTES}
            else:
                valores = dict(real)
            total = sum(valores.values())
            if total < 0.01:
                continue
            detalle = f"Retenciones SICORE + ARBA — {_nombre_periodo(p)}"
            if q == 2 and valores["perc"] > 0.009:
                detalle += " + percepciones IIBB del mes"
            partes = [f"{nombre} {_pesos(valores[k])}" for k, nombre in _COMPONENTES if valores[k] > 0.009]
            if ref_p:
                detalle = "[PROYECTADO] " + detalle
                cargado = sum(real.values())
                partes.append(f"estimado con la {_nombre_periodo(ref_p)}"
                              + (f"; cargado hasta hoy {_pesos(cargado)}" if cargado > 0.009 else ""))
            lineas.append(_linea(vto.isoformat(), detalle, total, " · ".join(partes)))
        mes = _add_months(mes, 1)
    return lineas
