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


def resumen_stock(conn, empresa_id: int) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT sistema_actual AS sistema, COUNT(*) AS n,
               AVG(peso_ultimo) AS peso_promedio
        FROM gan_animales
        WHERE empresa_id = ? AND estado = 'activo'
        GROUP BY sistema_actual;
        """,
        (empresa_id,),
    )
    por_sistema = [dict(r) for r in cur.fetchall()]
    cur.execute(
        """
        SELECT sexo, COUNT(*) AS n FROM gan_animales
        WHERE empresa_id = ? AND estado = 'activo' GROUP BY sexo;
        """,
        (empresa_id,),
    )
    por_sexo = [dict(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT COUNT(*) AS n FROM gan_animales WHERE empresa_id = ? AND estado = 'activo';",
        (empresa_id,),
    )
    total = int(cur.fetchone()["n"] or 0)
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM gan_animales
        WHERE empresa_id = ? AND estado = 'activo'
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
    limit: int = 500,
) -> List[dict]:
    cur = conn.cursor()
    where = ["a.empresa_id = ?"]
    params: list = [empresa_id]
    if estado:
        where.append("a.estado = ?")
        params.append(estado)
    if sistema:
        where.append("a.sistema_actual = ?")
        params.append(sistema)
    if rodeo_id:
        where.append("a.rodeo_id = ?")
        params.append(rodeo_id)
    if es_tambo is not None:
        where.append("a.es_tambo = ?")
        params.append(int(es_tambo))
    if q:
        where.append(
            "(a.caravana_visual LIKE ? OR a.caravana_electronica LIKE ? "
            "OR a.nombre LIKE ? OR a.senasa_id LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like])
    sql = f"""
        SELECT a.*,
               c.nombre AS categoria_nombre,
               r.nombre AS rodeo_nombre
        FROM gan_animales a
        LEFT JOIN gan_categorias c ON c.id = a.categoria_id
        LEFT JOIN gan_rodeos r ON r.id = a.rodeo_id
        WHERE {' AND '.join(where)}
        ORDER BY a.caravana_visual COLLATE NOCASE, a.id DESC
        LIMIT ?;
    """
    params.append(max(1, min(int(limit or 500), 2000)))
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


def crear_animal(conn, empresa_id: int, data: dict, usuario: str = "") -> int:
    cur = conn.cursor()
    ahora = _now()
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
            (data.get("raza") or "").strip() or None,
            data.get("sexo") or "Indefinido",
            data.get("fecha_nacimiento") or None,
            data.get("madre_id"),
            data.get("padre_id"),
            data.get("categoria_id"),
            data.get("rodeo_id"),
            data.get("sistema_actual") or "cria",
            data.get("estado") or "activo",
            data.get("peso_ultimo"),
            data.get("fecha_peso_ultimo") or (ahora[:10] if data.get("peso_ultimo") else None),
            data.get("fecha_alta") or ahora[:10],
            (data.get("origen") or "").strip() or None,
            (data.get("color") or "").strip() or None,
            (data.get("observaciones") or "").strip() or None,
            1 if data.get("es_tambo") else 0,
        ),
    )
    aid = int(cur.lastrowid)
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
            medicamento, dosis, litros, costo, detalle, usuario_registro
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
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
    if animal_id and tipo == "secado":
        cur.execute(
            """
            UPDATE tambo_lactancias SET estado = 'seca', fecha_secado = ?
            WHERE animal_id = ? AND estado = 'en_ordeño';
            """,
            (data.get("fecha") or _now()[:10], animal_id),
        )

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
