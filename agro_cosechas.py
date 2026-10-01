"""Cosechas: camiones al acopio, silobolsas, conciliación con los romaneos del acopio y stock de granos.

Cada especie es un solo stock (Soja 1ª y Soja 2ª forman SOJA); el cultivo de origen queda en cada carga.
Un camión cargado desde la planilla de cosecha queda "pendiente" con los kilos de campo hasta que se
vincula con el romaneo del acopio; desde ahí cuenta con los kilos netos del acopio.
"""
from __future__ import annotations

import io
import json
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from fastapi import Body, File, Form, HTTPException, Request, UploadFile

from agro_campania import codigo_campania_actual, normalizar_codigo_campania

ESPECIES = (
    ("soja", "SOJA"), ("maiz", "MAIZ"), ("trigo", "TRIGO"), ("cebada", "CEBADA"), ("girasol", "GIRASOL"),
    ("sorgo", "SORGO"), ("avena", "AVENA"), ("centeno", "CENTENO"), ("colza", "COLZA"), ("arveja", "ARVEJA"),
    ("poroto", "POROTO"), ("mani", "MANI"),
)
CULTIVOS = ["Soja 1ª", "Soja 2ª", "Maíz", "Maíz 2ª", "Maíz tardío", "Trigo", "Cebada", "Girasol", "Sorgo", "Avena"]


# ---------------------------------------------------------------- utilidades
def _ascii(txt) -> str:
    return unicodedata.normalize("NFKD", str(txt if txt is not None else "")).encode("ascii", "ignore").decode()


def _texto(txt) -> str:
    return _ascii(txt).lower().strip()


def especie(txt) -> str:
    t = _texto(txt)
    for clave, nombre in ESPECIES:
        if clave in t:
            return nombre
    return _ascii(txt).strip().upper()


