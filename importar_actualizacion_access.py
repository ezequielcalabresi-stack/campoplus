# -*- coding: utf-8 -*-
"""
Importa el Excel de actualización Access → campoplus.db
Archivo esperado: tablas/tablas actualizacion.xlsx (hoja 'movimientos')

- Upsert movimientos bancarios por id_access
- Alta de facturas / pagos en cuentas_corrientes (con id_access)
- Alta/actualización de cheques en cartera
- Resuelve CUIT desde entidades o proveedores.xlsx
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "campoplus.db")
EXCEL_ACT = os.path.join(BASE_DIR, "tablas", "tablas actualizacion.xlsx")
EXCEL_PROV = os.path.join(BASE_DIR, "tablas", "proveedores.xlsx")
if not os.path.exists(EXCEL_PROV):
    EXCEL_PROV = os.path.join(BASE_DIR, "proveedores.xlsx")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def safe_date(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    try:
        if hasattr(val, "strftime"):
            return val.strftime("%Y-%m-%d")
    except Exception:
        pass
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return ""
    try:
        return pd.to_datetime(val).strftime("%Y-%m-%d")
    except Exception:
        return s[:10]


def safe_float(val, default: float = 0.0) -> float:
    try:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def clean_cuit(val) -> str:
    digits = re.sub(r"\D", "", str(val or ""))
    if len(digits) == 11:
        return digits
    return ""


def norm_name(s: str) -> str:
    s = (s or "").upper().strip()
    s = re.sub(r"\s+", " ", s)
    s = s.replace(".", "").replace(",", "")
    for tok in (" S.A.", " S.A", " SA", " S.R.L.", " SRL", " S.H.", " SH"):
        s = s.replace(tok, "")
    return s.strip()


def ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(cuentas_corrientes)")}
    if "id_access" not in cols:
        cur.execute("ALTER TABLE cuentas_corrientes ADD COLUMN id_access INTEGER;")
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cc_id_access
        ON cuentas_corrientes(id_access) WHERE id_access IS NOT NULL;
        """
    )
    conn.commit()


def load_proveedores_map() -> Dict[str, dict]:
    """Mapa nombre normalizado -> datos proveedor (desde Excel Access)."""
    out: Dict[str, dict] = {}
    if not os.path.exists(EXCEL_PROV):
        return out
    df = pd.read_excel(EXCEL_PROV)
    for _, row in df.iterrows():
        cuit = clean_cuit(row.get("CUIT"))
        if not cuit:
            continue
        nombres = [
            str(row.get("Nombre Proveedor") or "").strip(),
            str(row.get("Nombre Real") or "").strip(),
        ]
        data = {
            "cuit": cuit,
            "nombre_fantasia": nombres[0] or nombres[1],
            "razon_social": (nombres[1] or nombres[0]).upper(),
            "telefono": str(row.get("Telefono") or "").strip(),
            "domicilio": str(row.get("Direccion") or "").strip(),
            "localidad": str(row.get("Localidad") or "").strip(),
            "provincia": str(row.get("Provincia") or "").strip(),
            "cbu": str(row.get("CBU") or "").strip(),
            "email": str(row.get("eMAIL") or "").strip(),
        }
        for n in nombres:
            if n and n.lower() != "nan":
                out[norm_name(n)] = data
                out[n.upper().strip()] = data
    return out


