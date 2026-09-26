# -*- coding: utf-8 -*-
"""
CAmpo+ — Producción porcina (ciclo completo).

Modelo típico argentino: granja → salas → animales de plantel (cerdas/padrillos)
y lotes de engorde. Eventos: servicio, parto, destete, vacunación, pesada, venta.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


CATEGORIAS_CERDO = (
    ("cachorra", "Cachorra de reposición"),
    ("cerda_vacia", "Cerda vacía"),
    ("cerda_gestante", "Cerda gestante"),
    ("cerda_lactante", "Cerda lactante"),
    ("padrillo", "Padrillo / verraco"),
    ("lechon", "Lechón (maternidad)"),
    ("destete", "Destete"),
    ("recria", "Recría"),
    ("engorde", "Engorde / terminación"),
)

TIPOS_SALA = (
    ("gestacion", "Gestación"),
    ("maternidad", "Maternidad"),
    ("destete", "Destete"),
    ("recria", "Recría"),
    ("engorde", "Engorde"),
    ("reproductores", "Reproductores"),
    ("cuarentena", "Cuarentena"),
)

TIPOS_EVENTO_POR = (
    "alta",
    "compra",
    "servicio",
    "parto",
    "destete",
    "vacunacion",
    "tratamiento",
    "pesada",
    "traslado",
    "cambio_categoria",
    "castracion",
    "mortandad",
    "venta",
    "descarte",
    "observacion",
)

SEXOS = ("Hembra", "Macho", "Indefinido")
ESTADOS = ("activo", "vendido", "muerto", "baja", "prestado")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_col(cur, table: str, col: str, decl: str) -> None:
    cols = {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl};")
        except Exception:
            pass


def init_porcino_schema(conn) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS por_granjas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            ubicacion TEXT,
            observaciones TEXT,
            activo INTEGER DEFAULT 1,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS por_salas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            granja_id INTEGER,
            nombre TEXT NOT NULL,
            tipo TEXT DEFAULT 'engorde',
            capacidad INTEGER DEFAULT 0,
            observaciones TEXT,
            activo INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS por_animales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            caravana TEXT,
            chip TEXT,
            nombre TEXT,
            sexo TEXT DEFAULT 'Hembra',
            raza TEXT,
            linea_genetica TEXT,
            categoria TEXT DEFAULT 'cerda_vacia',
            fecha_nacimiento TEXT,
            madre_id INTEGER,
            padre_id INTEGER,
            sala_id INTEGER,
            granja_id INTEGER,
            lote_id INTEGER,
            estado TEXT DEFAULT 'activo',
            peso_ultimo REAL,
            fecha_peso TEXT,
            condicion_corporal REAL,
            n_partos INTEGER DEFAULT 0,
            fecha_ultimo_servicio TEXT,
            fecha_ultimo_parto TEXT,
            origen TEXT,
            observaciones TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS por_lotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            codigo TEXT NOT NULL,
            granja_id INTEGER,
            sala_id INTEGER,
            etapa TEXT DEFAULT 'engorde',
            fecha_ingreso TEXT,
            n_inicial INTEGER DEFAULT 0,
            n_actual INTEGER DEFAULT 0,
            peso_ingreso_prom REAL,
            peso_actual_prom REAL,
            estado TEXT DEFAULT 'activo',
            observaciones TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS por_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            animal_id INTEGER,
            lote_id INTEGER,
            fecha TEXT NOT NULL,
            tipo TEXT NOT NULL,
            sala_id INTEGER,
            peso_kg REAL,
            cantidad INTEGER,
            detalle TEXT,
            medicamento TEXT,
            dosis TEXT,
            padrillo_id INTEGER,
            usuario_registro TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS por_partos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            cerda_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            nacidos_vivos INTEGER DEFAULT 0,
            nacidos_muertos INTEGER DEFAULT 0,
            momificados INTEGER DEFAULT 0,
            destetados INTEGER,
            fecha_destete TEXT,
            peso_destete_prom REAL,
            sala_id INTEGER,
            observaciones TEXT,
            created_at TEXT
        );
        """
    )
    conn.commit()
    _seed_si_vacio(conn)


def _seed_si_vacio(conn) -> None:
    cur = conn.cursor()
    # Semilla por empresa se hace en primera llamada con empresa_id
    pass


