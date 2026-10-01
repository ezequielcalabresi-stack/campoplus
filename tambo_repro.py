# -*- coding: utf-8 -*-
"""
CAmpo+ — Tambo: estado reproductivo de cada vaca y listados para el veterinario.

A partir de los eventos (parto, secado, celo, servicios, tacto, aborto) calcula por vaca:
etapa (ordeñe / seca / vaquillona), estado (espera voluntaria, a servir, servida, preñada,
vacía), DEL, días abiertos, concepción, fecha probable de parto y secado previsto.
Con eso salen los listados de tacto, a servir, próximos partos, a secar, secas/preparto,
vaquillonas, formación de rodeos e indicadores reproductivos.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, timedelta
from typing import Dict, List, Optional

from ganaderia import registrar_evento, listar_rodeos

PARAMETROS_DEFECTO = {
    "pev": 50,                  # período de espera voluntaria (días posparto sin servir)
    "dias_tacto": 35,           # días desde el servicio para el diagnóstico de preñez
    "gestacion": 283,
    "dias_secado": 60,          # se seca esta cantidad de días antes del parto
    "preparto": 21,             # días antes del parto en que pasa a preparto
    "posparto_desde": 20,       # ventana de revisión posparto (DEL)
    "posparto_hasta": 40,
    "anestro_extra": 21,        # sin celo ni servicio pasados PEV + estos días → revisar
    "edad_servicio_meses": 15,  # vaquillonas: edad para entrar a servicio
    "peso_servicio_kg": 340,    # vaquillonas: o este peso
    "dias_abiertos_alerta": 150,
    "servicios_problema": 3,    # repetidoras
    "ventana_secado": 15,       # listado a secar: secado previsto hasta hoy + N días
}

SERVICIOS = ("inseminacion", "iatf", "servicio_natural")
TIPOS_REPRO = SERVICIOS + ("celo", "diagnostico_prenez", "parto", "aborto", "secado")
TIPOS_REGISTRABLES = TIPOS_REPRO + ("cambio_rodeo",)


def _fold(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def _fecha(v) -> Optional[date]:
    try:
        return date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def resultado_tacto(texto) -> Optional[bool]:
    """True preñada, False vacía, None dudoso / sin dato."""
    t = _fold(texto)
    if not t:
        return None
    if re.search(r"vac[i]a|negativ|abierta|no pre|^v$|^n$|^-$|^no$", t):
        return False
    if re.search(r"pre[n]|positiv|cargada|^p$|^\+$|^si$", t):
        return True
    return None


def dias_gestacion_tacto(texto) -> Optional[int]:
    """'Preñada 60 días' / 'preñada 2,5 meses' → días de gestación al momento del tacto."""
    t = _fold(texto)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(m|mes|meses)\b", t)
    if m:
        return int(round(float(m.group(1).replace(",", ".")) * 30.4))
    m = re.search(r"(\d+)\s*(d|dia|dias)\b", t)
    if m:
        return int(m.group(1))
    return None


def _asegurar_tabla(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tambo_repro_parametros (
            empresa_id INTEGER PRIMARY KEY,
            datos TEXT
        );
        """
    )


def parametros(cur, empresa_id: int) -> dict:
    _asegurar_tabla(cur)
    out = dict(PARAMETROS_DEFECTO)
    row = cur.execute(
        "SELECT datos FROM tambo_repro_parametros WHERE empresa_id=?;", (empresa_id,)
    ).fetchone()
    if row and row[0]:
        try:
            for k, v in json.loads(row[0]).items():
                if k in out and v not in (None, ""):
                    out[k] = int(float(v))
        except (ValueError, TypeError):
            pass
    return out


def guardar_parametros(cur, empresa_id: int, datos: dict) -> dict:
    _asegurar_tabla(cur)
    limpio = {}
    for k in PARAMETROS_DEFECTO:
        if datos.get(k) not in (None, ""):
            v = int(float(datos[k]))
            if v < 0 or v > 2000:
                raise ValueError(f"Valor fuera de rango para {k}: {v}")
            limpio[k] = v
    cur.execute(
        """
        INSERT INTO tambo_repro_parametros (empresa_id, datos) VALUES (?, ?)
        ON CONFLICT(empresa_id) DO UPDATE SET datos = excluded.datos;
        """,
        (empresa_id, json.dumps(limpio)),
    )
    return parametros(cur, empresa_id)


def _toro(e: dict) -> str:
    return (e.get("toro_nombre") or e.get("pajuela") or e.get("semen_lote") or "").strip()