def ensure_entidad(conn: sqlite3.Connection, nombre: str, prov_map: dict) -> Optional[str]:
    """Devuelve CUIT (11 dígitos). Crea entidad si hace falta."""
    nombre = (nombre or "").strip()
    if not nombre or nombre.lower() == "nan":
        return None
    cur = conn.cursor()
    # 1) exacto en DB
    cur.execute(
        """
        SELECT cuit FROM entidades
        WHERE UPPER(TRIM(COALESCE(nombre_fantasia,''))) = UPPER(?)
           OR UPPER(TRIM(COALESCE(razon_social,''))) = UPPER(?)
        LIMIT 1;
        """,
        (nombre, nombre),
    )
    row = cur.fetchone()
    if row:
        return clean_cuit(row["cuit"]) or row["cuit"]

    # 2) like
    key = nombre[:30]
    cur.execute(
        """
        SELECT cuit, nombre_fantasia, razon_social FROM entidades
        WHERE UPPER(COALESCE(nombre_fantasia,'')) LIKE '%' || UPPER(?) || '%'
           OR UPPER(COALESCE(razon_social,'')) LIKE '%' || UPPER(?) || '%'
        ORDER BY
            CASE WHEN UPPER(TRIM(COALESCE(nombre_fantasia,''))) = UPPER(?) THEN 0 ELSE 1 END
        LIMIT 1;
        """,
        (key, key, nombre),
    )
    row = cur.fetchone()
    if row:
        return clean_cuit(row["cuit"]) or row["cuit"]

    # 3) proveedores.xlsx
    data = prov_map.get(norm_name(nombre)) or prov_map.get(nombre.upper().strip())
    if not data:
        # fuzzy partial
        nn = norm_name(nombre)
        for k, v in prov_map.items():
            if nn and (nn in k or k in nn) and len(nn) >= 6:
                data = v
                break
    if not data:
        return None

    cuit = data["cuit"]
    cur.execute("SELECT cuit FROM entidades WHERE cuit = ? OR REPLACE(cuit,'-','') = ?;", (cuit, cuit))
    if not cur.fetchone():
        cur.execute(
            """
            INSERT INTO entidades (
                cuit, razon_social, nombre_fantasia, domicilio, localidad, provincia,
                es_proveedor, es_cliente
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 0);
            """,
            (
                cuit,
                data.get("razon_social") or nombre.upper(),
                data.get("nombre_fantasia") or nombre,
                data.get("domicilio") or "",
                data.get("localidad") or "",
                data.get("provincia") or "",
            ),
        )
    return cuit


def mapa_cuentas(conn: sqlite3.Connection) -> Dict[str, int]:
    cur = conn.cursor()
    cur.execute("SELECT id, nro_cta_cte FROM ctas_ctes_bancarias;")
    return {
        str(r["nro_cta_cte"]).strip(): int(r["id"])
        for r in cur.fetchall()
        if r["nro_cta_cte"]
    }


def upsert_mov_banco(conn, row, mapa_cta: dict) -> str:
    """Inserta o actualiza movimiento bancario. Retorna 'insert'|'update'|'skip'."""
    cur = conn.cursor()
    try:
        id_access = int(float(row.get("Id")))
    except (TypeError, ValueError):
        return "skip"

    cta_nro = str(row.get("Cta Cte") or "").strip()
    if not cta_nro or cta_nro.lower() == "nan":
        return "skip"

    tipo = str(row.get("Tipo de Comprobante") or "").strip().upper()
    # Solo bancarios / con cta (incluye pagos cheque debitados de banco)
    bancarios = {"DEBITO BANCARIO", "CREDITO BANCARIO", "PAGO CHEQUE", "PAGO TRANSFERENCIA"}
    if tipo and tipo not in bancarios and safe_float(row.get("HABER")) == 0 and safe_float(row.get("DEBE")) == 0:
        return "skip"

    fec_cobro = safe_date(row.get("FechaCobro")) or safe_date(row.get("Fecha"))
    fec_debito = safe_date(row.get("Debitados"))
    conciliado = 1 if fec_debito else 0
    prov = str(row.get("Proveedor") or "Movimiento Bancario").strip()
    if prov.lower() == "nan":
        prov = "Movimiento Bancario"

    nro_chq = ""
    chq_raw = row.get("Nº Cheque")
    if pd.notnull(chq_raw) and str(chq_raw) not in ("0.0", "0", "nan", "None"):
        nro_chq = str(chq_raw).replace(".0", "").strip()

    haber = safe_float(row.get("HABER"))
    debe = safe_float(row.get("DEBE"))
    # cheques cartera a veces vienen con haber negativo
    if haber < 0:
        haber = abs(haber)
    imp_chq = safe_float(row.get("IMPALCHQ"))
    cuenta_id = mapa_cta.get(cta_nro, 1)

    es_prestamo = 0
    val = row.get("Credito Bancario")
    if pd.notnull(val) and str(val).strip().lower() in ("1", "true", "si", "sí", "yes"):
        es_prestamo = 1

    nro_credito = str(row.get("Nro de Credito") or "").strip()
    if nro_credito.lower() in ("nan", "none"):
        nro_credito = None
    nro_cuota = None
    if pd.notnull(row.get("Nº Cuota")):
        try:
            nro_cuota = int(float(row.get("Nº Cuota")))
        except (TypeError, ValueError):
            nro_cuota = None

    if es_prestamo:
        tipo_op = "Cuota crédito"
    elif tipo == "CREDITO BANCARIO" or (debe > 0 and haber <= 0):
        tipo_op = "Deposito"
    else:
        tipo_op = "Debito"

    cur.execute(
        "SELECT id FROM movimientos_cta_cte_bancos WHERE id_access = ? LIMIT 1;",
        (id_access,),
    )
    existing = cur.fetchone()
    if existing:
        cur.execute(
            """
            UPDATE movimientos_cta_cte_bancos SET
                cuenta_id=?, fecha_cobro=?, fecha_debito=?, proveedor=?, nro_cheque=?,
                haber=?, debe=?, imp_chq=?, cta_cte_nro=?, conciliado=?,
                es_prestamo=?, nro_credito=?, nro_cuota=?, tipo_operacion=?
            WHERE id_access=?;
            """,
            (
                cuenta_id, fec_cobro, fec_debito, prov, nro_chq,
                haber, debe, imp_chq, cta_nro, conciliado,
                es_prestamo, nro_credito, nro_cuota, tipo_op, id_access,
            ),
        )
        return "update"

    cur.execute(
        """
        INSERT INTO movimientos_cta_cte_bancos
        (cuenta_id, fecha_cobro, fecha_debito, proveedor, nro_cheque, haber, debe, imp_chq,
         cta_cte_nro, conciliado, id_access, es_prestamo, nro_credito, nro_cuota, tipo_operacion)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
        """,
        (
            cuenta_id, fec_cobro, fec_debito, prov, nro_chq, haber, debe, imp_chq,
            cta_nro, conciliado, id_access, es_prestamo, nro_credito, nro_cuota, tipo_op,
        ),
    )
    return "insert"


