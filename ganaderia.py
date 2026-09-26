# -*- coding: utf-8 -*-
"""
CAmpo+ — Ganadería y Tambo (ciclo productivo completo).

Cubre: Cría, Recría, Encierre, Feedlot tradicional, Ciclo completo y Tambo.
Tecnologías: caravana electrónica (EID/RFID), celos, IATF, pesadas,
diagnóstico de preñez, partos, lecturas de manga, producción lechera.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


SISTEMAS = (
    "cria",
    "recria",
    "encierre",
    "feedlot",
    "ciclo_completo",
    "tambo",
)

SEXOS = ("Hembra", "Macho", "Indefinido")

ESTADOS_ANIMAL = (
    "activo",
    "vendido",
    "muerto",
    "prestado",
    "baja",
)

RODEOS_TAMBO_SEED = (
    ("Vacas en ordeñe", "tambo"),
    ("Vacas secas", "tambo"),
    ("Vaquillonas", "tambo"),
    ("Recría lechera", "tambo"),
    ("Terneros/as", "tambo"),
)

# Campos extra de eventos (repro / bajas) — se agregan con ALTER si faltan
EVENTO_EXTRA_COLS = (
    ("score_celo", "TEXT"),
    ("toro_nombre", "TEXT"),
    ("pajuela", "TEXT"),
    ("facilidad_parto", "TEXT"),
    ("cria_sexo", "TEXT"),
    ("cria_peso", "REAL"),
    ("cria_destino", "TEXT"),
    ("cria_caravana", "TEXT"),
    ("motivo_baja", "TEXT"),
    ("comprador", "TEXT"),
    ("origen_dato", "TEXT"),
    ("ref_externa", "TEXT"),
)

ANIMAL_EXTRA_COLS = (
    ("rp", "TEXT"),
    ("pedigree", "TEXT"),
    ("condicion_corporal", "REAL"),
    ("estado_repro", "TEXT"),
    # Genética / Asociación Argentina de Angus (AAA)
    ("registro_aaa", "TEXT"),
    ("categoria_aaa", "TEXT"),
    ("color_capa", "TEXT"),
    ("criador_aaa", "TEXT"),
    ("prefijo_cabana", "TEXT"),
    ("fecha_registro_aaa", "TEXT"),
    ("dep_pn", "REAL"),
    ("dep_pd", "REAL"),
    ("dep_pf", "REAL"),
    ("dep_leche", "REAL"),
)

# Nomenclatura de registro usada por la Asociación Argentina de Angus
CATEGORIAS_AAA = (
    ("PP", "Puro de Pedigree"),
    ("PC", "Puro Controlado"),
    ("SR", "Sin registro AAA / comercial"),
)

COLORES_ANGUS = ("Negro", "Colorado")

# Razas típicas de tambo (no deben figurar en stock de carne)
RAZAS_LECHERAS = (
    "holando",
    "holstein",
    "jersey",
    "guernsey",
    "ayrshire",
    "pardo suizo",
    "brown swiss",
    "swedish red",
)


def es_raza_lechera(raza: Optional[str]) -> bool:
    r = (raza or "").strip().lower()
    if not r:
        return False
    return any(tok in r for tok in RAZAS_LECHERAS)

TIPOS_EVENTO = (
    "alta",
    "compra",
    "venta",
    "mortandad",
    "pesada",
    "lectura_eid",
    "celo",
    "iatf",
    "inseminacion",
    "servicio_natural",
    "diagnostico_prenez",
    "parto",
    "aborto",
    "destete",
    "cambio_categoria",
    "cambio_rodeo",
    "ingreso_feedlot",
    "salida_feedlot",
    "tratamiento",
    "vacunacion",
    "sanidad",
    "secado",
    "mastitis",
    "nota",
)

CATEGORIAS_SEED = [
    ("Ternero/a", "cria", "Indefinido"),
    ("Ternero", "cria", "Macho"),
    ("Ternera", "cria", "Hembra"),
    ("Vaquillona", "recria", "Hembra"),
    ("Novillito", "recria", "Macho"),
    ("Vaca de cría", "cria", "Hembra"),
    ("Toro", "cria", "Macho"),
    ("Novillo", "feedlot", "Macho"),
    ("Vaca invernada", "encierre", "Hembra"),
    ("Vaca lechera", "tambo", "Hembra"),
    ("Vaquillona lechera", "tambo", "Hembra"),
    ("Toro lechero", "tambo", "Macho"),
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_col(cursor, table: str, col: str, decl: str) -> None:
    cols = {r[1] for r in cursor.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl};")
        except Exception:
            pass


def init_ganaderia_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gan_categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            nombre TEXT NOT NULL,
            sistema TEXT DEFAULT 'cria',
            sexo TEXT DEFAULT 'Indefinido',
            activo INTEGER DEFAULT 1
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gan_rodeos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            nombre TEXT NOT NULL,
            sistema TEXT DEFAULT 'cria',
            campo_ref TEXT,
            superficie_has REAL DEFAULT 0,
            capacidad INTEGER DEFAULT 0,
            observaciones TEXT,
            activo INTEGER DEFAULT 1
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gan_animales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            caravana_visual TEXT,
            caravana_electronica TEXT,
            senasa_id TEXT,
            nombre TEXT,
            raza TEXT,
            sexo TEXT DEFAULT 'Indefinido',
            fecha_nacimiento TEXT,
            madre_id INTEGER,
            padre_id INTEGER,
            categoria_id INTEGER,
            rodeo_id INTEGER,
            sistema_actual TEXT DEFAULT 'cria',
            estado TEXT DEFAULT 'activo',
            peso_ultimo REAL,
            fecha_peso_ultimo TEXT,
            fecha_alta TEXT,
            origen TEXT,
            color TEXT,
            observaciones TEXT,
            es_tambo INTEGER DEFAULT 0,
            FOREIGN KEY(categoria_id) REFERENCES gan_categorias(id),
            FOREIGN KEY(rodeo_id) REFERENCES gan_rodeos(id),
            FOREIGN KEY(madre_id) REFERENCES gan_animales(id),
            FOREIGN KEY(padre_id) REFERENCES gan_animales(id)
        );
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gan_animal_eid_empresa
        ON gan_animales(empresa_id, caravana_electronica)
        WHERE caravana_electronica IS NOT NULL AND TRIM(caravana_electronica) != '';
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gan_animal_visual
        ON gan_animales(empresa_id, caravana_visual);
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gan_animal_estado
        ON gan_animales(empresa_id, estado, sistema_actual);
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gan_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            animal_id INTEGER,
            fecha TEXT NOT NULL,
            tipo TEXT NOT NULL,
            rodeo_id INTEGER,
            categoria_id INTEGER,
            peso_kg REAL,
            eid_leido TEXT,
            resultado TEXT,
            protocolo TEXT,
            toro_id INTEGER,
            semen_lote TEXT,
            tecnico TEXT,
            medicamento TEXT,
            dosis TEXT,
            litros REAL,
            costo REAL DEFAULT 0,
            detalle TEXT,
            usuario_registro TEXT,
            FOREIGN KEY(animal_id) REFERENCES gan_animales(id)
        );
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_gan_eventos_animal ON gan_eventos(animal_id, fecha);"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_gan_eventos_tipo ON gan_eventos(empresa_id, tipo, fecha);"
    )

    for col, decl in ANIMAL_EXTRA_COLS:
        _ensure_col(cursor, "gan_animales", col, decl)
    for col, decl in EVENTO_EXTRA_COLS:
        _ensure_col(cursor, "gan_eventos", col, decl)

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gan_protocolos_iatf (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            dias_protocolo INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1
        );
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tambo_establecimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            nombre TEXT NOT NULL,
            rodeo_id INTEGER,
            capacidad_ordeno INTEGER DEFAULT 0,
            observaciones TEXT,
            activo INTEGER DEFAULT 1
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tambo_produccion_diaria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            tambo_id INTEGER,
            fecha TEXT NOT NULL,
            turno TEXT DEFAULT 'mañana',
            litros_total REAL DEFAULT 0,
            vacas_ordenadas INTEGER DEFAULT 0,
            grasa_pct REAL,
            proteina_pct REAL,
            rcs INTEGER,
            urea REAL,
            temperatura_tanque REAL,
            observaciones TEXT,
            usuario_registro TEXT,
            FOREIGN KEY(tambo_id) REFERENCES tambo_establecimientos(id)
        );
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tambo_prod_dia
        ON tambo_produccion_diaria(empresa_id, tambo_id, fecha, turno);
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tambo_lactancias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            animal_id INTEGER NOT NULL,
            nro_lactancia INTEGER DEFAULT 1,
            fecha_parto TEXT,
            fecha_secado TEXT,
            dias_lactancia INTEGER DEFAULT 0,
            produccion_acum_litros REAL DEFAULT 0,
            estado TEXT DEFAULT 'en_ordeño',
            observaciones TEXT,
            FOREIGN KEY(animal_id) REFERENCES gan_animales(id)
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tambo_controles_lecheros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            animal_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            litros REAL DEFAULT 0,
            grasa_pct REAL,
            proteina_pct REAL,
            rcs INTEGER,
            lactancia_id INTEGER,
            observaciones TEXT,
            FOREIGN KEY(animal_id) REFERENCES gan_animales(id)
        );
        """
    )

    # Seeds categorías
    cursor.execute("SELECT COUNT(*) AS n FROM gan_categorias;")
    if int(cursor.fetchone()[0] or 0) == 0:
        for nombre, sistema, sexo in CATEGORIAS_SEED:
            cursor.execute(
                """
                INSERT INTO gan_categorias (empresa_id, nombre, sistema, sexo, activo)
                VALUES (1, ?, ?, ?, 1);
                """,
                (nombre, sistema, sexo),
            )

    # Seeds rodeos base
    cursor.execute("SELECT COUNT(*) AS n FROM gan_rodeos;")
    if int(cursor.fetchone()[0] or 0) == 0:
        for nombre, sistema in (
            ("Cría general", "cria"),
            ("Recría", "recria"),
            ("Encierre pastoril", "encierre"),
            ("Feedlot", "feedlot"),
            ("Tambo principal", "tambo"),
        ):
            cursor.execute(
                """
                INSERT INTO gan_rodeos (empresa_id, nombre, sistema, activo)
                VALUES (1, ?, ?, 1);
                """,
                (nombre, sistema),
            )

    # Rodeos operativos de tambo (idempotente por nombre)
    for nombre, sistema in RODEOS_TAMBO_SEED:
        cursor.execute(
            """
            SELECT id FROM gan_rodeos
            WHERE empresa_id = 1 AND LOWER(TRIM(nombre)) = LOWER(?) AND activo = 1
            LIMIT 1;
            """,
            (nombre,),
        )
        if not cursor.fetchone():
            cursor.execute(
                """
                INSERT INTO gan_rodeos (empresa_id, nombre, sistema, activo)
                VALUES (1, ?, ?, 1);
                """,
                (nombre, sistema),
            )

    # Protocolos IATF comunes
    cursor.execute("SELECT COUNT(*) AS n FROM gan_protocolos_iatf;")
    if int(cursor.fetchone()[0] or 0) == 0:
        for nombre, desc, dias in (
            ("IATF clásico 7 días", "Dispositivo + PGF2α + eCG / retiro + IATF 48-54h", 9),
            ("IATF J-Synch", "Protocolo J-Synch (dispositivo 6d + GnRH)", 8),
            ("Resincronización", "Resync post IATF con diagnóstico a 30d", 30),
        ):
            cursor.execute(
                """
                INSERT INTO gan_protocolos_iatf (empresa_id, nombre, descripcion, dias_protocolo, activo)
                VALUES (1, ?, ?, ?, 1);
                """,
                (nombre, desc, dias),
            )


