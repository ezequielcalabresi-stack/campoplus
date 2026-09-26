# -*- coding: utf-8 -*-
"""
CAmpo+ — Producción aviar.

Cubre: pollos parrilleros (engorde), gallinas ponedoras y planteles reproductores.
Unidad de gestión = lote / galpón (no animal individual).
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional


TIPOS_LOTE = (
    ("parrillero", "Pollo parrillero / engorde"),
    ("ponedora", "Gallina ponedora"),
    ("reproductora", "Reproductoras"),
)

LINEAS_COMUNES = (
    "Cobb",
    "Ross",
    "Hubbard",
    "Hy-Line Brown",
    "Hy-Line W-36",
    "Lohmann Brown",
    "Lohmann LSL",
    "Isa Brown",
    "Otra",
)

TIPOS_EVENTO_AVI = (
    "ingreso",
    "vacunacion",
    "tratamiento",
    "pesada",
    "traslado",
    "descarte",
    "mortandad",
    "faena",
    "venta",
    "observacion",
)

ESTADOS_LOTE = ("activo", "faenado", "vendido", "cerrado")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_aviar_schema(conn) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS avi_granjas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            ubicacion TEXT,
            observaciones TEXT,
            activo INTEGER DEFAULT 1,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS avi_galpones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            granja_id INTEGER,
            nombre TEXT NOT NULL,
            capacidad INTEGER DEFAULT 0,
            tipo_uso TEXT DEFAULT 'parrillero',
            observaciones TEXT,
            activo INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS avi_lotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            codigo TEXT NOT NULL,
            tipo TEXT DEFAULT 'parrillero',
            linea_genetica TEXT,
            granja_id INTEGER,
            galpon_id INTEGER,
            fecha_ingreso TEXT,
            fecha_cierre TEXT,
            n_inicial INTEGER DEFAULT 0,
            n_actual INTEGER DEFAULT 0,
            edad_dias_ingreso INTEGER DEFAULT 0,
            peso_ingreso_prom REAL,
            peso_actual_prom REAL,
            sexo TEXT DEFAULT 'Mixto',
            estado TEXT DEFAULT 'activo',
            destino TEXT,
            observaciones TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS avi_produccion_diaria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            lote_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            huevos INTEGER DEFAULT 0,
            huevos_rotos INTEGER DEFAULT 0,
            huevos_sucios INTEGER DEFAULT 0,
            mortalidad INTEGER DEFAULT 0,
            descartes INTEGER DEFAULT 0,
            consumo_alimento_kg REAL,
            consumo_agua_l REAL,
            peso_promedio REAL,
            temperatura_c REAL,
            humedad_pct REAL,
            observaciones TEXT,
            usuario_registro TEXT,
            created_at TEXT,
            UNIQUE(empresa_id, lote_id, fecha)
        );

        CREATE TABLE IF NOT EXISTS avi_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            lote_id INTEGER,
            fecha TEXT NOT NULL,
            tipo TEXT NOT NULL,
            cantidad INTEGER,
            peso_kg REAL,
            detalle TEXT,
            medicamento TEXT,
            dosis TEXT,
            usuario_registro TEXT,
            created_at TEXT
        );
        """
    )
    conn.commit()


def asegurar_seed_empresa(conn, empresa_id: int) -> None:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS n FROM avi_granjas WHERE empresa_id = ?;", (empresa_id,)
    )
    if int(cur.fetchone()["n"] or 0) > 0:
        return
    ahora = _now()
    cur.execute(
        """
        INSERT INTO avi_granjas (empresa_id, nombre, ubicacion, created_at)
        VALUES (?, 'Granja avícola principal', '', ?);
        """,
        (empresa_id, ahora),
    )
    gid = int(cur.lastrowid)
    for i, nombre in enumerate(("Galpón 1", "Galpón 2", "Galpón 3"), start=1):
        cur.execute(
            """
            INSERT INTO avi_galpones (empresa_id, granja_id, nombre, capacidad, tipo_uso)
            VALUES (?, ?, ?, ?, ?);
            """,
            (empresa_id, gid, nombre, 10000 if i < 3 else 5000, "parrillero" if i < 3 else "ponedora"),
        )
    conn.commit()


