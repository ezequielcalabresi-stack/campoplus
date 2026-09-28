# -*- coding: utf-8 -*-
"""
Ratios financieros y económicos sobre estados contables.

La base es el último ejercicio cerrado (corte al 30/06 del libro). Si ese
cierre no tiene saldos, se usa el corte parcial a hoy. Los EECC de ejercicios
anteriores se cargan a mano para armar la serie desde el primer ejercicio.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from motor_contable import CIERRE_EECC_DIA, CIERRE_EECC_MES, corte_parcial_rt54


def init_eecc_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS eecc_ejercicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            ejercicio TEXT NOT NULL,
            fecha_cierre TEXT,
            origen TEXT DEFAULT 'manual',
            cerrado INTEGER DEFAULT 1,
            activo_corriente REAL DEFAULT 0,
            activo_no_corriente REAL DEFAULT 0,
            pasivo_corriente REAL DEFAULT 0,
            pasivo_no_corriente REAL DEFAULT 0,
            patrimonio REAL DEFAULT 0,
            ingresos REAL DEFAULT 0,
            gastos REAL DEFAULT 0,
            resultado REAL DEFAULT 0,
            existencias REAL DEFAULT 0,
            notas TEXT,
            updated_at TEXT
        );
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_eecc_emp_ej
        ON eecc_ejercicios(empresa_id, ejercicio);
        """
    )


def _num(v: Any) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _div(numerador: float, denominador: float) -> Optional[float]:
    if abs(denominador) < 0.01:
        return None
    return round(numerador / denominador, 4)


def totales_de_fila(fila: dict) -> dict:
    ac = _num(fila.get("activo_corriente"))
    anc = _num(fila.get("activo_no_corriente"))
    pc = _num(fila.get("pasivo_corriente"))
    pnc = _num(fila.get("pasivo_no_corriente"))
    ingresos = _num(fila.get("ingresos"))
    gastos = _num(fila.get("gastos"))
    resultado = fila.get("resultado")
    resultado = _num(resultado) if resultado is not None and str(resultado) != "" else round(ingresos - gastos, 2)
    return {
        "activo_corriente": ac,
        "activo_no_corriente": anc,
        "activo": round(ac + anc, 2),
        "pasivo_corriente": pc,
        "pasivo_no_corriente": pnc,
        "pasivo": round(pc + pnc, 2),
        "patrimonio": _num(fila.get("patrimonio")),
        "ingresos": ingresos,
        "gastos": gastos,
        "resultado_periodo": resultado,
        "existencias": _num(fila.get("existencias")),
    }


def calcular_ratios(totales: dict) -> List[dict]:
    """Ratios clásicos. None cuando el denominador es cero."""
    ac = totales["activo_corriente"]
    anc = totales["activo_no_corriente"]
    activo = totales["activo"]
    pc = totales["pasivo_corriente"]
    pasivo = totales["pasivo"]
    pn = totales["patrimonio"]
    ingresos = totales["ingresos"]
    resultado = totales["resultado_periodo"]
    existencias = totales["existencias"]
    specs = (
        ("liquidez_corriente", "Liquidez corriente", "Liquidez", "veces", "Activo corriente / Pasivo corriente", _div(ac, pc)),
        ("prueba_acida", "Prueba ácida", "Liquidez", "veces", "(Activo corriente − existencias) / Pasivo corriente", _div(ac - existencias, pc)),
        ("capital_trabajo", "Capital de trabajo", "Liquidez", "pesos", "Activo corriente − Pasivo corriente", round(ac - pc, 2)),
        ("endeudamiento", "Endeudamiento", "Solvencia", "tanto", "Pasivo total / Activo total", _div(pasivo, activo)),
        ("solvencia", "Solvencia", "Solvencia", "veces", "Activo total / Pasivo total", _div(activo, pasivo)),
        ("autonomia", "Autonomía", "Solvencia", "tanto", "(Patrimonio + resultado) / Activo", _div(pn + resultado, activo)),
        ("inmovilizacion", "Inmovilización", "Solvencia", "tanto", "Activo no corriente / Activo total", _div(anc, activo)),
        ("roa", "Rentabilidad económica (ROA)", "Rentabilidad", "tanto", "Resultado / Activo total", _div(resultado, activo)),
        ("roe", "Rentabilidad financiera (ROE)", "Rentabilidad", "tanto", "Resultado / Patrimonio (sin el resultado)", _div(resultado, pn)),
        ("margen", "Margen neto", "Rentabilidad", "tanto", "Resultado / Ingresos", _div(resultado, ingresos)),
        ("rotacion", "Rotación del activo", "Rentabilidad", "veces", "Ingresos / Activo total", _div(ingresos, activo)),
    )
    return [
        {"codigo": c, "nombre": n, "grupo": g, "unidad": u, "formula": f, "valor": v}
        for c, n, g, u, f, v in specs
    ]


