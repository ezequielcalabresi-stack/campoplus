# -*- coding: utf-8 -*-
"""Lectura de recibos de sueldo de convenio (PDF del liquidador, formato ARCA con costo total empleador)."""
from __future__ import annotations

import io
import re
import unicodedata
from typing import Optional

_NUM = r"-?[\d\.]+,\d{2}"
_RE_CABECERA = re.compile(rf"^\$\s*({_NUM})\s+(\S+)\s+(\d{{2}})\s+(\d{{4}})\s+(.+?)\s*$")
_RE_CUIL = re.compile(r"^(\d{2}-\d{8}-\d)\s+(\d{2}/\d{2}/\d{4})")
_RE_LEGAJO = re.compile(r"^(\d{8})$")
_RE_CONCEPTO = re.compile(rf"^(.+?)\s+(\d{{4}})\s+({_NUM})(?:\s+({_NUM}))?\s*$")
_RE_COMPOSICION = re.compile(rf"COMPOSICI.N SALARIAL:\s*Remunerativo:\s*\$\s*({_NUM})(?:\s*\$\s*({_NUM}))?(?:\s*\$\s*({_NUM}))?")
_RE_NETO = re.compile(rf"^\$\s*({_NUM})\s+SUELDO NETO")
_RE_BRUTO = re.compile(rf"SUELDO BRUTO\s*\$\s*({_NUM})")
_RE_MONTO_SOLO = re.compile(rf"^\$\s*({_NUM})\s*$")


def _n(s: Optional[str]) -> float:
    if not s:
        return 0.0
    return float(s.replace(".", "").replace(",", "."))


def norm_txt(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s).strip().lower()


def solo_digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def detectar_convenio(conceptos: list[dict], categoria: str) -> str:
    txt = norm_txt(" ".join(c["concepto"] for c in conceptos) + " " + categoria)
    if "uatre" in txt or "trabajo agrario" in txt:
        return "UATRE"
    if "comercio" in txt or "faecys" in txt or "cct130" in txt:
        return "Empleados de Comercio"
    if "kilometraje" in txt or "chofer" in txt or "(4.2." in txt:
        return "Camioneros"
    return "Fuera de convenio"


def parsear_pagina(texto: str) -> Optional[dict]:
    lineas = [l.strip() for l in (texto or "").splitlines()]
    rec: dict = {"conceptos": []}
    for i, l in enumerate(lineas):
        m = _RE_CABECERA.match(l)
        if m and "nombre" not in rec:
            rec.update(
                sueldo_basico=_n(m.group(1)),
                tipo_liquidacion=m.group(2),
                periodo=f"{m.group(4)}-{m.group(3)}",
                nombre=re.sub(r"\s+", " ", m.group(5)).strip(),
                categoria=lineas[i - 1] if i else "",
            )
            continue
        m = _RE_CUIL.match(l)
        if m and "cuil" not in rec:
            d, mth, y = m.group(2).split("/")
            rec.update(cuil=m.group(1), fecha_ingreso=f"{y}-{mth}-{d}")
            continue
        m = _RE_LEGAJO.match(l)
        if m and "legajo" not in rec:
            rec["legajo"] = m.group(1)
            continue
        m = _RE_BRUTO.search(l)
        if m:
            rec["bruto"] = _n(m.group(1))
            montos = [_RE_MONTO_SOLO.match(x) for x in lineas[i + 1:i + 4]]
            montos = [_n(x.group(1)) for x in montos if x]
            if montos:
                rec["contribuciones"] = montos[0]
            if len(montos) > 1:
                rec["costo_total"] = montos[1]
            continue
        m = _RE_COMPOSICION.search(l)
        if m:
            rec["remunerativo"] = _n(m.group(1))
            rec["descuentos"] = _n(m.group(2))
            rec["no_remunerativo"] = _n(m.group(3))
            continue
        m = _RE_NETO.match(l)
        if m:
            rec["neto"] = _n(m.group(1))
            continue
        m = _RE_CONCEPTO.match(l)
        if m and "nombre" in rec and not l.startswith("$"):
            unidad, monto = (m.group(3), m.group(4)) if m.group(4) else (None, m.group(3))
            rec["conceptos"].append({
                "concepto": m.group(1).strip(),
                "codigo": m.group(2),
                "unidad": _n(unidad) if unidad else None,
                "monto": _n(monto),
            })
    if not rec.get("nombre") or "neto" not in rec:
        return None
    rec.setdefault("cuil", "")
    rec.setdefault("legajo", "")
    rec.setdefault("fecha_ingreso", "")
    rec.setdefault("remunerativo", 0.0)
    rec.setdefault("no_remunerativo", 0.0)
    rec.setdefault("descuentos", 0.0)
    rec.setdefault("contribuciones", 0.0)
    rec.setdefault("costo_total", round(rec.get("bruto", 0.0) + rec["contribuciones"], 2))
    rec["convenio"] = detectar_convenio(rec["conceptos"], rec.get("categoria", ""))
    rec["es_sac"] = bool(re.search(r"sac|aguin", norm_txt(rec.get("tipo_liquidacion", ""))))
    return rec


def parsear_pdf(contenido: bytes) -> list[dict]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(contenido))
    out = []
    for page in reader.pages:
        rec = parsear_pagina(page.extract_text() or "")
        if rec:
            out.append(rec)
    return out


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", norm_txt(s)) if t]


def puntaje_nombre(nombre_empleado: str, nombre_recibo: str) -> float:
    """Fracción de palabras del nombre corto (Access) presentes en el nombre del recibo."""
    te, tr = _tokens(nombre_empleado), _tokens(nombre_recibo)
    if not te or not tr:
        return 0.0
    ok = 0
    for t in te:
        if t in tr or (len(t) >= 4 and any(x.startswith(t) for x in tr)):
            ok += 1
    return ok / len(te)