def upsert_cc(conn, row, cuit: str, empresa_id: int = 1) -> str:
    cur = conn.cursor()
    try:
        id_access = int(float(row.get("Id")))
    except (TypeError, ValueError):
        return "skip"

    tipo_raw = str(row.get("Tipo de Comprobante") or "").strip().upper()
    fecha = safe_date(row.get("Fecha"))
    factura = str(row.get("factura Nº") or "").strip()
    if factura.lower() in ("nan", "none"):
        factura = ""

    debe = safe_float(row.get("DEBE"))
    haber = abs(safe_float(row.get("HABER")))
    neto = safe_float(row.get("Importe Neto"))
    iva = safe_float(row.get("Iva Credito Fiscal")) + safe_float(row.get("Iva Percepcion"))
    iibb = safe_float(row.get("IIBB"))
    forma = str(row.get("Forma de Pago") or "").strip()
    if forma.lower() == "nan":
        forma = ""
    nro_chq = ""
    chq_raw = row.get("Nº Cheque")
    if pd.notnull(chq_raw) and str(chq_raw) not in ("0.0", "0", "nan", "None"):
        nro_chq = str(chq_raw).replace(".0", "").strip()

    if tipo_raw == "FACTURA":
        tipo = "FACTURA"
        numero = factura or f"ACC-{id_access}"
        total = debe if debe > 0 else (neto + iva + iibb)
        if total <= 0:
            return "skip"
        debe_v, haber_v = total, 0.0
        estado = "Pendiente"
        if not neto:
            neto = total
    elif tipo_raw in ("PAGO EFECTIVO", "PAGO CHEQUE", "PAGO TRANSFERENCIA"):
        tipo = "Pago"
        numero = factura or tipo_raw or f"PAGO-{id_access}"
        total = haber if haber > 0 else debe
        if total <= 0:
            return "skip"
        debe_v, haber_v = 0.0, total
        estado = "Pagado"
        if not forma:
            forma = {
                "PAGO EFECTIVO": "Caja",
                "PAGO CHEQUE": "Cheque",
                "PAGO TRANSFERENCIA": "Transferencia",
            }.get(tipo_raw, tipo_raw)
    else:
        return "skip"

    if not cuit:
        return "skip_sin_cuit"

    usuario = str(row.get("USUARIO PC") or "Access sync").strip() or "Access sync"

    cur.execute(
        "SELECT id FROM cuentas_corrientes WHERE id_access = ? LIMIT 1;",
        (id_access,),
    )
    existing = cur.fetchone()
    if existing:
        cur.execute(
            """
            UPDATE cuentas_corrientes SET
                entidad_id=?, tipo_comprobante=?, numero_comprobante=?, forma_pago=?,
                nro_cheque=?, fecha=?, vencimiento=?, neto=?, iva=?, debe=?, haber=?,
                total=?, estado=?, usuario_registro=?, empresa_id=?
            WHERE id_access=?;
            """,
            (
                cuit, tipo, numero, forma, nro_chq, fecha, fecha, neto, iva,
                debe_v, haber_v, total, estado, usuario, empresa_id, id_access,
            ),
        )
        return "update"

    # Evitar duplicado por misma factura sin id_access
    if tipo == "FACTURA" and numero:
        cur.execute(
            """
            SELECT id FROM cuentas_corrientes
            WHERE entidad_id = ? AND numero_comprobante = ? AND fecha = ?
              AND ABS(COALESCE(debe,0) - ?) < 0.05
            LIMIT 1;
            """,
            (cuit, numero, fecha, debe_v),
        )
        dup = cur.fetchone()
        if dup:
            cur.execute(
                "UPDATE cuentas_corrientes SET id_access = ? WHERE id = ? AND id_access IS NULL;",
                (id_access, dup["id"]),
            )
            return "linked"

    cur.execute(
        """
        INSERT INTO cuentas_corrientes (
            entidad_id, tipo_comprobante, numero_comprobante, forma_pago, nro_cheque,
            fecha, vencimiento, neto, iva, debe, haber, total, estado,
            usuario_registro, empresa_id, id_access
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
        """,
        (
            cuit, tipo, numero, forma, nro_chq, fecha, fecha, neto, iva,
            debe_v, haber_v, total, estado, usuario, empresa_id, id_access,
        ),
    )
    return "insert"