def listar_granjas(conn, empresa_id: int) -> List[dict]:
    asegurar_seed_empresa(conn, empresa_id)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM avi_granjas WHERE empresa_id = ? AND activo = 1 ORDER BY nombre;",
        (empresa_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def listar_galpones(conn, empresa_id: int, granja_id: Optional[int] = None) -> List[dict]:
    asegurar_seed_empresa(conn, empresa_id)
    cur = conn.cursor()
    if granja_id:
        cur.execute(
            """
            SELECT g.*, gr.nombre AS granja_nombre
            FROM avi_galpones g
            LEFT JOIN avi_granjas gr ON gr.id = g.granja_id
            WHERE g.empresa_id = ? AND g.activo = 1 AND g.granja_id = ?
            ORDER BY g.nombre;
            """,
            (empresa_id, granja_id),
        )
    else:
        cur.execute(
            """
            SELECT g.*, gr.nombre AS granja_nombre
            FROM avi_galpones g
            LEFT JOIN avi_granjas gr ON gr.id = g.granja_id
            WHERE g.empresa_id = ? AND g.activo = 1
            ORDER BY g.nombre;
            """,
            (empresa_id,),
        )
    return [dict(r) for r in cur.fetchall()]


def crear_galpon(conn, empresa_id: int, data: dict) -> int:
    asegurar_seed_empresa(conn, empresa_id)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO avi_galpones (empresa_id, granja_id, nombre, capacidad, tipo_uso, observaciones)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (
            empresa_id,
            data.get("granja_id"),
            (data.get("nombre") or "").strip(),
            int(data.get("capacidad") or 0),
            data.get("tipo_uso") or "parrillero",
            data.get("observaciones") or "",
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def resumen_aviar(conn, empresa_id: int) -> dict:
    asegurar_seed_empresa(conn, empresa_id)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT tipo, COUNT(*) AS n, COALESCE(SUM(n_actual),0) AS aves
        FROM avi_lotes WHERE empresa_id = ? AND estado = 'activo'
        GROUP BY tipo;
        """,
        (empresa_id,),
    )
    por_tipo = [dict(r) for r in cur.fetchall()]
    cur.execute(
        """
        SELECT COALESCE(SUM(n_actual),0) AS aves, COUNT(*) AS lotes
        FROM avi_lotes WHERE empresa_id = ? AND estado = 'activo';
        """,
        (empresa_id,),
    )
    tot = cur.fetchone()
    cur.execute(
        """
        SELECT COALESCE(SUM(huevos),0) AS huevos,
               COALESCE(SUM(mortalidad),0) AS mort,
               COALESCE(SUM(consumo_alimento_kg),0) AS alimento
        FROM avi_produccion_diaria
        WHERE empresa_id = ? AND fecha >= date('now','-7 days');
        """,
        (empresa_id,),
    )
    sem = cur.fetchone()
    cur.execute(
        """
        SELECT COALESCE(SUM(huevos),0) AS huevos
        FROM avi_produccion_diaria
        WHERE empresa_id = ? AND fecha = date('now');
        """,
        (empresa_id,),
    )
    hoy = cur.fetchone()
    return {
        "lotes_activos": int(tot["lotes"] or 0),
        "aves_actuales": int(tot["aves"] or 0),
        "por_tipo": por_tipo,
        "huevos_hoy": int(hoy["huevos"] or 0),
        "huevos_7d": int(sem["huevos"] or 0),
        "mortalidad_7d": int(sem["mort"] or 0),
        "alimento_kg_7d": float(sem["alimento"] or 0),
    }


def crear_lote(conn, empresa_id: int, data: dict, usuario: str = "") -> int:
    asegurar_seed_empresa(conn, empresa_id)
    cur = conn.cursor()
    n = int(data.get("n_inicial") or 0)
    ahora = _now()
    fecha = (data.get("fecha_ingreso") or ahora[:10])[:10]
    cur.execute(
        """
        INSERT INTO avi_lotes (
            empresa_id, codigo, tipo, linea_genetica, granja_id, galpon_id,
            fecha_ingreso, n_inicial, n_actual, edad_dias_ingreso,
            peso_ingreso_prom, peso_actual_prom, sexo, estado, destino,
            observaciones, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
        """,
        (
            empresa_id,
            (data.get("codigo") or "").strip(),
            data.get("tipo") or "parrillero",
            data.get("linea_genetica") or "",
            data.get("granja_id"),
            data.get("galpon_id"),
            fecha,
            n,
            int(data.get("n_actual") if data.get("n_actual") is not None else n),
            int(data.get("edad_dias_ingreso") or 0),
            data.get("peso_ingreso_prom"),
            data.get("peso_actual_prom") or data.get("peso_ingreso_prom"),
            data.get("sexo") or "Mixto",
            data.get("estado") or "activo",
            data.get("destino") or "",
            data.get("observaciones") or "",
            ahora,
            ahora,
        ),
    )
    lid = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO avi_eventos (
            empresa_id, lote_id, fecha, tipo, cantidad, peso_kg, detalle,
            usuario_registro, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?);
        """,
        (
            empresa_id,
            lid,
            fecha,
            "ingreso",
            n,
            data.get("peso_ingreso_prom"),
            data.get("observaciones") or "Ingreso de lote",
            usuario,
            ahora,
        ),
    )
    conn.commit()
    return lid


def listar_lotes(
    conn,
    empresa_id: int,
    *,
    tipo: Optional[str] = None,
    estado: str = "activo",
    q: Optional[str] = None,
) -> List[dict]:
    cur = conn.cursor()
    where = ["l.empresa_id = ?"]
    params: list = [empresa_id]
    if estado and estado != "todos":
        where.append("l.estado = ?")
        params.append(estado)
    if tipo:
        where.append("l.tipo = ?")
        params.append(tipo)
    if q:
        like = f"%{q.strip()}%"
        where.append("(l.codigo LIKE ? OR l.linea_genetica LIKE ?)")
        params.extend([like, like])
    cur.execute(
        f"""
        SELECT l.*, g.nombre AS galpon_nombre, gr.nombre AS granja_nombre,
               CAST(julianday('now') - julianday(l.fecha_ingreso) AS INTEGER)
                 + COALESCE(l.edad_dias_ingreso,0) AS edad_dias
        FROM avi_lotes l
        LEFT JOIN avi_galpones g ON g.id = l.galpon_id
        LEFT JOIN avi_granjas gr ON gr.id = l.granja_id
        WHERE {' AND '.join(where)}
        ORDER BY l.fecha_ingreso DESC, l.id DESC;
        """,
        params,
    )
    return [dict(r) for r in cur.fetchall()]


def obtener_lote(conn, empresa_id: int, lote_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT l.*, g.nombre AS galpon_nombre, gr.nombre AS granja_nombre,
               CAST(julianday('now') - julianday(l.fecha_ingreso) AS INTEGER)
                 + COALESCE(l.edad_dias_ingreso,0) AS edad_dias
        FROM avi_lotes l
        LEFT JOIN avi_galpones g ON g.id = l.galpon_id
        LEFT JOIN avi_granjas gr ON gr.id = l.granja_id
        WHERE l.empresa_id = ? AND l.id = ?;
        """,
        (empresa_id, lote_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def actualizar_lote(conn, empresa_id: int, lote_id: int, data: dict) -> None:
    if not obtener_lote(conn, empresa_id, lote_id):
        raise ValueError("Lote no encontrado")
    allowed = (
        "codigo", "tipo", "linea_genetica", "granja_id", "galpon_id", "fecha_ingreso",
        "fecha_cierre", "n_inicial", "n_actual", "edad_dias_ingreso", "peso_ingreso_prom",
        "peso_actual_prom", "sexo", "estado", "destino", "observaciones",
    )
    fields, vals = [], []
    for k in allowed:
        if k in data:
            fields.append(f"{k} = ?")
            vals.append(data[k])
    if not fields:
        return
    fields.append("updated_at = ?")
    vals.append(_now())
    vals.extend([empresa_id, lote_id])
    cur = conn.cursor()
    cur.execute(
        f"UPDATE avi_lotes SET {', '.join(fields)} WHERE empresa_id = ? AND id = ?;",
        vals,
    )
    conn.commit()


def registrar_produccion(conn, empresa_id: int, data: dict, usuario: str = "") -> int:
    lote_id = data.get("lote_id")
    if not lote_id or not obtener_lote(conn, empresa_id, int(lote_id)):
        raise ValueError("Lote no encontrado")
    fecha = (data.get("fecha") or _now()[:10])[:10]
    mort = int(data.get("mortalidad") or 0)
    desc = int(data.get("descartes") or 0)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO avi_produccion_diaria (
            empresa_id, lote_id, fecha, huevos, huevos_rotos, huevos_sucios,
            mortalidad, descartes, consumo_alimento_kg, consumo_agua_l,
            peso_promedio, temperatura_c, humedad_pct, observaciones,
            usuario_registro, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(empresa_id, lote_id, fecha) DO UPDATE SET
            huevos = excluded.huevos,
            huevos_rotos = excluded.huevos_rotos,
            huevos_sucios = excluded.huevos_sucios,
            mortalidad = excluded.mortalidad,
            descartes = excluded.descartes,
            consumo_alimento_kg = excluded.consumo_alimento_kg,
            consumo_agua_l = excluded.consumo_agua_l,
            peso_promedio = excluded.peso_promedio,
            temperatura_c = excluded.temperatura_c,
            humedad_pct = excluded.humedad_pct,
            observaciones = excluded.observaciones,
            usuario_registro = excluded.usuario_registro;
        """,
        (
            empresa_id,
            int(lote_id),
            fecha,
            int(data.get("huevos") or 0),
            int(data.get("huevos_rotos") or 0),
            int(data.get("huevos_sucios") or 0),
            mort,
            desc,
            data.get("consumo_alimento_kg"),
            data.get("consumo_agua_l"),
            data.get("peso_promedio"),
            data.get("temperatura_c"),
            data.get("humedad_pct"),
            data.get("observaciones") or "",
            usuario,
            _now(),
        ),
    )
    pid = int(cur.lastrowid)
    # Ajustar stock del lote
    if mort or desc:
        cur.execute(
            """
            UPDATE avi_lotes
            SET n_actual = CASE
                    WHEN COALESCE(n_actual,0) - ? < 0 THEN 0
                    ELSE COALESCE(n_actual,0) - ?
                END,
                updated_at = ?
            WHERE empresa_id = ? AND id = ?;
            """,
            (mort + desc, mort + desc, _now(), empresa_id, int(lote_id)),
        )
    if data.get("peso_promedio") is not None:
        cur.execute(
            """
            UPDATE avi_lotes SET peso_actual_prom = ?, updated_at = ?
            WHERE empresa_id = ? AND id = ?;
            """,
            (data["peso_promedio"], _now(), empresa_id, int(lote_id)),
        )
    conn.commit()
    return pid


def listar_produccion(
    conn,
    empresa_id: int,
    *,
    lote_id: Optional[int] = None,
    limit: int = 60,
) -> List[dict]:
    cur = conn.cursor()
    if lote_id:
        cur.execute(
            """
            SELECT p.*, l.codigo AS lote_codigo
            FROM avi_produccion_diaria p
            LEFT JOIN avi_lotes l ON l.id = p.lote_id
            WHERE p.empresa_id = ? AND p.lote_id = ?
            ORDER BY p.fecha DESC LIMIT ?;
            """,
            (empresa_id, lote_id, limit),
        )
    else:
        cur.execute(
            """
            SELECT p.*, l.codigo AS lote_codigo
            FROM avi_produccion_diaria p
            LEFT JOIN avi_lotes l ON l.id = p.lote_id
            WHERE p.empresa_id = ?
            ORDER BY p.fecha DESC, p.id DESC LIMIT ?;
            """,
            (empresa_id, limit),
        )
    return [dict(r) for r in cur.fetchall()]


def registrar_evento(conn, empresa_id: int, data: dict, usuario: str = "") -> int:
    if data.get("tipo") not in TIPOS_EVENTO_AVI:
        raise ValueError(f"Tipo de evento inválido: {data.get('tipo')}")
    lote_id = data.get("lote_id")
    if lote_id and not obtener_lote(conn, empresa_id, int(lote_id)):
        raise ValueError("Lote no encontrado")
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO avi_eventos (
            empresa_id, lote_id, fecha, tipo, cantidad, peso_kg, detalle,
            medicamento, dosis, usuario_registro, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?);
        """,
        (
            empresa_id,
            lote_id,
            (data.get("fecha") or _now()[:10])[:10],
            data["tipo"],
            data.get("cantidad"),
            data.get("peso_kg"),
            data.get("detalle") or "",
            data.get("medicamento") or "",
            data.get("dosis") or "",
            usuario,
            _now(),
        ),
    )
    eid = int(cur.lastrowid)
    if lote_id and data["tipo"] in ("faena", "venta") and data.get("cantidad"):
        cant = int(data["cantidad"])
        nuevo_estado = "faenado" if data["tipo"] == "faena" else "vendido"
        fecha = data.get("fecha") or _now()[:10]
        cur.execute(
            """
            UPDATE avi_lotes
            SET n_actual = CASE
                    WHEN COALESCE(n_actual,0) - ? < 0 THEN 0
                    ELSE COALESCE(n_actual,0) - ?
                END,
                estado = CASE
                    WHEN COALESCE(n_actual,0) - ? <= 0 THEN ?
                    ELSE estado
                END,
                fecha_cierre = CASE
                    WHEN COALESCE(n_actual,0) - ? <= 0 THEN ?
                    ELSE fecha_cierre
                END,
                updated_at = ?
            WHERE empresa_id = ? AND id = ?;
            """,
            (
                cant, cant,
                cant, nuevo_estado,
                cant, fecha,
                _now(),
                empresa_id,
                int(lote_id),
            ),
        )
    if lote_id and data["tipo"] == "pesada" and data.get("peso_kg") is not None:
        cur.execute(
            "UPDATE avi_lotes SET peso_actual_prom = ?, updated_at = ? WHERE empresa_id = ? AND id = ?;",
            (data["peso_kg"], _now(), empresa_id, int(lote_id)),
        )
    conn.commit()
    return eid


def listar_eventos(conn, empresa_id: int, lote_id: Optional[int] = None, limit: int = 100) -> List[dict]:
    cur = conn.cursor()
    if lote_id:
        cur.execute(
            """
            SELECT * FROM avi_eventos
            WHERE empresa_id = ? AND lote_id = ?
            ORDER BY fecha DESC, id DESC LIMIT ?;
            """,
            (empresa_id, lote_id, limit),
        )
    else:
        cur.execute(
            """
            SELECT e.*, l.codigo AS lote_codigo FROM avi_eventos e
            LEFT JOIN avi_lotes l ON l.id = e.lote_id
            WHERE e.empresa_id = ?
            ORDER BY e.fecha DESC, e.id DESC LIMIT ?;
            """,
            (empresa_id, limit),
        )
    return [dict(r) for r in cur.fetchall()]


def ficha_lote(conn, empresa_id: int, lote_id: int) -> Optional[dict]:
    lote = obtener_lote(conn, empresa_id, lote_id)
    if not lote:
        return None
    prod = listar_produccion(conn, empresa_id, lote_id=lote_id, limit=90)
    eventos = listar_eventos(conn, empresa_id, lote_id=lote_id, limit=100)
    n_ini = int(lote.get("n_inicial") or 0)
    n_act = int(lote.get("n_actual") or 0)
    mort_acum = sum(int(p.get("mortalidad") or 0) for p in prod)
    huevos = sum(int(p.get("huevos") or 0) for p in prod)
    alimento = sum(float(p.get("consumo_alimento_kg") or 0) for p in prod)
    pct_mort = round(100.0 * mort_acum / n_ini, 2) if n_ini else None
    # % postura aprox: huevos del último día / aves actuales
    ultimo = prod[0] if prod else None
    pct_postura = None
    if ultimo and n_act and lote.get("tipo") in ("ponedora", "reproductora"):
        pct_postura = round(100.0 * int(ultimo.get("huevos") or 0) / n_act, 1)
    ganancia = None
    if lote.get("peso_actual_prom") is not None and lote.get("peso_ingreso_prom") is not None:
        ganancia = round(float(lote["peso_actual_prom"]) - float(lote["peso_ingreso_prom"]), 3)
    return {
        "lote": lote,
        "produccion": prod,
        "eventos": eventos,
        "kpis": {
            "n_inicial": n_ini,
            "n_actual": n_act,
            "mortalidad_acum": mort_acum,
            "pct_mortalidad": pct_mort,
            "huevos_acum": huevos,
            "alimento_kg_acum": round(alimento, 1),
            "pct_postura_ultimo_dia": pct_postura,
            "ganancia_peso_kg": ganancia,
            "edad_dias": lote.get("edad_dias"),
        },
        "catalogos": {
            "tipos_lote": [{"codigo": c, "nombre": n} for c, n in TIPOS_LOTE],
            "lineas": list(LINEAS_COMUNES),
            "tipos_evento": list(TIPOS_EVENTO_AVI),
        },
    }
