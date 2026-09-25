"""
Motor contable CAmpo+: asientos vinculados a operaciones de gestión.
Cada movimiento de origen (bancos, etc.) genera un asiento; al eliminar
la operación se elimina el asiento y, si aplica, movimientos hermanos
(p. ej. transferencia).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Cierre de EECC: 30 de junio → ejercicio 01/07/AAAA – 30/06/AAAA+1
CIERRE_EECC_MES = 6
CIERRE_EECC_DIA = 30


def ejercicio_desde_fecha(fecha: str, cierre_mes: int = CIERRE_EECC_MES, cierre_dia: int = CIERRE_EECC_DIA) -> str:
    """
    Devuelve el rótulo de ejercicio contable (ej. '2025/2026').
    Con cierre 30/06: desde 01/07/2025 hasta 30/06/2026 inclusive = 2025/2026.
    """
    if not fecha:
        return ""
    try:
        dt = datetime.strptime(str(fecha)[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    if (dt.month, dt.day) > (cierre_mes, cierre_dia):
        return f"{dt.year}/{dt.year + 1}"
    return f"{dt.year - 1}/{dt.year}"


def ejercicios_disponibles(cursor, empresa_id: Optional[int] = None) -> List[str]:
    params: List[Any] = []
    where = "WHERE COALESCE(anulado,0)=0 AND ejercicio IS NOT NULL AND TRIM(ejercicio) != ''"
    if empresa_id is not None:
        where += " AND empresa_id = ?"
        params.append(empresa_id)
    cursor.execute(
        f"SELECT DISTINCT ejercicio FROM asientos_contables {where} ORDER BY ejercicio DESC;",
        params,
    )
    return [r[0] if not isinstance(r, sqlite3.Row) else r["ejercicio"] for r in cursor.fetchall()]


PLAN_CUENTAS_BASE = [
    ("1.1.01", "Caja y Bancos", "Activo"),
    ("1.1.02", "Cuentas a Cobrar / Créditos", "Activo"),
    ("1.1.03", "Inversiones Temporarias (FCI)", "Activo"),
    ("1.1.04", "Insumos y Existencias", "Activo"),
    ("1.2.01", "IVA Crédito Fiscal 21%", "Activo"),
    ("1.2.02", "Percepciones IIBB", "Activo"),
    ("1.2.03", "IVA Crédito Fiscal 10.5%", "Activo"),
    ("2.1.01", "Proveedores Varios", "Pasivo"),
    ("2.1.02", "Deudas Financieras", "Pasivo"),
    ("2.1.03", "Cargas Fiscales y Sociales", "Pasivo"),
    ("4.1.01", "Producción Agricultura y Ganadería", "Resultado"),
    ("4.2.02", "Gastos de Administración", "Resultado"),
    ("4.2.04", "Gastos de Financiación e Impuestos Bancarios", "Resultado"),
]


def init_contabilidad(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plan_de_cuentas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo_cuenta TEXT UNIQUE NOT NULL,
            nombre_cuenta TEXT NOT NULL,
            tipo_cuenta TEXT,
            empresa_id INTEGER DEFAULT 1,
            activa INTEGER DEFAULT 1
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS asientos_contables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            concepto TEXT,
            total_debe REAL DEFAULT 0,
            total_haber REAL DEFAULT 0,
            empresa_id INTEGER DEFAULT 1,
            origen_modulo TEXT,
            origen_id INTEGER,
            referencia TEXT,
            anulado INTEGER DEFAULT 0,
            ejercicio TEXT,
            centro_costo TEXT DEFAULT '1'
        );
    """)
    cursor.execute("PRAGMA table_info(asientos_contables);")
    cols_asi = [c[1] for c in cursor.fetchall()]
    if "ejercicio" not in cols_asi:
        cursor.execute("ALTER TABLE asientos_contables ADD COLUMN ejercicio TEXT;")
    if "centro_costo" not in cols_asi:
        cursor.execute("ALTER TABLE asientos_contables ADD COLUMN centro_costo TEXT DEFAULT '1';")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_asientos_emp_fecha ON asientos_contables (empresa_id, anulado, fecha, id);"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS detalles_asiento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asiento_id INTEGER NOT NULL,
            cuenta_id INTEGER NOT NULL,
            debe REAL DEFAULT 0,
            haber REAL DEFAULT 0,
            concepto_linea TEXT,
            FOREIGN KEY (asiento_id) REFERENCES asientos_contables(id),
            FOREIGN KEY (cuenta_id) REFERENCES plan_de_cuentas(id)
        );
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_detalles_asiento_id ON detalles_asiento (asiento_id);"
    )

    # Configuración de cierre EECC (por defecto 30/06)
    cursor.execute("PRAGMA table_info(configuracion_empresa);")
    cols_cfg = [c[1] for c in cursor.fetchall()]
    if "cierre_eecc_mes" not in cols_cfg:
        cursor.execute("ALTER TABLE configuracion_empresa ADD COLUMN cierre_eecc_mes INTEGER DEFAULT 6;")
    if "cierre_eecc_dia" not in cols_cfg:
        cursor.execute("ALTER TABLE configuracion_empresa ADD COLUMN cierre_eecc_dia INTEGER DEFAULT 30;")
    cursor.execute(
        """
        UPDATE configuracion_empresa
        SET cierre_eecc_mes = COALESCE(cierre_eecc_mes, 6),
            cierre_eecc_dia = COALESCE(cierre_eecc_dia, 30)
        WHERE id = 1;
        """
    )
    for codigo, nombre, tipo in PLAN_CUENTAS_BASE:
        cursor.execute(
            """
            INSERT OR IGNORE INTO plan_de_cuentas (codigo_cuenta, nombre_cuenta, tipo_cuenta)
            VALUES (?, ?, ?);
            """,
            (codigo, nombre, tipo),
        )

    cursor.execute("PRAGMA table_info(movimientos_cta_cte_bancos);")
    cols_mov = [c[1] for c in cursor.fetchall()]
    if "asiento_id" not in cols_mov:
        cursor.execute("ALTER TABLE movimientos_cta_cte_bancos ADD COLUMN asiento_id INTEGER;")
    if "tipo_operacion" not in cols_mov:
        cursor.execute("ALTER TABLE movimientos_cta_cte_bancos ADD COLUMN tipo_operacion TEXT;")

    cursor.execute("PRAGMA table_info(ctas_ctes_bancarias);")
    cols_cta = [c[1] for c in cursor.fetchall()]
    if "cuenta_contable_id" not in cols_cta:
        cursor.execute("ALTER TABLE ctas_ctes_bancarias ADD COLUMN cuenta_contable_id INTEGER;")


