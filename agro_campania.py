# -*- coding: utf-8 -*-
"""
CAmpo+ — Márgenes brutos / campaña agropecuaria.
Campos propios y arrendados, lotes, georreferencia, contratos
(kilos fijos / aparcería con escalas) y planificación con rotación.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Ciclo productivo ideal (orientación; el ingeniero puede modificar):
# Trigo → Soja 2ª → Maíz → Soja 1ª → (repite)
ROTACION_IDEAL = ["Trigo", "Soja 2ª", "Maíz", "Soja 1ª"]

# Campaña agrícola: junio/AAAA → mayo/AAAA+1 → código "AA-AA+1" (ej. 26-27)
# No usar 2026/2027: siempre los dos dígitos finales de cada año afectado.

CULTIVOS_CANONICOS = {
    "trigo": "Trigo",
    "soja 2": "Soja 2ª",
    "soja 2a": "Soja 2ª",
    "soja 2ª": "Soja 2ª",
    "soja de segunda": "Soja 2ª",
    "soja 2da": "Soja 2ª",
    "maiz": "Maíz",
    "maíz": "Maíz",
    "soja 1": "Soja 1ª",
    "soja 1a": "Soja 1ª",
    "soja 1ª": "Soja 1ª",
    "soja de primera": "Soja 1ª",
    "soja 1ra": "Soja 1ª",
}


def codigo_campania_desde_anio_inicio(anio_inicio: int) -> str:
    """Campaña que arranca en junio de `anio_inicio` → 'AA-(AA+1)'. Ej: 2026 → '26-27'."""
    a = int(anio_inicio) % 100
    b = (int(anio_inicio) + 1) % 100
    return f"{a:02d}-{b:02d}"


def codigo_campania_actual(fecha: Optional[str] = None) -> str:
    """
    Campaña vigente según fecha (default: hoy).
    Junio–diciembre del año N → N/(N+1); enero–mayo → (N-1)/N.
    """
    if fecha:
        try:
            dt = datetime.strptime(str(fecha)[:10], "%Y-%m-%d")
        except ValueError:
            dt = datetime.now()
    else:
        dt = datetime.now()
    anio_inicio = dt.year if dt.month >= 6 else dt.year - 1
    return codigo_campania_desde_anio_inicio(anio_inicio)


def normalizar_codigo_campania(codigo: Optional[str]) -> str:
    """
    Normaliza a 'AA-BB' (ej. 26-27).
    Acepta: 26-27, 26/27, 2026-2027, 2026/2027, Campaña 26-27, etc.
    """
    raw = (codigo or "").strip().upper()
    raw = re.sub(r"^CAMPA[NÑ]A\s*", "", raw, flags=re.IGNORECASE)
    nums = re.findall(r"\d{2,4}", raw)
    if len(nums) >= 2:
        a = int(nums[0]) % 100
        b = int(nums[1]) % 100
        return f"{a:02d}-{b:02d}"
    if len(nums) == 1 and len(nums[0]) == 4:
        # Solo año de inicio
        return codigo_campania_desde_anio_inicio(int(nums[0]))
    return raw.replace("/", "-")


def etiqueta_campania(codigo: Optional[str]) -> str:
    c = normalizar_codigo_campania(codigo)
    return f"Campaña {c}" if c else "Campaña"


def normalizar_cultivo(nombre: Optional[str]) -> str:
    if not nombre:
        return ""
    key = re.sub(r"\s+", " ", str(nombre).strip().lower())
    key = key.replace("ª", "a").replace("º", "o")
    if key in CULTIVOS_CANONICOS:
        return CULTIVOS_CANONICOS[key]
    for k, v in CULTIVOS_CANONICOS.items():
        if k in key:
            return v
    # Devolver capitalizado original si no matchea
    return str(nombre).strip()


def sugerir_cultivo_rotacion(antecesor: Optional[str]) -> str:
    """Dado el cultivo antecesor, sugiere el siguiente del ciclo ideal."""
    ant = normalizar_cultivo(antecesor)
    if not ant:
        return ""
    try:
        i = ROTACION_IDEAL.index(ant)
        return ROTACION_IDEAL[(i + 1) % len(ROTACION_IDEAL)]
    except ValueError:
        return ""


def init_agro_schema(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS campanias_agro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            codigo TEXT NOT NULL,
            nombre TEXT,
            activa INTEGER DEFAULT 0,
            fecha_inicio TEXT,
            fecha_fin TEXT,
            notas TEXT
        );
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_campania_empresa_codigo
        ON campanias_agro(empresa_id, codigo);
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS campos_agro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            tipo TEXT NOT NULL DEFAULT 'propio',
            nombre TEXT NOT NULL,
            superficie_total REAL DEFAULT 0,
            arrendador_cuit TEXT,
            arrendador_razon TEXT,
            arrendador_domicilio TEXT,
            arrendador_telefono TEXT,
            arrendador_email TEXT,
            arrendador_localidad TEXT,
            lat REAL,
            lng REAL,
            direccion_ref TEXT,
            notas TEXT,
            baja INTEGER DEFAULT 0,
            fecha_alta TEXT
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lotes_agro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campo_id INTEGER NOT NULL,
            codigo TEXT,
            nombre TEXT NOT NULL,
            superficie_base REAL DEFAULT 0,
            lat REAL,
            lng REAL,
            geojson TEXT,
            orden INTEGER DEFAULT 0,
            baja INTEGER DEFAULT 0,
            FOREIGN KEY(campo_id) REFERENCES campos_agro(id)
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lote_superficie_campania (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            campania_id INTEGER NOT NULL,
            superficie REAL NOT NULL,
            UNIQUE(lote_id, campania_id),
            FOREIGN KEY(lote_id) REFERENCES lotes_agro(id),
            FOREIGN KEY(campania_id) REFERENCES campanias_agro(id)
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contratos_arrendamiento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campo_id INTEGER NOT NULL,
            empresa_id INTEGER DEFAULT 1,
            campania_id INTEGER,
            modalidad TEXT NOT NULL DEFAULT 'kilos_fijos',
            grano TEXT DEFAULT 'Soja',
            kilos_por_ha REAL DEFAULT 0,
            porcentaje_base REAL DEFAULT 0,
            monto_fijo REAL DEFAULT 0,
            vigencia_desde TEXT,
            vigencia_hasta TEXT,
            observaciones TEXT,
            FOREIGN KEY(campo_id) REFERENCES campos_agro(id)
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS escalas_aparceria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contrato_id INTEGER NOT NULL,
            rendimiento_tn_ha REAL NOT NULL,
            porcentaje REAL NOT NULL,
            orden INTEGER DEFAULT 0,
            FOREIGN KEY(contrato_id) REFERENCES contratos_arrendamiento(id)
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS planificacion_lote (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campania_id INTEGER NOT NULL,
            lote_id INTEGER NOT NULL,
            cultivo_antecesor TEXT,
            cultivo_sugerido TEXT,
            cultivo_planificado TEXT,
            superficie REAL DEFAULT 0,
            modificado_manual INTEGER DEFAULT 0,
            notas TEXT,
            UNIQUE(campania_id, lote_id),
            FOREIGN KEY(campania_id) REFERENCES campanias_agro(id),
            FOREIGN KEY(lote_id) REFERENCES lotes_agro(id)
        );
    """)

    # Cultivos de referencia (catálogo)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cultivos_ref (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT UNIQUE,
            nombre TEXT NOT NULL,
            orden_rotacion INTEGER DEFAULT 0
        );
    """)
    for i, nombre in enumerate(ROTACION_IDEAL, start=1):
        codigo = nombre.upper().replace(" ", "_").replace("ª", "A")
        cursor.execute(
            "INSERT OR IGNORE INTO cultivos_ref (codigo, nombre, orden_rotacion) VALUES (?, ?, ?);",
            (codigo, nombre, i),
        )


def asegurar_campania_activa(cursor, empresa_id: int, codigo: str, nombre: Optional[str] = None) -> int:
    codigo = normalizar_codigo_campania(codigo)
    cursor.execute(
        "SELECT id FROM campanias_agro WHERE empresa_id = ? AND codigo = ?;",
        (empresa_id, codigo),
    )
    row = cursor.fetchone()
    if row:
        return int(row["id"] if hasattr(row, "keys") else row[0])
    cursor.execute(
        """
        INSERT INTO campanias_agro (empresa_id, codigo, nombre, activa)
        VALUES (?, ?, ?, 1);
        """,
        (empresa_id, codigo, nombre or etiqueta_campania(codigo)),
    )
    return int(cursor.lastrowid)


def sincronizar_arrendador_proveedor(cursor, campo: Dict[str, Any], empresa_id: int = 1) -> Optional[str]:
    """
    Alta/actualiza el arrendador en entidades (proveedor para pagos).
    Como propietario de inmueble queda de antemano con régimen SICORE 032
    (RG 830 Anexo — Bienes Inmuebles Rurales, incluye leasing).
    Acepta CUIT o, si falta, busca/crea por razón social.
    """
    cuit = re.sub(r"\D", "", campo.get("arrendador_cuit") or "")
    razon = (campo.get("arrendador_razon") or "").strip()
    if len(cuit) < 10 and not razon:
        return None

    cursor.execute("PRAGMA table_info(entidades);")
    cols = {r[1] for r in cursor.fetchall()}
    if "regimen_sicore" not in cols:
        cursor.execute("ALTER TABLE entidades ADD COLUMN regimen_sicore TEXT DEFAULT '';")
    if "es_propietario_inmueble" not in cols:
        cursor.execute("ALTER TABLE entidades ADD COLUMN es_propietario_inmueble INTEGER DEFAULT 0;")
    if "es_proveedor" not in cols:
        cursor.execute("ALTER TABLE entidades ADD COLUMN es_proveedor INTEGER DEFAULT 1;")
    if "es_cuenta_ajuste" not in cols:
        cursor.execute("ALTER TABLE entidades ADD COLUMN es_cuenta_ajuste INTEGER DEFAULT 0;")

    REGIMEN_PROPIETARIO = "032"
    loc = (campo.get("arrendador_localidad") or "").strip()

    existe = None
    if len(cuit) >= 10:
        cursor.execute(
            "SELECT cuit FROM entidades WHERE REPLACE(cuit,'-','') = ?;",
            (cuit,),
        )
        existe = cursor.fetchone()
    if not existe and razon:
        cursor.execute(
            """
            SELECT cuit FROM entidades
            WHERE UPPER(TRIM(COALESCE(razon_social,''))) = UPPER(TRIM(?))
               OR UPPER(TRIM(COALESCE(nombre_fantasia,''))) = UPPER(TRIM(?))
            LIMIT 1;
            """,
            (razon, razon),
        )
        existe = cursor.fetchone()
        if existe:
            cuit = re.sub(r"\D", "", existe["cuit"] or "")

    cuit_fmt = cuit
    if len(cuit) == 11:
        cuit_fmt = f"{cuit[0:2]}-{cuit[2:10]}-{cuit[10]}"

    if existe:
        cursor.execute(
            """
            UPDATE entidades SET
                razon_social = COALESCE(NULLIF(?, ''), razon_social),
                nombre_fantasia = COALESCE(NULLIF(?, ''), nombre_fantasia),
                localidad = COALESCE(NULLIF(?, ''), localidad),
                es_cuenta_ajuste = 0,
                es_cuenta_bancaria = 0,
                es_proveedor = 1,
                es_propietario_inmueble = 1,
                regimen_sicore = ?
            WHERE REPLACE(cuit,'-','') = REPLACE(?, '-', '');
            """,
            (razon, razon, loc, REGIMEN_PROPIETARIO, existe["cuit"]),
        )
        return str(existe["cuit"])

    if len(cuit) < 10:
        # Sin CUIT válido no damos de alta (evitar fantasmas); el usuario completa CUIT en padrón.
        return None

    cursor.execute(
        """
        INSERT INTO entidades (
            cuit, razon_social, nombre_fantasia, condicion_iva, condicion_iva_codigo, localidad,
            es_cuenta_ajuste, es_cuenta_bancaria, centro_costo,
            es_proveedor, es_propietario_inmueble, regimen_sicore
        ) VALUES (?, ?, ?, 'Responsable Inscripto', '01', ?, 0, 0, '1', 1, 1, ?);
        """,
        (
            cuit_fmt,
            razon or f"Arrendador {cuit_fmt}",
            razon or f"Arrendador {cuit_fmt}",
            loc,
            REGIMEN_PROPIETARIO,
        ),
    )
    return cuit_fmt


def porcentaje_aparceria_por_rendimiento(escalas: List[Dict[str, Any]], rendimiento_tn_ha: float, porcentaje_base: float = 0) -> float:
    """
    Escalas ordenadas por rendimiento ascendente.
    Se aplica el % de la escala cuyo umbral es el mayor <= rendimiento.
    """
    if not escalas:
        return float(porcentaje_base or 0)
    ordenadas = sorted(escalas, key=lambda e: float(e.get("rendimiento_tn_ha") or 0))
    elegido = float(porcentaje_base or 0)
    for e in ordenadas:
        if float(e.get("rendimiento_tn_ha") or 0) <= float(rendimiento_tn_ha or 0) + 1e-9:
            elegido = float(e.get("porcentaje") or 0)
        else:
            break
    return elegido


def campo_con_lotes(cursor, campo_id: int, campania_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    cursor.execute("SELECT * FROM campos_agro WHERE id = ? AND COALESCE(baja,0)=0;", (campo_id,))
    row = cursor.fetchone()
    if not row:
        return None
    campo = dict(row)
    cursor.execute(
        """
        SELECT l.*,
               COALESCE(
                   (SELECT s.superficie FROM lote_superficie_campania s
                    WHERE s.lote_id = l.id AND s.campania_id = ?),
                   l.superficie_base
               ) AS superficie_campania
        FROM lotes_agro l
        WHERE l.campo_id = ? AND COALESCE(l.baja,0)=0
        ORDER BY l.orden ASC, l.id ASC;
        """,
        (campania_id or -1, campo_id),
    )
    campo["lotes"] = [dict(r) for r in cursor.fetchall()]
    cursor.execute(
        """
        SELECT * FROM contratos_arrendamiento
        WHERE campo_id = ?
        ORDER BY id DESC LIMIT 1;
        """,
        (campo_id,),
    )
    contrato = cursor.fetchone()
    if contrato:
        c = dict(contrato)
        cursor.execute(
            "SELECT * FROM escalas_aparceria WHERE contrato_id = ? ORDER BY rendimiento_tn_ha ASC, orden ASC;",
            (c["id"],),
        )
        c["escalas"] = [dict(r) for r in cursor.fetchall()]
        campo["contrato"] = c
    else:
        campo["contrato"] = None
    return campo


def listar_campos(cursor, empresa_id: int, tipo: Optional[str] = None) -> List[Dict[str, Any]]:
    q = "SELECT * FROM campos_agro WHERE empresa_id = ? AND COALESCE(baja,0)=0"
    params: List[Any] = [empresa_id]
    if tipo in ("propio", "arrendado"):
        q += " AND tipo = ?"
        params.append(tipo)
    q += " ORDER BY nombre ASC;"
    cursor.execute(q, params)
    out = []
    for r in cursor.fetchall():
        d = dict(r)
        cursor.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(superficie_base),0) AS has FROM lotes_agro WHERE campo_id=? AND COALESCE(baja,0)=0;",
            (d["id"],),
        )
        agg = cursor.fetchone()
        d["cant_lotes"] = int(agg["n"] or 0)
        d["has_lotes"] = float(agg["has"] or 0)
        out.append(d)
    return out


def anio_inicio_codigo_campania(codigo: str) -> int:
    """Primer año de 'AA-BB' como int 0-99; -1 si inválido."""
    codigo = normalizar_codigo_campania(codigo or "")
    m = re.match(r"^(\d{2})-(\d{2})$", codigo)
    if not m:
        return -1
    return int(m.group(1))


def campania_previa_id(cursor, empresa_id: int, campania_id: int) -> Optional[Tuple[int, str]]:
    """
    Campaña inmediatamente anterior por año de inicio (no lexicográfico).
    Evita que '92-14' o '35-36' se tomen como previas de '26-27'.
    """
    cursor.execute(
        "SELECT codigo FROM campanias_agro WHERE id=? AND empresa_id=?;",
        (campania_id, empresa_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    anio_actual = anio_inicio_codigo_campania(row["codigo"] if hasattr(row, "keys") else row[0])
    if anio_actual < 0:
        return None
    cursor.execute(
        "SELECT id, codigo FROM campanias_agro WHERE empresa_id=? AND id!=?;",
        (empresa_id, campania_id),
    )
    mejor = None  # (anio, id, codigo)
    for r in cursor.fetchall():
        d = dict(r)
        a = anio_inicio_codigo_campania(d["codigo"])
        # descartar códigos basura / futuros (ej. 91-92, 35-36, 27-28 si actual es 26-27)
        if a < 0 or a >= anio_actual:
            continue
        # preferir campañas "modernas" (>= 15 = 2015+) pero permitir cualquiera si es la más cercana
        if mejor is None or a > mejor[0]:
            mejor = (a, int(d["id"]), d["codigo"])
    if not mejor:
        return None
    return mejor[1], mejor[2]


def _antecesor_desde_margenes(
    cursor, empresa_id: int, campo_nombre: str, lote_nombre: str, campania_codigo: str
) -> str:
    """Último cultivo cargado en márgenes Access para campo+lote en esa campaña."""
    if not campania_codigo or not campo_nombre or not lote_nombre:
        return ""
    cursor.execute(
        """
        SELECT cultivo, COUNT(*) AS n
        FROM margenes_access
        WHERE empresa_id = ?
          AND campania_codigo = ?
          AND UPPER(TRIM(campo)) = UPPER(TRIM(?))
          AND UPPER(TRIM(lote)) = UPPER(TRIM(?))
          AND COALESCE(cultivo,'') != ''
        GROUP BY cultivo
        ORDER BY n DESC
        LIMIT 1;
        """,
        (empresa_id, campania_codigo, campo_nombre, lote_nombre),
    )
    row = cursor.fetchone()
    if not row:
        return ""
    return normalizar_cultivo(row["cultivo"] if hasattr(row, "keys") else row[0])


def generar_plan_campania(
    cursor,
    campania_id: int,
    empresa_id: int,
    usar_antecesor_previo: bool = True,
) -> Dict[str, Any]:
    """
    Arma / completa planificacion_lote para todos los lotes activos.
    Antecesor (en orden):
      1) cultivo_planificado de la campaña anterior (por año)
      2) lotes_agro.cultivo_actual
      3) cultivo más frecuente en margenes_access de la campaña previa
    """
    prev = campania_previa_id(cursor, empresa_id, campania_id) if usar_antecesor_previo else None
    prev_id = prev[0] if prev else None
    prev_codigo = prev[1] if prev else ""

    cursor.execute(
        """
        SELECT l.id AS lote_id, l.nombre, l.superficie_base, l.cultivo_actual,
               c.id AS campo_id, c.nombre AS campo_nombre
        FROM lotes_agro l
        JOIN campos_agro c ON c.id = l.campo_id
        WHERE c.empresa_id = ? AND COALESCE(c.baja,0)=0 AND COALESCE(l.baja,0)=0;
        """,
        (empresa_id,),
    )
    lotes = [dict(r) for r in cursor.fetchall()]
    creados = 0
    actualizados = 0
    con_antecesor = 0

    for lote in lotes:
        lid = lote["lote_id"]
        antecesor = ""
        if usar_antecesor_previo and prev_id:
            cursor.execute(
                """
                SELECT cultivo_planificado, cultivo_sugerido
                FROM planificacion_lote
                WHERE campania_id = ? AND lote_id = ?;
                """,
                (prev_id, lid),
            )
            ant_row = cursor.fetchone()
            if ant_row:
                antecesor = ant_row["cultivo_planificado"] or ant_row["cultivo_sugerido"] or ""

        if not antecesor:
            antecesor = lote.get("cultivo_actual") or ""

        if not antecesor and prev_codigo:
            antecesor = _antecesor_desde_margenes(
                cursor,
                empresa_id,
                lote.get("campo_nombre") or "",
                lote.get("nombre") or "",
                prev_codigo,
            )

        antecesor = normalizar_cultivo(antecesor) if antecesor else ""
        sugerido = sugerir_cultivo_rotacion(antecesor) if antecesor else ""
        if antecesor:
            con_antecesor += 1

        cursor.execute(
            "SELECT superficie FROM lote_superficie_campania WHERE lote_id=? AND campania_id=?;",
            (lid, campania_id),
        )
        srow = cursor.fetchone()
        superficie = float(srow["superficie"]) if srow else float(lote["superficie_base"] or 0)

        cursor.execute(
            "SELECT id, modificado_manual, cultivo_planificado, cultivo_antecesor FROM planificacion_lote WHERE campania_id=? AND lote_id=?;",
            (campania_id, lid),
        )
        existe = cursor.fetchone()
        if existe:
            if int(existe["modificado_manual"] or 0) == 1:
                # Solo completar antecesor/sugerido vacíos; no pisar edición manual
                cursor.execute(
                    """
                    UPDATE planificacion_lote SET
                        cultivo_sugerido = COALESCE(NULLIF(cultivo_sugerido,''), ?),
                        cultivo_antecesor = COALESCE(NULLIF(cultivo_antecesor,''), ?),
                        cultivo_planificado = COALESCE(NULLIF(cultivo_planificado,''), ?),
                        superficie = CASE WHEN superficie > 0 THEN superficie ELSE ? END
                    WHERE id = ?;
                    """,
                    (sugerido, antecesor, sugerido, superficie, existe["id"]),
                )
            else:
                cursor.execute(
                    """
                    UPDATE planificacion_lote SET
                        cultivo_antecesor = ?,
                        cultivo_sugerido = ?,
                        cultivo_planificado = COALESCE(NULLIF(cultivo_planificado,''), ?),
                        superficie = ?
                    WHERE id = ?;
                    """,
                    (antecesor, sugerido, sugerido, superficie, existe["id"]),
                )
            actualizados += 1
        else:
            cursor.execute(
                """
                INSERT INTO planificacion_lote
                (campania_id, lote_id, cultivo_antecesor, cultivo_sugerido, cultivo_planificado, superficie, modificado_manual)
                VALUES (?, ?, ?, ?, ?, ?, 0);
                """,
                (campania_id, lid, antecesor, sugerido, sugerido or "", superficie),
            )
            creados += 1

    return {
        "status": "success",
        "campania_id": campania_id,
        "campania_previa_id": prev_id,
        "campania_previa_codigo": prev_codigo,
        "creados": creados,
        "actualizados": actualizados,
        "con_antecesor": con_antecesor,
        "rotacion": ROTACION_IDEAL,
    }


def listar_planificacion(cursor, campania_id: int) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT p.*,
               l.nombre AS lote_nombre, l.codigo AS lote_codigo,
               l.lat AS lote_lat, l.lng AS lote_lng,
               c.nombre AS campo_nombre, c.tipo AS campo_tipo, c.id AS campo_id
        FROM planificacion_lote p
        JOIN lotes_agro l ON l.id = p.lote_id
        JOIN campos_agro c ON c.id = l.campo_id
        WHERE p.campania_id = ?
        ORDER BY c.nombre ASC, l.orden ASC, l.nombre ASC;
        """,
        (campania_id,),
    )
    return [dict(r) for r in cursor.fetchall()]
