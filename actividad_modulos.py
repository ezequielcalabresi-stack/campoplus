# -*- coding: utf-8 -*-
"""
Vínculo actividad ↔ módulo contratado.

Las liquidaciones solo se muestran si la empresa tiene el pack habilitado y
la actividad cargada; las actividades de imputación de packs no contratados
se ocultan:
  - Liquidaciones de hacienda / actividad Ganadería → mod_ganaderia
  - Liquidaciones de leche / actividad Tambo        → mod_tambo
  - Liquidaciones de granos (LPG) / Agricultura      → mod_agro
"""
from __future__ import annotations

import unicodedata
from typing import Callable, Dict, Optional, Set

LIQUIDACION_MODULO: Dict[str, str] = {
    "HACIENDA": "mod_ganaderia",
    "LECHE": "mod_tambo",
    "GRANO": "mod_agro",
}

LIQUIDACION_LABEL: Dict[str, str] = {
    "HACIENDA": "Liquidaciones de hacienda (pack Ganadería)",
    "LECHE": "Liquidaciones de leche (pack Tambo)",
    "GRANO": "Liquidaciones de granos LPG (pack Agrícola)",
}

# Orden importa: TAMBO antes que GANADERIA ("Descarte Tambo" es tambo).
_PALABRAS_MODULO = (
    ("mod_tambo", ("TAMBO", "LECHE", "LECHER", "GUACHERA")),
    ("mod_porcino", ("PORCIN", "CERDO")),
    ("mod_aviar", ("AVIAR", "AVICOL", "POLLO", "GALLINA")),
    ("mod_ganaderia", ("GANAD", "HACIENDA", "INVERNADA", "FEEDLOT", "CRIA ")),
    ("mod_agro", ("AGRIC", "GRANO", "SOJA", "MAIZ", "TRIGO", "GIRASOL", "CEBADA", "SORGO")),
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " " + s.upper().strip() + " "


def modulo_de_actividad(nombre: str) -> Optional[str]:
    """Módulo requerido por la actividad, o None si es transversal (Administración, Camión…)."""
    n = _norm(nombre)
    for mod, palabras in _PALABRAS_MODULO:
        if any(p in n for p in palabras):
            return mod
    return None


def modulos_empresa(get_db: Callable, empresa_id: int) -> Dict[str, int]:
    """
    Flags mod_* de la empresa. Los módulos se guardan en la base del grupo;
    si una columna no existe se asume habilitado (compatibilidad con bases viejas).
    """
    conns = []
    try:
        from plataforma import conexion_grupo

        g = conexion_grupo()
        if g is not None:
            conns.append(g)
    except Exception:
        pass
    try:
        conns.append(get_db())
    except Exception:
        pass

    flags: Dict[str, int] = {}
    try:
        for conn in conns:
            try:
                row = conn.execute("SELECT * FROM empresas WHERE id = ?;", (int(empresa_id),)).fetchone()
            except Exception:
                row = None
            if not row:
                continue
            keys = row.keys() if hasattr(row, "keys") else []
            for k in keys:
                if k.startswith("mod_") and row[k] is not None:
                    flags[k] = 1 if int(row[k] or 0) else 0
            if flags:
                break
    finally:
        for c in conns:
            try:
                c.close()
            except Exception:
                pass
    return flags


def modulo_habilitado(flags: Dict[str, int], mod: Optional[str]) -> bool:
    if not mod:
        return True
    return int(flags.get(mod, 1)) == 1


def actividad_visible(nombre: str, flags: Dict[str, int]) -> bool:
    return modulo_habilitado(flags, modulo_de_actividad(nombre))


def modulos_con_actividad(conn, empresa_id: int) -> Set[str]:
    """Packs que tienen al menos una actividad activa cargada en la empresa."""
    try:
        rows = conn.execute(
            "SELECT nombre FROM actividades WHERE empresa_id = ? AND COALESCE(activo, 1) = 1;",
            (int(empresa_id),),
        ).fetchall()
    except Exception:
        return set()
    out: Set[str] = set()
    for r in rows:
        mod = modulo_de_actividad(r[0] or "")
        if mod:
            out.add(mod)
    return out


def liquidacion_habilitada(
    tipo: str, flags: Dict[str, int], mods_con_actividad: Optional[Set[str]] = None
) -> bool:
    """Requiere mod_liquidaciones, el pack contratado y la actividad cargada."""
    if not modulo_habilitado(flags, "mod_liquidaciones"):
        return False
    mod = LIQUIDACION_MODULO.get((tipo or "").upper())
    if not modulo_habilitado(flags, mod):
        return False
    if mods_con_actividad is not None and mod and mod not in mods_con_actividad:
        return False
    return True


def estado_liquidaciones(get_db: Callable, empresa_id: int) -> Dict[str, dict]:
    flags = modulos_empresa(get_db, empresa_id)
    conn = get_db()
    try:
        con_act = modulos_con_actividad(conn, empresa_id)
    finally:
        conn.close()
    out: Dict[str, dict] = {}
    for tipo, mod in LIQUIDACION_MODULO.items():
        if not modulo_habilitado(flags, "mod_liquidaciones"):
            motivo = "módulo liquidaciones no contratado"
        elif not modulo_habilitado(flags, mod):
            motivo = "pack no contratado"
        elif mod not in con_act:
            motivo = "actividad no cargada"
        else:
            motivo = ""
        out[tipo] = {
            "habilitada": liquidacion_habilitada(tipo, flags, con_act),
            "modulo": mod,
            "label": LIQUIDACION_LABEL[tipo],
            "motivo": motivo,
        }
    return out
