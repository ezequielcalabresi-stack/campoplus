# -*- coding: utf-8 -*-
"""
Sincroniza entidades (padrón) desde tablas/proveedores.xlsx (Access).
- Marca es_propietario_inmueble / es_locador según «Es Locador/arrendador?»
- S/P → centro_costo=SP (CUIT Access placeholder, p.ej. 00001200000 ≈ «CUIT 0»)
- Upsert por CUIT Access; también alinea filas provisorias 99… del mismo nombre.
"""
from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from typing import Any, Dict, Optional, Tuple

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "campoplus.db")
EXCEL = os.path.join(BASE_DIR, "tablas", "proveedores.xlsx")
if not os.path.exists(EXCEL):
    EXCEL = os.path.join(BASE_DIR, "proveedores.xlsx")


def _norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.upper().replace(".", " ").replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # unificar variantes S/P
    s = s.replace("(S/P)", " S/P").replace("S/P", " S/P")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _digits(v: Any) -> str:
    return "".join(ch for ch in str(v if v is not None and str(v) != "nan" else "") if ch.isdigit())


def _to_bool(v: Any) -> bool:
    if v is True or v is False:
        return bool(v)
    if isinstance(v, (int, float)) and not pd.isna(v):
        return int(v) != 0
    s = str(v if v is not None else "").strip().lower()
    return s in ("si", "sí", "1", "true", "verdadero", "-1", "x", "s")


def _es_cuit_afip_valido(cuit: str) -> bool:
    """CUIT fiscal AFIP (no placeholder Access / no todo cero)."""
    if not cuit or len(cuit) != 11:
        return False
    if cuit == "0" * 11 or int(cuit) == 0:
        return False
    # placeholders Access S/P suelen ser 00… con pocos dígitos significativos
    if cuit.startswith("00") and int(cuit) < 10_000_000:
        return False
    pref = cuit[:2]
    return pref in ("20", "23", "24", "27", "30", "33", "34")


def _es_sp(nombre: str, cuit: str) -> bool:
    n = (nombre or "").upper()
    if "(S/P)" in n or n.endswith("S/P") or " S/P" in n or n.strip().endswith("/P"):
        return True
    if not _es_cuit_afip_valido(cuit):
        # CUIT 0 / placeholder Access
        if cuit and (cuit == "0" * 11 or cuit.startswith("00") or int(cuit or "0") < 10_000_000):
            return True
    return False