def _row(r) -> dict:
    return dict(r) if r is not None else {}


def _asegurar_rodeos_tambo(conn, empresa_id: int) -> None:
    cur = conn.cursor()
    for nombre, sistema in RODEOS_TAMBO_SEED:
        cur.execute(
            """
            SELECT id FROM gan_rodeos
            WHERE empresa_id = ? AND LOWER(TRIM(nombre)) = LOWER(?) AND activo = 1
            LIMIT 1;
            """,
            (empresa_id, nombre),
        )
        if not cur.fetchone():
            cur.execute(
                """
                INSERT INTO gan_rodeos (empresa_id, nombre, sistema, activo)
                VALUES (?, ?, ?, 1);
                """,
                (empresa_id, nombre, sistema),
            )
    conn.commit()


def listar_categorias(conn, empresa_id: int) -> List[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM gan_categorias
        WHERE empresa_id = ? AND activo = 1
        ORDER BY sistema, nombre COLLATE NOCASE;
        """,
        (empresa_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def listar_rodeos(conn, empresa_id: int, sistema: Optional[str] = None) -> List[dict]:
    _asegurar_rodeos_tambo(conn, empresa_id)
    cur = conn.cursor()
    if sistema:
        cur.execute(
            """
            SELECT r.*,
                   (SELECT COUNT(*) FROM gan_animales a
                    WHERE a.rodeo_id = r.id AND a.estado = 'activo') AS cabezas
            FROM gan_rodeos r
            WHERE r.empresa_id = ? AND r.activo = 1 AND r.sistema = ?
            ORDER BY r.nombre COLLATE NOCASE;
            """,
            (empresa_id, sistema),
        )
    else:
        cur.execute(
            """
            SELECT r.*,
                   (SELECT COUNT(*) FROM gan_animales a
                    WHERE a.rodeo_id = r.id AND a.estado = 'activo') AS cabezas
            FROM gan_rodeos r
            WHERE r.empresa_id = ? AND r.activo = 1
            ORDER BY r.sistema, r.nombre COLLATE NOCASE;
            """,
            (empresa_id,),
        )
    return [dict(r) for r in cur.fetchall()]


def reclasificar_animales_lecheros(conn, empresa_id: int) -> int:
    """Marca Holando/Jersey/etc. como tambo y los saca del stock de carne."""
    _asegurar_rodeos_tambo(conn, empresa_id)
    cur = conn.cursor()
    likes = " OR ".join(["LOWER(COALESCE(raza,'')) LIKE ?" for _ in RAZAS_LECHERAS])
    params = [f"%{t}%" for t in RAZAS_LECHERAS]
    cur.execute(
        f"""
        SELECT id, sistema_actual, rodeo_id, peso_ultimo
        FROM gan_animales
        WHERE empresa_id = ? AND estado = 'activo'
          AND COALESCE(es_tambo, 0) = 0
          AND ({likes});
        """,
        [empresa_id, *params],
    )
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return 0
    cur.execute(
        """
        SELECT id, nombre FROM gan_rodeos
        WHERE empresa_id = ? AND sistema = 'tambo' AND activo = 1;
        """,
        (empresa_id,),
    )
    rodeos = {}
    for r in cur.fetchall():
        key = str(r["nombre"] or "").lower().replace("í", "i").replace("ñ", "n")
        rodeos[key] = int(r["id"])
    id_recria = rodeos.get("recria lechera")
    id_vaq = rodeos.get("vaquillonas")
    id_ordene = rodeos.get("vacas en ordene") or rodeos.get("vacas en ordeñe")
    n = 0
    for a in rows:
        peso = float(a.get("peso_ultimo") or 0)
        rodeo = a.get("rodeo_id")
        if not rodeo:
            if peso and peso < 180 and id_recria:
                rodeo = id_recria
            elif peso and peso < 400 and id_vaq:
                rodeo = id_vaq
            else:
                rodeo = id_ordene or id_vaq or id_recria
        cur.execute(
            """
            UPDATE gan_animales
            SET es_tambo = 1, sistema_actual = 'tambo', rodeo_id = COALESCE(?, rodeo_id)
            WHERE id = ?;
            """,
            (rodeo, a["id"]),
        )
        n += 1
    if n:
        conn.commit()
    return n


def resumen_stock(conn, empresa_id: int) -> dict:
    reclasificar_animales_lecheros(conn, empresa_id)
    cur = conn.cursor()
    filtro_carne = """
        empresa_id = ? AND estado = 'activo'
          AND COALESCE(es_tambo, 0) = 0
          AND COALESCE(sistema_actual, '') != 'tambo'
    """
    cur.execute(
        f"""
        SELECT sistema_actual AS sistema, COUNT(*) AS n,
               AVG(peso_ultimo) AS peso_promedio
        FROM gan_animales
        WHERE {filtro_carne}
        GROUP BY sistema_actual;
        """,
        (empresa_id,),
    )
    por_sistema = [dict(r) for r in cur.fetchall()]
    cur.execute(
        f"""
        SELECT sexo, COUNT(*) AS n FROM gan_animales
        WHERE {filtro_carne}
        GROUP BY sexo;
        """,
        (empresa_id,),
    )
    por_sexo = [dict(r) for r in cur.fetchall()]
    cur.execute(
        f"SELECT COUNT(*) AS n FROM gan_animales WHERE {filtro_carne};",
        (empresa_id,),
    )
    total = int(cur.fetchone()["n"] or 0)
    cur.execute(
        f"""
        SELECT COUNT(*) AS n FROM gan_animales
        WHERE {filtro_carne}
          AND caravana_electronica IS NOT NULL AND TRIM(caravana_electronica) != '';
        """,
        (empresa_id,),
    )
    con_eid = int(cur.fetchone()["n"] or 0)
    return {
        "total_activos": total,
        "con_eid": con_eid,
        "por_sistema": por_sistema,
        "por_sexo": por_sexo,
    }


def buscar_animales(
    conn,
    empresa_id: int,
    *,
    q: Optional[str] = None,
    sistema: Optional[str] = None,
    rodeo_id: Optional[int] = None,
    estado: str = "activo",
    es_tambo: Optional[int] = None,
    categoria_aaa: Optional[str] = None,
    raza: Optional[str] = None,
    limit: int = 500,
) -> List[dict]:
    if es_tambo == 0:
        reclasificar_animales_lecheros(conn, empresa_id)
    cur = conn.cursor()
    where = ["a.empresa_id = ?"]
    params: list = [empresa_id]
    if estado and estado != "todos":
        where.append("a.estado = ?")
        params.append(estado)
    if sistema:
        where.append("a.sistema_actual = ?")
        params.append(sistema)
    if rodeo_id:
        where.append("a.rodeo_id = ?")
        params.append(rodeo_id)
    if es_tambo is not None:
        if int(es_tambo) == 0:
            # Stock de carne: sin tambo ni razas lecheras
            where.append("COALESCE(a.es_tambo, 0) = 0")
            where.append("COALESCE(a.sistema_actual, '') != 'tambo'")
        else:
            where.append("(COALESCE(a.es_tambo, 0) = 1 OR COALESCE(a.sistema_actual, '') = 'tambo')")
    if categoria_aaa:
        where.append("UPPER(TRIM(COALESCE(a.categoria_aaa,''))) = ?")
        params.append(categoria_aaa.strip().upper())
    if raza:
        where.append("LOWER(COALESCE(a.raza,'')) LIKE ?")
        params.append(f"%{raza.strip().lower()}%")
    if q:
        like = f"%{q.strip()}%"
        where.append(
            """(
                a.caravana_visual LIKE ? OR a.caravana_electronica LIKE ?
                OR a.nombre LIKE ? OR a.senasa_id LIKE ?
                OR COALESCE(a.rp,'') LIKE ? OR COALESCE(a.registro_aaa,'') LIKE ?
                OR COALESCE(a.prefijo_cabana,'') LIKE ?
            )"""
        )
        params.extend([like, like, like, like, like, like, like])
    params.append(max(1, min(int(limit or 500), 2000)))
    sql = f"""
        SELECT a.*,
               c.nombre AS categoria_nombre,
               r.nombre AS rodeo_nombre
        FROM gan_animales a
        LEFT JOIN gan_categorias c ON c.id = a.categoria_id
        LEFT JOIN gan_rodeos r ON r.id = a.rodeo_id
        WHERE {' AND '.join(where)}
        ORDER BY a.id DESC
        LIMIT ?;
    """
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def obtener_animal(conn, empresa_id: int, animal_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.*,
               c.nombre AS categoria_nombre,
               r.nombre AS rodeo_nombre
        FROM gan_animales a
        LEFT JOIN gan_categorias c ON c.id = a.categoria_id
        LEFT JOIN gan_rodeos r ON r.id = a.rodeo_id
        WHERE a.empresa_id = ? AND a.id = ?;
        """,
        (empresa_id, animal_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _aplicar_campos_geneticos(cur, animal_id: int, data: dict) -> None:
    """Actualiza campos de genética AAA / pedigree si vinieron en el payload."""
    cat = (data.get("categoria_aaa") or "").strip().upper()
    if cat:
        codes = {c[0] for c in CATEGORIAS_AAA}
        if cat not in codes:
            for code, nombre in CATEGORIAS_AAA:
                if cat in nombre.upper() or nombre.upper() in cat:
                    cat = code
                    break
            else:
                cat = None

    candidatos = {
        "rp": (data.get("rp") or "").strip() or None,
        "pedigree": (data.get("pedigree") or "").strip() or None,
        "registro_aaa": (data.get("registro_aaa") or "").strip() or None,
        "categoria_aaa": cat,
        "color_capa": (data.get("color_capa") or data.get("color") or "").strip() or None,
        "criador_aaa": (data.get("criador_aaa") or "").strip() or None,
        "prefijo_cabana": (data.get("prefijo_cabana") or "").strip() or None,
        "fecha_registro_aaa": (data.get("fecha_registro_aaa") or "").strip() or None,
        "dep_pn": data.get("dep_pn"),
        "dep_pd": data.get("dep_pd"),
        "dep_pf": data.get("dep_pf"),
        "dep_leche": data.get("dep_leche"),
    }
    sets, vals = [], []
    for k, v in candidatos.items():
        if k not in data and not (k == "color_capa" and data.get("color")):
            continue
        if k.startswith("dep_") and data.get(k) is None:
            continue
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return
    vals.append(animal_id)
    cur.execute(f"UPDATE gan_animales SET {', '.join(sets)} WHERE id = ?;", vals)


def crear_animal(conn, empresa_id: int, data: dict, usuario: str = "") -> int:
    cur = conn.cursor()
    ahora = _now()
    raza = (data.get("raza") or "").strip() or None
    es_tambo = 1 if data.get("es_tambo") else 0
    sistema = data.get("sistema_actual") or "cria"
    if es_raza_lechera(raza) or sistema == "tambo":
        es_tambo = 1
        sistema = "tambo"
        _asegurar_rodeos_tambo(conn, empresa_id)
    cur.execute(
        """
        INSERT INTO gan_animales (
            empresa_id, caravana_visual, caravana_electronica, senasa_id, nombre,
            raza, sexo, fecha_nacimiento, madre_id, padre_id, categoria_id, rodeo_id,
            sistema_actual, estado, peso_ultimo, fecha_peso_ultimo, fecha_alta,
            origen, color, observaciones, es_tambo
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
        """,
        (
            empresa_id,
            (data.get("caravana_visual") or "").strip() or None,
            (data.get("caravana_electronica") or "").strip() or None,
            (data.get("senasa_id") or "").strip() or None,
            (data.get("nombre") or "").strip() or None,
            raza,
            data.get("sexo") or "Indefinido",
            data.get("fecha_nacimiento") or None,
            data.get("madre_id"),
            data.get("padre_id"),
            data.get("categoria_id"),
            data.get("rodeo_id"),
            sistema,
            data.get("estado") or "activo",
            data.get("peso_ultimo"),
            data.get("fecha_peso_ultimo") or (ahora[:10] if data.get("peso_ultimo") else None),
            data.get("fecha_alta") or ahora[:10],
            (data.get("origen") or "").strip() or None,
            (data.get("color") or "").strip() or None,
            (data.get("observaciones") or "").strip() or None,
            es_tambo,
        ),
    )
    aid = int(cur.lastrowid)
    _aplicar_campos_geneticos(cur, aid, data)
    _insert_evento(
        cur,
        empresa_id,
        {
            "animal_id": aid,
            "fecha": data.get("fecha_alta") or ahora[:10],
            "tipo": "compra" if (data.get("origen") or "").lower().startswith("compra") else "alta",
            "peso_kg": data.get("peso_ultimo"),
            "eid_leido": data.get("caravana_electronica"),
            "rodeo_id": data.get("rodeo_id"),
            "categoria_id": data.get("categoria_id"),
            "detalle": data.get("observaciones") or "Alta de animal",
            "usuario_registro": usuario,
        },
    )
    if data.get("peso_ultimo"):
        _insert_evento(
            cur,
            empresa_id,
            {
                "animal_id": aid,
                "fecha": data.get("fecha_peso_ultimo") or ahora[:10],
                "tipo": "pesada",
                "peso_kg": data.get("peso_ultimo"),
                "detalle": "Pesada de alta",
                "usuario_registro": usuario,
            },
        )
    conn.commit()
    return aid


def actualizar_animal(conn, empresa_id: int, animal_id: int, data: dict) -> None:
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM gan_animales WHERE empresa_id = ? AND id = ?;",
        (empresa_id, animal_id),
    )
    if not cur.fetchone():
        raise ValueError("Animal no encontrado")
    fields = []
    vals: list = []
    allowed = (
        "caravana_visual",
        "caravana_electronica",
        "senasa_id",
        "nombre",
        "raza",
        "sexo",
        "fecha_nacimiento",
        "madre_id",
        "padre_id",
        "categoria_id",
        "rodeo_id",
        "sistema_actual",
        "estado",
        "origen",
        "color",
        "observaciones",
        "es_tambo",
        "rp",
        "pedigree",
        "condicion_corporal",
        "estado_repro",
        "registro_aaa",
        "categoria_aaa",
        "color_capa",
        "criador_aaa",
        "prefijo_cabana",
        "fecha_registro_aaa",
        "dep_pn",
        "dep_pd",
        "dep_pf",
        "dep_leche",
    )
    for k in allowed:
        if k in data:
            fields.append(f"{k} = ?")
            v = data[k]
            if k in ("caravana_visual", "caravana_electronica", "senasa_id", "nombre", "raza", "origen", "color", "observaciones"):
                v = (v or "").strip() or None
            if k == "es_tambo":
                v = 1 if v else 0
            vals.append(v)
    if not fields:
        return
    vals.extend([empresa_id, animal_id])
    cur.execute(
        f"UPDATE gan_animales SET {', '.join(fields)} WHERE empresa_id = ? AND id = ?;",
        vals,
    )
    conn.commit()


def _insert_evento(cur, empresa_id: int, data: dict) -> int:
    cur.execute(
        """
        INSERT INTO gan_eventos (
            empresa_id, animal_id, fecha, tipo, rodeo_id, categoria_id, peso_kg,
            eid_leido, resultado, protocolo, toro_id, semen_lote, tecnico,
            medicamento, dosis, litros, costo, detalle, usuario_registro,
            score_celo, toro_nombre, pajuela, facilidad_parto, cria_sexo,
            cria_peso, cria_destino, cria_caravana, motivo_baja, comprador,
            origen_dato, ref_externa
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
        """,
        (
            empresa_id,
            data.get("animal_id"),
            data.get("fecha") or _now()[:10],
            data.get("tipo") or "nota",
            data.get("rodeo_id"),
            data.get("categoria_id"),
            data.get("peso_kg"),
            (data.get("eid_leido") or "").strip() or None,
            data.get("resultado"),
            data.get("protocolo"),
            data.get("toro_id"),
            data.get("semen_lote"),
            data.get("tecnico"),
            data.get("medicamento"),
            data.get("dosis"),
            data.get("litros"),
            float(data.get("costo") or 0),
            data.get("detalle"),
            data.get("usuario_registro") or "",
            data.get("score_celo"),
            data.get("toro_nombre"),
            data.get("pajuela"),
            data.get("facilidad_parto"),
            data.get("cria_sexo"),
            data.get("cria_peso"),
            data.get("cria_destino"),
            data.get("cria_caravana"),
            data.get("motivo_baja"),
            data.get("comprador"),
            data.get("origen_dato") or "manual",
            data.get("ref_externa"),
        ),
    )
    return int(cur.lastrowid)


def registrar_evento(conn, empresa_id: int, data: dict, usuario: str = "") -> dict:
    """Registra un evento y actualiza el animal cuando corresponde."""
    tipo = (data.get("tipo") or "").strip().lower()
    if tipo not in TIPOS_EVENTO:
        raise ValueError(f"Tipo de evento no válido: {tipo}")
    animal_id = data.get("animal_id")
    cur = conn.cursor()
    animal = None
    if animal_id:
        cur.execute(
            "SELECT * FROM gan_animales WHERE empresa_id = ? AND id = ?;",
            (empresa_id, animal_id),
        )
        animal = cur.fetchone()
        if not animal:
            raise ValueError("Animal no encontrado")
        animal = dict(animal)

    # Resolver por EID si no hay animal_id
    eid = (data.get("eid_leido") or "").strip()
    if not animal_id and eid:
        cur.execute(
            """
            SELECT * FROM gan_animales
            WHERE empresa_id = ? AND caravana_electronica = ?;
            """,
            (empresa_id, eid),
        )
        row = cur.fetchone()
        if row:
            animal = dict(row)
            animal_id = animal["id"]
            data["animal_id"] = animal_id

    data = dict(data)
    data["usuario_registro"] = usuario or data.get("usuario_registro") or ""
    eid_id = _insert_evento(cur, empresa_id, data)

    if animal_id and tipo == "pesada" and data.get("peso_kg") is not None:
        cur.execute(
            """
            UPDATE gan_animales
            SET peso_ultimo = ?, fecha_peso_ultimo = ?
            WHERE id = ?;
            """,
            (float(data["peso_kg"]), data.get("fecha") or _now()[:10], animal_id),
        )
    if animal_id and tipo == "lectura_eid" and eid:
        cur.execute(
            """
            UPDATE gan_animales SET caravana_electronica = ?
            WHERE id = ? AND (caravana_electronica IS NULL OR TRIM(caravana_electronica) = '');
            """,
            (eid, animal_id),
        )
    if animal_id and tipo == "cambio_rodeo" and data.get("rodeo_id"):
        cur.execute(
            "UPDATE gan_animales SET rodeo_id = ? WHERE id = ?;",
            (data["rodeo_id"], animal_id),
        )
    if animal_id and tipo == "cambio_categoria" and data.get("categoria_id"):
        cur.execute(
            "UPDATE gan_animales SET categoria_id = ? WHERE id = ?;",
            (data["categoria_id"], animal_id),
        )
    if animal_id and tipo in ("ingreso_feedlot", "salida_feedlot", "destete"):
        sistema = data.get("sistema_destino")
        if not sistema:
            sistema = {
                "ingreso_feedlot": "feedlot",
                "salida_feedlot": "cria",
                "destete": "recria",
            }.get(tipo)
        if sistema:
            cur.execute(
                "UPDATE gan_animales SET sistema_actual = ? WHERE id = ?;",
                (sistema, animal_id),
            )
        if data.get("rodeo_id"):
            cur.execute(
                "UPDATE gan_animales SET rodeo_id = ? WHERE id = ?;",
                (data["rodeo_id"], animal_id),
            )
    if animal_id and tipo == "venta":
        cur.execute(
            "UPDATE gan_animales SET estado = 'vendido' WHERE id = ?;",
            (animal_id,),
        )
    if animal_id and tipo == "mortandad":
        cur.execute(
            "UPDATE gan_animales SET estado = 'muerto' WHERE id = ?;",
            (animal_id,),
        )
    if animal_id and tipo == "parto":
        # Abrir lactancia si es tambo
        if animal and (animal.get("es_tambo") or animal.get("sistema_actual") == "tambo"):
            cur.execute(
                """
                SELECT COALESCE(MAX(nro_lactancia), 0) AS n FROM tambo_lactancias
                WHERE animal_id = ?;
                """,
                (animal_id,),
            )
            nro = int(cur.fetchone()["n"] or 0) + 1
            cur.execute(
                """
                INSERT INTO tambo_lactancias (
                    empresa_id, animal_id, nro_lactancia, fecha_parto, estado
                ) VALUES (?, ?, ?, ?, 'en_ordeño');
                """,
                (empresa_id, animal_id, nro, data.get("fecha") or _now()[:10]),
            )
            # Mover a rodeo en ordeñe si existe
            cur.execute(
                """
                SELECT id FROM gan_rodeos
                WHERE empresa_id = ? AND activo = 1
                  AND LOWER(nombre) LIKE '%orde%'
                ORDER BY id LIMIT 1;
                """,
                (empresa_id,),
            )
            rodeo_ord = cur.fetchone()
            if rodeo_ord:
                cur.execute(
                    "UPDATE gan_animales SET rodeo_id = ?, estado_repro = 'en_ordeño' WHERE id = ?;",
                    (int(rodeo_ord["id"]), animal_id),
                )
        # Alta de cría si vino caravana / datos
        cria_vis = (data.get("cria_caravana") or "").strip()
        if cria_vis or data.get("cria_sexo"):
            cria_id = crear_animal(
                conn,
                empresa_id,
                {
                    "caravana_visual": cria_vis or None,
                    "sexo": data.get("cria_sexo") or "Indefinido",
                    "fecha_nacimiento": data.get("fecha") or _now()[:10],
                    "madre_id": animal_id,
                    "padre_id": data.get("toro_id"),
                    "sistema_actual": "tambo" if animal and animal.get("es_tambo") else (
                        animal.get("sistema_actual") if animal else "cria"
                    ),
                    "peso_ultimo": data.get("cria_peso"),
                    "fecha_peso_ultimo": data.get("fecha") if data.get("cria_peso") else None,
                    "origen": "Nacimiento",
                    "es_tambo": 1 if animal and animal.get("es_tambo") else 0,
                    "observaciones": data.get("cria_destino") or "",
                },
                usuario=usuario,
            )
            extra = f"Cría #{cria_id}" + (f" {cria_vis}" if cria_vis else "")
            cur.execute(
                """
                UPDATE gan_eventos
                SET detalle = TRIM(COALESCE(detalle,'') || ' | ' || ?)
                WHERE id = ?;
                """,
                (extra, eid_id),
            )

    if animal_id and tipo == "secado":
        cur.execute(
            """
            UPDATE tambo_lactancias SET estado = 'seca', fecha_secado = ?
            WHERE animal_id = ? AND estado = 'en_ordeño';
            """,
            (data.get("fecha") or _now()[:10], animal_id),
        )
        cur.execute(
            """
            SELECT id FROM gan_rodeos
            WHERE empresa_id = ? AND activo = 1 AND LOWER(nombre) LIKE '%seca%'
            ORDER BY id LIMIT 1;
            """,
            (empresa_id,),
        )
        rodeo_seca = cur.fetchone()
        if rodeo_seca:
            cur.execute(
                "UPDATE gan_animales SET rodeo_id = ?, estado_repro = 'seca' WHERE id = ?;",
                (int(rodeo_seca["id"]), animal_id),
            )

    if animal_id and tipo in ("celo", "inseminacion", "iatf", "servicio_natural", "diagnostico_prenez"):
        mapa_repro = {
            "celo": "en_celo",
            "inseminacion": "servida",
            "iatf": "servida",
            "servicio_natural": "servida",
            "diagnostico_prenez": (data.get("resultado") or "diagnostico").strip().lower()[:40] or "diagnosticada",
        }
        cur.execute(
            "UPDATE gan_animales SET estado_repro = ? WHERE id = ?;",
            (mapa_repro.get(tipo, tipo), animal_id),
        )

    if animal_id and tipo in ("venta", "mortandad") and data.get("motivo_baja"):
        pass  # ya guardado en el evento

    conn.commit()
    return {"evento_id": eid_id, "animal_id": animal_id}


def importar_lecturas_eid(conn, empresa_id: int, lecturas: List[dict], usuario: str = "") -> dict:
    """
    Importa lecturas de mangueo / stick reader.
    Cada item: {eid, fecha?, peso_kg?, caravana_visual?, crear_si_no_existe?}
    """
    creados = 0
    actualizados = 0
    eventos = 0
    desconocidos = []
    cur = conn.cursor()
    for item in lecturas:
        eid = (item.get("eid") or item.get("caravana_electronica") or "").strip()
        if not eid:
            continue
        fecha = item.get("fecha") or _now()[:10]
        peso = item.get("peso_kg")
        cur.execute(
            """
            SELECT id FROM gan_animales
            WHERE empresa_id = ? AND caravana_electronica = ?;
            """,
            (empresa_id, eid),
        )
        row = cur.fetchone()
        if row:
            animal_id = int(row["id"])
            actualizados += 1
        elif item.get("crear_si_no_existe"):
            animal_id = crear_animal(
                conn,
                empresa_id,
                {
                    "caravana_electronica": eid,
                    "caravana_visual": item.get("caravana_visual"),
                    "sexo": item.get("sexo") or "Indefinido",
                    "sistema_actual": item.get("sistema_actual") or "cria",
                    "rodeo_id": item.get("rodeo_id"),
                    "peso_ultimo": peso,
                    "fecha_peso_ultimo": fecha if peso else None,
                    "origen": "Lectura EID",
                },
                usuario=usuario,
            )
            creados += 1
            eventos += 1
            continue
        else:
            desconocidos.append(eid)
            continue

        registrar_evento(
            conn,
            empresa_id,
            {
                "animal_id": animal_id,
                "fecha": fecha,
                "tipo": "lectura_eid",
                "eid_leido": eid,
                "peso_kg": peso,
                "detalle": item.get("detalle") or "Lectura stick/manga",
            },
            usuario=usuario,
        )
        eventos += 1
        if peso is not None:
            registrar_evento(
                conn,
                empresa_id,
                {
                    "animal_id": animal_id,
                    "fecha": fecha,
                    "tipo": "pesada",
                    "peso_kg": peso,
                    "detalle": "Pesada asociada a lectura EID",
                },
                usuario=usuario,
            )
            eventos += 1
    return {
        "creados": creados,
        "actualizados": actualizados,
        "eventos": eventos,
        "desconocidos": desconocidos,
    }


def listar_eventos(
    conn,
    empresa_id: int,
    *,
    animal_id: Optional[int] = None,
    tipo: Optional[str] = None,
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    limit: int = 200,
) -> List[dict]:
    cur = conn.cursor()
    where = ["e.empresa_id = ?"]
    params: list = [empresa_id]
    if animal_id:
        where.append("e.animal_id = ?")
        params.append(animal_id)
    if tipo:
        where.append("e.tipo = ?")
        params.append(tipo)
    if desde:
        where.append("e.fecha >= ?")
        params.append(desde)
    if hasta:
        where.append("e.fecha <= ?")
        params.append(hasta)
    params.append(max(1, min(int(limit or 200), 1000)))
    cur.execute(
        f"""
        SELECT e.*,
               a.caravana_visual, a.caravana_electronica, a.nombre AS animal_nombre
        FROM gan_eventos e
        LEFT JOIN gan_animales a ON a.id = e.animal_id
        WHERE {' AND '.join(where)}
        ORDER BY e.fecha DESC, e.id DESC
        LIMIT ?;
        """,
        params,
    )
    return [dict(r) for r in cur.fetchall()]


def listar_protocolos_iatf(conn, empresa_id: int) -> List[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM gan_protocolos_iatf
        WHERE empresa_id = ? AND activo = 1 ORDER BY nombre COLLATE NOCASE;
        """,
        (empresa_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def ficha_animal(conn, empresa_id: int, animal_id: int) -> Optional[dict]:
    """Ficha unificada: identificación, genética, timeline, lactancias y producción."""
    animal = obtener_animal(conn, empresa_id, animal_id)
    if not animal:
        return None
    cur = conn.cursor()

    def _ref(aid):
        if not aid:
            return None
        cur.execute(
            """
            SELECT id, caravana_visual, caravana_electronica, nombre, rp, raza, sexo
            FROM gan_animales WHERE empresa_id = ? AND id = ?;
            """,
            (empresa_id, aid),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    madre = _ref(animal.get("madre_id"))
    padre = _ref(animal.get("padre_id"))

    cur.execute(
        """
        SELECT id, caravana_visual, caravana_electronica, nombre, sexo,
               fecha_nacimiento, peso_ultimo, estado, sistema_actual
        FROM gan_animales
        WHERE empresa_id = ? AND madre_id = ?
        ORDER BY fecha_nacimiento DESC, id DESC;
        """,
        (empresa_id, animal_id),
    )
    crias = [dict(r) for r in cur.fetchall()]

    eventos = listar_eventos(conn, empresa_id, animal_id=animal_id, limit=500)
    tipos_repro = {
        "celo", "iatf", "inseminacion", "servicio_natural",
        "diagnostico_prenez", "parto", "aborto", "secado",
    }
    eventos_repro = [e for e in eventos if e.get("tipo") in tipos_repro]
    eventos_baja = [e for e in eventos if e.get("tipo") in ("venta", "mortandad")]

    def _ultimo(tipos):
        for e in eventos:
            if e.get("tipo") in tipos:
                return e
        return None

    cur.execute(
        """
        SELECT * FROM tambo_lactancias
        WHERE empresa_id = ? AND animal_id = ?
        ORDER BY nro_lactancia DESC;
        """,
        (empresa_id, animal_id),
    )
    lactancias = [dict(r) for r in cur.fetchall()]

    cur.execute(
        """
        SELECT * FROM tambo_controles_lecheros
        WHERE empresa_id = ? AND animal_id = ?
        ORDER BY fecha DESC, id DESC
        LIMIT 60;
        """,
        (empresa_id, animal_id),
    )
    controles = [dict(r) for r in cur.fetchall()]

    prod_prom = None
    if controles:
        litros = [float(c["litros"] or 0) for c in controles if c.get("litros") is not None]
        if litros:
            prod_prom = round(sum(litros) / len(litros), 1)

    return {
        "animal": animal,
        "genetica": {
            "madre": madre,
            "padre": padre,
            "crias": crias,
            "pedigree": _armar_pedigree(conn, empresa_id, animal, profundidad=3),
            "aaa": {
                "categorias": [{"codigo": c, "nombre": n} for c, n in CATEGORIAS_AAA],
                "colores": list(COLORES_ANGUS),
                "registro": animal.get("registro_aaa"),
                "categoria": animal.get("categoria_aaa"),
                "categoria_nombre": next(
                    (n for c, n in CATEGORIAS_AAA if c == (animal.get("categoria_aaa") or "")),
                    None,
                ),
                "color_capa": animal.get("color_capa") or animal.get("color"),
                "criador": animal.get("criador_aaa"),
                "prefijo": animal.get("prefijo_cabana"),
                "fecha_registro": animal.get("fecha_registro_aaa"),
                "deps": {
                    "pn": animal.get("dep_pn"),
                    "pd": animal.get("dep_pd"),
                    "pf": animal.get("dep_pf"),
                    "leche": animal.get("dep_leche"),
                },
                "es_angus": "angus" in (animal.get("raza") or "").lower(),
            },
        },
        "resumen_repro": {
            "estado_repro": animal.get("estado_repro"),
            "ultimo_celo": _ultimo({"celo"}),
            "ultimo_servicio": _ultimo({"iatf", "inseminacion", "servicio_natural"}),
            "ultimo_diagnostico": _ultimo({"diagnostico_prenez"}),
            "ultimo_parto": _ultimo({"parto"}),
            "ultimo_secado": _ultimo({"secado"}),
            "n_servicios": sum(
                1 for e in eventos_repro
                if e.get("tipo") in ("iatf", "inseminacion", "servicio_natural")
            ),
            "n_partos": sum(1 for e in eventos_repro if e.get("tipo") == "parto"),
        },
        "eventos": eventos,
        "eventos_repro": eventos_repro,
        "eventos_baja": eventos_baja,
        "lactancias": lactancias,
        "controles_lecheros": controles,
        "productivo": {
            "promedio_litros_control": prod_prom,
            "n_controles": len(controles),
            "lactancia_activa": next(
                (l for l in lactancias if l.get("estado") == "en_ordeño"), None
            ),
        },
    }


def _nodo_pedigree(conn, empresa_id: int, animal: Optional[dict], profundidad: int) -> Optional[dict]:
    if not animal or profundidad < 0:
        return None
    cur = conn.cursor()

    def _load(aid):
        if not aid:
            return None
        cur.execute(
            """
            SELECT id, caravana_visual, nombre, registro_aaa, categoria_aaa,
                   raza, color_capa, sexo, prefijo_cabana, madre_id, padre_id
            FROM gan_animales WHERE empresa_id = ? AND id = ?;
            """,
            (empresa_id, aid),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    madre = _load(animal.get("madre_id"))
    padre = _load(animal.get("padre_id"))
    return {
        "id": animal.get("id"),
        "etiqueta": animal.get("caravana_visual")
        or animal.get("nombre")
        or (f"#{animal.get('id')}" if animal.get("id") else "—"),
        "registro_aaa": animal.get("registro_aaa"),
        "categoria_aaa": animal.get("categoria_aaa"),
        "raza": animal.get("raza"),
        "color_capa": animal.get("color_capa"),
        "sexo": animal.get("sexo"),
        "prefijo_cabana": animal.get("prefijo_cabana"),
        "madre": _nodo_pedigree(conn, empresa_id, madre, profundidad - 1) if profundidad > 0 else None,
        "padre": _nodo_pedigree(conn, empresa_id, padre, profundidad - 1) if profundidad > 0 else None,
    }


def _armar_pedigree(conn, empresa_id: int, animal: dict, profundidad: int = 3) -> dict:
    return _nodo_pedigree(conn, empresa_id, animal, profundidad) or {}


def importar_eventos_masivo(
    conn, empresa_id: int, filas: List[dict], usuario: str = ""
) -> dict:
    """
    Carga masiva de eventos reproductivos/productivos.
    Cada fila: caravana o eid o animal_id + tipo + fecha + campos opcionales.
    """
    ok = 0
    errores = []
    for i, raw in enumerate(filas, start=1):
        try:
            data = dict(raw)
            animal_id = data.get("animal_id")
            if not animal_id:
                car = (data.get("caravana") or data.get("caravana_visual") or "").strip()
                eid = (data.get("eid") or data.get("caravana_electronica") or "").strip()
                rp = (data.get("rp") or "").strip()
                cur = conn.cursor()
                row = None
                if eid:
                    cur.execute(
                        """
                        SELECT id FROM gan_animales
                        WHERE empresa_id = ? AND caravana_electronica = ?;
                        """,
                        (empresa_id, eid),
                    )
                    row = cur.fetchone()
                if not row and car:
                    cur.execute(
                        """
                        SELECT id FROM gan_animales
                        WHERE empresa_id = ? AND LOWER(TRIM(caravana_visual)) = LOWER(?);
                        """,
                        (empresa_id, car),
                    )
                    row = cur.fetchone()
                if not row and rp:
                    cur.execute(
                        """
                        SELECT id FROM gan_animales
                        WHERE empresa_id = ? AND LOWER(TRIM(COALESCE(rp,''))) = LOWER(?);
                        """,
                        (empresa_id, rp),
                    )
                    row = cur.fetchone()
                if not row:
                    raise ValueError("Animal no encontrado (caravana/EID/RP)")
                data["animal_id"] = int(row["id"])
            data["origen_dato"] = data.get("origen_dato") or "import_masivo"
            registrar_evento(conn, empresa_id, data, usuario=usuario)
            ok += 1
        except Exception as exc:
            errores.append({"fila": i, "error": str(exc), "dato": raw})
    return {"ok": ok, "errores": errores, "total": len(filas)}


def composicion_rodeos_tambo(conn, empresa_id: int) -> List[dict]:
    """Cabezas activas por rodeo de sistema tambo (+ sin rodeo)."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COALESCE(r.id, 0) AS rodeo_id,
               COALESCE(r.nombre, 'Sin rodeo') AS rodeo,
               COUNT(*) AS cabezas
        FROM gan_animales a
        LEFT JOIN gan_rodeos r ON r.id = a.rodeo_id
        WHERE a.empresa_id = ? AND a.estado = 'activo'
          AND (a.es_tambo = 1 OR a.sistema_actual = 'tambo' OR COALESCE(r.sistema,'') = 'tambo')
        GROUP BY COALESCE(r.id, 0), COALESCE(r.nombre, 'Sin rodeo')
        ORDER BY cabezas DESC, rodeo COLLATE NOCASE;
        """,
        (empresa_id,),
    )
    return [dict(r) for r in cur.fetchall()]


# ——— Tambo ———

def listar_tambos(conn, empresa_id: int) -> List[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT t.*,
               (SELECT COUNT(*) FROM gan_animales a
                WHERE a.empresa_id = t.empresa_id AND a.es_tambo = 1 AND a.estado = 'activo') AS vacas_activas
        FROM tambo_establecimientos t
        WHERE t.empresa_id = ? AND t.activo = 1
        ORDER BY t.nombre COLLATE NOCASE;
        """,
        (empresa_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        # auto-crear tambo default
        cur.execute(
            """
            INSERT INTO tambo_establecimientos (empresa_id, nombre, activo)
            VALUES (?, 'Tambo principal', 1);
            """,
            (empresa_id,),
        )
        conn.commit()
        return listar_tambos(conn, empresa_id)
    return rows


def registrar_produccion_diaria(conn, empresa_id: int, data: dict, usuario: str = "") -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO tambo_produccion_diaria (
            empresa_id, tambo_id, fecha, turno, litros_total, vacas_ordenadas,
            grasa_pct, proteina_pct, rcs, urea, temperatura_tanque,
            observaciones, usuario_registro
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(empresa_id, tambo_id, fecha, turno) DO UPDATE SET
            litros_total = excluded.litros_total,
            vacas_ordenadas = excluded.vacas_ordenadas,
            grasa_pct = excluded.grasa_pct,
            proteina_pct = excluded.proteina_pct,
            rcs = excluded.rcs,
            urea = excluded.urea,
            temperatura_tanque = excluded.temperatura_tanque,
            observaciones = excluded.observaciones,
            usuario_registro = excluded.usuario_registro;
        """,
        (
            empresa_id,
            data.get("tambo_id"),
            data.get("fecha") or _now()[:10],
            data.get("turno") or "mañana",
            float(data.get("litros_total") or 0),
            int(data.get("vacas_ordenadas") or 0),
            data.get("grasa_pct"),
            data.get("proteina_pct"),
            data.get("rcs"),
            data.get("urea"),
            data.get("temperatura_tanque"),
            data.get("observaciones"),
            usuario or data.get("usuario_registro") or "",
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def listar_produccion_diaria(
    conn, empresa_id: int, *, tambo_id: Optional[int] = None, limit: int = 60
) -> List[dict]:
    cur = conn.cursor()
    if tambo_id:
        cur.execute(
            """
            SELECT * FROM tambo_produccion_diaria
            WHERE empresa_id = ? AND tambo_id = ?
            ORDER BY fecha DESC, turno
            LIMIT ?;
            """,
            (empresa_id, tambo_id, limit),
        )
    else:
        cur.execute(
            """
            SELECT * FROM tambo_produccion_diaria
            WHERE empresa_id = ?
            ORDER BY fecha DESC, turno
            LIMIT ?;
            """,
            (empresa_id, limit),
        )
    return [dict(r) for r in cur.fetchall()]


def registrar_control_lechero(conn, empresa_id: int, data: dict) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO tambo_controles_lecheros (
            empresa_id, animal_id, fecha, litros, grasa_pct, proteina_pct,
            rcs, lactancia_id, observaciones
        ) VALUES (?,?,?,?,?,?,?,?,?);
        """,
        (
            empresa_id,
            data["animal_id"],
            data.get("fecha") or _now()[:10],
            float(data.get("litros") or 0),
            data.get("grasa_pct"),
            data.get("proteina_pct"),
            data.get("rcs"),
            data.get("lactancia_id"),
            data.get("observaciones"),
        ),
    )
    cid = int(cur.lastrowid)
    _insert_evento(
        cur,
        empresa_id,
        {
            "animal_id": data["animal_id"],
            "fecha": data.get("fecha") or _now()[:10],
            "tipo": "nota",
            "litros": data.get("litros"),
            "detalle": f"Control lechero: {data.get('litros') or 0} L",
            "resultado": data.get("observaciones"),
        },
    )
    conn.commit()
    return cid


def resumen_tambo(conn, empresa_id: int) -> dict:
    reclasificar_animales_lecheros(conn, empresa_id)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM gan_animales
        WHERE empresa_id = ? AND es_tambo = 1 AND estado = 'activo';
        """,
        (empresa_id,),
    )
    vacas = int(cur.fetchone()["n"] or 0)
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM tambo_lactancias
        WHERE empresa_id = ? AND estado = 'en_ordeño';
        """,
        (empresa_id,),
    )
    en_ordeno = int(cur.fetchone()["n"] or 0)
    cur.execute(
        """
        SELECT fecha, SUM(litros_total) AS litros, SUM(vacas_ordenadas) AS vacas
        FROM tambo_produccion_diaria
        WHERE empresa_id = ?
        GROUP BY fecha
        ORDER BY fecha DESC LIMIT 1;
        """,
        (empresa_id,),
    )
    ultimo = cur.fetchone()
    cur.execute(
        """
        SELECT AVG(litros_total) AS promedio
        FROM tambo_produccion_diaria
        WHERE empresa_id = ? AND fecha >= date('now', '-30 days');
        """,
        (empresa_id,),
    )
    prom = cur.fetchone()
    return {
        "vacas_tambo": vacas,
        "en_ordeno": en_ordeno,
        "ultimo_dia": dict(ultimo) if ultimo else None,
        "promedio_litros_30d": float(prom["promedio"] or 0) if prom else 0,
    }


def crear_rodeo(conn, empresa_id: int, data: dict) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO gan_rodeos (
            empresa_id, nombre, sistema, campo_ref, superficie_has,
            capacidad, observaciones, activo
        ) VALUES (?,?,?,?,?,?,?,1);
        """,
        (
            empresa_id,
            (data.get("nombre") or "").strip(),
            data.get("sistema") or "cria",
            data.get("campo_ref"),
            float(data.get("superficie_has") or 0),
            int(data.get("capacidad") or 0),
            data.get("observaciones"),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)
