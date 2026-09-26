# -*- coding: utf-8 -*-
"""
CAmpo+ — Convenio Multilateral / CM05 (ayuda para contadores).

No reemplaza SIFERE: arma la planilla anual (ingresos/gastos por jurisdicción
y coeficiente unificado) a partir de carga manual + sugerencias del sistema.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional


# Códigos habituales en CM (orden oficial aproximado)
JURISDICCIONES_CM = (
    ("01", "CABA"),
    ("02", "Buenos Aires"),
    ("03", "Catamarca"),
    ("04", "Córdoba"),
    ("05", "Corrientes"),
    ("06", "Chaco"),
    ("07", "Chubut"),
    ("08", "Entre Ríos"),
    ("09", "Formosa"),
    ("10", "Jujuy"),
    ("11", "La Pampa"),
    ("12", "La Rioja"),
    ("13", "Mendoza"),
    ("14", "Misiones"),
    ("15", "Neuquén"),
    ("16", "Río Negro"),
    ("17", "Salta"),
    ("18", "San Juan"),
    ("19", "San Luis"),
    ("20", "Santa Cruz"),
    ("21", "Santa Fe"),
    ("22", "Santiago del Estero"),
    ("23", "Tucumán"),
    ("24", "Tierra del Fuego"),
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_cm05_schema(conn) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS cm_jurisdicciones (
            codigo TEXT PRIMARY KEY,
            nombre TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cm_empresa (
            empresa_id INTEGER PRIMARY KEY,
            es_cm INTEGER DEFAULT 1,
            sede_codigo TEXT DEFAULT '02',
            nro_inscripcion_cm TEXT,
            observaciones TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS cm05_periodos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            anio INTEGER NOT NULL,
            estado TEXT DEFAULT 'borrador',
            notas TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(empresa_id, anio)
        );

        CREATE TABLE IF NOT EXISTS cm05_lineas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            periodo_id INTEGER NOT NULL,
            empresa_id INTEGER NOT NULL,
            juris_codigo TEXT NOT NULL,
            ingresos REAL DEFAULT 0,
            gastos REAL DEFAULT 0,
            origen TEXT DEFAULT 'manual',
            notas TEXT,
            UNIQUE(periodo_id, juris_codigo),
            FOREIGN KEY(periodo_id) REFERENCES cm05_periodos(id)
        );
        """
    )
    for codigo, nombre in JURISDICCIONES_CM:
        cur.execute(
            """
            INSERT OR IGNORE INTO cm_jurisdicciones (codigo, nombre) VALUES (?, ?);
            """,
            (codigo, nombre),
        )
    conn.commit()


def listar_jurisdicciones(conn) -> List[dict]:
    cur = conn.cursor()
    cur.execute("SELECT codigo, nombre FROM cm_jurisdicciones ORDER BY codigo;")
    return [dict(r) for r in cur.fetchall()]


def obtener_config_empresa(conn, empresa_id: int) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT * FROM cm_empresa WHERE empresa_id = ?;", (empresa_id,))
    row = cur.fetchone()
    if row:
        return dict(row)
    return {
        "empresa_id": empresa_id,
        "es_cm": 1,
        "sede_codigo": "02",
        "nro_inscripcion_cm": "",
        "observaciones": "",
    }