def _safe_str(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if s.lower() in ("nan", "none", "nat"):
        return ""
    return s


def sincronizar_padron_access(db_path: str = DB_PATH, excel_path: str = EXCEL) -> Dict[str, Any]:
    if not os.path.exists(excel_path):
        return {"status": "error", "message": f"No se encuentra {excel_path}"}

    df = pd.read_excel(excel_path, sheet_name="proveedores")
    col_loc = "Es Locador/arrendador?"
    if col_loc not in df.columns:
        # fallback nombres viejos
        for c in df.columns:
            if "locador" in str(c).lower() or "arrendador" in str(c).lower():
                col_loc = c
                break

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # columnas necesarias
    cols = {r[1] for r in cur.execute("PRAGMA table_info(entidades)")}
    for col, ddl in [
        ("es_propietario_inmueble", "INTEGER DEFAULT 0"),
        ("es_locador", "INTEGER DEFAULT 0"),
        ("es_proveedor", "INTEGER DEFAULT 1"),
        ("es_cliente", "INTEGER DEFAULT 0"),
        ("es_empleado", "INTEGER DEFAULT 0"),
        ("centro_costo", "TEXT DEFAULT '1'"),
        ("regimen_sicore", "TEXT DEFAULT ''"),
        ("es_cuenta_ajuste", "INTEGER DEFAULT 0"),
        ("es_cuenta_bancaria", "INTEGER DEFAULT 0"),
        ("telefono", "TEXT"),
        ("domicilio", "TEXT"),
        ("localidad", "TEXT"),
        ("codigo_postal", "TEXT"),
        ("provincia", "TEXT"),
        ("cbu", "TEXT"),
        ("email", "TEXT"),
        ("condicion_iva", "TEXT"),
        ("banco", "TEXT"),
        ("nombre_fantasia", "TEXT"),
        ("razon_social", "TEXT"),
    ]:
        if col not in cols:
            cur.execute(f"ALTER TABLE entidades ADD COLUMN {col} {ddl};")

    stats = {
        "leidos": 0,
        "arrendadores_excel": 0,
        "upsert_arrendadores": 0,
        "upsert_otros": 0,
        "alineados_provisorios": 0,
        "omitidos": 0,
        "sp": 0,
    }

    # índice nombre → filas existentes
    existentes = list(cur.execute("SELECT cuit, razon_social, nombre_fantasia, centro_costo FROM entidades"))
    by_name: Dict[str, list] = {}
    for e in existentes:
        for campo in ("razon_social", "nombre_fantasia"):
            k = _norm_name(e[campo] or "")
            if k:
                by_name.setdefault(k, []).append(dict(e))

    def upsert_entidad(payload: Dict[str, Any], es_arrendador: bool) -> str:
        cuit = payload["cuit"]
        cur.execute("SELECT cuit FROM entidades WHERE REPLACE(cuit,'-','') = ?;", (cuit,))
        existe = cur.fetchone()
        if existe:
            cur.execute(
                """
                UPDATE entidades SET
                    nombre_fantasia = COALESCE(NULLIF(?,''), nombre_fantasia),
                    razon_social = COALESCE(NULLIF(?,''), razon_social),
                    telefono = COALESCE(NULLIF(?,''), telefono),
                    domicilio = COALESCE(NULLIF(?,''), domicilio),
                    localidad = COALESCE(NULLIF(?,''), localidad),
                    codigo_postal = COALESCE(NULLIF(?,''), codigo_postal),
                    provincia = COALESCE(NULLIF(?,''), provincia),
                    email = COALESCE(NULLIF(?,''), email),
                    cbu = COALESCE(NULLIF(?,''), cbu),
                    banco = COALESCE(NULLIF(?,''), banco),
                    condicion_iva = COALESCE(NULLIF(?,''), condicion_iva),
                    es_proveedor = CASE WHEN ? = 1 THEN 1 ELSE COALESCE(es_proveedor, 0) END,
                    es_cliente = CASE WHEN ? = 1 THEN 1 ELSE COALESCE(es_cliente, 0) END,
                    es_empleado = CASE WHEN ? = 1 THEN 1 ELSE COALESCE(es_empleado, 0) END,
                    es_propietario_inmueble = CASE WHEN ? = 1 THEN 1 ELSE COALESCE(es_propietario_inmueble, 0) END,
                    es_locador = CASE WHEN ? = 1 THEN 1 ELSE COALESCE(es_locador, 0) END,
                    centro_costo = CASE WHEN ? = 'SP' THEN 'SP' ELSE COALESCE(NULLIF(centro_costo,''), '1') END,
                    regimen_sicore = CASE
                        WHEN ? = 1 AND TRIM(COALESCE(regimen_sicore,'')) = '' THEN '032'
                        WHEN ? = 1 THEN COALESCE(NULLIF(regimen_sicore,''), '032')
                        ELSE regimen_sicore
                    END,
                    es_cuenta_ajuste = 0,
                    es_cuenta_bancaria = CASE WHEN ? = 1 THEN 1 ELSE COALESCE(es_cuenta_bancaria, 0) END
                WHERE REPLACE(cuit,'-','') = ?;
                """,
                (
                    payload["nombre_fantasia"], payload["razon_social"],
                    payload["telefono"], payload["domicilio"], payload["localidad"],
                    payload["codigo_postal"], payload["provincia"], payload["email"],
                    payload["cbu"], payload["banco"], payload["condicion_iva"],
                    payload["es_proveedor"], payload["es_cliente"], payload["es_empleado"],
                    payload["es_propietario_inmueble"], payload["es_locador"],
                    payload["centro_costo"],
                    payload["es_propietario_inmueble"], payload["es_propietario_inmueble"],
                    payload.get("es_cuenta_bancaria", 0),
                    cuit,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO entidades (
                    cuit, nombre_fantasia, razon_social, telefono, domicilio, localidad,
                    codigo_postal, provincia, email, cbu, banco, condicion_iva,
                    es_proveedor, es_cliente, es_empleado,
                    es_propietario_inmueble, es_locador, centro_costo, regimen_sicore,
                    es_cuenta_ajuste, es_cuenta_bancaria
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
                """,
                (
                    cuit, payload["nombre_fantasia"], payload["razon_social"],
                    payload["telefono"], payload["domicilio"], payload["localidad"],
                    payload["codigo_postal"], payload["provincia"], payload["email"],
                    payload["cbu"], payload["banco"], payload["condicion_iva"],
                    payload["es_proveedor"], payload["es_cliente"], payload["es_empleado"],
                    payload["es_propietario_inmueble"], payload["es_locador"],
                    payload["centro_costo"],
                    "032" if payload["es_propietario_inmueble"] else "",
                    0, payload.get("es_cuenta_bancaria", 0),
                ),
            )
        return "update" if existe else "insert"

    for _, row in df.iterrows():
        stats["leidos"] += 1
        nombre = _safe_str(row.get("Nombre Proveedor"))
        if not nombre:
            stats["omitidos"] += 1
            continue

        baja = _to_bool(row.get("Baja???"))
        es_forma_pago = _to_bool(row.get("Se Utiliza como Forma de Pago"))
        es_cta_bancaria = _to_bool(row.get("Cuenta Bancaria"))
        es_arrendador = _to_bool(row.get(col_loc))
        es_cliente = _to_bool(row.get("CLIENTE"))
        es_empleado = _to_bool(row.get("Es Empleado?"))

        # SILO CHICO / Galicia basura
        if "SILO CHICO" in nombre.upper() and baja:
            stats["omitidos"] += 1
            continue

        cuit_raw = _digits(row.get("CUIT"))
        # Access a veces pone texto
        if not cuit_raw:
            if es_arrendador:
                # forzar placeholder 0 + hash nombre para unicidad S/P
                import hashlib
                h = int(hashlib.md5(nombre.encode("utf-8")).hexdigest()[:6], 16) % 1_000_000
                cuit_raw = f"{h:011d}"
            else:
                stats["omitidos"] += 1
                continue

        cuit = cuit_raw.zfill(11)[-11:]
        sp = _es_sp(nombre, cuit)
        if sp:
            if int(cuit) == 0:
                import hashlib
                h = int(hashlib.md5(nombre.encode("utf-8")).hexdigest()[:6], 16) % 1_000_000
                cuit = f"{h:011d}"
            stats["sp"] += 1

        # Si el CUIT placeholder ya pertenece a OTRA razón social, no pisar: generar CUIT SP único
        cur.execute(
            "SELECT razon_social, nombre_fantasia FROM entidades WHERE REPLACE(cuit,'-','') = ?;",
            (cuit,),
        )
        choc = cur.fetchone()
        if choc:
            ex_names = {_norm_name(choc["razon_social"] or ""), _norm_name(choc["nombre_fantasia"] or "")}
            my_names = {_norm_name(nombre), _norm_name(_safe_str(row.get("Nombre Real")) or nombre)}
            if ex_names.isdisjoint(my_names) and not (my_names & ex_names):
                # nombres distintos → colisión de placeholder Access
                import hashlib
                h = int(hashlib.md5(("SP|" + nombre).encode("utf-8")).hexdigest()[:8], 16) % 90_000_000
                cuit = f"00{h:09d}"[-11:]
                sp = True
                # asegurar libre
                for salto in range(50):
                    cand = f"{(int(cuit) + salto):011d}"[-11:]
                    cur.execute("SELECT 1 FROM entidades WHERE REPLACE(cuit,'-','')=?;", (cand,))
                    if not cur.fetchone():
                        cuit = cand
                        break

        # no importar masivamente cuentas bancarias / formas de pago salvo que sean arrendadores
        if (es_forma_pago or es_cta_bancaria) and not es_arrendador:
            stats["omitidos"] += 1
            continue
        if baja and not es_arrendador:
            stats["omitidos"] += 1
            continue

        razon = _safe_str(row.get("Nombre Real")) or nombre
        payload = {
            "cuit": cuit,
            "nombre_fantasia": nombre,
            "razon_social": razon,
            "telefono": _safe_str(row.get("Telefono")),
            "domicilio": _safe_str(row.get("Direccion")),
            "localidad": _safe_str(row.get("Localidad")),
            "codigo_postal": _safe_str(row.get("Cod Postal")),
            "provincia": _safe_str(row.get("Provincia")),
            "email": _safe_str(row.get("eMAIL")),
            "cbu": _safe_str(row.get("CBU")),
            "banco": _safe_str(row.get("BANCO")),
            "condicion_iva": _safe_str(row.get("Cod AfipCondicion")),
            "es_proveedor": 0 if (es_cliente and not es_arrendador) else 1,
            "es_cliente": 1 if es_cliente else 0,
            "es_empleado": 1 if es_empleado else 0,
            "es_propietario_inmueble": 1 if es_arrendador else 0,
            "es_locador": 1 if es_arrendador else 0,
            "centro_costo": "SP" if sp else "1",
            "es_cuenta_bancaria": 1 if es_cta_bancaria else 0,
        }
        # proveedores normales: solo sincronizar flag arrendador / alta si arrendador
        # para no inflar el padrón con 1800 filas de una: priorizar arrendadores + actualizar existentes
        if es_arrendador:
            stats["arrendadores_excel"] += 1
            upsert_entidad(payload, True)
            stats["upsert_arrendadores"] += 1

            # alinear provisionales 99… / mismo nombre
            for key in {_norm_name(nombre), _norm_name(razon)}:
                for ex in by_name.get(key, []):
                    ex_cuit = _digits(ex["cuit"])
                    if ex_cuit == cuit:
                        continue
                    # mismo nombre, otro CUIT (provisorio): marcar arrendador + SP si corresponde
                    cur.execute(
                        """
                        UPDATE entidades SET
                            es_propietario_inmueble = 1,
                            es_locador = 1,
                            es_proveedor = 1,
                            regimen_sicore = COALESCE(NULLIF(regimen_sicore,''), '032'),
                            centro_costo = CASE WHEN ? = 'SP' THEN 'SP' ELSE centro_costo END
                        WHERE REPLACE(cuit,'-','') = ?;
                        """,
                        (payload["centro_costo"], ex_cuit),
                    )
                    if cur.rowcount:
                        stats["alineados_provisorios"] += 1
        else:
            # si ya existe por CUIT, solo refrescar datos básicos / no tocar propietario a 0
            cur.execute("SELECT 1 FROM entidades WHERE REPLACE(cuit,'-','') = ?;", (cuit,))
            if cur.fetchone():
                payload["es_propietario_inmueble"] = 0  # no bajar el flag en este branch
                # update suave sin apagar arrendador
                cur.execute(
                    """
                    UPDATE entidades SET
                        nombre_fantasia = COALESCE(NULLIF(?,''), nombre_fantasia),
                        razon_social = COALESCE(NULLIF(?,''), razon_social),
                        telefono = COALESCE(NULLIF(?,''), telefono),
                        domicilio = COALESCE(NULLIF(?,''), domicilio),
                        localidad = COALESCE(NULLIF(?,''), localidad),
                        es_cliente = CASE WHEN ? = 1 THEN 1 ELSE es_cliente END
                    WHERE REPLACE(cuit,'-','') = ?;
                    """,
                    (
                        payload["nombre_fantasia"], payload["razon_social"],
                        payload["telefono"], payload["domicilio"], payload["localidad"],
                        payload["es_cliente"], cuit,
                    ),
                )
                stats["upsert_otros"] += 1
            # no insertar masivamente el resto del excel aquí

    # Pasada final: todos los excel arrendadores por nombre (por si CUIT distinto)
    for _, row in df.iterrows():
        if not _to_bool(row.get(col_loc)):
            continue
        if _to_bool(row.get("Baja???")):
            continue
        nombre = _safe_str(row.get("Nombre Proveedor"))
        if not nombre or "SILO CHICO" in nombre.upper():
            continue
        razon = _safe_str(row.get("Nombre Real")) or nombre
        sp = _es_sp(nombre, _digits(row.get("CUIT")).zfill(11)[-11:] if _digits(row.get("CUIT")) else "")
        for key in {_norm_name(nombre), _norm_name(razon)}:
            for ex in by_name.get(key, []):
                cur.execute(
                    """
                    UPDATE entidades SET
                        es_propietario_inmueble = 1,
                        es_locador = 1,
                        es_proveedor = 1,
                        regimen_sicore = COALESCE(NULLIF(TRIM(regimen_sicore),''), '032'),
                        centro_costo = CASE WHEN ? THEN 'SP' ELSE COALESCE(NULLIF(centro_costo,''), '1') END
                    WHERE REPLACE(cuit,'-','') = ?;
                    """,
                    (1 if sp else 0, _digits(ex["cuit"])),
                )

    conn.commit()
    n_arr = cur.execute(
        "SELECT COUNT(*) FROM entidades WHERE COALESCE(es_propietario_inmueble,0)=1"
    ).fetchone()[0]
    n_sp = cur.execute(
        "SELECT COUNT(*) FROM entidades WHERE COALESCE(es_propietario_inmueble,0)=1 AND UPPER(TRIM(COALESCE(centro_costo,''))) IN ('SP','S/P')"
    ).fetchone()[0]
    conn.close()
    stats["arrendadores_en_db"] = n_arr
    stats["arrendadores_sp_en_db"] = n_sp
    stats["status"] = "ok"
    return stats


if __name__ == "__main__":
    r = sincronizar_padron_access()
    print(r)