def _num(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return 0.0 if v != v else float(v)
    s = str(v).strip().replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _str(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _fecha_iso(v) -> str:
    if v is None or v == "" or (isinstance(v, float) and v != v):
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", s)
    if m:
        a = int(m.group(3))
        a = a + 2000 if a < 100 else a
        return f"{a:04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def _campania(campania, fecha="") -> str:
    c = normalizar_codigo_campania(_str(campania)) if _str(campania) else ""
    if re.fullmatch(r"\d{2}-\d{2}", c or ""):
        return c
    return codigo_campania_actual(fecha) if fecha else ""


def _cpe(v) -> str:
    grupos = re.findall(r"\d+", _str(v))
    return str(int(grupos[-1])) if grupos else ""


def _patente(v) -> str:
    return re.sub(r"[^A-Z0-9]", "", _ascii(v).upper())


_NO_NOMBRE = {"de", "del", "la", "las", "los", "y", "sa", "srl", "s.a", "sas"}


def _tokens_nombre(txt) -> set:
    return {t for t in re.split(r"[^a-z]+", _texto(txt)) if len(t) >= 3 and t not in _NO_NOMBRE}


def nombres_coinciden(a, b) -> Optional[bool]:
    """Comparte un apellido/palabra (tolerando un error de tipeo). None si falta alguno."""
    ta, tb = _tokens_nombre(a), _tokens_nombre(b)
    if not ta or not tb:
        return None
    for x in ta:
        for y in tb:
            if x == y or (len(x) >= 5 and len(y) >= 5 and SequenceMatcher(None, x, y).ratio() >= 0.85):
                return True
    return False


def _sesion_usuario(get_db, request) -> str:
    try:
        from saas_auth import sesion_actual

        s = sesion_actual(get_db, request) or {}
        return s.get("nombre") or s.get("login") or ""
    except Exception:
        return ""


# ---------------------------------------------------------------- esquema
def asegurar_schema_cosechas(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cosecha_silobolsas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            campania TEXT, fecha TEXT, grano TEXT, cultivo TEXT,
            campo TEXT, lote TEXT, identificacion TEXT,
            kilos REAL DEFAULT 0, humedad REAL,
            observaciones TEXT, vaciada INTEGER DEFAULT 0,
            usuario TEXT, created_at TEXT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cosecha_camiones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            campania TEXT, fecha TEXT, grano TEXT, cultivo TEXT,
            campo TEXT, lote TEXT, silobolsa_id INTEGER,
            destino TEXT, chofer TEXT, patente TEXT, cpe TEXT,
            kilos REAL DEFAULT 0, humedad REAL, observaciones TEXT,
            usuario TEXT, created_at TEXT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS acopio_romaneos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            acopio TEXT, productor TEXT, nro_romaneo TEXT,
            grano_cod TEXT, grano_nombre TEXT, grano TEXT, campania TEXT,
            nro_procedencia TEXT, procedencia TEXT,
            kilos_netos REAL DEFAULT 0, humedad REAL, merma REAL, kilos_bruto REAL,
            chofer TEXT, cpe TEXT, fecha TEXT, patente TEXT,
            archivo TEXT, importado_at TEXT,
            camion_id INTEGER, conciliado_modo TEXT, conciliado_at TEXT, conciliado_por TEXT,
            UNIQUE (empresa_id, acopio, productor, nro_romaneo, cpe)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS acopio_stock_informado (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            acopio TEXT, fecha_informe TEXT, cuenta TEXT,
            grano_cod TEXT, grano TEXT, campania TEXT,
            tt_max_finales REAL, tt_entregadas REAL, tt_certificadas REAL, tt_liquidadas REAL, tt_a_vender REAL,
            archivo TEXT, importado_at TEXT
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS ix_cos_cam_emp ON cosecha_camiones(empresa_id, campania, grano);")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_aco_rom_emp ON acopio_romaneos(empresa_id, campania, grano);")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_aco_rom_cam ON acopio_romaneos(camion_id);")


# ---------------------------------------------------------------- lectura del Excel del acopio
def _cabecera(fila) -> Dict[str, int]:
    m: Dict[str, int] = {}
    for i, h in enumerate(fila):
        t = _ascii(h).upper().strip()
        if not t or t == "NAN":
            continue

        def put(k):
            m.setdefault(k, i)

        if "PRODUCTOR" in t or t == "CUENTA":
            put("productor")
        elif "ROMANEO" in t or "TICKET" in t:
            put("nro_romaneo")
        elif "PROCEDENCIA" in t and ("NRO" in t or "COD" in t):
            put("nro_procedencia")
        elif "PROCEDENCIA" in t or "ORIGEN" in t:
            put("procedencia")
        elif "GRANO" in t and "COD" in t:
            put("grano_cod")
        elif t == "GRANO":
            put("grano")
        elif t in ("NOMBRE", "PRODUCTO", "ESPECIE"):
            put("grano_nombre")
        elif "COSECHA" in t or "CAMPA" in t:
            put("cosecha")
        elif "NETO" in t:
            put("kilos_netos")
        elif "BRUTO" in t:
            put("kilos_bruto")
        elif "MERMA" in t:
            put("merma")
        elif "HUM" in t:
            put("humedad")
        elif "CHOFER" in t or "CONDUCTOR" in t:
            put("chofer")
        elif "CARTA" in t or "CPE" in t or t.startswith("C.P"):
            put("cpe")
        elif "PATENTE" in t or "DOMINIO" in t:
            put("patente")
        elif "FECHA" in t:
            put("fecha")
        elif "MAX" in t:
            put("tt_max_finales")
        elif "ENTRE" in t:
            put("tt_entregadas")
        elif "CERTIF" in t:
            put("tt_certificadas")
        elif "LIQUID" in t:
            put("tt_liquidadas")
        elif "VENDER" in t:
            put("tt_a_vender")
    return m


def leer_excel_acopio(contenido: bytes, archivo: str = "") -> dict:
    import pandas as pd

    try:
        hojas = pd.read_excel(io.BytesIO(contenido), sheet_name=None, header=None)
    except Exception as e:
        raise ValueError(f"No se pudo leer el Excel: {e}")
    romaneos: List[dict] = []
    stock: List[dict] = []
    for _, df in hojas.items():
        cab: Dict[str, int] = {}
        modo = ""
        for fila in df.itertuples(index=False):
            fila = list(fila)
            m = _cabecera(fila)
            if "kilos_netos" in m and ("cpe" in m or "nro_romaneo" in m):
                cab, modo = m, "romaneos"
                continue
            if "tt_a_vender" in m or ("tt_entregadas" in m and "cosecha" in m):
                cab, modo = m, "stock"
                continue
            if not modo:
                continue
            val = lambda k: fila[cab[k]] if k in cab and cab[k] < len(fila) else None
            if modo == "romaneos":
                netos = _num(val("kilos_netos"))
                nro, cpe = _str(val("nro_romaneo")), _str(val("cpe"))
                if not netos or not (nro or cpe):
                    continue
                cod, nombre = _str(val("grano_cod")), _str(val("grano_nombre"))
                g = _str(val("grano"))
                if g and re.fullmatch(r"\d+", g):
                    cod = cod or g
                elif g:
                    nombre = nombre or g
                bruto = _num(val("kilos_bruto"))
                merma = _num(val("merma"))
                romaneos.append({
                    "productor": _str(val("productor")), "nro_romaneo": nro, "grano_cod": cod,
                    "grano_nombre": nombre, "grano": especie(nombre or cod),
                    "campania": _campania(val("cosecha"), _fecha_iso(val("fecha"))),
                    "nro_procedencia": _str(val("nro_procedencia")), "procedencia": _str(val("procedencia")),
                    "kilos_netos": netos, "humedad": _num(val("humedad")), "merma": merma,
                    "kilos_bruto": bruto or round(netos + merma, 2),
                    "chofer": _str(val("chofer")), "cpe": cpe, "fecha": _fecha_iso(val("fecha")),
                    "patente": _patente(val("patente")),
                })
            else:
                nombre = _str(val("grano")) or _str(val("grano_nombre"))
                if not nombre or not _str(val("cosecha")):
                    continue
                stock.append({
                    "cuenta": _str(val("productor")), "grano_cod": _str(val("grano_cod")), "grano": especie(nombre),
                    "campania": _campania(val("cosecha")),
                    **{k: _num(val(k)) for k in ("tt_max_finales", "tt_entregadas", "tt_certificadas", "tt_liquidadas", "tt_a_vender")},
                })
    if not romaneos and not stock:
        raise ValueError("No encontré romaneos ni stock en el archivo (busco columnas como Nº ROMANEO, KGS. NETOS, Nº CARTA PORTE).")
    base = re.sub(r"\.[a-z0-9]+$", "", archivo or "", flags=re.I)
    m_ac = re.match(r"^(.*?)\s+(romaneo|stock|liquid|certif)", base, flags=re.I)
    m_fe = re.search(r"(\d{1,2})[-_.](\d{1,2})[-_.](\d{2,4})", base)
    return {
        "romaneos": romaneos, "stock": stock,
        "acopio_sugerido": (m_ac.group(1) if m_ac else base[:40]).strip(),
        "fecha_informe": _fecha_iso("/".join(m_fe.groups())) if m_fe else datetime.now().strftime("%Y-%m-%d"),
    }


def _empresa_nombre(cur, eid: int) -> str:
    try:
        r = cur.execute("SELECT razon_social FROM empresas WHERE id=?;", (eid,)).fetchone()
        return (r[0] or "") if r else ""
    except Exception:
        return ""


def previa_importacion(cur, eid: int, contenido: bytes, archivo: str) -> dict:
    datos = leer_excel_acopio(contenido, archivo)
    emp = _empresa_nombre(cur, eid)
    prod: Dict[str, dict] = {}
    for r in datos["romaneos"]:
        p = prod.setdefault(r["productor"], {"productor": r["productor"], "n": 0, "kilos_netos": 0.0, "granos": set()})
        p["n"] += 1
        p["kilos_netos"] += r["kilos_netos"]
        p["granos"].add(f'{r["grano"]} {r["campania"]}')
    lista = []
    for p in prod.values():
        p["granos"] = sorted(p["granos"])
        p["kilos_netos"] = round(p["kilos_netos"], 2)
        p["es_empresa"] = bool(nombres_coinciden(p["productor"], emp)) if emp else False
        lista.append(p)
    if not any(p["es_empresa"] for p in lista):
        for p in lista:
            p["es_empresa"] = True
    return {
        "acopio_sugerido": datos["acopio_sugerido"], "fecha_informe": datos["fecha_informe"],
        "productores": sorted(lista, key=lambda p: _texto(p["productor"])), "stock": datos["stock"],
        "empresa": emp,
    }


def importar_acopio(cur, eid: int, contenido: bytes, archivo: str, acopio: str, productores: List[str],
                    fecha_informe: str = "") -> dict:
    asegurar_schema_cosechas(cur)
    acopio = (acopio or "").strip()
    if not acopio:
        raise ValueError("Indicá el nombre del acopio.")
    datos = leer_excel_acopio(contenido, archivo)
    elegidos = set(productores or [])
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nuevos = actualizados = 0
    for r in datos["romaneos"]:
        if elegidos and r["productor"] not in elegidos:
            continue
        previo = cur.execute(
            "SELECT id FROM acopio_romaneos WHERE empresa_id=? AND acopio=? AND productor=? AND nro_romaneo=? AND cpe=?;",
            (eid, acopio, r["productor"], r["nro_romaneo"], r["cpe"]),
        ).fetchone()
        campos = ("grano_cod", "grano_nombre", "grano", "campania", "nro_procedencia", "procedencia", "kilos_netos",
                  "humedad", "merma", "kilos_bruto", "chofer", "fecha", "patente")
        if previo:
            cur.execute(
                f"UPDATE acopio_romaneos SET {', '.join(c + '=?' for c in campos)}, archivo=?, importado_at=? WHERE id=?;",
                [r[c] for c in campos] + [archivo, ahora, previo[0]],
            )
            actualizados += 1
        else:
            cur.execute(
                f"""
                INSERT INTO acopio_romaneos (empresa_id, acopio, productor, nro_romaneo, cpe, {', '.join(campos)}, archivo, importado_at)
                VALUES ({','.join('?' * (5 + len(campos) + 2))});
                """,
                [eid, acopio, r["productor"], r["nro_romaneo"], r["cpe"]] + [r[c] for c in campos] + [archivo, ahora],
            )
            nuevos += 1
    fecha_inf = _fecha_iso(fecha_informe) or datos["fecha_informe"]
    n_stock = 0
    filas_stock = [s for s in datos["stock"] if not elegidos or not s["cuenta"] or s["cuenta"] in elegidos
                   or any(nombres_coinciden(s["cuenta"], p) for p in elegidos)]
    if filas_stock:
        cur.execute("DELETE FROM acopio_stock_informado WHERE empresa_id=? AND acopio=? AND fecha_informe=?;", (eid, acopio, fecha_inf))
        for s in filas_stock:
            cur.execute(
                """
                INSERT INTO acopio_stock_informado (empresa_id, acopio, fecha_informe, cuenta, grano_cod, grano, campania,
                    tt_max_finales, tt_entregadas, tt_certificadas, tt_liquidadas, tt_a_vender, archivo, importado_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?);
                """,
                (eid, acopio, fecha_inf, s["cuenta"], s["grano_cod"], s["grano"], s["campania"], s["tt_max_finales"],
                 s["tt_entregadas"], s["tt_certificadas"], s["tt_liquidadas"], s["tt_a_vender"], archivo, ahora),
            )
            n_stock += 1
    return {"nuevos": nuevos, "actualizados": actualizados, "stock": n_stock, "fecha_informe": fecha_inf}


# ---------------------------------------------------------------- conciliación
def evaluar(cam: dict, rom: dict) -> Optional[dict]:
    """Qué datos coinciden entre un camión de la planilla y un romaneo del acopio."""
    if cam["grano"] != rom["grano"]:
        return None
    if cam["campania"] and rom["campania"] and cam["campania"] != rom["campania"]:
        return None
    if nombres_coinciden(cam["destino"], rom["acopio"]) is False:
        return None
    c: Dict[str, Optional[bool]] = {}
    a, b = _cpe(cam["cpe"]), _cpe(rom["cpe"])
    c["cpe"] = (a == b) if a and b else None
    kc, kb = float(cam["kilos"] or 0), float(rom["kilos_bruto"] or rom["kilos_netos"] or 0)
    cerca = False
    if kc and kb:
        dif = abs(kc - kb) / max(kc, kb)
        c["kilos"] = dif <= 0.005
        cerca = dif <= 0.03
    else:
        c["kilos"] = None
    c["chofer"] = nombres_coinciden(cam["chofer"], rom["chofer"])
    pa, pb = _patente(cam["patente"]), _patente(rom["patente"])
    c["patente"] = (pa == pb) if pa and pb else None
    if cam["fecha"] and rom["fecha"]:
        try:
            dias = abs((datetime.strptime(cam["fecha"][:10], "%Y-%m-%d") - datetime.strptime(rom["fecha"][:10], "%Y-%m-%d")).days)
            c["fecha"] = dias <= 3
        except ValueError:
            c["fecha"] = None
    else:
        c["fecha"] = None
    c["campo"] = nombres_coinciden(cam["campo"], rom["procedencia"])
    puntos = (50 if c["cpe"] else 0) + (30 if c["kilos"] else 12 if cerca else 0) + (20 if c["chofer"] else 0) \
        + (10 if c["patente"] else 0) + (5 if c["fecha"] else 0) + (5 if c["campo"] else 0)
    exigidos = [c[k] for k in ("cpe", "kilos", "chofer", "patente", "fecha")]
    auto = c["cpe"] is True and c["kilos"] is True and all(v is not False for v in exigidos)
    return {"puntos": puntos, "coincide": c, "auto": auto, "kilos_cerca": cerca}


_SQL_CAMIONES = """
    SELECT c.*, r.id AS romaneo_id, r.kilos_netos AS kilos_netos_acopio, r.kilos_bruto AS kilos_bruto_acopio,
           r.acopio AS acopio_conciliado, r.nro_romaneo, r.conciliado_modo
    FROM cosecha_camiones c
    LEFT JOIN acopio_romaneos r ON r.camion_id = c.id AND r.empresa_id = c.empresa_id
    WHERE c.empresa_id = ?
"""


def _pendientes(cur, eid: int, campania: str = "", grano: str = ""):
    extra, params = "", [eid]
    if campania:
        extra += " AND c.campania = ?"
        params.append(campania)
    if grano:
        extra += " AND c.grano = ?"
        params.append(grano)
    camiones = [dict(r) for r in cur.execute(_SQL_CAMIONES + extra + " AND r.id IS NULL ORDER BY c.fecha, c.id;", params).fetchall()]
    extra_r, params_r = "", [eid]
    if campania:
        extra_r += " AND campania = ?"
        params_r.append(campania)
    if grano:
        extra_r += " AND grano = ?"
        params_r.append(grano)
    romaneos = [dict(r) for r in cur.execute(
        f"SELECT * FROM acopio_romaneos WHERE empresa_id=? AND camion_id IS NULL {extra_r} ORDER BY acopio, productor, CAST(cpe AS INTEGER);",
        params_r,
    ).fetchall()]
    return camiones, romaneos


def _parejas(camiones: List[dict], romaneos: List[dict]):
    pares = []
    for cam in camiones:
        for rom in romaneos:
            ev = evaluar(cam, rom)
            if ev and (ev["auto"] or ev["puntos"] >= 50):
                pares.append((ev, cam, rom))
    pares.sort(key=lambda p: (not p[0]["auto"], -p[0]["puntos"]))
    usados_c, usados_r, elegidos = set(), set(), []
    for ev, cam, rom in pares:
        if cam["id"] in usados_c or rom["id"] in usados_r:
            continue
        usados_c.add(cam["id"])
        usados_r.add(rom["id"])
        elegidos.append((ev, cam, rom))
    return elegidos


def vincular(cur, eid: int, camion_id: int, romaneo_id: int, modo: str, usuario: str) -> None:
    cam = cur.execute("SELECT id FROM cosecha_camiones WHERE id=? AND empresa_id=?;", (camion_id, eid)).fetchone()
    rom = cur.execute("SELECT id, camion_id FROM acopio_romaneos WHERE id=? AND empresa_id=?;", (romaneo_id, eid)).fetchone()
    if not cam or not rom:
        raise ValueError("No encontré el camión o el romaneo.")
    if rom["camion_id"] and rom["camion_id"] != camion_id:
        raise ValueError("Ese romaneo ya está conciliado con otro camión.")
    if cur.execute("SELECT 1 FROM acopio_romaneos WHERE camion_id=? AND id!=? AND empresa_id=?;", (camion_id, romaneo_id, eid)).fetchone():
        raise ValueError("Ese camión ya está conciliado con otro romaneo.")
    cur.execute(
        "UPDATE acopio_romaneos SET camion_id=?, conciliado_modo=?, conciliado_at=?, conciliado_por=? WHERE id=?;",
        (camion_id, modo, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), usuario, romaneo_id),
    )


def conciliar_auto(cur, eid: int, usuario: str, campania: str = "", grano: str = "") -> int:
    camiones, romaneos = _pendientes(cur, eid, campania, grano)
    n = 0
    for ev, cam, rom in _parejas(camiones, romaneos):
        if ev["auto"]:
            vincular(cur, eid, cam["id"], rom["id"], "auto", usuario)
            n += 1
    return n


def estado_conciliacion(cur, eid: int, campania: str = "", grano: str = "") -> dict:
    camiones, romaneos = _pendientes(cur, eid, campania, grano)
    sugerencias = []
    con_sug_c, con_sug_r = set(), set()
    for ev, cam, rom in _parejas(camiones, romaneos):
        sugerencias.append({"camion": cam, "romaneo": rom, "coincide": ev["coincide"], "puntos": ev["puntos"],
                            "auto": ev["auto"], "kilos_cerca": ev["kilos_cerca"]})
        con_sug_c.add(cam["id"])
        con_sug_r.add(rom["id"])
    extra, params = "", [eid]
    if campania:
        extra += " AND c.campania = ?"
        params.append(campania)
    if grano:
        extra += " AND c.grano = ?"
        params.append(grano)
    conciliados = [dict(r) for r in cur.execute(
        f"""
        SELECT r.id AS romaneo_id, r.acopio, r.nro_romaneo, r.cpe AS cpe_acopio, r.chofer AS chofer_acopio,
               r.kilos_bruto, r.kilos_netos, r.humedad AS humedad_acopio, r.merma, r.conciliado_modo, r.conciliado_por,
               c.id AS camion_id, c.fecha, c.grano, c.cultivo, c.campo, c.lote, c.chofer, c.patente, c.cpe, c.kilos, c.campania
        FROM acopio_romaneos r JOIN cosecha_camiones c ON c.id = r.camion_id AND c.empresa_id = r.empresa_id
        WHERE r.empresa_id = ? {extra}
        ORDER BY c.fecha DESC, c.id DESC;
        """,
        params,
    ).fetchall()]
    return {
        "sugerencias": sugerencias,
        "camiones_pendientes": camiones,
        "romaneos_pendientes": romaneos,
        "conciliados": conciliados,
        "resumen": {
            "camiones_pendientes": len(camiones), "romaneos_pendientes": len(romaneos),
            "sugerencias": len(sugerencias), "conciliados": len(conciliados),
            "kilos_campo_pendientes": round(sum(c["kilos"] or 0 for c in camiones), 2),
            "kilos_netos_sin_camion": round(sum(r["kilos_netos"] or 0 for r in romaneos), 2),
            "kilos_netos_conciliados": round(sum(r["kilos_netos"] or 0 for r in conciliados), 2),
            "kilos_campo_conciliados": round(sum(r["kilos"] or 0 for r in conciliados), 2),
        },
    }


# ---------------------------------------------------------------- stock y producción
def _extraido_por_bolsa(cur, eid: int) -> Dict[int, dict]:
    out: Dict[int, dict] = {}
    for r in cur.execute(
        _SQL_CAMIONES.replace("SELECT c.*,", "SELECT c.id, c.silobolsa_id, c.kilos,") + " AND c.silobolsa_id IS NOT NULL;",
        (eid,),
    ).fetchall():
        d = out.setdefault(r["silobolsa_id"], {"camiones": 0, "kilos_campo": 0.0, "kilos": 0.0})
        d["camiones"] += 1
        d["kilos_campo"] += r["kilos"] or 0
        d["kilos"] += (r["kilos_netos_acopio"] if r["romaneo_id"] else r["kilos"]) or 0
    return out


def listar_silobolsas(cur, eid: int, campania: str = "", grano: str = "") -> List[dict]:
    extra, params = "", [eid]
    if campania:
        extra += " AND campania=?"
        params.append(campania)
    if grano:
        extra += " AND grano=?"
        params.append(grano)
    ext = _extraido_por_bolsa(cur, eid)
    out = []
    for r in cur.execute(f"SELECT * FROM cosecha_silobolsas WHERE empresa_id=? {extra} ORDER BY fecha DESC, id DESC;", params).fetchall():
        d = dict(r)
        e = ext.get(d["id"], {"camiones": 0, "kilos_campo": 0.0, "kilos": 0.0})
        d["camiones_extraccion"] = e["camiones"]
        d["kilos_extraidos"] = round(e["kilos_campo"], 2)
        d["saldo"] = 0.0 if d["vaciada"] else round(max(0.0, (d["kilos"] or 0) - e["kilos_campo"]), 2)
        out.append(d)
    return out


def stock(cur, eid: int, campania: str = "") -> dict:
    filas: Dict[tuple, dict] = {}

    def fila(g, c):
        return filas.setdefault((g, c), {
            "grano": g, "campania": c, "silobolsas": 0.0, "acopio_conciliado": 0.0, "acopio_pendiente": 0.0,
            "camiones_pendientes": 0, "romaneos_sin_camion": 0.0, "vendido": 0.0,
        })

    for b in listar_silobolsas(cur, eid, campania):
        fila(b["grano"], b["campania"])["silobolsas"] += b["saldo"]
    extra, params = "", [eid]
    if campania:
        extra = " AND c.campania=?"
        params.append(campania)
    for r in cur.execute(_SQL_CAMIONES + extra + ";", params).fetchall():
        f = fila(r["grano"], r["campania"])
        if r["romaneo_id"]:
            f["acopio_conciliado"] += r["kilos_netos_acopio"] or 0
        else:
            f["acopio_pendiente"] += r["kilos"] or 0
            f["camiones_pendientes"] += 1
    extra_r, params_r = "", [eid]
    if campania:
        extra_r = " AND campania=?"
        params_r.append(campania)
    for r in cur.execute(
        f"SELECT grano, campania, SUM(kilos_netos) k FROM acopio_romaneos WHERE empresa_id=? AND camion_id IS NULL {extra_r} GROUP BY 1,2;",
        params_r,
    ).fetchall():
        fila(r["grano"], r["campania"])["romaneos_sin_camion"] += r["k"] or 0
    if cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='liquidaciones_granos';").fetchone():
        for r in cur.execute(
            "SELECT grano, campania, SUM(kilos_netos) k FROM liquidaciones_granos WHERE COALESCE(empresa_id,1)=? GROUP BY 1,2;", (eid,)
        ).fetchall():
            c = _campania(r["campania"])
            if campania and c != campania:
                continue
            fila(especie(r["grano"]), c)["vendido"] += r["k"] or 0
    out = []
    for f in filas.values():
        f["en_acopio"] = f["acopio_conciliado"] + f["acopio_pendiente"] + f["romaneos_sin_camion"]
        f["disponible"] = f["en_acopio"] + f["silobolsas"] - f["vendido"]
        out.append({k: (round(v, 2) if isinstance(v, float) else v) for k, v in f.items()})
    out.sort(key=lambda f: (f["grano"], f["campania"] or ""), reverse=False)
    out.sort(key=lambda f: f["campania"] or "", reverse=True)
    informado = []
    ultimo = cur.execute(
        "SELECT acopio, MAX(fecha_informe) f FROM acopio_stock_informado WHERE empresa_id=? GROUP BY acopio;", (eid,)
    ).fetchall()
    for u in ultimo:
        for r in cur.execute(
            "SELECT * FROM acopio_stock_informado WHERE empresa_id=? AND acopio=? AND fecha_informe=? ORDER BY campania DESC, grano;",
            (eid, u["acopio"], u["f"]),
        ).fetchall():
            if campania and r["campania"] != campania:
                continue
            informado.append(dict(r))
    return {"stock": out, "informado": informado}


def _superficies(cur, eid: int, campania: str):
    from agro_nombres import _tokens, parecidos

    if not cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lotes_agro';").fetchone():
        return lambda campo, lote: None
    cols = {r[1] for r in cur.execute("PRAGMA table_info(lotes_agro);").fetchall()}
    camp = cur.execute("SELECT id FROM campanias_agro WHERE COALESCE(empresa_id,1)=? AND codigo=?;", (eid, campania)).fetchone() \
        if campania and cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='campanias_agro';").fetchone() else None
    sup_total = "COALESCE(l.superficie_total_lote,0)" if "superficie_total_lote" in cols else "0"
    filas = cur.execute(
        f"""
        SELECT c.nombre AS campo, l.nombre AS lote,
               CASE WHEN {sup_total} > 0 THEN {sup_total}
                    ELSE COALESCE((SELECT s.superficie FROM lote_superficie_campania s WHERE s.lote_id=l.id AND s.campania_id=?),
                                  l.superficie_base, 0) END AS sup
        FROM lotes_agro l JOIN campos_agro c ON c.id = l.campo_id
        WHERE COALESCE(c.empresa_id,1)=? AND COALESCE(l.baja,0)=0 AND COALESCE(c.baja,0)=0;
        """,
        ((camp[0] if camp else -1), eid),
    ).fetchall()

    def buscar(campo, lote):
        tl = set(_tokens(lote))
        if not tl:
            return None
        for r in filas:
            if (set(_tokens(r["campo"])) == set(_tokens(campo)) or parecidos(r["campo"], campo)) and set(_tokens(r["lote"])) == tl:
                return float(r["sup"] or 0) or None
        return None

    return buscar


def produccion(cur, eid: int, campania: str, grano: str = "") -> List[dict]:
    grupos: Dict[tuple, dict] = {}

    def g(campo, lote, cultivo, gr):
        k = ((campo or "").strip().upper(), (lote or "").strip().upper(), cultivo or "", gr)
        return grupos.setdefault(k, {"campo": campo, "lote": lote, "cultivo": cultivo, "grano": gr, "kilos": 0.0,
                                     "kilos_campo": 0.0, "camiones": 0, "pendientes": 0, "silobolsas": 0, "kilos_silobolsa": 0.0})

    extra, params = " AND c.campania=?", [eid, campania]
    if grano:
        extra += " AND c.grano=?"
        params.append(grano)
    for r in cur.execute(_SQL_CAMIONES + extra + " AND c.silobolsa_id IS NULL;", params).fetchall():
        d = g(r["campo"], r["lote"], r["cultivo"], r["grano"])
        d["camiones"] += 1
        d["kilos_campo"] += r["kilos"] or 0
        if r["romaneo_id"]:
            d["kilos"] += r["kilos_netos_acopio"] or 0
        else:
            d["kilos"] += r["kilos"] or 0
            d["pendientes"] += 1
    ext = _extraido_por_bolsa(cur, eid)
    for b in listar_silobolsas(cur, eid, campania, grano):
        d = g(b["campo"], b["lote"], b["cultivo"], b["grano"])
        d["silobolsas"] += 1
        e = ext.get(b["id"])
        k = e["kilos"] if (b["vaciada"] and e and e["camiones"]) else (b["kilos"] or 0)
        d["kilos"] += k
        d["kilos_silobolsa"] += k
    sup = _superficies(cur, eid, campania)
    out = []
    for d in grupos.values():
        s = sup(d["campo"], d["lote"])
        d["sup_lote"] = s
        d["kg_ha"] = round(d["kilos"] / s, 1) if s else None
        out.append({k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()})
    from agro_nombres import clave_orden

    out.sort(key=lambda d: (clave_orden(d["campo"] or ""), clave_orden(d["lote"] or ""), d["cultivo"] or ""))
    return out


# ---------------------------------------------------------------- rutas
def register_cosechas_routes(app, get_db, get_empresa_activa_id):
    def conexion():
        conn = get_db()
        cur = conn.cursor()
        asegurar_schema_cosechas(cur)
        return conn, cur

    def fila_camion(f: dict, base: dict) -> dict:
        def v(k):
            x = f.get(k)
            return x if x not in (None, "") else base.get(k)

        fecha = _fecha_iso(v("fecha"))
        cultivo = _str(v("cultivo"))
        d = {
            "fecha": fecha, "cultivo": cultivo, "grano": especie(cultivo), "campania": _campania(v("campania"), fecha),
            "campo": _str(v("campo")), "lote": _str(v("lote")), "silobolsa_id": int(f.get("silobolsa_id") or 0) or None,
            "destino": _str(v("destino")), "chofer": _str(f.get("chofer")), "patente": _patente(f.get("patente")),
            "cpe": _str(f.get("cpe")), "kilos": _num(f.get("kilos")), "humedad": _num(f.get("humedad")) or None,
            "observaciones": _str(f.get("observaciones")),
        }
        return d

    def validar_camion(cur, eid, d: dict, n: int = 0):
        donde = f"Renglón {n}: " if n else ""
        if d["silobolsa_id"]:
            b = cur.execute("SELECT * FROM cosecha_silobolsas WHERE id=? AND empresa_id=?;", (d["silobolsa_id"], eid)).fetchone()
            if not b:
                raise ValueError(donde + "la silobolsa no existe.")
            for k in ("campania", "grano", "cultivo", "campo", "lote"):
                d[k] = b[k]
        if not d["fecha"]:
            raise ValueError(donde + "falta la fecha.")
        if not d["cultivo"]:
            raise ValueError(donde + "falta el grano / cultivo.")
        if not d["campo"]:
            raise ValueError(donde + "falta el campo.")
        if d["kilos"] <= 0:
            raise ValueError(donde + "faltan los kilos.")

    COLS_CAMION = ("campania", "fecha", "grano", "cultivo", "campo", "lote", "silobolsa_id", "destino", "chofer",
                   "patente", "cpe", "kilos", "humedad", "observaciones")

    @app.get("/api/agro/cosechas/maestros")
    def api_cos_maestros():
        conn, cur = conexion()
        try:
            eid = get_empresa_activa_id()
            conn.commit()
            campos = []
            if cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='campos_agro';").fetchone():
                for c in cur.execute(
                    "SELECT id, nombre FROM campos_agro WHERE COALESCE(empresa_id,1)=? AND COALESCE(baja,0)=0 ORDER BY nombre COLLATE NOCASE;",
                    (eid,),
                ).fetchall():
                    lotes = [r[0] for r in cur.execute(
                        "SELECT nombre FROM lotes_agro WHERE campo_id=? AND COALESCE(baja,0)=0 ORDER BY nombre COLLATE NOCASE;", (c["id"],)
                    ).fetchall()]
                    campos.append({"nombre": c["nombre"], "lotes": lotes})
            campanias = set()
            if cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='campanias_agro';").fetchone():
                campanias |= {r[0] for r in cur.execute("SELECT codigo FROM campanias_agro WHERE COALESCE(empresa_id,1)=?;", (eid,)).fetchall()}
            for t in ("cosecha_camiones", "cosecha_silobolsas", "acopio_romaneos"):
                campanias |= {r[0] for r in cur.execute(f"SELECT DISTINCT campania FROM {t} WHERE empresa_id=?;", (eid,)).fetchall()}
            tope = int(codigo_campania_actual()[:2]) + 1

            def valida(c):
                m = re.fullmatch(r"(\d{2})-(\d{2})", c or "")
                return bool(m) and int(m.group(2)) == (int(m.group(1)) + 1) % 100 and int(m.group(1)) <= tope

            campanias = sorted((c for c in campanias if valida(c)), reverse=True)
            distintos = lambda col, t="cosecha_camiones": [r[0] for r in cur.execute(
                f"SELECT {col}, COUNT(*) n FROM {t} WHERE empresa_id=? AND TRIM(COALESCE({col},''))!='' GROUP BY {col} ORDER BY n DESC LIMIT 200;",
                (eid,),
            ).fetchall()]
            acopios = list(dict.fromkeys(distintos("destino") + distintos("acopio", "acopio_romaneos")))
            granos = sorted({r[0] for r in cur.execute(
                "SELECT grano FROM cosecha_camiones WHERE empresa_id=? UNION SELECT grano FROM cosecha_silobolsas WHERE empresa_id=? "
                "UNION SELECT grano FROM acopio_romaneos WHERE empresa_id=?;", (eid, eid, eid),
            ).fetchall() if r[0]})
            return {
                "campos": campos, "campanias": campanias, "actual": codigo_campania_actual(), "cultivos": CULTIVOS,
                "acopios": acopios, "choferes": distintos("chofer"), "patentes": distintos("patente"), "granos": granos,
            }
        finally:
            conn.close()

    @app.get("/api/agro/cosechas/camiones")
    def api_cos_camiones(campania: Optional[str] = None, grano: Optional[str] = None, campo: Optional[str] = None,
                         estado: Optional[str] = None):
        conn, cur = conexion()
        try:
            extra, params = "", [get_empresa_activa_id()]
            if campania:
                extra += " AND c.campania=?"
                params.append(campania)
            if grano:
                extra += " AND c.grano=?"
                params.append(grano)
            if campo:
                extra += " AND UPPER(TRIM(c.campo))=UPPER(TRIM(?))"
                params.append(campo)
            if estado == "pendiente":
                extra += " AND r.id IS NULL"
            elif estado == "conciliado":
                extra += " AND r.id IS NOT NULL"
            filas = [dict(r) for r in cur.execute(_SQL_CAMIONES + extra + " ORDER BY c.fecha DESC, c.id DESC LIMIT 3000;", params).fetchall()]
            bolsas = {b["id"]: b["identificacion"] or f"Bolsa {b['id']}" for b in cur.execute(
                "SELECT id, identificacion FROM cosecha_silobolsas WHERE empresa_id=?;", (params[0],)
            ).fetchall()}
            for f in filas:
                f["silobolsa"] = bolsas.get(f["silobolsa_id"]) if f["silobolsa_id"] else None
            return {"camiones": filas}
        finally:
            conn.close()

    @app.post("/api/agro/cosechas/camiones")
    def api_cos_camiones_alta(request: Request, data: dict = Body(...)):
        conn, cur = conexion()
        try:
            eid = get_empresa_activa_id()
            base = data.get("cabecera") or {}
            filas = [f for f in (data.get("filas") or []) if any(_str(f.get(k)) for k in ("kilos", "chofer", "cpe", "patente"))]
            if not filas:
                raise ValueError("La planilla no tiene renglones con datos.")
            usuario = _sesion_usuario(get_db, request)
            ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ids = []
            for n, f in enumerate(filas, 1):
                d = fila_camion(f, base)
                validar_camion(cur, eid, d, n)
                cur.execute(
                    f"INSERT INTO cosecha_camiones (empresa_id, {', '.join(COLS_CAMION)}, usuario, created_at) "
                    f"VALUES ({','.join('?' * (len(COLS_CAMION) + 3))});",
                    [eid] + [d[k] for k in COLS_CAMION] + [usuario, ahora],
                )
                ids.append(cur.lastrowid)
            auto = conciliar_auto(cur, eid, usuario)
            conn.commit()
            msg = f"{len(ids)} camión(es) cargado(s)."
            if auto:
                msg += f" {auto} se conciliaron solos con romaneos del acopio."
            return {"ids": ids, "conciliados": auto, "message": msg}
        except ValueError as e:
            conn.rollback()
            raise HTTPException(400, str(e))
        finally:
            conn.close()

    @app.put("/api/agro/cosechas/camiones/{camion_id}")
    def api_cos_camiones_editar(camion_id: int, data: dict = Body(...)):
        conn, cur = conexion()
        try:
            eid = get_empresa_activa_id()
            if not cur.execute("SELECT 1 FROM cosecha_camiones WHERE id=? AND empresa_id=?;", (camion_id, eid)).fetchone():
                raise HTTPException(404, "Camión no encontrado.")
            d = fila_camion(data, {})
            validar_camion(cur, eid, d)
            cur.execute(f"UPDATE cosecha_camiones SET {', '.join(k + '=?' for k in COLS_CAMION)} WHERE id=?;",
                        [d[k] for k in COLS_CAMION] + [camion_id])
            conn.commit()
            return {"status": "ok"}
        except ValueError as e:
            conn.rollback()
            raise HTTPException(400, str(e))
        finally:
            conn.close()

    @app.delete("/api/agro/cosechas/camiones/{camion_id}")
    def api_cos_camiones_borrar(camion_id: int):
        conn, cur = conexion()
        try:
            eid = get_empresa_activa_id()
            cur.execute("UPDATE acopio_romaneos SET camion_id=NULL, conciliado_modo=NULL, conciliado_at=NULL, conciliado_por=NULL "
                        "WHERE camion_id=? AND empresa_id=?;", (camion_id, eid))
            cur.execute("DELETE FROM cosecha_camiones WHERE id=? AND empresa_id=?;", (camion_id, eid))
            conn.commit()
            return {"status": "ok"}
        finally:
            conn.close()

    COLS_BOLSA = ("campania", "fecha", "grano", "cultivo", "campo", "lote", "identificacion", "kilos", "humedad", "observaciones", "vaciada")

    def fila_bolsa(data: dict) -> dict:
        fecha = _fecha_iso(data.get("fecha"))
        cultivo = _str(data.get("cultivo"))
        d = {
            "fecha": fecha, "cultivo": cultivo, "grano": especie(cultivo), "campania": _campania(data.get("campania"), fecha),
            "campo": _str(data.get("campo")), "lote": _str(data.get("lote")), "identificacion": _str(data.get("identificacion")),
            "kilos": _num(data.get("kilos")), "humedad": _num(data.get("humedad")) or None,
            "observaciones": _str(data.get("observaciones")), "vaciada": 1 if data.get("vaciada") else 0,
        }
        if not d["fecha"] or not d["cultivo"] or not d["campo"]:
            raise ValueError("Completá fecha, grano / cultivo y campo.")
        if d["kilos"] <= 0:
            raise ValueError("Indicá los kilos de la silobolsa.")
        return d

    @app.get("/api/agro/cosechas/silobolsas")
    def api_cos_bolsas(campania: Optional[str] = None, grano: Optional[str] = None):
        conn, cur = conexion()
        try:
            return {"silobolsas": listar_silobolsas(cur, get_empresa_activa_id(), campania or "", grano or "")}
        finally:
            conn.close()

    @app.post("/api/agro/cosechas/silobolsas")
    def api_cos_bolsas_alta(request: Request, data: dict = Body(...)):
        conn, cur = conexion()
        try:
            d = fila_bolsa(data)
            cur.execute(
                f"INSERT INTO cosecha_silobolsas (empresa_id, {', '.join(COLS_BOLSA)}, usuario, created_at) "
                f"VALUES ({','.join('?' * (len(COLS_BOLSA) + 3))});",
                [get_empresa_activa_id()] + [d[k] for k in COLS_BOLSA]
                + [_sesion_usuario(get_db, request), datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            )
            conn.commit()
            return {"id": cur.lastrowid}
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            conn.close()

    @app.put("/api/agro/cosechas/silobolsas/{bolsa_id}")
    def api_cos_bolsas_editar(bolsa_id: int, data: dict = Body(...)):
        conn, cur = conexion()
        try:
            eid = get_empresa_activa_id()
            if not cur.execute("SELECT 1 FROM cosecha_silobolsas WHERE id=? AND empresa_id=?;", (bolsa_id, eid)).fetchone():
                raise HTTPException(404, "Silobolsa no encontrada.")
            d = fila_bolsa(data)
            cur.execute(f"UPDATE cosecha_silobolsas SET {', '.join(k + '=?' for k in COLS_BOLSA)} WHERE id=?;",
                        [d[k] for k in COLS_BOLSA] + [bolsa_id])
            cur.execute(
                "UPDATE cosecha_camiones SET campania=?, grano=?, cultivo=?, campo=?, lote=? WHERE silobolsa_id=? AND empresa_id=?;",
                (d["campania"], d["grano"], d["cultivo"], d["campo"], d["lote"], bolsa_id, eid),
            )
            conn.commit()
            return {"status": "ok"}
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            conn.close()

    @app.delete("/api/agro/cosechas/silobolsas/{bolsa_id}")
    def api_cos_bolsas_borrar(bolsa_id: int):
        conn, cur = conexion()
        try:
            eid = get_empresa_activa_id()
            n = cur.execute("SELECT COUNT(*) FROM cosecha_camiones WHERE silobolsa_id=? AND empresa_id=?;", (bolsa_id, eid)).fetchone()[0]
            if n:
                raise HTTPException(400, f"La silobolsa tiene {n} camión(es) de extracción; borralos primero o marcala como vaciada.")
            cur.execute("DELETE FROM cosecha_silobolsas WHERE id=? AND empresa_id=?;", (bolsa_id, eid))
            conn.commit()
            return {"status": "ok"}
        finally:
            conn.close()

    @app.post("/api/agro/cosechas/acopio/previa")
    async def api_cos_acopio_previa(file: UploadFile = File(...)):
        contenido = await file.read()
        conn, cur = conexion()
        try:
            return previa_importacion(cur, get_empresa_activa_id(), contenido, file.filename or "")
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            conn.close()

    @app.post("/api/agro/cosechas/acopio/importar")
    async def api_cos_acopio_importar(request: Request, file: UploadFile = File(...), acopio: str = Form(...),
                                      productores: str = Form("[]"), fecha_informe: str = Form("")):
        contenido = await file.read()
        conn, cur = conexion()
        try:
            eid = get_empresa_activa_id()
            r = importar_acopio(cur, eid, contenido, file.filename or "", acopio, json.loads(productores or "[]"), fecha_informe)
            r["conciliados"] = conciliar_auto(cur, eid, _sesion_usuario(get_db, request))
            conn.commit()
            r["message"] = (f"Romaneos de {acopio.strip()}: {r['nuevos']} nuevos, {r['actualizados']} actualizados. "
                            f"Stock informado: {r['stock']} renglones al {r['fecha_informe']}. "
                            f"Conciliados automáticamente: {r['conciliados']}.")
            return r
        except ValueError as e:
            conn.rollback()
            raise HTTPException(400, str(e))
        finally:
            conn.close()

    @app.delete("/api/agro/cosechas/romaneos/{romaneo_id}")
    def api_cos_romaneo_borrar(romaneo_id: int):
        conn, cur = conexion()
        try:
            eid = get_empresa_activa_id()
            r = cur.execute("SELECT camion_id FROM acopio_romaneos WHERE id=? AND empresa_id=?;", (romaneo_id, eid)).fetchone()
            if not r:
                raise HTTPException(404, "Romaneo no encontrado.")
            if r["camion_id"]:
                raise HTTPException(400, "El romaneo está conciliado; desvinculalo primero.")
            cur.execute("DELETE FROM acopio_romaneos WHERE id=?;", (romaneo_id,))
            conn.commit()
            return {"status": "ok"}
        finally:
            conn.close()

    @app.get("/api/agro/cosechas/conciliacion")
    def api_cos_conciliacion(campania: Optional[str] = None, grano: Optional[str] = None):
        conn, cur = conexion()
        try:
            return estado_conciliacion(cur, get_empresa_activa_id(), campania or "", grano or "")
        finally:
            conn.close()

    @app.post("/api/agro/cosechas/conciliacion/auto")
    def api_cos_conciliar_auto(request: Request, data: dict = Body(default={})):
        conn, cur = conexion()
        try:
            n = conciliar_auto(cur, get_empresa_activa_id(), _sesion_usuario(get_db, request),
                               _str(data.get("campania")), _str(data.get("grano")))
            conn.commit()
            return {"conciliados": n, "message": f"Conciliados automáticamente: {n}."}
        finally:
            conn.close()

    @app.post("/api/agro/cosechas/conciliacion/vincular")
    def api_cos_vincular(request: Request, data: dict = Body(...)):
        conn, cur = conexion()
        try:
            pares = data.get("pares") or [{"camion_id": data.get("camion_id"), "romaneo_id": data.get("romaneo_id")}]
            usuario = _sesion_usuario(get_db, request)
            for p in pares:
                vincular(cur, get_empresa_activa_id(), int(p["camion_id"]), int(p["romaneo_id"]), "manual", usuario)
            conn.commit()
            return {"vinculados": len(pares)}
        except (ValueError, TypeError, KeyError) as e:
            conn.rollback()
            raise HTTPException(400, str(e) or "Datos incompletos.")
        finally:
            conn.close()

    @app.post("/api/agro/cosechas/conciliacion/desvincular")
    def api_cos_desvincular(data: dict = Body(...)):
        conn, cur = conexion()
        try:
            cur.execute(
                "UPDATE acopio_romaneos SET camion_id=NULL, conciliado_modo=NULL, conciliado_at=NULL, conciliado_por=NULL "
                "WHERE id=? AND empresa_id=?;",
                (int(data.get("romaneo_id") or 0), get_empresa_activa_id()),
            )
            conn.commit()
            return {"status": "ok"}
        finally:
            conn.close()

    @app.get("/api/agro/cosechas/stock")
    def api_cos_stock(campania: Optional[str] = None):
        conn, cur = conexion()
        try:
            return stock(cur, get_empresa_activa_id(), campania or "")
        finally:
            conn.close()

    @app.get("/api/agro/cosechas/produccion")
    def api_cos_produccion(campania: str, grano: Optional[str] = None):
        conn, cur = conexion()
        try:
            return {"filas": produccion(cur, get_empresa_activa_id(), campania, grano or "")}
        finally:
            conn.close()