def guardar_config_empresa(conn, empresa_id: int, data: dict) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO cm_empresa (empresa_id, es_cm, sede_codigo, nro_inscripcion_cm, observaciones, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(empresa_id) DO UPDATE SET
            es_cm = excluded.es_cm,
            sede_codigo = excluded.sede_codigo,
            nro_inscripcion_cm = excluded.nro_inscripcion_cm,
            observaciones = excluded.observaciones,
            updated_at = excluded.updated_at;
        """,
        (
            empresa_id,
            1 if data.get("es_cm", 1) else 0,
            (data.get("sede_codigo") or "02").strip(),
            (data.get("nro_inscripcion_cm") or "").strip(),
            (data.get("observaciones") or "").strip(),
            _now(),
        ),
    )
    conn.commit()
    return obtener_config_empresa(conn, empresa_id)


def listar_periodos(conn, empresa_id: int) -> List[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM cm05_periodos
        WHERE empresa_id = ?
        ORDER BY anio DESC;
        """,
        (empresa_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def asegurar_periodo(conn, empresa_id: int, anio: int) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM cm05_periodos WHERE empresa_id = ? AND anio = ?;
        """,
        (empresa_id, int(anio)),
    )
    row = cur.fetchone()
    if row:
        return dict(row)
    ahora = _now()
    cur.execute(
        """
        INSERT INTO cm05_periodos (empresa_id, anio, estado, created_at, updated_at)
        VALUES (?, ?, 'borrador', ?, ?);
        """,
        (empresa_id, int(anio), ahora, ahora),
    )
    pid = int(cur.lastrowid)
    # Sembrar jurisdicciones típicas agro (BA, SF, CBA, ER, LP) + sede
    cfg = obtener_config_empresa(conn, empresa_id)
    seeds = {cfg.get("sede_codigo") or "02", "02", "21", "04", "08", "11"}
    for codigo in seeds:
        cur.execute(
            """
            INSERT OR IGNORE INTO cm05_lineas (periodo_id, empresa_id, juris_codigo, ingresos, gastos, origen)
            VALUES (?, ?, ?, 0, 0, 'seed');
            """,
            (pid, empresa_id, codigo),
        )
    conn.commit()
    cur.execute("SELECT * FROM cm05_periodos WHERE id = ?;", (pid,))
    return dict(cur.fetchone())


def listar_lineas(conn, empresa_id: int, periodo_id: int) -> List[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT l.*, j.nombre AS juris_nombre
        FROM cm05_lineas l
        LEFT JOIN cm_jurisdicciones j ON j.codigo = l.juris_codigo
        WHERE l.empresa_id = ? AND l.periodo_id = ?
        ORDER BY l.juris_codigo;
        """,
        (empresa_id, periodo_id),
    )
    return [dict(r) for r in cur.fetchall()]


def upsert_linea(conn, empresa_id: int, periodo_id: int, data: dict) -> dict:
    codigo = (data.get("juris_codigo") or "").strip()
    if not codigo:
        raise ValueError("Código de jurisdicción obligatorio")
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM cm05_periodos WHERE id = ? AND empresa_id = ?;",
        (periodo_id, empresa_id),
    )
    if not cur.fetchone():
        raise ValueError("Período no encontrado")
    cur.execute(
        """
        INSERT INTO cm05_lineas (periodo_id, empresa_id, juris_codigo, ingresos, gastos, origen, notas)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(periodo_id, juris_codigo) DO UPDATE SET
            ingresos = excluded.ingresos,
            gastos = excluded.gastos,
            origen = excluded.origen,
            notas = excluded.notas;
        """,
        (
            periodo_id,
            empresa_id,
            codigo,
            float(data.get("ingresos") or 0),
            float(data.get("gastos") or 0),
            (data.get("origen") or "manual").strip(),
            (data.get("notas") or "").strip(),
        ),
    )
    conn.commit()
    cur.execute(
        """
        SELECT l.*, j.nombre AS juris_nombre FROM cm05_lineas l
        LEFT JOIN cm_jurisdicciones j ON j.codigo = l.juris_codigo
        WHERE l.periodo_id = ? AND l.juris_codigo = ?;
        """,
        (periodo_id, codigo),
    )
    return dict(cur.fetchone())


def borrar_linea(conn, empresa_id: int, linea_id: int) -> None:
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM cm05_lineas WHERE id = ? AND empresa_id = ?;",
        (linea_id, empresa_id),
    )
    conn.commit()


def _sugerir_desde_sistema(conn, empresa_id: int, anio: int) -> Dict[str, float]:
    """Suma netos de liquidaciones del año (sin jurisdicción → sede)."""
    cfg = obtener_config_empresa(conn, empresa_id)
    sede = cfg.get("sede_codigo") or "02"
    desde = f"{anio}-01-01"
    hasta = f"{anio}-12-31"
    cur = conn.cursor()
    total = 0.0
    # Hacienda
    try:
        cur.execute(
            """
            SELECT COALESCE(SUM(neto_final),0) AS n FROM liquidaciones_hacienda
            WHERE empresa_id = ? AND COALESCE(fecha,'') BETWEEN ? AND ?;
            """,
            (empresa_id, desde, hasta),
        )
        total += float(cur.fetchone()["n"] or 0)
    except Exception:
        pass
    # Granos
    try:
        cur.execute(
            """
            SELECT COALESCE(SUM(COALESCE(neto_a_pagar, neto)),0) AS n FROM liquidaciones_granos
            WHERE empresa_id = ? AND COALESCE(fecha, fecha_pago, '') BETWEEN ? AND ?;
            """,
            (empresa_id, desde, hasta),
        )
        total += float(cur.fetchone()["n"] or 0)
    except Exception:
        pass
    # Leche
    try:
        cur.execute(
            """
            SELECT COALESCE(SUM(neto_final),0) AS n FROM liquidaciones_leche
            WHERE empresa_id = ? AND COALESCE(fecha,'') BETWEEN ? AND ?;
            """,
            (empresa_id, desde, hasta),
        )
        total += float(cur.fetchone()["n"] or 0)
    except Exception:
        pass
    return {sede: total} if total else {}