def upsert_cheque_cartera(conn, row) -> str:
    """Filas de cheques en cartera (sin tipo o con Nº Cheque y haber)."""
    cur = conn.cursor()
    prov = str(row.get("Proveedor") or "").strip()
    if "cheque" not in prov.lower() and "cartera" not in prov.lower():
        # también cheques empresa pagos
        tipo = str(row.get("Tipo de Comprobante") or "").strip().upper()
        if tipo != "PAGO CHEQUE":
            return "skip"

    nro = ""
    chq_raw = row.get("Nº Cheque")
    if pd.notnull(chq_raw) and str(chq_raw) not in ("0.0", "0", "nan", "None"):
        nro = str(chq_raw).replace(".0", "").strip()
    if not nro:
        return "skip"

    monto = abs(safe_float(row.get("HABER"))) or abs(safe_float(row.get("Importe Cheque terceros")))
    if monto <= 0:
        monto = abs(safe_float(row.get("IMPALCHQ")))
    fecha_em = safe_date(row.get("Fecha"))
    fecha_pago = safe_date(row.get("FechaCobro")) or fecha_em
    banco = str(row.get("Banco") or "").strip()
    if banco.lower() == "nan":
        banco = ""
    librador = str(row.get("Titular del Cheque") or row.get("Dador del cheque") or prov).strip()
    if librador.lower() == "nan":
        librador = prov

    tipo_comp = str(row.get("Tipo de Comprobante") or "").strip().upper()
    forma = str(row.get("Forma de Pago") or "").lower()
    # Emitido = cheque propio de la empresa entregado a proveedor (no es "en cartera")
    es_emitido = (
        tipo_comp == "PAGO CHEQUE"
        or "empresa" in forma
        or "cheque propio" in forma
        or "emitido" in forma
    )
    if "cartera" in prov.lower() or ("cheque" in prov.lower() and not es_emitido):
        tipo_ch, estado_ch = "Tercero", "En cartera"
    elif es_emitido:
        tipo_ch, estado_ch = "Emitido", "Emitido"
    else:
        tipo_ch, estado_ch = "Tercero", "En cartera"

    cur.execute(
        "SELECT id, tipo, estado FROM cartera_cheques WHERE TRIM(nro_cheque)=? ORDER BY id DESC LIMIT 1;",
        (nro,),
    )
    ex = cur.fetchone()
    if ex:
        # No degradar un emitido ya entregado a "En cartera"
        if str(ex["tipo"] or "").upper() == "EMITIDO" or str(ex["estado"] or "").lower() in (
            "emitido", "entregado proveedor", "depositado",
        ):
            return "skip"
        cur.execute(
            """
            UPDATE cartera_cheques SET
                tipo=?, banco=?, librador=?, fecha_emision=?, fecha_pago=?, monto=?, estado=?
            WHERE id=?;
            """,
            (tipo_ch, banco, librador, fecha_em, fecha_pago, monto, estado_ch, ex["id"]),
        )
        return "update"

    cur.execute(
        """
        INSERT INTO cartera_cheques
        (tipo, nro_cheque, banco, cuit_emisor, librador, fecha_emision, fecha_pago, monto, moneda, estado, cuenta_id)
        VALUES (?,?,?,?,?,?,?,?, 'ARS', ?, NULL);
        """,
        (tipo_ch, nro, banco, "", librador, fecha_em, fecha_pago, monto, estado_ch),
    )
    return "insert"