def _fecha_cierre_anterior(hoy: date, mes: int = CIERRE_EECC_MES, dia: int = CIERRE_EECC_DIA) -> date:
    cierre_este_anio = date(hoy.year, mes, dia)
    if hoy >= cierre_este_anio:
        return cierre_este_anio
    return date(hoy.year - 1, mes, dia)


def _ejercicio_de_cierre(fecha: date) -> str:
    return f"{fecha.year - 1}/{fecha.year}"


def _tiene_saldos(totales: dict) -> bool:
    return any(abs(totales.get(k) or 0) > 0.01 for k in (
        "activo", "pasivo", "patrimonio", "resultado_periodo", "ingresos", "gastos"
    ))


def _fila_desde_corte(corte: dict, ejercicio: str, cerrado: bool, origen: str) -> dict:
    t = corte.get("totales") or {}
    return {
        "ejercicio": ejercicio,
        "fecha_cierre": corte.get("fecha"),
        "origen": origen,
        "cerrado": 1 if cerrado else 0,
        "activo_corriente": t.get("activo_corriente"),
        "activo_no_corriente": t.get("activo_no_corriente"),
        "pasivo_corriente": t.get("pasivo_corriente"),
        "pasivo_no_corriente": t.get("pasivo_no_corriente"),
        "patrimonio": t.get("patrimonio"),
        "ingresos": t.get("ingresos"),
        "gastos": t.get("gastos"),
        "resultado": t.get("resultado_periodo"),
        "existencias": 0,
        "notas": "",
    }


def _enriquecer(fila: dict, base_ratios: Optional[Dict[str, Optional[float]]] = None) -> dict:
    totales = totales_de_fila(fila)
    ratios = calcular_ratios(totales)
    out = {
        "id": fila.get("id"),
        "ejercicio": fila.get("ejercicio") or "",
        "fecha_cierre": (fila.get("fecha_cierre") or "")[:10],
        "origen": fila.get("origen") or "manual",
        "cerrado": int(fila.get("cerrado") or 0),
        "notas": fila.get("notas") or "",
        "totales": totales,
        "ratios": ratios,
    }
    if base_ratios is not None:
        vs = {}
        for r in ratios:
            cero = base_ratios.get(r["codigo"])
            if r["valor"] is None or cero is None:
                vs[r["codigo"]] = None
            else:
                vs[r["codigo"]] = round(r["valor"] - cero, 4)
        out["vs_momento_0"] = vs
    return out