def aplicar_sugerencias(conn, empresa_id: int, periodo_id: int) -> dict:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM cm05_periodos WHERE id = ? AND empresa_id = ?;",
        (periodo_id, empresa_id),
    )
    per = cur.fetchone()
    if not per:
        raise ValueError("Período no encontrado")
    anio = int(per["anio"])
    sug = _sugerir_desde_sistema(conn, empresa_id, anio)
    aplicados = 0
    for codigo, monto in sug.items():
        if monto <= 0:
            continue
        cur.execute(
            """
            INSERT INTO cm05_lineas (periodo_id, empresa_id, juris_codigo, ingresos, gastos, origen, notas)
            VALUES (?, ?, ?, ?, 0, 'sugerido', 'Sugerido desde liquidaciones del sistema')
            ON CONFLICT(periodo_id, juris_codigo) DO UPDATE SET
                ingresos = CASE
                    WHEN cm05_lineas.origen = 'manual' AND cm05_lineas.ingresos > 0
                    THEN cm05_lineas.ingresos
                    ELSE excluded.ingresos
                END,
                origen = CASE
                    WHEN cm05_lineas.origen = 'manual' AND cm05_lineas.ingresos > 0
                    THEN cm05_lineas.origen
                    ELSE 'sugerido'
                END;
            """,
            (periodo_id, empresa_id, codigo, float(monto)),
        )
        aplicados += 1
    conn.commit()
    return {"aplicados": aplicados, "detalle": sug}


def calcular_cm05(conn, empresa_id: int, periodo_id: int) -> dict:
    lineas = listar_lineas(conn, empresa_id, periodo_id)
    tot_i = sum(float(l.get("ingresos") or 0) for l in lineas)
    tot_g = sum(float(l.get("gastos") or 0) for l in lineas)
    out = []
    for l in lineas:
        ing = float(l.get("ingresos") or 0)
        gas = float(l.get("gastos") or 0)
        pct_i = (ing / tot_i * 100.0) if tot_i else 0.0
        pct_g = (gas / tot_g * 100.0) if tot_g else 0.0
        # Coeficiente unificado clásico: promedio de % ingresos y % gastos
        if tot_i and tot_g:
            coef = (pct_i + pct_g) / 2.0
        elif tot_i:
            coef = pct_i
        elif tot_g:
            coef = pct_g
        else:
            coef = 0.0
        out.append(
            {
                **l,
                "pct_ingresos": round(pct_i, 6),
                "pct_gastos": round(pct_g, 6),
                "coeficiente": round(coef, 6),
            }
        )
    suma_coef = sum(x["coeficiente"] for x in out)
    return {
        "lineas": out,
        "totales": {
            "ingresos": round(tot_i, 2),
            "gastos": round(tot_g, 2),
            "suma_coeficientes": round(suma_coef, 6),
        },
        "config": obtener_config_empresa(conn, empresa_id),
    }


def ficha_periodo(conn, empresa_id: int, periodo_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM cm05_periodos WHERE id = ? AND empresa_id = ?;",
        (periodo_id, empresa_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    calc = calcular_cm05(conn, empresa_id, periodo_id)
    return {"periodo": dict(row), **calc}


def export_csv(conn, empresa_id: int, periodo_id: int) -> str:
    ficha = ficha_periodo(conn, empresa_id, periodo_id)
    if not ficha:
        raise ValueError("Período no encontrado")
    lines = [
        "codigo;jurisdiccion;ingresos;gastos;pct_ingresos;pct_gastos;coeficiente_unificado"
    ]
    for l in ficha["lineas"]:
        lines.append(
            ";".join(
                [
                    str(l.get("juris_codigo") or ""),
                    str(l.get("juris_nombre") or ""),
                    f"{float(l.get('ingresos') or 0):.2f}",
                    f"{float(l.get('gastos') or 0):.2f}",
                    f"{float(l.get('pct_ingresos') or 0):.6f}",
                    f"{float(l.get('pct_gastos') or 0):.6f}",
                    f"{float(l.get('coeficiente') or 0):.6f}",
                ]
            )
        )
    t = ficha["totales"]
    lines.append(
        f"TOTAL;;{t['ingresos']:.2f};{t['gastos']:.2f};;;{t['suma_coeficientes']:.6f}"
    )
    return "\n".join(lines)