def _id_cuenta(cursor, codigo: str) -> int:
    cursor.execute("SELECT id FROM plan_de_cuentas WHERE codigo_cuenta = ?;", (codigo,))
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Cuenta contable inexistente: {codigo}")
    return int(row["id"] if isinstance(row, sqlite3.Row) else row[0])


def _asegurar_cuenta(cursor, codigo: str, nombre: str, tipo: str = "Activo") -> int:
    cursor.execute(
        """
        INSERT OR IGNORE INTO plan_de_cuentas (codigo_cuenta, nombre_cuenta, tipo_cuenta)
        VALUES (?, ?, ?);
        """,
        (codigo, nombre, tipo),
    )
    return _id_cuenta(cursor, codigo)


def _fecha_iso_contable(fecha: str) -> str:
    """Acepta YYYY-MM-DD o DD/MM/AAAA → ISO."""
    f = (fecha or "").strip()
    if len(f) >= 10 and f[2] == "/" and f[5] == "/":
        d, m, y = f[:10].split("/")
        return f"{y}-{m}-{d}"
    return f[:10]


def asiento_para_comprobante_compra(
    cursor,
    *,
    fecha: str,
    tipo_comprobante: str,
    numero_comprobante: str,
    cuit_proveedor: str,
    neto: float,
    iva: float = 0.0,
    iva_21: float = 0.0,
    iva_105: float = 0.0,
    percepcion_iibb: float = 0.0,
    total: float = 0.0,
    es_nota_credito: bool = False,
    empresa_id: int = 1,
    origen_id: Optional[int] = None,
) -> Optional[int]:
    """
    Asiento de factura/ND de compra, o el INVERSO para Nota de Crédito.

    Factura/ND:
      Debe  Insumos/Existencias (neto) + IVA CF + Perc. IIBB
      Haber Proveedores (total)

    Nota de Crédito (inverso — productos devueltos / crédito en cta cte):
      Debe  Proveedores (total)
      Haber Insumos/Existencias + IVA CF + Perc. IIBB
    """
    init_contabilidad(cursor)

    # Proveedor S/P: solo gestión, sin partida oficial
    if proveedor_omite_asiento_oficial(cursor, cuit_proveedor):
        return None
    # También por CUIT exacto
    cuit_clean = "".join(ch for ch in str(cuit_proveedor or "") if ch.isdigit())
    if cuit_clean:
        cursor.execute(
            "SELECT centro_costo FROM entidades WHERE REPLACE(cuit,'-','') = ? LIMIT 1;",
            (cuit_clean,),
        )
        row = cursor.fetchone()
        if row:
            cc = (row["centro_costo"] if isinstance(row, sqlite3.Row) else row[0]) or "1"
            if str(cc).strip().upper() in ("SP", "S/P"):
                return None

    neto = round(float(neto or 0), 2)
    total = round(float(total or 0), 2)
    iva = round(float(iva or 0), 2)
    iibb = round(float(percepcion_iibb or 0), 2)
    iva21 = round(float(iva_21 or 0), 2)
    iva105 = round(float(iva_105 or 0), 2)
    if iva21 <= 0 and iva105 <= 0 and iva > 0:
        iva21 = iva  # si no discriminan, todo al 21%

    if total <= 0 and neto <= 0:
        return None

    # Si total no viene, armarlo
    if total <= 0:
        total = round(neto + iva21 + iva105 + iibb, 2)

    cta_insumos = _asegurar_cuenta(cursor, "1.1.04", "Insumos y Existencias", "Activo")
    cta_iva21 = _asegurar_cuenta(cursor, "1.2.01", "IVA Crédito Fiscal 21%", "Activo")
    cta_iibb = _asegurar_cuenta(cursor, "1.2.02", "Percepciones IIBB", "Activo")
    cta_iva105 = _asegurar_cuenta(cursor, "1.2.03", "IVA Crédito Fiscal 10.5%", "Activo")
    cta_prov = _asegurar_cuenta(cursor, "2.1.01", "Proveedores Varios", "Pasivo")

    etiqueta = "Nota de Crédito" if es_nota_credito else "Factura/ND compra"
    concepto = f"{etiqueta} {tipo_comprobante} {numero_comprobante}".strip()
    fecha_iso = _fecha_iso_contable(fecha)

    lineas: List[Dict[str, Any]] = []
    if es_nota_credito:
        # Inverso: Deudor Proveedores / Acreedor existencias e impuestos
        lineas.append({"cuenta_id": cta_prov, "debe": total, "haber": 0, "concepto_linea": concepto})
        if neto > 0:
            lineas.append({"cuenta_id": cta_insumos, "debe": 0, "haber": neto, "concepto_linea": "Devolución insumos / gasto"})
        if iva21 > 0:
            lineas.append({"cuenta_id": cta_iva21, "debe": 0, "haber": iva21, "concepto_linea": "Reverso IVA CF 21%"})
        if iva105 > 0:
            lineas.append({"cuenta_id": cta_iva105, "debe": 0, "haber": iva105, "concepto_linea": "Reverso IVA CF 10.5%"})
        if iibb > 0:
            lineas.append({"cuenta_id": cta_iibb, "debe": 0, "haber": iibb, "concepto_linea": "Reverso Perc. IIBB"})
    else:
        if neto > 0:
            lineas.append({"cuenta_id": cta_insumos, "debe": neto, "haber": 0, "concepto_linea": "Insumos / gasto"})
        if iva21 > 0:
            lineas.append({"cuenta_id": cta_iva21, "debe": iva21, "haber": 0, "concepto_linea": "IVA CF 21%"})
        if iva105 > 0:
            lineas.append({"cuenta_id": cta_iva105, "debe": iva105, "haber": 0, "concepto_linea": "IVA CF 10.5%"})
        if iibb > 0:
            lineas.append({"cuenta_id": cta_iibb, "debe": iibb, "haber": 0, "concepto_linea": "Perc. IIBB"})
        lineas.append({"cuenta_id": cta_prov, "debe": 0, "haber": total, "concepto_linea": concepto})

    # Ajuste de redondeo: diferencia a Proveedores
    td = round(sum(float(l.get("debe") or 0) for l in lineas), 2)
    th = round(sum(float(l.get("haber") or 0) for l in lineas), 2)
    diff = round(td - th, 2)
    if abs(diff) > 0.001 and abs(diff) <= 0.05:
        if es_nota_credito:
            # ajustar haber de insumos o debe de proveedores
            lineas[0]["debe"] = round(float(lineas[0]["debe"]) - diff, 2)
        else:
            lineas[-1]["haber"] = round(float(lineas[-1]["haber"]) + diff, 2)

    return crear_asiento(
        cursor,
        fecha=fecha_iso,
        concepto=concepto,
        lineas=lineas,
        empresa_id=empresa_id,
        origen_modulo="compras_nc" if es_nota_credito else "compras",
        origen_id=origen_id,
        referencia=f"{tipo_comprobante} {numero_comprobante}".strip(),
        centro_costo="1",
    )