def importar_actualizacion(excel_path: Optional[str] = None, empresa_id: int = 1) -> dict:
    path = excel_path or EXCEL_ACT
    if not os.path.exists(path):
        return {"status": "error", "message": f"No se encuentra {path}"}

    conn = get_db()
    ensure_schema(conn)
    prov_map = load_proveedores_map()
    mapa_cta = mapa_cuentas(conn)

    df = pd.read_excel(path, sheet_name=0)
    # Prefer sheet named movimientos
    try:
        xl = pd.ExcelFile(path)
        if "movimientos" in xl.sheet_names:
            df = pd.read_excel(path, sheet_name="movimientos")
    except Exception:
        pass

    stats = {
        "filas": len(df),
        "banco_insert": 0,
        "banco_update": 0,
        "cc_insert": 0,
        "cc_update": 0,
        "cc_linked": 0,
        "cc_sin_cuit": 0,
        "cheques_insert": 0,
        "cheques_update": 0,
        "entidades_nuevas_antes": 0,
        "omitidas": 0,
        "sin_cuit_detalle": [],
    }
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM entidades;")
    stats["entidades_antes"] = int(cur.fetchone()["n"])

    for _, row in df.iterrows():
        tipo = str(row.get("Tipo de Comprobante") or "").strip().upper()
        if tipo in ("", "NAN"):
            tipo = ""

        # Vacías
        if (
            safe_float(row.get("DEBE")) == 0
            and safe_float(row.get("HABER")) == 0
            and not str(row.get("factura Nº") or "").strip()
            and str(row.get("Nº Cheque") or "") in ("0", "0.0", "nan", "")
        ):
            stats["omitidas"] += 1
            continue

        # Banco
        if str(row.get("Cta Cte") or "").strip() not in ("", "nan", "None"):
            r = upsert_mov_banco(conn, row, mapa_cta)
            if r == "insert":
                stats["banco_insert"] += 1
            elif r == "update":
                stats["banco_update"] += 1

        # CC facturas / pagos
        if tipo in ("FACTURA", "PAGO EFECTIVO", "PAGO CHEQUE", "PAGO TRANSFERENCIA"):
            cuit_excel = clean_cuit(row.get("CUIT"))
            nombre = str(row.get("Proveedor") or "").strip()
            cuit = cuit_excel or ensure_entidad(conn, nombre, prov_map)
            r = upsert_cc(conn, row, cuit or "", empresa_id=empresa_id)
            if r == "insert":
                stats["cc_insert"] += 1
            elif r == "update":
                stats["cc_update"] += 1
            elif r == "linked":
                stats["cc_linked"] += 1
            elif r == "skip_sin_cuit":
                stats["cc_sin_cuit"] += 1
                stats["sin_cuit_detalle"].append({"id": row.get("Id"), "proveedor": nombre, "tipo": tipo})

        # Cheques cartera
        rch = upsert_cheque_cartera(conn, row)
        if rch == "insert":
            stats["cheques_insert"] += 1
        elif rch == "update":
            stats["cheques_update"] += 1

    conn.commit()
    cur.execute("SELECT COUNT(*) AS n FROM entidades;")
    stats["entidades_despues"] = int(cur.fetchone()["n"])
    stats["entidades_nuevas"] = stats["entidades_despues"] - stats["entidades_antes"]
    conn.close()
    stats["status"] = "ok"
    stats["archivo"] = path
    stats["ts"] = datetime.now().isoformat(timespec="seconds")
    return stats


if __name__ == "__main__":
    result = importar_actualizacion()
    print(result)