def asegurar_seed_empresa(conn, empresa_id: int) -> None:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS n FROM por_granjas WHERE empresa_id = ?;", (empresa_id,)
    )
    if int(cur.fetchone()["n"] or 0) > 0:
        return
    ahora = _now()
    cur.execute(
        """
        INSERT INTO por_granjas (empresa_id, nombre, ubicacion, created_at)
        VALUES (?, 'Granja principal', '', ?);
        """,
        (empresa_id, ahora),
    )
    gid = int(cur.lastrowid)
    for tipo, nombre in TIPOS_SALA:
        cur.execute(
            """
            INSERT INTO por_salas (empresa_id, granja_id, nombre, tipo, capacidad)
            VALUES (?, ?, ?, ?, 0);
            """,
            (empresa_id, gid, nombre, tipo),
        )
    conn.commit()


def listar_granjas(conn, empresa_id: int) -> List[dict]:
    asegurar_seed_empresa(conn, empresa_id)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM por_granjas WHERE empresa_id = ? AND activo = 1 ORDER BY nombre;",
        (empresa_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def listar_salas(conn, empresa_id: int, granja_id: Optional[int] = None) -> List[dict]:
    asegurar_seed_empresa(conn, empresa_id)
    cur = conn.cursor()
    if granja_id:
        cur.execute(
            """
            SELECT s.*, g.nombre AS granja_nombre
            FROM por_salas s
            LEFT JOIN por_granjas g ON g.id = s.granja_id
            WHERE s.empresa_id = ? AND s.activo = 1 AND s.granja_id = ?
            ORDER BY s.tipo, s.nombre;
            """,
            (empresa_id, granja_id),
        )
    else:
        cur.execute(
            """
            SELECT s.*, g.nombre AS granja_nombre
            FROM por_salas s
            LEFT JOIN por_granjas g ON g.id = s.granja_id
            WHERE s.empresa_id = ? AND s.activo = 1
            ORDER BY s.tipo, s.nombre;
            """,
            (empresa_id,),
        )
    return [dict(r) for r in cur.fetchall()]


def crear_sala(conn, empresa_id: int, data: dict) -> int:
    asegurar_seed_empresa(conn, empresa_id)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO por_salas (empresa_id, granja_id, nombre, tipo, capacidad, observaciones)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (
            empresa_id,
            data.get("granja_id"),
            (data.get("nombre") or "").strip(),
            data.get("tipo") or "engorde",
            int(data.get("capacidad") or 0),
            data.get("observaciones") or "",
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def resumen_porcino(conn, empresa_id: int) -> dict:
    asegurar_seed_empresa(conn, empresa_id)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT categoria, COUNT(*) AS n
        FROM por_animales
        WHERE empresa_id = ? AND estado = 'activo'
        GROUP BY categoria;
        """,
        (empresa_id,),
    )
    por_cat = [dict(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT COUNT(*) AS n FROM por_animales WHERE empresa_id = ? AND estado = 'activo';",
        (empresa_id,),
    )
    total = int(cur.fetchone()["n"] or 0)
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM por_animales
        WHERE empresa_id = ? AND estado = 'activo'
          AND categoria IN ('cerda_vacia','cerda_gestante','cerda_lactante','cachorra');
        """,
        (empresa_id,),
    )
    cerdas = int(cur.fetchone()["n"] or 0)
    cur.execute(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(nacidos_vivos),0) AS nv,
               COALESCE(SUM(COALESCE(destetados,0)),0) AS dest
        FROM por_partos
        WHERE empresa_id = ? AND fecha >= date('now','-90 days');
        """,
        (empresa_id,),
    )
    p = cur.fetchone()
    cur.execute(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(n_actual),0) AS cabezas
        FROM por_lotes WHERE empresa_id = ? AND estado = 'activo';
        """,
        (empresa_id,),
    )
    lotes = cur.fetchone()
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM por_eventos
        WHERE empresa_id = ? AND tipo = 'mortandad'
          AND fecha >= date('now','-30 days');
        """,
        (empresa_id,),
    )
    mort_30 = int(cur.fetchone()["n"] or 0)
    return {
        "total_activos": total,
        "cerdas_plantel": cerdas,
        "por_categoria": por_cat,
        "partos_90d": int(p["n"] or 0),
        "nacidos_vivos_90d": int(p["nv"] or 0),
        "destetados_90d": int(p["dest"] or 0),
        "lotes_activos": int(lotes["n"] or 0),
        "cabezas_en_lotes": int(lotes["cabezas"] or 0),
        "eventos_mortandad_30d": mort_30,
    }


def buscar_animales(
    conn,
    empresa_id: int,
    *,
    q: Optional[str] = None,
    categoria: Optional[str] = None,
    sala_id: Optional[int] = None,
    estado: str = "activo",
    limit: int = 500,
) -> List[dict]:
    cur = conn.cursor()
    where = ["a.empresa_id = ?"]
    params: list = [empresa_id]
    if estado and estado != "todos":
        where.append("a.estado = ?")
        params.append(estado)
    if categoria:
        where.append("a.categoria = ?")
        params.append(categoria)
    if sala_id:
        where.append("a.sala_id = ?")
        params.append(sala_id)
    if q:
        like = f"%{q.strip()}%"
        where.append(
            "(a.caravana LIKE ? OR a.chip LIKE ? OR a.nombre LIKE ? OR CAST(a.id AS TEXT) = ?)"
        )
        params.extend([like, like, like, q.strip()])
    params.append(max(1, min(int(limit or 500), 2000)))
    cur.execute(
        f"""
        SELECT a.*, s.nombre AS sala_nombre, g.nombre AS granja_nombre
        FROM por_animales a
        LEFT JOIN por_salas s ON s.id = a.sala_id
        LEFT JOIN por_granjas g ON g.id = a.granja_id
        WHERE {' AND '.join(where)}
        ORDER BY a.id DESC
        LIMIT ?;
        """,
        params,
    )
    return [dict(r) for r in cur.fetchall()]


def obtener_animal(conn, empresa_id: int, animal_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.*, s.nombre AS sala_nombre, g.nombre AS granja_nombre
        FROM por_animales a
        LEFT JOIN por_salas s ON s.id = a.sala_id
        LEFT JOIN por_granjas g ON g.id = a.granja_id
        WHERE a.empresa_id = ? AND a.id = ?;
        """,
        (empresa_id, animal_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def crear_animal(conn, empresa_id: int, data: dict, usuario: str = "") -> int:
    asegurar_seed_empresa(conn, empresa_id)
    ahora = _now()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO por_animales (
            empresa_id, caravana, chip, nombre, sexo, raza, linea_genetica,
            categoria, fecha_nacimiento, madre_id, padre_id, sala_id, granja_id,
            lote_id, estado, peso_ultimo, fecha_peso, condicion_corporal,
            origen, observaciones, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
        """,
        (
            empresa_id,
            (data.get("caravana") or "").strip() or None,
            (data.get("chip") or "").strip() or None,
            (data.get("nombre") or "").strip() or None,
            data.get("sexo") or "Hembra",
            (data.get("raza") or "").strip() or None,
            (data.get("linea_genetica") or "").strip() or None,
            data.get("categoria") or "cerda_vacia",
            data.get("fecha_nacimiento") or None,
            data.get("madre_id"),
            data.get("padre_id"),
            data.get("sala_id"),
            data.get("granja_id"),
            data.get("lote_id"),
            data.get("estado") or "activo",
            data.get("peso_ultimo"),
            data.get("fecha_peso") or None,
            data.get("condicion_corporal"),
            data.get("origen") or "",
            data.get("observaciones") or "",
            ahora,
            ahora,
        ),
    )
    aid = int(cur.lastrowid)
    _insert_evento(
        cur,
        empresa_id,
        {
            "animal_id": aid,
            "fecha": (data.get("fecha_nacimiento") or ahora)[:10],
            "tipo": "compra" if (data.get("origen") or "").lower().startswith("compra") else "alta",
            "peso_kg": data.get("peso_ultimo"),
            "sala_id": data.get("sala_id"),
            "detalle": data.get("observaciones") or "Alta plantel / animal",
            "usuario_registro": usuario,
        },
    )
    conn.commit()
    return aid


def actualizar_animal(conn, empresa_id: int, animal_id: int, data: dict) -> None:
    cur = conn.cursor()
    if not obtener_animal(conn, empresa_id, animal_id):
        raise ValueError("Animal no encontrado")
    allowed = (
        "caravana", "chip", "nombre", "sexo", "raza", "linea_genetica", "categoria",
        "fecha_nacimiento", "madre_id", "padre_id", "sala_id", "granja_id", "lote_id",
        "estado", "peso_ultimo", "fecha_peso", "condicion_corporal", "origen",
        "observaciones", "n_partos", "fecha_ultimo_servicio", "fecha_ultimo_parto",
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
    vals.extend([empresa_id, animal_id])
    cur.execute(
        f"UPDATE por_animales SET {', '.join(fields)} WHERE empresa_id = ? AND id = ?;",
        vals,
    )
    conn.commit()


def _insert_evento(cur, empresa_id: int, data: dict) -> int:
    cur.execute(
        """
        INSERT INTO por_eventos (
            empresa_id, animal_id, lote_id, fecha, tipo, sala_id, peso_kg,
            cantidad, detalle, medicamento, dosis, padrillo_id, usuario_registro, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?);
        """,
        (
            empresa_id,
            data.get("animal_id"),
            data.get("lote_id"),
            (data.get("fecha") or _now()[:10])[:10],
            data.get("tipo") or "observacion",
            data.get("sala_id"),
            data.get("peso_kg"),
            data.get("cantidad"),
            data.get("detalle") or "",
            data.get("medicamento") or "",
            data.get("dosis") or "",
            data.get("padrillo_id"),
            data.get("usuario_registro") or "",
            _now(),
        ),
    )
    return int(cur.lastrowid)


def registrar_evento(conn, empresa_id: int, data: dict, usuario: str = "") -> int:
    if data.get("tipo") not in TIPOS_EVENTO_POR:
        raise ValueError(f"Tipo de evento inválido: {data.get('tipo')}")
    cur = conn.cursor()
    data = dict(data)
    data["usuario_registro"] = usuario
    eid = _insert_evento(cur, empresa_id, data)

    aid = data.get("animal_id")
    if aid and data.get("tipo") == "servicio":
        cur.execute(
            """
            UPDATE por_animales
            SET categoria = 'cerda_gestante', fecha_ultimo_servicio = ?, updated_at = ?
            WHERE empresa_id = ? AND id = ?;
            """,
            (data.get("fecha") or _now()[:10], _now(), empresa_id, aid),
        )
    if aid and data.get("tipo") == "pesada" and data.get("peso_kg") is not None:
        cur.execute(
            """
            UPDATE por_animales
            SET peso_ultimo = ?, fecha_peso = ?, updated_at = ?
            WHERE empresa_id = ? AND id = ?;
            """,
            (data["peso_kg"], data.get("fecha") or _now()[:10], _now(), empresa_id, aid),
        )
    if aid and data.get("tipo") in ("mortandad", "venta", "descarte"):
        est = {"mortandad": "muerto", "venta": "vendido", "descarte": "baja"}[data["tipo"]]
        cur.execute(
            "UPDATE por_animales SET estado = ?, updated_at = ? WHERE empresa_id = ? AND id = ?;",
            (est, _now(), empresa_id, aid),
        )
    if aid and data.get("tipo") == "cambio_categoria" and data.get("detalle"):
        # detalle puede traer la nueva categoría
        cat = (data.get("detalle") or "").strip().lower()
        if cat in {c[0] for c in CATEGORIAS_CERDO}:
            cur.execute(
                "UPDATE por_animales SET categoria = ?, updated_at = ? WHERE empresa_id = ? AND id = ?;",
                (cat, _now(), empresa_id, aid),
            )
    if data.get("tipo") == "traslado" and data.get("sala_id") and aid:
        cur.execute(
            "UPDATE por_animales SET sala_id = ?, updated_at = ? WHERE empresa_id = ? AND id = ?;",
            (data["sala_id"], _now(), empresa_id, aid),
        )
    conn.commit()
    return eid


def registrar_parto(conn, empresa_id: int, data: dict, usuario: str = "") -> int:
    cur = conn.cursor()
    cerda_id = data.get("cerda_id")
    if not cerda_id or not obtener_animal(conn, empresa_id, int(cerda_id)):
        raise ValueError("Cerda no encontrada")
    fecha = (data.get("fecha") or _now()[:10])[:10]
    cur.execute(
        """
        INSERT INTO por_partos (
            empresa_id, cerda_id, fecha, nacidos_vivos, nacidos_muertos, momificados,
            destetados, fecha_destete, peso_destete_prom, sala_id, observaciones, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);
        """,
        (
            empresa_id,
            int(cerda_id),
            fecha,
            int(data.get("nacidos_vivos") or 0),
            int(data.get("nacidos_muertos") or 0),
            int(data.get("momificados") or 0),
            data.get("destetados"),
            data.get("fecha_destete"),
            data.get("peso_destete_prom"),
            data.get("sala_id"),
            data.get("observaciones") or "",
            _now(),
        ),
    )
    pid = int(cur.lastrowid)
    cur.execute(
        """
        UPDATE por_animales
        SET categoria = 'cerda_lactante', fecha_ultimo_parto = ?,
            n_partos = COALESCE(n_partos,0) + 1, updated_at = ?
        WHERE empresa_id = ? AND id = ?;
        """,
        (fecha, _now(), empresa_id, int(cerda_id)),
    )
    det = (
        f"NV {data.get('nacidos_vivos') or 0} / NM {data.get('nacidos_muertos') or 0}"
        f" / Mom {data.get('momificados') or 0}"
    )
    _insert_evento(
        cur,
        empresa_id,
        {
            "animal_id": int(cerda_id),
            "fecha": fecha,
            "tipo": "parto",
            "sala_id": data.get("sala_id"),
            "cantidad": int(data.get("nacidos_vivos") or 0),
            "detalle": det,
            "usuario_registro": usuario,
        },
    )
    conn.commit()
    return pid


def registrar_destete_parto(conn, empresa_id: int, parto_id: int, data: dict, usuario: str = "") -> None:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM por_partos WHERE empresa_id = ? AND id = ?;",
        (empresa_id, parto_id),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError("Parto no encontrado")
    parto = dict(row)
    fecha = (data.get("fecha_destete") or _now()[:10])[:10]
    dest = int(data.get("destetados") if data.get("destetados") is not None else parto.get("nacidos_vivos") or 0)
    cur.execute(
        """
        UPDATE por_partos
        SET destetados = ?, fecha_destete = ?, peso_destete_prom = ?
        WHERE id = ?;
        """,
        (dest, fecha, data.get("peso_destete_prom"), parto_id),
    )
    cur.execute(
        """
        UPDATE por_animales SET categoria = 'cerda_vacia', updated_at = ?
        WHERE empresa_id = ? AND id = ?;
        """,
        (_now(), empresa_id, parto["cerda_id"]),
    )
    _insert_evento(
        cur,
        empresa_id,
        {
            "animal_id": parto["cerda_id"],
            "fecha": fecha,
            "tipo": "destete",
            "cantidad": dest,
            "peso_kg": data.get("peso_destete_prom"),
            "detalle": f"Destete parto #{parto_id}",
            "usuario_registro": usuario,
        },
    )
    conn.commit()


def listar_partos(conn, empresa_id: int, cerda_id: Optional[int] = None, limit: int = 100) -> List[dict]:
    cur = conn.cursor()
    if cerda_id:
        cur.execute(
            """
            SELECT p.*, a.caravana AS cerda_caravana
            FROM por_partos p
            LEFT JOIN por_animales a ON a.id = p.cerda_id
            WHERE p.empresa_id = ? AND p.cerda_id = ?
            ORDER BY p.fecha DESC LIMIT ?;
            """,
            (empresa_id, cerda_id, limit),
        )
    else:
        cur.execute(
            """
            SELECT p.*, a.caravana AS cerda_caravana
            FROM por_partos p
            LEFT JOIN por_animales a ON a.id = p.cerda_id
            WHERE p.empresa_id = ?
            ORDER BY p.fecha DESC LIMIT ?;
            """,
            (empresa_id, limit),
        )
    return [dict(r) for r in cur.fetchall()]


def listar_eventos(
    conn,
    empresa_id: int,
    *,
    animal_id: Optional[int] = None,
    lote_id: Optional[int] = None,
    limit: int = 200,
) -> List[dict]:
    cur = conn.cursor()
    where = ["empresa_id = ?"]
    params: list = [empresa_id]
    if animal_id:
        where.append("animal_id = ?")
        params.append(animal_id)
    if lote_id:
        where.append("lote_id = ?")
        params.append(lote_id)
    params.append(max(1, min(int(limit or 200), 1000)))
    cur.execute(
        f"""
        SELECT * FROM por_eventos
        WHERE {' AND '.join(where)}
        ORDER BY fecha DESC, id DESC
        LIMIT ?;
        """,
        params,
    )
    return [dict(r) for r in cur.fetchall()]


def crear_lote(conn, empresa_id: int, data: dict) -> int:
    asegurar_seed_empresa(conn, empresa_id)
    cur = conn.cursor()
    n = int(data.get("n_inicial") or data.get("n_actual") or 0)
    cur.execute(
        """
        INSERT INTO por_lotes (
            empresa_id, codigo, granja_id, sala_id, etapa, fecha_ingreso,
            n_inicial, n_actual, peso_ingreso_prom, peso_actual_prom, estado,
            observaciones, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?);
        """,
        (
            empresa_id,
            (data.get("codigo") or "").strip(),
            data.get("granja_id"),
            data.get("sala_id"),
            data.get("etapa") or "engorde",
            data.get("fecha_ingreso") or _now()[:10],
            n,
            int(data.get("n_actual") if data.get("n_actual") is not None else n),
            data.get("peso_ingreso_prom"),
            data.get("peso_actual_prom") or data.get("peso_ingreso_prom"),
            data.get("estado") or "activo",
            data.get("observaciones") or "",
            _now(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def listar_lotes(conn, empresa_id: int, estado: str = "activo") -> List[dict]:
    cur = conn.cursor()
    if estado and estado != "todos":
        cur.execute(
            """
            SELECT l.*, s.nombre AS sala_nombre
            FROM por_lotes l
            LEFT JOIN por_salas s ON s.id = l.sala_id
            WHERE l.empresa_id = ? AND l.estado = ?
            ORDER BY l.fecha_ingreso DESC, l.id DESC;
            """,
            (empresa_id, estado),
        )
    else:
        cur.execute(
            """
            SELECT l.*, s.nombre AS sala_nombre
            FROM por_lotes l
            LEFT JOIN por_salas s ON s.id = l.sala_id
            WHERE l.empresa_id = ?
            ORDER BY l.fecha_ingreso DESC, l.id DESC;
            """,
            (empresa_id,),
        )
    return [dict(r) for r in cur.fetchall()]


def ficha_cerdo(conn, empresa_id: int, animal_id: int) -> Optional[dict]:
    animal = obtener_animal(conn, empresa_id, animal_id)
    if not animal:
        return None
    cur = conn.cursor()
    madre = padre = None
    if animal.get("madre_id"):
        madre = obtener_animal(conn, empresa_id, int(animal["madre_id"]))
    if animal.get("padre_id"):
        padre = obtener_animal(conn, empresa_id, int(animal["padre_id"]))
    cur.execute(
        """
        SELECT id, caravana, sexo, categoria, fecha_nacimiento, estado
        FROM por_animales
        WHERE empresa_id = ? AND (madre_id = ? OR padre_id = ?)
        ORDER BY fecha_nacimiento DESC LIMIT 50;
        """,
        (empresa_id, animal_id, animal_id),
    )
    crias = [dict(r) for r in cur.fetchall()]
    partos = listar_partos(conn, empresa_id, cerda_id=animal_id, limit=50)
    eventos = listar_eventos(conn, empresa_id, animal_id=animal_id, limit=200)
    cat_nombre = next((n for c, n in CATEGORIAS_CERDO if c == animal.get("categoria")), animal.get("categoria"))
    nv = sum(int(p.get("nacidos_vivos") or 0) for p in partos)
    dest = sum(int(p.get("destetados") or 0) for p in partos if p.get("destetados") is not None)
    n_partos = len(partos)
    return {
        "animal": animal,
        "categoria_nombre": cat_nombre,
        "genetica": {"madre": madre, "padre": padre, "crias": crias},
        "partos": partos,
        "eventos": eventos,
        "resumen_repro": {
            "n_partos": n_partos or animal.get("n_partos") or 0,
            "nacidos_vivos_total": nv,
            "destetados_total": dest,
            "prom_nv_parto": round(nv / n_partos, 2) if n_partos else None,
            "prom_dest_parto": round(dest / n_partos, 2) if n_partos else None,
            "fecha_ultimo_servicio": animal.get("fecha_ultimo_servicio"),
            "fecha_ultimo_parto": animal.get("fecha_ultimo_parto"),
        },
        "catalogos": {
            "categorias": [{"codigo": c, "nombre": n} for c, n in CATEGORIAS_CERDO],
            "tipos_evento": list(TIPOS_EVENTO_POR),
        },
    }