def asiento_para_factura_venta(
    cursor,
    fecha: str,
    tipo_comprobante: str,
    numero_comprobante: str,
    cuit_cliente: str,
    neto: float,
    iva: float = 0.0,
    percepcion_iibb: float = 0.0,
    total: float = 0.0,
    empresa_id: int = 1,
    origen_id: Optional[int] = None,
) -> Optional[int]:
    """
    Factura de venta (Silo Chico agente de percepción IIBB):
      Debe  Clientes (total = neto + IVA + perc)
      Haber Ventas (neto)
      Haber IVA Débito Fiscal (iva)
      Haber Percepciones IIBB a Pagar (perc)
    """
    init_contabilidad(cursor)
    neto = round(float(neto or 0), 2)
    iva = round(float(iva or 0), 2)
    perc = round(float(percepcion_iibb or 0), 2)
    total = round(float(total or 0), 2)
    if total <= 0:
        total = round(neto + iva + perc, 2)
    if total <= 0:
        return None

    cta_cli = _asegurar_cuenta(cursor, "1.1.02", "Clientes", "Activo")
    cta_vta = _asegurar_cuenta(cursor, "4.1.01", "Ventas", "Ingreso")
    cta_iva = _asegurar_cuenta(cursor, "2.1.02", "IVA Débito Fiscal", "Pasivo")
    cta_perc = _asegurar_cuenta(cursor, "2.1.05", "Percepciones IIBB a Pagar", "Pasivo")

    concepto = f"Venta {tipo_comprobante} {numero_comprobante}".strip()
    fecha_iso = _fecha_iso_contable(fecha)
    lineas: List[Dict[str, Any]] = [
        {"cuenta_id": cta_cli, "debe": total, "haber": 0, "concepto_linea": concepto},
    ]
    if neto > 0:
        lineas.append({"cuenta_id": cta_vta, "debe": 0, "haber": neto, "concepto_linea": "Ventas"})
    if iva > 0:
        lineas.append({"cuenta_id": cta_iva, "debe": 0, "haber": iva, "concepto_linea": "IVA DF"})
    if perc > 0:
        lineas.append({"cuenta_id": cta_perc, "debe": 0, "haber": perc, "concepto_linea": "Perc. IIBB ventas"})

    td = round(sum(float(l.get("debe") or 0) for l in lineas), 2)
    th = round(sum(float(l.get("haber") or 0) for l in lineas), 2)
    diff = round(td - th, 2)
    if abs(diff) > 0.001 and abs(diff) <= 0.05:
        lineas[0]["debe"] = round(float(lineas[0]["debe"]) - diff, 2)

    return crear_asiento(
        cursor,
        fecha=fecha_iso,
        concepto=concepto,
        lineas=lineas,
        empresa_id=empresa_id,
        origen_modulo="ventas",
        origen_id=origen_id,
        referencia=f"{tipo_comprobante} {numero_comprobante}".strip(),
        centro_costo="1",
    )