def _ids_rodeos(rodeos: List[dict]) -> Dict[str, Optional[dict]]:
    def buscar(*claves):
        for r in rodeos:
            n = _fold(r.get("nombre"))
            if any(c in n for c in claves):
                return r
        return None

    return {
        "ordene": buscar("orde"),
        "secas": buscar("seca"),
        "preparto": buscar("prepart"),
        "vaquillonas": buscar("vaquill"),
        "recria": buscar("recr"),
        "terneros": buscar("tern"),
    }


def _vaca(a: dict, eventos: List[dict], lactancias: List[dict], p: dict, hoy: date) -> dict:
    nac = _fecha(a.get("fecha_nacimiento"))
    edad_meses = round((hoy - nac).days / 30.44, 1) if nac else None

    partos = sorted({d for d in (
        [_fecha(e["fecha"]) for e in eventos if e["tipo"] == "parto"]
        + [_fecha(l.get("fecha_parto")) for l in lactancias]
    ) if d})
    secados = sorted({d for d in (
        [_fecha(e["fecha"]) for e in eventos if e["tipo"] == "secado"]
        + [_fecha(l.get("fecha_secado")) for l in lactancias]
    ) if d})
    n_lact = max(len(partos), max((int(l.get("nro_lactancia") or 0) for l in lactancias), default=0))
    ult_parto = partos[-1] if partos else None
    ult_secado = secados[-1] if secados else None

    rodeo_n = _fold(a.get("rodeo_nombre"))
    cat_n = _fold(a.get("categoria_nombre"))
    sin_datos_parto = False
    if ult_parto:
        etapa = "seca" if ult_secado and ult_secado >= ult_parto else "ordene"
    elif "seca" in rodeo_n:
        etapa, sin_datos_parto = "seca", True
    elif "orde" in rodeo_n or ("vaca" in cat_n and "vaquill" not in cat_n):
        etapa, sin_datos_parto = "ordene", True
    else:
        etapa = "vaquillona"

    periodo = [e for e in eventos if not ult_parto or (_fecha(e["fecha"]) or hoy) > ult_parto]
    estado = "sin_servicio"
    servicios: List[dict] = []
    concepcion = None
    diag = None
    ult_celo = None
    aborto = None
    for e in periodo:
        f = _fecha(e["fecha"])
        t = e["tipo"]
        if t in SERVICIOS:
            servicios.append(e)
            estado, concepcion = "servida", None
        elif t == "celo":
            ult_celo = f
        elif t == "diagnostico_prenez":
            texto = " ".join(str(e.get(k) or "") for k in ("resultado", "detalle"))
            r = resultado_tacto(e.get("resultado")) if resultado_tacto(e.get("resultado")) is not None else resultado_tacto(texto)
            diag = {"fecha": _iso(f), "resultado": e.get("resultado") or "", "tecnico": e.get("tecnico") or "", "prenada": r}
            if r is True:
                estado = "prenada"
                ult_serv = _fecha(servicios[-1]["fecha"]) if servicios else None
                gest = dias_gestacion_tacto(texto)
                if ult_serv and f and 25 <= (f - ult_serv).days <= 300 and not gest:
                    concepcion = ult_serv
                elif gest and f:
                    concepcion = f - timedelta(days=gest)
                elif ult_serv:
                    concepcion = ult_serv
            elif r is False:
                estado, concepcion = "vacia", None
        elif t == "aborto":
            aborto = f
            estado, concepcion = "vacia", None

    if estado == "sin_servicio" and etapa != "vaquillona":
        dpp = (hoy - ult_parto).days if ult_parto else None
        if dpp is not None and dpp < p["pev"]:
            estado = "espera"
        else:
            estado = "a_servir"
    if estado == "sin_servicio" and etapa == "vaquillona":
        apta = (edad_meses is not None and edad_meses >= p["edad_servicio_meses"]) or (
            a.get("peso_ultimo") and float(a["peso_ultimo"]) >= p["peso_servicio_kg"]
        ) or (edad_meses is None and not a.get("peso_ultimo"))
        estado = "a_servir" if apta else "recria"

    ult_serv = servicios[-1] if servicios else None
    f_ult_serv = _fecha(ult_serv["fecha"]) if ult_serv else None
    fpp = concepcion + timedelta(days=p["gestacion"]) if concepcion else None
    secado_prev = fpp - timedelta(days=p["dias_secado"]) if fpp else None
    del_ = (hoy - ult_parto).days if ult_parto and etapa == "ordene" else None

    dias_abiertos = None
    if ult_parto:
        if estado == "prenada":
            dias_abiertos = (concepcion - ult_parto).days if concepcion else None
        else:
            dias_abiertos = (hoy - ult_parto).days

    toro_concep = None
    if estado == "prenada" and concepcion:
        cand = [s for s in servicios if _fecha(s["fecha"]) and _fecha(s["fecha"]) <= concepcion]
        toro_concep = _toro(cand[-1]) if cand else None

    iep = (partos[-1] - partos[-2]).days if len(partos) >= 2 else None
    edad_1er_parto = round((partos[0] - nac).days / 30.44, 1) if partos and nac else None

    return {
        "id": a["id"],
        "caravana": a.get("caravana_visual") or a.get("rp") or a.get("nombre") or f"#{a['id']}",
        "rp": a.get("rp") or "",
        "nombre": a.get("nombre") or "",
        "eid": a.get("caravana_electronica") or "",
        "raza": a.get("raza") or "",
        "rodeo_id": a.get("rodeo_id"),
        "rodeo": a.get("rodeo_nombre") or "Sin rodeo",
        "categoria": a.get("categoria_nombre") or "",
        "edad_meses": edad_meses,
        "peso": a.get("peso_ultimo"),
        "etapa": etapa,
        "sin_datos_parto": sin_datos_parto,
        "lactancia": n_lact,
        "ult_parto": _iso(ult_parto),
        "del": del_,
        "ult_secado": _iso(ult_secado) if etapa == "seca" else None,
        "dias_seca": (hoy - ult_secado).days if etapa == "seca" and ult_secado else None,
        "estado": estado,
        "n_servicios": len(servicios),
        "ult_servicio": _iso(f_ult_serv),
        "ult_servicio_tipo": ult_serv["tipo"] if ult_serv else None,
        "ult_toro": _toro(ult_serv) if ult_serv else "",
        "dias_desde_servicio": (hoy - f_ult_serv).days if f_ult_serv else None,
        "ult_celo": _iso(ult_celo),
        "diagnostico": diag,
        "aborto": _iso(aborto),
        "concepcion": _iso(concepcion),
        "toro_concepcion": toro_concep or "",
        "dias_gestacion": (hoy - concepcion).days if concepcion else None,
        "fpp": _iso(fpp),
        "dias_a_parto": (fpp - hoy).days if fpp else None,
        "secado_previsto": _iso(secado_prev),
        "dias_a_secado": (secado_prev - hoy).days if secado_prev else None,
        "dias_abiertos": dias_abiertos,
        "iep": iep,
        "edad_1er_parto": edad_1er_parto,
    }