def listar_eecc(conn, empresa_id: int) -> List[dict]:
    cur = conn.cursor()
    init_eecc_schema(cur)
    cur.execute(
        """
        SELECT * FROM eecc_ejercicios
        WHERE empresa_id = ?
        ORDER BY COALESCE(fecha_cierre, ejercicio), id;
        """,
        (empresa_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def guardar_eecc(conn, empresa_id: int, data: dict, eecc_id: Optional[int] = None) -> dict:
    cur = conn.cursor()
    init_eecc_schema(cur)
    ejercicio = (data.get("ejercicio") or "").strip()
    if not ejercicio:
        raise ValueError("Indicá el ejercicio (ej. 2023/2024).")
    fecha = (data.get("fecha_cierre") or "").strip()[:10]
    if not fecha and "/" in ejercicio:
        try:
            anio_cierre = int(ejercicio.split("/")[-1])
            fecha = date(anio_cierre, CIERRE_EECC_MES, CIERRE_EECC_DIA).isoformat()
        except ValueError:
            fecha = ""
    totales = totales_de_fila(data)
    ahora = date.today().isoformat()
    vals = (
        ejercicio, fecha or None, (data.get("origen") or "manual").strip() or "manual",
        1 if int(data.get("cerrado", 1) or 0) else 0,
        totales["activo_corriente"], totales["activo_no_corriente"],
        totales["pasivo_corriente"], totales["pasivo_no_corriente"],
        totales["patrimonio"], totales["ingresos"], totales["gastos"],
        totales["resultado_periodo"], totales["existencias"],
        (data.get("notas") or "").strip() or None, ahora,
    )
    if eecc_id:
        cur.execute(
            """
            UPDATE eecc_ejercicios SET
                ejercicio=?, fecha_cierre=?, origen=?, cerrado=?,
                activo_corriente=?, activo_no_corriente=?,
                pasivo_corriente=?, pasivo_no_corriente=?,
                patrimonio=?, ingresos=?, gastos=?, resultado=?, existencias=?,
                notas=?, updated_at=?
            WHERE id=? AND empresa_id=?
            """,
            vals + (eecc_id, empresa_id),
        )
        if cur.rowcount == 0:
            raise ValueError("EECC no encontrado.")
        rid = eecc_id
    else:
        cur.execute(
            "SELECT id FROM eecc_ejercicios WHERE empresa_id=? AND ejercicio=?",
            (empresa_id, ejercicio),
        )
        ya = cur.fetchone()
        if ya:
            rid = int(ya["id"] if hasattr(ya, "keys") else ya[0])
            cur.execute(
                """
                UPDATE eecc_ejercicios SET
                    fecha_cierre=?, origen=?, cerrado=?,
                    activo_corriente=?, activo_no_corriente=?,
                    pasivo_corriente=?, pasivo_no_corriente=?,
                    patrimonio=?, ingresos=?, gastos=?, resultado=?, existencias=?,
                    notas=?, updated_at=?
                WHERE id=?
                """,
                vals[1:] + (rid,),
            )
        else:
            cur.execute(
                """
                INSERT INTO eecc_ejercicios (
                    empresa_id, ejercicio, fecha_cierre, origen, cerrado,
                    activo_corriente, activo_no_corriente,
                    pasivo_corriente, pasivo_no_corriente,
                    patrimonio, ingresos, gastos, resultado, existencias,
                    notas, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (empresa_id,) + vals,
            )
            rid = int(cur.lastrowid)
    conn.commit()
    cur.execute("SELECT * FROM eecc_ejercicios WHERE id=?", (rid,))
    return _enriquecer(dict(cur.fetchone()))


def borrar_eecc(conn, empresa_id: int, eecc_id: int) -> None:
    cur = conn.cursor()
    init_eecc_schema(cur)
    cur.execute(
        "DELETE FROM eecc_ejercicios WHERE id=? AND empresa_id=?",
        (eecc_id, empresa_id),
    )
    if cur.rowcount == 0:
        raise ValueError("EECC no encontrado.")
    conn.commit()


def panel_ratios(conn, empresa_id: int, hoy: Optional[date] = None) -> dict:
    """Último cierre (o corte parcial) más la serie cargada desde el primer ejercicio."""
    cur = conn.cursor()
    init_eecc_schema(cur)
    hoy = hoy or date.today()
    cierre = _fecha_cierre_anterior(hoy)
    ejercicio_cierre = _ejercicio_de_cierre(cierre)
    corte_cierre = corte_parcial_rt54(cur, empresa_id, cierre.isoformat())
    fila_cierre = _fila_desde_corte(corte_cierre, ejercicio_cierre, True, "libros")
    corte_hoy = corte_parcial_rt54(cur, empresa_id, hoy.isoformat())
    fila_parcial = _fila_desde_corte(
        corte_hoy, _ejercicio_de_cierre(_fecha_cierre_anterior(hoy)) + " (en curso)", False, "parcial"
    )
    # El ejercicio en curso es el que cierra el próximo 30/06, no el anterior.
    proximo = date(cierre.year + 1, CIERRE_EECC_MES, CIERRE_EECC_DIA)
    fila_parcial["ejercicio"] = f"{proximo.year - 1}/{proximo.year}"
    fila_parcial["fecha_cierre"] = hoy.isoformat()

    cargados = listar_eecc(conn, empresa_id)
    cerrados = [f for f in cargados if int(f.get("cerrado") or 0)]
    ultimo_cargado = cerrados[-1] if cerrados else None

    if _tiene_saldos(totales_de_fila(fila_cierre)):
        base_fila = fila_cierre
        base_motivo = f"Último cierre del libro al {cierre.strftime('%d/%m/%Y')} (ejercicio {ejercicio_cierre})."
    elif ultimo_cargado and _tiene_saldos(totales_de_fila(ultimo_cargado)):
        base_fila = ultimo_cargado
        base_motivo = f"EECC cargado del ejercicio {ultimo_cargado.get('ejercicio')} (el libro no tiene saldos al último cierre)."
    elif _tiene_saldos(totales_de_fila(fila_parcial)):
        base_fila = fila_parcial
        base_motivo = "No hay un cierre con saldos. Ratios del corte parcial a hoy."
    else:
        base_fila = fila_cierre
        base_motivo = "Sin saldos en el libro. Cargá un EECC de un ejercicio anterior para arrancar la serie."

    serie_filas = list(cargados)
    ejercicios_cargados = {(f.get("ejercicio") or "").strip() for f in cargados}
    if base_fila.get("origen") != "parcial" and base_fila["ejercicio"] not in ejercicios_cargados:
        serie_filas.append(base_fila)
    serie_filas.sort(key=lambda f: ((f.get("fecha_cierre") or "")[:10], f.get("ejercicio") or ""))

    momento = serie_filas[0] if serie_filas else None
    base_map = None
    if momento:
        base_map = {r["codigo"]: r["valor"] for r in calcular_ratios(totales_de_fila(momento))}
    serie = [_enriquecer(f, base_map if i else None) for i, f in enumerate(serie_filas)]
    if serie:
        serie[0]["vs_momento_0"] = {r["codigo"]: 0 for r in serie[0]["ratios"]}

    return {
        "base": _enriquecer(base_fila),
        "base_motivo": base_motivo,
        "cierre": cierre.isoformat(),
        "momento_0": (momento or {}).get("ejercicio") or "",
        "serie": serie,
        "cargados": [_enriquecer(f) for f in cargados],
    }