def asegurar_cuenta_banco(cursor, cuenta_bancaria: Dict[str, Any]) -> int:
    """Cuenta patrimonial 1.1.01.NNN por cada cta cte bancaria."""
    cid = int(cuenta_bancaria["id"])
    if cuenta_bancaria.get("cuenta_contable_id"):
        return int(cuenta_bancaria["cuenta_contable_id"])

    codigo = f"1.1.01.{cid:03d}"
    nombre = f"Banco {cuenta_bancaria.get('banco') or ''} {cuenta_bancaria.get('nro_cta_cte') or ''}".strip()
    cursor.execute("SELECT id FROM plan_de_cuentas WHERE codigo_cuenta = ?;", (codigo,))
    row = cursor.fetchone()
    if row:
        cuenta_id = int(row["id"] if isinstance(row, sqlite3.Row) else row[0])
    else:
        cursor.execute(
            """
            INSERT INTO plan_de_cuentas (codigo_cuenta, nombre_cuenta, tipo_cuenta)
            VALUES (?, ?, 'Activo');
            """,
            (codigo, nombre),
        )
        cuenta_id = int(cursor.lastrowid)

    cursor.execute(
        "UPDATE ctas_ctes_bancarias SET cuenta_contable_id = ? WHERE id = ?;",
        (cuenta_id, cid),
    )
    return cuenta_id