def _sugerir_rodeo(v: dict, ids: dict, p: dict) -> Optional[dict]:
    prep = ids["preparto"]
    cerca_parto = v["dias_a_parto"] is not None and v["dias_a_parto"] <= p["preparto"]
    if v["etapa"] == "ordene":
        return ids["ordene"]
    if v["etapa"] == "seca":
        return prep if prep and cerca_parto else ids["secas"]
    if v["estado"] == "prenada" and cerca_parto and prep:
        return prep
    if v["estado"] == "recria":
        if v["edad_meses"] is not None and v["edad_meses"] < 6 and ids["terneros"]:
            return ids["terneros"]
        return ids["recria"] or ids["vaquillonas"]
    return ids["vaquillonas"]


def _prom(vals) -> Optional[float]:
    vals = [float(x) for x in vals if x is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def indicadores(vacas: List[dict], p: dict) -> dict:
    paridas = [v for v in vacas if v["etapa"] in ("ordene", "seca") and not v["sin_datos_parto"]]
    ordene = [v for v in vacas if v["etapa"] == "ordene"]
    prenadas = [v for v in paridas if v["estado"] == "prenada"]
    abiertas = [v for v in paridas if v["estado"] != "prenada"]
    exito = sum(1 for v in prenadas if v["n_servicios"] == 1)
    fracaso = sum(1 for v in paridas if v["n_servicios"] > 1 or (v["n_servicios"] == 1 and v["estado"] == "vacia"))
    return {
        "vacas": len([v for v in vacas if v["etapa"] != "vaquillona"]),
        "en_ordene": len(ordene),
        "secas": len([v for v in vacas if v["etapa"] == "seca"]),
        "vaquillonas": len([v for v in vacas if v["etapa"] == "vaquillona"]),
        "pct_prenez": round(100 * len(prenadas) / len(paridas), 1) if paridas else None,
        "dias_abiertos_prom": _prom(v["dias_abiertos"] for v in paridas),
        "pct_abiertas_alerta": round(
            100 * sum(1 for v in abiertas if (v["dias_abiertos"] or 0) > p["dias_abiertos_alerta"]) / len(paridas), 1
        ) if paridas else None,
        "iep_prom": _prom(v["iep"] for v in vacas),
        "iep_proyectado": _prom(
            v["dias_abiertos"] + p["gestacion"] for v in prenadas if v["dias_abiertos"] is not None
        ),
        "servicios_por_prenez": _prom(v["n_servicios"] for v in prenadas if v["n_servicios"]),
        "concepcion_1er_servicio": round(100 * exito / (exito + fracaso), 1) if exito + fracaso else None,
        "del_prom": _prom(v["del"] for v in ordene),
        "edad_1er_parto": _prom(v["edad_1er_parto"] for v in vacas),
        "sin_datos_parto": sum(1 for v in vacas if v["sin_datos_parto"]),
    }


def estado_tambo(conn, empresa_id: int, hoy: Optional[date] = None) -> dict:
    hoy = hoy or date.today()
    cur = conn.cursor()
    p = parametros(cur, empresa_id)
    rodeos = [r for r in listar_rodeos(conn, empresa_id) if (r.get("sistema") or "") == "tambo"]
    ids = _ids_rodeos(rodeos)

    animales = [dict(r) for r in cur.execute(
        """
        SELECT a.*, c.nombre AS categoria_nombre, r.nombre AS rodeo_nombre
        FROM gan_animales a
        LEFT JOIN gan_categorias c ON c.id = a.categoria_id
        LEFT JOIN gan_rodeos r ON r.id = a.rodeo_id
        WHERE a.empresa_id = ? AND a.estado = 'activo'
          AND (COALESCE(a.es_tambo,0) = 1 OR COALESCE(a.sistema_actual,'') = 'tambo')
          AND COALESCE(a.sexo,'') != 'Macho';
        """,
        (empresa_id,),
    ).fetchall()]
    ev_por: Dict[int, List[dict]] = {}
    marcas = ",".join("?" * len(TIPOS_REPRO))
    for r in cur.execute(
        f"""
        SELECT e.* FROM gan_eventos e
        JOIN gan_animales a ON a.id = e.animal_id
        WHERE e.empresa_id = ? AND a.estado = 'activo' AND e.tipo IN ({marcas})
        ORDER BY e.fecha, e.id;
        """,
        (empresa_id, *TIPOS_REPRO),
    ).fetchall():
        ev_por.setdefault(r["animal_id"], []).append(dict(r))
    lac_por: Dict[int, List[dict]] = {}
    for r in cur.execute("SELECT * FROM tambo_lactancias WHERE empresa_id = ?;", (empresa_id,)).fetchall():
        lac_por.setdefault(r["animal_id"], []).append(dict(r))

    vacas = []
    for a in animales:
        v = _vaca(a, ev_por.get(a["id"], []), lac_por.get(a["id"], []), p, hoy)
        sug = _sugerir_rodeo(v, ids, p)
        v["rodeo_sugerido_id"] = sug["id"] if sug else None
        v["rodeo_sugerido"] = sug["nombre"] if sug else ""
        vacas.append(v)

    return {
        "hoy": hoy.isoformat(),
        "parametros": p,
        "rodeos": [{"id": r["id"], "nombre": r["nombre"], "cabezas": r.get("cabezas")} for r in rodeos],
        "vacas": vacas,
        "indicadores": indicadores(vacas, p),
    }


def registrar_eventos(conn, empresa_id: int, eventos: List[dict], usuario: str = "") -> dict:
    ok, errores = 0, []
    for i, raw in enumerate(eventos, start=1):
        try:
            tipo = (raw.get("tipo") or "").strip().lower()
            if tipo not in TIPOS_REGISTRABLES:
                raise ValueError(f"Tipo no permitido: {tipo}")
            if not raw.get("animal_id"):
                raise ValueError("Falta la vaca")
            if tipo == "cambio_rodeo" and not raw.get("rodeo_id"):
                raise ValueError("Falta el rodeo destino")
            data = {k: raw.get(k) for k in (
                "animal_id", "fecha", "resultado", "tecnico", "toro_nombre", "rodeo_id", "detalle",
            )}
            data["tipo"] = tipo
            data["origen_dato"] = "listados_tambo"
            registrar_evento(conn, empresa_id, data, usuario=usuario)
            ok += 1
        except Exception as exc:
            errores.append({"fila": i, "animal_id": raw.get("animal_id"), "error": str(exc)})
    return {"ok": ok, "errores": errores}