def crear_asiento(
    cursor,
    *,
    fecha: str,
    concepto: str,
    lineas: List[Dict[str, Any]],
    empresa_id: int = 1,
    origen_modulo: str = "",
    origen_id: Optional[int] = None,
    referencia: str = "",
    centro_costo: str = "1",
) -> int:
    lineas_ok = [
        {
            "cuenta_id": int(l["cuenta_id"]),
            "debe": round(float(l.get("debe") or 0), 2),
            "haber": round(float(l.get("haber") or 0), 2),
            "concepto_linea": l.get("concepto_linea") or concepto,
        }
        for l in lineas
        if float(l.get("debe") or 0) > 0 or float(l.get("haber") or 0) > 0
    ]
    if not lineas_ok:
        raise ValueError("El asiento no tiene líneas con importes.")

    total_debe = round(sum(l["debe"] for l in lineas_ok), 2)
    total_haber = round(sum(l["haber"] for l in lineas_ok), 2)
    if abs(total_debe - total_haber) > 0.05:
        raise ValueError(f"Asiento desbalanceado: Debe {total_debe} != Haber {total_haber}")

    # Contabilidad oficial = centro '1'. SP nunca debería llegar acá.
    cc = (centro_costo or "1").strip().upper()
    if cc in ("SP", "S/P"):
        raise ValueError("Centro de costos S/P: no se genera asiento en contabilidad oficial.")

    ejercicio = ejercicio_desde_fecha(fecha)
    cursor.execute(
        """
        INSERT INTO asientos_contables
        (fecha, concepto, total_debe, total_haber, empresa_id, origen_modulo, origen_id, referencia, ejercicio, centro_costo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (fecha, concepto, total_debe, total_haber, empresa_id, origen_modulo, origen_id, referencia, ejercicio, "1"),
    )
    asiento_id = int(cursor.lastrowid)
    for l in lineas_ok:
        cursor.execute(
            """
            INSERT INTO detalles_asiento (asiento_id, cuenta_id, debe, haber, concepto_linea)
            VALUES (?, ?, ?, ?, ?);
            """,
            (asiento_id, l["cuenta_id"], l["debe"], l["haber"], l["concepto_linea"]),
        )
    return asiento_id


def eliminar_asiento(cursor, asiento_id: int) -> None:
    if not asiento_id:
        return
    cursor.execute("DELETE FROM detalles_asiento WHERE asiento_id = ?;", (asiento_id,))
    cursor.execute("DELETE FROM asientos_contables WHERE id = ?;", (asiento_id,))


def eliminar_cascada_movimiento_banco(cursor, id_mov: int) -> Dict[str, Any]:
    """
    Elimina el movimiento y su asiento.
    Si es transferencia (varios movimientos con el mismo asiento_id), elimina el par.
    """
    cursor.execute(
        "SELECT id, asiento_id FROM movimientos_cta_cte_bancos WHERE id = ?;",
        (id_mov,),
    )
    row = cursor.fetchone()
    if not row:
        return {"ok": False, "message": "Movimiento no encontrado."}

    asiento_id = row["asiento_id"] if isinstance(row, sqlite3.Row) else row[1]
    eliminados = []

    if asiento_id:
        cursor.execute(
            "SELECT id FROM movimientos_cta_cte_bancos WHERE asiento_id = ?;",
            (asiento_id,),
        )
        ids = [int(r["id"] if isinstance(r, sqlite3.Row) else r[0]) for r in cursor.fetchall()]
        cursor.execute(
            "DELETE FROM movimientos_cta_cte_bancos WHERE asiento_id = ?;",
            (asiento_id,),
        )
        eliminar_asiento(cursor, int(asiento_id))
        eliminados = ids
        return {
            "ok": True,
            "message": f"Se eliminaron {len(ids)} movimiento(s) y el asiento #{asiento_id}.",
            "movimientos_eliminados": eliminados,
            "asiento_eliminado": int(asiento_id),
        }

    cursor.execute("DELETE FROM movimientos_cta_cte_bancos WHERE id = ?;", (id_mov,))
    return {
        "ok": True,
        "message": "Movimiento eliminado (sin asiento vinculado).",
        "movimientos_eliminados": [id_mov],
        "asiento_eliminado": None,
    }


def asiento_para_alquiler(
    cursor,
    *,
    fecha: str,
    locador: str,
    importe: float,
    detalle: str,
    empresa_id: int = 1,
    origen_id: Optional[int] = None,
) -> Optional[int]:
    """
    Liquidación / venta de cuota de alquiler.
    Debe Alquileres, Haber Proveedores.
    Arrendador S/P: no genera partida oficial.
    """
    init_contabilidad(cursor)
    monto = round(float(importe or 0), 2)
    if monto <= 0:
        return None
    if proveedor_omite_asiento_oficial(cursor, locador):
        return None
    cta_gasto = _asegurar_cuenta(cursor, "5.1.01", "Alquileres", "Resultado")
    cta_prov = _asegurar_cuenta(cursor, "2.1.01", "Proveedores Varios", "Pasivo")
    concepto = (detalle or f"Alquiler {locador}").strip()
    return crear_asiento(
        cursor,
        fecha=_fecha_iso_contable(fecha),
        concepto=concepto[:180],
        lineas=[
            {"cuenta_id": cta_gasto, "debe": monto, "haber": 0, "concepto_linea": concepto[:120]},
            {"cuenta_id": cta_prov, "debe": 0, "haber": monto, "concepto_linea": locador[:120]},
        ],
        empresa_id=empresa_id,
        origen_modulo="alquileres",
        origen_id=origen_id,
        referencia=locador[:80],
        centro_costo="1",
    )


def proveedor_omite_asiento_oficial(cursor, proveedor: str) -> bool:
    """True si el proveedor está marcado Centro de Costos S/P (Sin Partida)."""
    texto = (proveedor or "").strip()
    if not texto:
        return False
    upper = texto.upper()
    if "(S/P)" in upper or upper.endswith("S/P") or " S/P" in upper:
        return True
    # Match por razón social / fantasia / cuit contenido en el texto
    digitos = "".join(ch for ch in texto if ch.isdigit())
    cursor.execute(
        """
        SELECT centro_costo, razon_social, nombre_fantasia, cuit
        FROM entidades
        WHERE COALESCE(centro_costo, '1') = 'SP'
           OR UPPER(COALESCE(razon_social, '')) LIKE '%(S/P)%'
           OR UPPER(COALESCE(nombre_fantasia, '')) LIKE '%(S/P)%'
        """
    )
    for row in cursor.fetchall():
        cc = (row["centro_costo"] if isinstance(row, sqlite3.Row) else row[0]) or "1"
        razon = (row["razon_social"] if isinstance(row, sqlite3.Row) else row[1]) or ""
        fantasia = (row["nombre_fantasia"] if isinstance(row, sqlite3.Row) else row[2]) or ""
        cuit = "".join(ch for ch in str(row["cuit"] if isinstance(row, sqlite3.Row) else row[3] or "") if ch.isdigit())
        if cc != "SP" and "(S/P)" not in (razon + fantasia).upper():
            continue
        nombres = [n.strip().upper() for n in (razon, fantasia) if n and str(n).strip()]
        if any(n and n in upper for n in nombres):
            return True
        if digitos and cuit and digitos == cuit:
            return True
    return False


def asiento_para_movimiento_banco(
    cursor,
    *,
    fecha: str,
    tipo_operacion: str,
    proveedor: str,
    monto: float,
    imp_chq: float,
    cuenta_origen: Dict[str, Any],
    cuenta_destino: Optional[Dict[str, Any]] = None,
    empresa_id: int = 1,
    origen_id: Optional[int] = None,
    nro_cheque: str = "",
) -> Optional[int]:
    """Genera asiento contable según tipo de operación bancaria.
    Proveedores Centro de Costos S/P: no generan asiento en contabilidad oficial.
    Transferencias entre bancos propias sí se asientan (no dependen del proveedor).
    """
    tipo = (tipo_operacion or "").strip().lower()
    monto = round(float(monto or 0), 2)
    imp_chq = round(float(imp_chq or 0), 2)
    cta_banco = asegurar_cuenta_banco(cursor, cuenta_origen)
    cta_prov = _id_cuenta(cursor, "2.1.01")
    cta_cobrar = _id_cuenta(cursor, "1.1.02")
    cta_imp = _id_cuenta(cursor, "4.2.04")

    if tipo in ("transferencia", "transferencias"):
        if not cuenta_destino:
            raise ValueError("Transferencia requiere cuenta destino.")
        cta_dest = asegurar_cuenta_banco(cursor, cuenta_destino)
        concepto = (
            f"Transferencia {cuenta_origen.get('banco')} {cuenta_origen.get('nro_cta_cte')} "
            f"→ {cuenta_destino.get('banco')} {cuenta_destino.get('nro_cta_cte')}"
        )
        lineas = [
            {"cuenta_id": cta_dest, "debe": monto, "haber": 0, "concepto_linea": concepto},
            {"cuenta_id": cta_banco, "debe": 0, "haber": monto, "concepto_linea": concepto},
        ]
        return crear_asiento(
            cursor,
            fecha=fecha,
            concepto=concepto,
            lineas=lineas,
            empresa_id=empresa_id,
            origen_modulo="bancos_transferencia",
            origen_id=origen_id,
            referencia=f"TRF-{cuenta_origen.get('nro_cta_cte')}-{cuenta_destino.get('nro_cta_cte')}",
            centro_costo="1",
        )

    # Débitos / depósitos asociados a proveedor S/P → solo gestión, sin partida oficial
    # FCI: es disponibilidad de la empresa (como plazo fijo), no depende del proveedor
    cta_fci = _id_cuenta(cursor, "1.1.03")

    if tipo in ("fci", "suscripcion fci", "suscripción fci"):
        # Sale de banco → Inversiones FCI (sigue siendo disponibilidad)
        concepto = f"Suscripción FCI — {proveedor or 'FCI'}"
        lineas = [
            {"cuenta_id": cta_fci, "debe": monto, "haber": 0, "concepto_linea": concepto},
            {"cuenta_id": cta_banco, "debe": 0, "haber": monto, "concepto_linea": concepto},
        ]
        return crear_asiento(
            cursor,
            fecha=fecha,
            concepto=concepto,
            lineas=lineas,
            empresa_id=empresa_id,
            origen_modulo="bancos_fci",
            origen_id=origen_id,
            referencia=cuenta_origen.get("nro_cta_cte") or "",
            centro_costo="1",
        )

    if tipo in ("rescate fci", "rescate_fci", "rescatefci"):
        # Vuelve al banco desde Inversiones FCI (sin impuesto a los créditos)
        concepto = f"Rescate FCI — {proveedor or 'FCI'}"
        lineas = [
            {"cuenta_id": cta_banco, "debe": monto, "haber": 0, "concepto_linea": concepto},
            {"cuenta_id": cta_fci, "debe": 0, "haber": monto, "concepto_linea": concepto},
        ]
        return crear_asiento(
            cursor,
            fecha=fecha,
            concepto=concepto,
            lineas=lineas,
            empresa_id=empresa_id,
            origen_modulo="bancos_fci",
            origen_id=origen_id,
            referencia=cuenta_origen.get("nro_cta_cte") or "",
            centro_costo="1",
        )

    if proveedor_omite_asiento_oficial(cursor, proveedor):
        return None

    if tipo in ("deposito", "depósito"):
        concepto = f"Depósito bancario — {proveedor}"
        lineas = [
            {"cuenta_id": cta_banco, "debe": monto, "haber": 0, "concepto_linea": concepto},
            {"cuenta_id": cta_cobrar, "debe": 0, "haber": monto, "concepto_linea": concepto},
        ]
        return crear_asiento(
            cursor,
            fecha=fecha,
            concepto=concepto,
            lineas=lineas,
            empresa_id=empresa_id,
            origen_modulo="bancos_movimiento",
            origen_id=origen_id,
            referencia=cuenta_origen.get("nro_cta_cte") or "",
            centro_costo="1",
        )

    # Débito / Cheque: sale dinero del banco
    etiqueta = "Cheque" if tipo == "cheque" else "Débito bancario"
    chq = f" Nº {nro_cheque}" if nro_cheque else ""
    concepto = f"{etiqueta}{chq} — {proveedor}"
    lineas = [
        {"cuenta_id": cta_prov, "debe": monto, "haber": 0, "concepto_linea": concepto},
        {"cuenta_id": cta_banco, "debe": 0, "haber": monto, "concepto_linea": concepto},
    ]
    if imp_chq > 0:
        lineas.append(
            {"cuenta_id": cta_imp, "debe": imp_chq, "haber": 0, "concepto_linea": "Impuesto débitos/créditos 6‰"}
        )
        lineas.append(
            {"cuenta_id": cta_banco, "debe": 0, "haber": imp_chq, "concepto_linea": "Impuesto débitos/créditos 6‰"}
        )

    return crear_asiento(
        cursor,
        fecha=fecha,
        concepto=concepto,
        lineas=lineas,
        empresa_id=empresa_id,
        origen_modulo="bancos_movimiento",
        origen_id=origen_id,
        referencia=nro_cheque or (cuenta_origen.get("nro_cta_cte") or ""),
        centro_costo="1",
    )


def listar_asientos_plano(
    cursor,
    empresa_id: Optional[int] = None,
    limit: int = 500,
    ejercicio: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filas planas para el libro diario (una fila por línea de detalle)."""
    params: List[Any] = []
    where = "WHERE COALESCE(a.anulado, 0) = 0 AND COALESCE(a.centro_costo, '1') = '1'"
    if empresa_id is not None:
        where += " AND a.empresa_id = ?"
        params.append(empresa_id)
    if ejercicio:
        where += " AND a.ejercicio = ?"
        params.append(ejercicio)
    params.append(limit)
    cursor.execute(
        f"""
        SELECT a.id AS asiento_id, a.fecha, a.concepto, a.referencia, a.origen_modulo, a.origen_id,
               a.ejercicio, p.codigo_cuenta, p.nombre_cuenta,
               d.debe, d.haber, d.concepto_linea
        FROM asientos_contables a
        JOIN detalles_asiento d ON d.asiento_id = a.id
        JOIN plan_de_cuentas p ON p.id = d.cuenta_id
        {where}
        ORDER BY a.fecha DESC, a.id DESC, d.id ASC
        LIMIT ?;
        """,
        params,
    )
    return [dict(r) for r in cursor.fetchall()]


def _rubro_rt54(codigo: str, tipo: str = "", nombre: str = "") -> str:
    """Agrupa el plan real de Campo+ según la presentación de la RT 54.

    En este plan, 1.1 y 1.2 son activo corriente (caja, créditos, existencias,
    IVA crédito y percepciones). Bienes de uso y cuentas 1.3 / 3.x se separan.
    """
    c = (codigo or "").strip()
    t = (tipo or "").strip().lower()
    n = (nombre or "").strip().lower()
    if c.startswith("3.") or t in ("patrimonio", "pn"):
        return "patrimonio"
    if c.startswith("4.") or c.startswith("5.") or t in ("resultado", "ingreso", "gasto"):
        if c.startswith("4.2") or c.startswith("5.") or t == "gasto" or n.startswith("gasto"):
            return "gastos"
        return "ingresos"
    if c.startswith("2.") or t == "pasivo":
        if c.startswith("2.2") or "no corriente" in n or "largo plazo" in n:
            return "pasivo_no_corriente"
        return "pasivo_corriente"
    if (
        c.startswith("1.3")
        or "bienes de uso" in n
        or "no corriente" in n
    ):
        return "activo_no_corriente"
    return "activo_corriente"


def corte_parcial_rt54(cursor, empresa_id: int, fecha: str) -> Dict[str, Any]:
    """Saldos acumulados a una fecha, presentados como corte parcial (RT 54).

    No cierra resultados contra patrimonio: el resultado del período queda abierto,
    que es lo que corresponde a un corte que no es el cierre anual.
    """
    fecha = _fecha_iso_contable(fecha)
    cursor.execute(
        """
        SELECT p.codigo_cuenta, p.nombre_cuenta, p.tipo_cuenta,
               COALESCE(SUM(d.debe), 0) AS debe,
               COALESCE(SUM(d.haber), 0) AS haber
        FROM plan_de_cuentas p
        JOIN detalles_asiento d ON d.cuenta_id = p.id
        JOIN asientos_contables a ON a.id = d.asiento_id
        WHERE COALESCE(a.anulado, 0) = 0
          AND COALESCE(a.centro_costo, '1') = '1'
          AND a.empresa_id = ?
          AND (
            CASE
              WHEN length(trim(COALESCE(a.fecha, ''))) >= 10
                   AND substr(trim(a.fecha), 3, 1) = '/'
                THEN substr(trim(a.fecha), 7, 4) || '-' || substr(trim(a.fecha), 4, 2) || '-' || substr(trim(a.fecha), 1, 2)
              ELSE substr(trim(COALESCE(a.fecha, '')), 1, 10)
            END
          ) <= ?
          AND COALESCE(p.activa, 1) = 1
        GROUP BY p.id
        HAVING ROUND(COALESCE(SUM(d.debe), 0) - COALESCE(SUM(d.haber), 0), 2) != 0
        ORDER BY p.codigo_cuenta;
        """,
        (empresa_id, fecha),
    )
    rubros = {
        "activo_corriente": [],
        "activo_no_corriente": [],
        "pasivo_corriente": [],
        "pasivo_no_corriente": [],
        "patrimonio": [],
        "ingresos": [],
        "gastos": [],
        "otros": [],
    }
    acreedoras = {"pasivo_corriente", "pasivo_no_corriente", "patrimonio", "ingresos"}
    for r in cursor.fetchall():
        debe = float(r["debe"] or 0)
        haber = float(r["haber"] or 0)
        rubro = _rubro_rt54(r["codigo_cuenta"], r["tipo_cuenta"], r["nombre_cuenta"])
        saldo = (haber - debe) if rubro in acreedoras else (debe - haber)
        if round(saldo, 2) == 0:
            continue
        rubros[rubro].append({
            "codigo": r["codigo_cuenta"],
            "nombre": r["nombre_cuenta"],
            "saldo": round(saldo, 2),
        })

    def total(clave: str) -> float:
        return round(sum(x["saldo"] for x in rubros[clave]), 2)

    activo = round(total("activo_corriente") + total("activo_no_corriente"), 2)
    pasivo = round(total("pasivo_corriente") + total("pasivo_no_corriente"), 2)
    patrimonio = total("patrimonio")
    ingresos = total("ingresos")
    gastos = total("gastos")
    resultado = round(ingresos - gastos, 2)
    # Activo = Pasivo + PN + resultado del período (si el diario balancea).
    control = round(activo - pasivo - patrimonio - resultado - total("otros"), 2)
    return {
        "fecha": fecha,
        "norma": "RT 54 FACPCE",
        "rubros": rubros,
        "totales": {
            "activo": activo,
            "activo_corriente": total("activo_corriente"),
            "activo_no_corriente": total("activo_no_corriente"),
            "pasivo": pasivo,
            "pasivo_corriente": total("pasivo_corriente"),
            "pasivo_no_corriente": total("pasivo_no_corriente"),
            "patrimonio": patrimonio,
            "ingresos": ingresos,
            "gastos": gastos,
            "resultado_periodo": resultado,
            "control": control,
        },
    }


def _inferir_tipo_movimiento(mov: Dict[str, Any]) -> str:
    tipo = (mov.get("tipo_operacion") or "").strip()
    if tipo:
        return tipo
    haber = float(mov.get("haber") or 0)
    debe = float(mov.get("debe") or 0)
    nro = str(mov.get("nro_cheque") or "").strip()
    if haber > 0 and nro and nro not in ("0", "0.0", "-"):
        return "Cheque"
    if haber > 0:
        return "Debito"
    if debe > 0:
        return "Deposito"
    return "Debito"


def recalcular_asientos_movimientos_bancarios(
    conn,
    *,
    empresa_id: int = 1,
    solo_sin_asiento: bool = True,
    limit: Optional[int] = None,
    ejercicio: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Genera asientos contables para movimientos bancarios históricos.
    - solo_sin_asiento=True: no toca los que ya tienen asiento
    - ejercicio: si se indica, solo movimientos cuya fecha cae en ese ejercicio (cierre 30/06)
    """
    cursor = conn.cursor()
    init_contabilidad(cursor)

    cursor.execute("SELECT id, nro_cta_cte, banco, titular, cuenta_contable_id FROM ctas_ctes_bancarias;")
    cuentas = {}
    por_nro = {}
    for r in cursor.fetchall():
        d = dict(r)
        cuentas[int(d["id"])] = d
        if d.get("nro_cta_cte"):
            por_nro[str(d["nro_cta_cte"]).strip()] = d

    query = "SELECT * FROM movimientos_cta_cte_bancos WHERE 1=1"
    params: List[Any] = []
    if solo_sin_asiento:
        query += " AND (asiento_id IS NULL OR asiento_id = 0)"
    query += " ORDER BY fecha_cobro ASC, id ASC"
    if limit:
        query += " LIMIT ?"
        params.append(int(limit))

    cursor.execute(query, params)
    movs = [dict(r) for r in cursor.fetchall()]

    ok = 0
    skip = 0
    errores = []
    por_ejercicio: Dict[str, int] = {}

    for mov in movs:
        fecha = (mov.get("fecha_cobro") or mov.get("fecha_debito") or "")[:10]
        if not fecha:
            skip += 1
            continue
        ej = ejercicio_desde_fecha(fecha)
        if ejercicio and ej != ejercicio:
            skip += 1
            continue

        cuenta = None
        if mov.get("cuenta_id") and int(mov["cuenta_id"]) in cuentas:
            cuenta = cuentas[int(mov["cuenta_id"])]
        elif mov.get("cta_cte_nro") and str(mov["cta_cte_nro"]).strip() in por_nro:
            cuenta = por_nro[str(mov["cta_cte_nro"]).strip()]
        if not cuenta:
            skip += 1
            errores.append({"mov_id": mov.get("id"), "error": "sin cuenta bancaria"})
            continue

        haber = float(mov.get("haber") or 0)
        debe = float(mov.get("debe") or 0)
        monto = haber if haber > 0 else debe
        if monto <= 0:
            skip += 1
            continue

        tipo = _inferir_tipo_movimiento(mov)
        try:
            asiento_id = asiento_para_movimiento_banco(
                cursor,
                fecha=fecha,
                tipo_operacion=tipo,
                proveedor=mov.get("proveedor") or "Movimiento bancario",
                monto=monto,
                imp_chq=float(mov.get("imp_chq") or 0),
                cuenta_origen=cuenta,
                empresa_id=empresa_id,
                origen_id=int(mov["id"]),
                nro_cheque=str(mov.get("nro_cheque") or ""),
            )
            if asiento_id is None:
                # Proveedor S/P: gestión sin partida oficial
                skip += 1
                continue
            cursor.execute(
                "UPDATE movimientos_cta_cte_bancos SET asiento_id = ?, tipo_operacion = COALESCE(NULLIF(tipo_operacion,''), ?) WHERE id = ?;",
                (asiento_id, tipo, int(mov["id"])),
            )
            ok += 1
            por_ejercicio[ej] = por_ejercicio.get(ej, 0) + 1
            if ok % 500 == 0:
                conn.commit()
        except Exception as e:
            errores.append({"mov_id": mov.get("id"), "error": str(e)})
            if len(errores) > 50:
                # no inundar
                pass

    conn.commit()
    return {
        "status": "success",
        "procesados": ok,
        "omitidos": skip,
        "errores": len(errores),
        "detalle_errores": errores[:30],
        "por_ejercicio": por_ejercicio,
        "cierre_eecc": f"{CIERRE_EECC_DIA:02d}/{CIERRE_EECC_MES:02d}",
    }
