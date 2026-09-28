"""
Arrendamientos ganaderos: contratos pactados en kg de carne/ha por año,
cuotas periódicas (trimestral por defecto) que se liquidan en pesos con el
Índice Sugerido de Alquiler Ganadero (MAG) a la fecha de pago.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import Body, HTTPException, Request

MAG_INICIAL = 4249.438
MAG_FECHA_INICIAL = "2026-09-28"

MESES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]
FORMAS_PAGO = {"Mensual": 12, "Bimestral": 6, "Trimestral": 4, "Cuatrimestral": 3, "Semestral": 2, "Anual": 1}
TIPO_CC = "Alquiler ganadero"


def init_arrend_ganadero_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gan_contratos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            tipo TEXT DEFAULT 'Ganadero',
            fecha_contrato TEXT,
            fecha_fin TEXT,
            arrendador_cuit TEXT,
            arrendador_nombre TEXT,
            campo_id INTEGER,
            campo_nombre TEXT,
            lotes TEXT,
            lat REAL,
            lng REAL,
            renspa TEXT,
            forma_pago TEXT DEFAULT 'Trimestral',
            dia_pago INTEGER DEFAULT 5,
            hectareas REAL DEFAULT 0,
            indice_codigo TEXT DEFAULT 'MAG',
            calculo_precio TEXT DEFAULT 'Promedio 30 días anteriores al día de pago',
            observaciones TEXT,
            estado TEXT DEFAULT 'Vigente',
            fecha_alta TEXT
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gan_contrato_anios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contrato_id INTEGER NOT NULL,
            nro_anio INTEGER NOT NULL,
            kg_ha REAL DEFAULT 0,
            UNIQUE(contrato_id, nro_anio)
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gan_contrato_cuotas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contrato_id INTEGER NOT NULL,
            empresa_id INTEGER DEFAULT 1,
            nro_anio INTEGER,
            nro_cuota INTEGER,
            periodo TEXT,
            fecha_pago TEXT,
            kg REAL DEFAULT 0,
            indice REAL DEFAULT 0,
            importe REAL DEFAULT 0,
            ret_gcias REAL DEFAULT 0,
            ret_iibb REAL DEFAULT 0,
            otros REAL DEFAULT 0,
            total_pagar REAL DEFAULT 0,
            estado TEXT DEFAULT 'Pendiente',
            cc_id INTEGER,
            fecha_liquidacion TEXT,
            observaciones TEXT
        );
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_gan_cuotas_contrato ON gan_contrato_cuotas(contrato_id, nro_anio, nro_cuota);"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS indices_referencia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            codigo TEXT NOT NULL,
            fecha TEXT NOT NULL,
            valor REAL NOT NULL,
            usuario TEXT,
            UNIQUE(empresa_id, codigo, fecha)
        );
        """
    )
    cursor.execute("SELECT COUNT(*) FROM indices_referencia WHERE codigo = 'MAG';")
    if int(cursor.fetchone()[0] or 0) == 0:
        cursor.execute(
            "INSERT OR IGNORE INTO indices_referencia (empresa_id, codigo, fecha, valor, usuario) VALUES (1, 'MAG', ?, ?, 'inicial');",
            (MAG_FECHA_INICIAL, MAG_INICIAL),
        )


# ---------------------------------------------------------------- fechas

def _iso(valor: Any) -> str:
    s = str(valor or "").strip()[:10]
    if not s:
        return ""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return ""


def _sumar_meses(d: date, meses: int, dia: Optional[int] = None) -> date:
    total = d.month - 1 + meses
    anio, mes = d.year + total // 12, total % 12 + 1
    dia = dia or d.day
    for dd in (dia, 30, 29, 28):
        try:
            return date(anio, mes, dd)
        except ValueError:
            continue
    return date(anio, mes, 28)


def cantidad_anios(fecha_contrato: str, fecha_fin: str) -> int:
    ini, fin = _iso(fecha_contrato), _iso(fecha_fin)
    if not ini or not fin:
        return 0
    dias = (date.fromisoformat(fin) - date.fromisoformat(ini)).days
    return max(1, int(round(dias / 365.25)))


# ---------------------------------------------------------------- índices

def indice_vigente(cursor, empresa_id: int, codigo: str = "MAG", fecha: str = "") -> Dict[str, Any]:
    fecha = _iso(fecha) or date.today().isoformat()
    cursor.execute(
        """
        SELECT fecha, valor FROM indices_referencia
        WHERE codigo = ? AND COALESCE(empresa_id, 1) IN (?, 1) AND fecha <= ?
        ORDER BY fecha DESC, (COALESCE(empresa_id,1) = ?) DESC LIMIT 1;
        """,
        (codigo, empresa_id, fecha, empresa_id),
    )
    r = cursor.fetchone()
    if not r:
        cursor.execute(
            "SELECT fecha, valor FROM indices_referencia WHERE codigo = ? ORDER BY fecha ASC LIMIT 1;",
            (codigo,),
        )
        r = cursor.fetchone()
    if not r:
        return {"codigo": codigo, "fecha": "", "valor": 0.0}
    return {"codigo": codigo, "fecha": r[0], "valor": float(r[1] or 0)}


def indice_promedio(cursor, empresa_id: int, codigo: str, fecha_pago: str, dias: int = 30) -> Dict[str, Any]:
    fin = _iso(fecha_pago) or date.today().isoformat()
    ini = (date.fromisoformat(fin) - timedelta(days=dias)).isoformat()
    cursor.execute(
        """
        SELECT AVG(valor), COUNT(*) FROM indices_referencia
        WHERE codigo = ? AND COALESCE(empresa_id, 1) IN (?, 1) AND fecha >= ? AND fecha < ?;
        """,
        (codigo, empresa_id, ini, fin),
    )
    avg, n = cursor.fetchone()
    if n:
        return {"valor": round(float(avg), 4), "cantidad": int(n), "desde": ini, "hasta": fin, "criterio": f"Promedio de {n} valor(es) cargados entre {ini} y {fin}"}
    v = indice_vigente(cursor, empresa_id, codigo, fin)
    return {"valor": v["valor"], "cantidad": 0, "desde": ini, "hasta": fin, "criterio": f"Sin valores en los 30 días previos: último cargado ({v['fecha']})"}


def guardar_indice(cursor, empresa_id: int, codigo: str, fecha: str, valor: float, usuario: str = "") -> Dict[str, Any]:
    fecha = _iso(fecha) or date.today().isoformat()
    if valor <= 0:
        raise HTTPException(status_code=400, detail="El valor del índice debe ser mayor a cero.")
    cursor.execute(
        """
        INSERT INTO indices_referencia (empresa_id, codigo, fecha, valor, usuario) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(empresa_id, codigo, fecha) DO UPDATE SET valor = excluded.valor, usuario = excluded.usuario;
        """,
        (empresa_id, codigo, fecha, float(valor), usuario),
    )
    return {"codigo": codigo, "fecha": fecha, "valor": float(valor)}


def listar_indices(cursor, empresa_id: int, codigo: str, limite: int = 60) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, fecha, valor, usuario FROM indices_referencia
        WHERE codigo = ? AND COALESCE(empresa_id, 1) IN (?, 1)
        ORDER BY fecha DESC LIMIT ?;
        """,
        (codigo, empresa_id, limite),
    )
    return [dict(zip(("id", "fecha", "valor", "usuario"), r)) for r in cursor.fetchall()]


# ---------------------------------------------------------------- cronograma

def generar_cronograma(contrato: Dict[str, Any], kg_por_anio: Dict[int, float]) -> List[Dict[str, Any]]:
    ini = _iso(contrato.get("fecha_contrato"))
    if not ini:
        return []
    anios = cantidad_anios(ini, contrato.get("fecha_fin") or "")
    n = FORMAS_PAGO.get(contrato.get("forma_pago") or "Trimestral", 4)
    salto = 12 // n
    dia_pago = int(contrato.get("dia_pago") or 5)
    has = float(contrato.get("hectareas") or 0)
    base = date.fromisoformat(ini).replace(day=1)
    out = []
    for a in range(1, anios + 1):
        kg_ha = float(kg_por_anio.get(a) or 0)
        kg_cuota = round(has * kg_ha / n, 2)
        for q in range(1, n + 1):
            inicio = _sumar_meses(base, (a - 1) * 12 + (q - 1) * salto, 1)
            meses = [MESES[(inicio.month - 1 + i) % 12] for i in range(salto)]
            out.append({
                "nro_anio": a,
                "nro_cuota": q,
                "periodo": "/".join(meses),
                "fecha_pago": _sumar_meses(inicio, 0, dia_pago).isoformat(),
                "kg": kg_cuota,
            })
    return out


def _num_o_none(v: Any) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _cuit(v: Any) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _nombre_entidad(cursor, cuit: str) -> str:
    if not cuit:
        return ""
    cursor.execute(
        "SELECT COALESCE(NULLIF(razon_social,''), nombre_fantasia, '') FROM entidades WHERE REPLACE(cuit,'-','') = ? LIMIT 1;",
        (cuit,),
    )
    r = cursor.fetchone()
    return (r[0] or "") if r else ""


def guardar_contrato(cursor, empresa_id: int, data: Dict[str, Any], contrato_id: Optional[int] = None) -> int:
    fecha_contrato = _iso(data.get("fecha_contrato"))
    fecha_fin = _iso(data.get("fecha_fin"))
    if not fecha_contrato or not fecha_fin or fecha_fin <= fecha_contrato:
        raise HTTPException(status_code=400, detail="Indicá fecha de contrato y de finalización válidas.")
    has = float(data.get("hectareas") or 0)
    if has <= 0:
        raise HTTPException(status_code=400, detail="Indicá las hectáreas del contrato.")
    forma = data.get("forma_pago") or "Trimestral"
    if forma not in FORMAS_PAGO:
        raise HTTPException(status_code=400, detail=f"Forma de pago no válida: {forma}")
    cuit = _cuit(data.get("arrendador_cuit"))
    nombre = (data.get("arrendador_nombre") or "").strip() or _nombre_entidad(cursor, cuit)
    if len(cuit) == 11:
        try:
            from agro_campania import sincronizar_arrendador_proveedor
            sincronizar_arrendador_proveedor(
                cursor, {"arrendador_cuit": cuit, "arrendador_razon": nombre}, empresa_id
            )
        except Exception as exc:
            print(f"AVISO alta arrendador ganadero {cuit}: {exc}")
        nombre = nombre or _nombre_entidad(cursor, cuit)

    campos = {
        "tipo": data.get("tipo") or "Ganadero",
        "fecha_contrato": fecha_contrato,
        "fecha_fin": fecha_fin,
        "arrendador_cuit": cuit,
        "arrendador_nombre": nombre,
        "campo_id": int(data["campo_id"]) if str(data.get("campo_id") or "").isdigit() else None,
        "campo_nombre": (data.get("campo_nombre") or "").strip(),
        "lotes": (data.get("lotes") or "").strip(),
        "lat": _num_o_none(data.get("lat")),
        "lng": _num_o_none(data.get("lng")),
        "renspa": (data.get("renspa") or "").strip(),
        "forma_pago": forma,
        "dia_pago": max(1, min(28, int(data.get("dia_pago") or 5))),
        "hectareas": has,
        "indice_codigo": (data.get("indice_codigo") or "MAG").strip().upper(),
        "calculo_precio": (data.get("calculo_precio") or "Promedio 30 días anteriores al día de pago").strip(),
        "observaciones": (data.get("observaciones") or "").strip(),
        "estado": data.get("estado") or "Vigente",
    }
    if not campos["campo_nombre"]:
        raise HTTPException(status_code=400, detail="Indicá el campo.")

    if contrato_id:
        cursor.execute("SELECT id FROM gan_contratos WHERE id = ? AND COALESCE(empresa_id,1) = ?;", (contrato_id, empresa_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Contrato no encontrado.")
        sets = ", ".join(f"{k} = ?" for k in campos)
        cursor.execute(f"UPDATE gan_contratos SET {sets} WHERE id = ?;", (*campos.values(), contrato_id))
    else:
        cols = ", ".join(campos) + ", empresa_id, fecha_alta"
        marks = ", ".join("?" for _ in range(len(campos) + 2))
        cursor.execute(
            f"INSERT INTO gan_contratos ({cols}) VALUES ({marks});",
            (*campos.values(), empresa_id, datetime.now().isoformat(timespec="seconds")),
        )
        contrato_id = int(cursor.lastrowid)

    anios = cantidad_anios(fecha_contrato, fecha_fin)
    kg_por_anio: Dict[int, float] = {}
    for item in data.get("anios") or []:
        try:
            a = int(item.get("nro_anio"))
            kg_por_anio[a] = float(item.get("kg_ha") or 0)
        except (TypeError, ValueError):
            continue
    cursor.execute("DELETE FROM gan_contrato_anios WHERE contrato_id = ?;", (contrato_id,))
    for a in range(1, anios + 1):
        cursor.execute(
            "INSERT INTO gan_contrato_anios (contrato_id, nro_anio, kg_ha) VALUES (?, ?, ?);",
            (contrato_id, a, kg_por_anio.get(a, 0.0)),
        )

    cursor.execute(
        "SELECT nro_anio, nro_cuota FROM gan_contrato_cuotas WHERE contrato_id = ? AND estado <> 'Pendiente';",
        (contrato_id,),
    )
    cerradas = {(r[0], r[1]) for r in cursor.fetchall()}
    cursor.execute("DELETE FROM gan_contrato_cuotas WHERE contrato_id = ? AND estado = 'Pendiente';", (contrato_id,))
    hist = _iso(data.get("pagadas_hasta"))
    for c in generar_cronograma(campos, kg_por_anio):
        if (c["nro_anio"], c["nro_cuota"]) in cerradas:
            continue
        estado = "Pagada" if hist and c["fecha_pago"] < hist else "Pendiente"
        cursor.execute(
            """
            INSERT INTO gan_contrato_cuotas
                (contrato_id, empresa_id, nro_anio, nro_cuota, periodo, fecha_pago, kg, estado, observaciones)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (contrato_id, empresa_id, c["nro_anio"], c["nro_cuota"], c["periodo"], c["fecha_pago"], c["kg"],
             estado, "Histórica (pagada fuera de Campo+)" if estado == "Pagada" else ""),
        )
    return contrato_id


def listar_contratos(cursor, empresa_id: int) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT c.*,
               (SELECT COUNT(*) FROM gan_contrato_cuotas q WHERE q.contrato_id = c.id AND q.estado = 'Pendiente') AS cuotas_pendientes,
               (SELECT MIN(fecha_pago) FROM gan_contrato_cuotas q WHERE q.contrato_id = c.id AND q.estado = 'Pendiente') AS proximo_pago,
               (SELECT kg FROM gan_contrato_cuotas q WHERE q.contrato_id = c.id AND q.estado = 'Pendiente'
                 ORDER BY fecha_pago LIMIT 1) AS proximo_kg,
               (SELECT COALESCE(SUM(kg),0) FROM gan_contrato_cuotas q WHERE q.contrato_id = c.id AND q.estado = 'Pendiente') AS kg_pendientes
        FROM gan_contratos c
        WHERE COALESCE(c.empresa_id, 1) = ?
        ORDER BY (c.estado = 'Vigente') DESC, c.campo_nombre, c.id;
        """,
        (empresa_id,),
    )
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) for r in cursor.fetchall()]


def detalle_contrato(cursor, empresa_id: int, contrato_id: int) -> Dict[str, Any]:
    cursor.execute("SELECT * FROM gan_contratos WHERE id = ? AND COALESCE(empresa_id,1) = ?;", (contrato_id, empresa_id))
    r = cursor.fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Contrato no encontrado.")
    cols = [d[0] for d in cursor.description]
    contrato = dict(zip(cols, r))
    cursor.execute("SELECT nro_anio, kg_ha FROM gan_contrato_anios WHERE contrato_id = ? ORDER BY nro_anio;", (contrato_id,))
    has = float(contrato.get("hectareas") or 0)
    anios = [{"nro_anio": a, "kg_ha": float(k or 0), "kg_total": round(has * float(k or 0), 2)} for a, k in cursor.fetchall()]
    cursor.execute(
        """
        SELECT q.*, cc.estado AS cc_estado
        FROM gan_contrato_cuotas q
        LEFT JOIN cuentas_corrientes cc ON cc.id = q.cc_id
        WHERE q.contrato_id = ?
        ORDER BY q.nro_anio, q.nro_cuota;
        """,
        (contrato_id,),
    )
    qcols = [d[0] for d in cursor.description]
    cuotas = [dict(zip(qcols, x)) for x in cursor.fetchall()]
    mag = indice_vigente(cursor, empresa_id, contrato.get("indice_codigo") or "MAG")
    for q in cuotas:
        if q["estado"] == "Pendiente":
            q["importe_estimado"] = round(float(q["kg"] or 0) * mag["valor"], 2)
    return {"contrato": contrato, "anios": anios, "cuotas": cuotas, "indice": mag}


# ---------------------------------------------------------------- liquidación

def _cuota(cursor, empresa_id: int, cuota_id: int) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT q.*, c.arrendador_cuit, c.arrendador_nombre, c.campo_nombre, c.indice_codigo
        FROM gan_contrato_cuotas q JOIN gan_contratos c ON c.id = q.contrato_id
        WHERE q.id = ? AND COALESCE(c.empresa_id,1) = ?;
        """,
        (cuota_id, empresa_id),
    )
    r = cursor.fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Cuota no encontrada.")
    return dict(zip([d[0] for d in cursor.description], r))


def _cc_editable(cursor, cc_id: Optional[int]) -> bool:
    if not cc_id:
        return True
    cursor.execute("SELECT estado FROM cuentas_corrientes WHERE id = ?;", (cc_id,))
    r = cursor.fetchone()
    return (not r) or (r[0] or "Pendiente") in ("Pendiente", "pendiente", "")


def liquidar_cuota(cursor, empresa_id: int, cuota_id: int, data: Dict[str, Any], usuario: str = "") -> Dict[str, Any]:
    q = _cuota(cursor, empresa_id, cuota_id)
    if not _cc_editable(cursor, q.get("cc_id")):
        raise HTTPException(status_code=400, detail="La cuota ya tiene pagos imputados en la cuenta corriente; no se puede reliquidar.")
    indice = float(data.get("indice") or 0)
    if indice <= 0:
        raise HTTPException(status_code=400, detail="Indicá el valor del índice.")
    fecha_pago = _iso(data.get("fecha_pago")) or q["fecha_pago"]
    kg = float(data.get("kg") or q["kg"] or 0)
    importe = round(kg * indice, 2)
    ret_g = round(float(data.get("ret_gcias") or 0), 2)
    ret_i = round(float(data.get("ret_iibb") or 0), 2)
    otros = round(float(data.get("otros") or 0), 2)
    total = round(importe - ret_g - ret_i - otros, 2)
    obs = (data.get("observaciones") or "").strip()
    cargar_cc = bool(data.get("cargar_cc", True))
    cc_id = q.get("cc_id")
    cuit = _cuit(q.get("arrendador_cuit"))

    if cargar_cc:
        if len(cuit) != 11:
            raise HTTPException(status_code=400, detail="El contrato no tiene CUIT de arrendador para cargar la cuenta corriente.")
        deuda = round(importe - otros, 2)
        nro = f"AG-{q['contrato_id']}-{q['nro_anio']}.{q['nro_cuota']}"
        detalle = (
            f"{q['campo_nombre']} | Año {q['nro_anio']} {q['periodo']} | "
            f"{kg:,.2f} kg x {indice:,.4f}"
            + (f" | Otros descuentos {otros:,.2f}" if otros else "")
            + (f" | {obs}" if obs else "")
        )
        if cc_id:
            cursor.execute(
                """
                UPDATE cuentas_corrientes SET fecha = ?, vencimiento = ?, neto = ?, iva = 0, debe = ?, haber = 0,
                       total = ?, observaciones = ?, numero_comprobante = ?
                WHERE id = ?;
                """,
                (fecha_pago, fecha_pago, deuda, deuda, deuda, detalle, nro, cc_id),
            )
            if cursor.rowcount == 0:
                cc_id = None
        if not cc_id:
            cursor.execute(
                """
                INSERT INTO cuentas_corrientes
                    (entidad_id, tipo_comprobante, numero_comprobante, fecha, vencimiento, neto, iva,
                     debe, haber, total, estado, usuario_registro, empresa_id, observaciones)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, ?, 'Pendiente', ?, ?, ?);
                """,
                (cuit, TIPO_CC, nro, fecha_pago, fecha_pago, deuda, deuda, deuda, usuario, empresa_id, detalle),
            )
            cc_id = int(cursor.lastrowid)
    elif cc_id:
        cursor.execute("DELETE FROM cuentas_corrientes WHERE id = ?;", (cc_id,))
        cc_id = None

    estado = data.get("estado") or ("Liquidada" if cargar_cc else "Pagada")
    cursor.execute(
        """
        UPDATE gan_contrato_cuotas SET fecha_pago = ?, kg = ?, indice = ?, importe = ?, ret_gcias = ?, ret_iibb = ?,
               otros = ?, total_pagar = ?, estado = ?, cc_id = ?, fecha_liquidacion = ?, observaciones = ?
        WHERE id = ?;
        """,
        (fecha_pago, kg, indice, importe, ret_g, ret_i, otros, total, estado, cc_id,
         datetime.now().isoformat(timespec="seconds"), obs, cuota_id),
    )
    return {"ok": True, "cuota_id": cuota_id, "importe": importe, "total_pagar": total, "cc_id": cc_id, "estado": estado}


def anular_liquidacion(cursor, empresa_id: int, cuota_id: int) -> Dict[str, Any]:
    q = _cuota(cursor, empresa_id, cuota_id)
    if not _cc_editable(cursor, q.get("cc_id")):
        raise HTTPException(status_code=400, detail="La cuota ya tiene pagos imputados en la cuenta corriente; anulá primero la orden de pago.")
    if q.get("cc_id"):
        cursor.execute("DELETE FROM cuentas_corrientes WHERE id = ?;", (q["cc_id"],))
    cursor.execute(
        """
        UPDATE gan_contrato_cuotas SET indice = 0, importe = 0, ret_gcias = 0, ret_iibb = 0, otros = 0,
               total_pagar = 0, estado = 'Pendiente', cc_id = NULL, fecha_liquidacion = NULL
        WHERE id = ?;
        """,
        (cuota_id,),
    )
    return {"ok": True}


# ---------------------------------------------------------------- flujo proyectado

def lineas_ff_ganadero(cursor, empresa_id: int, desde: str, hasta: str, mag: float = 0.0) -> List[Dict[str, Any]]:
    """Cuotas pendientes (sin liquidar) valuadas kg x índice. Las liquidadas ya viven en cta cte."""
    try:
        cursor.execute(
            """
            SELECT q.fecha_pago, q.kg, q.nro_anio, q.nro_cuota, q.periodo, c.campo_nombre,
                   c.arrendador_nombre, c.arrendador_cuit, c.indice_codigo
            FROM gan_contrato_cuotas q JOIN gan_contratos c ON c.id = q.contrato_id
            WHERE COALESCE(c.empresa_id, 1) = ? AND q.estado = 'Pendiente'
              AND COALESCE(c.estado, 'Vigente') = 'Vigente'
              AND q.fecha_pago BETWEEN ? AND ?
            ORDER BY q.fecha_pago;
            """,
            (empresa_id, desde, hasta),
        )
        filas = cursor.fetchall()
    except Exception:
        return []
    cache: Dict[str, float] = {}
    out = []
    for fecha, kg, anio, cuota, periodo, campo, nombre, cuit, codigo in filas:
        codigo = codigo or "MAG"
        if codigo == "MAG" and mag > 0:
            idx = mag
        else:
            if codigo not in cache:
                cache[codigo] = indice_vigente(cursor, empresa_id, codigo)["valor"]
            idx = cache[codigo]
        monto = round(float(kg or 0) * idx, 2)
        if monto < 0.01:
            continue
        out.append({
            "fecha": fecha,
            "detalle": f"Alquiler ganadero {campo} | {nombre or cuit}".strip(),
            "forma_pago": "Transferencia",
            "cta_cte": nombre or cuit or "",
            "nro_cuota": f"{anio}.{cuota}",
            "tipo_cambio": 0,
            "plazo": periodo or "",
            "capital": monto,
            "intereses": 0,
            "impuestos": 0,
            "cargos": 0,
            "subtotal": monto,
            "monto_ars": monto,
            "monto_usd": 0,
            "varios": f"{float(kg or 0):,.2f} kg carne x {codigo} {idx:,.3f}",
            "origen": "Alquiler ganadero (a liquidar)",
            "fuente": "alquiler",
            "moneda": "ARS",
            "es_disponibilidad": False,
        })
    return out


# ---------------------------------------------------------------- rutas

def register_arrend_ganadero_routes(app, get_db, get_empresa_activa_id) -> None:
    try:
        conn = get_db()
        init_arrend_ganadero_schema(conn.cursor())
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"AVISO init arrendamientos ganaderos: {exc}")

    def _usuario(request: Request) -> str:
        try:
            from audit import usuario_desde_headers
            u = usuario_desde_headers(request)
            return u["nombre"] if u["nombre"] != "Sin firmar" else ""
        except Exception:
            return ""

    def _con(fn, commit: bool = False):
        conn = get_db()
        try:
            cur = conn.cursor()
            init_arrend_ganadero_schema(cur)
            res = fn(cur, get_empresa_activa_id())
            if commit:
                conn.commit()
            return res
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @app.get("/api/indices/{codigo}")
    def api_indice(codigo: str, fecha: str = ""):
        cod = codigo.upper()
        return _con(lambda cur, eid: {
            "vigente": indice_vigente(cur, eid, cod, fecha),
            "historial": listar_indices(cur, eid, cod),
        })

    @app.post("/api/indices/{codigo}")
    def api_indice_guardar(codigo: str, request: Request, data: dict = Body(...)):
        valor = float(data.get("valor") or 0)
        usuario = _usuario(request)
        return _con(lambda cur, eid: guardar_indice(cur, eid, codigo.upper(), data.get("fecha") or "", valor, usuario), commit=True)

    @app.delete("/api/indices/{codigo}/{indice_id}")
    def api_indice_borrar(codigo: str, indice_id: int):
        def fn(cur, eid):
            cur.execute("DELETE FROM indices_referencia WHERE id = ? AND codigo = ?;", (indice_id, codigo.upper()))
            return {"ok": cur.rowcount > 0}
        return _con(fn, commit=True)

    @app.get("/api/ganaderia/contratos")
    def api_contratos():
        return _con(lambda cur, eid: {
            "contratos": listar_contratos(cur, eid),
            "indice": indice_vigente(cur, eid, "MAG"),
            "formas_pago": list(FORMAS_PAGO),
        })

    @app.post("/api/ganaderia/contratos/preview")
    def api_contrato_preview(data: dict = Body(...)):
        kg = {}
        for it in data.get("anios") or []:
            try:
                kg[int(it.get("nro_anio"))] = float(it.get("kg_ha") or 0)
            except (TypeError, ValueError):
                pass
        return {
            "anios": cantidad_anios(data.get("fecha_contrato") or "", data.get("fecha_fin") or ""),
            "cuotas": generar_cronograma(data, kg),
        }

    @app.get("/api/ganaderia/contratos/{contrato_id}")
    def api_contrato(contrato_id: int):
        return _con(lambda cur, eid: detalle_contrato(cur, eid, contrato_id))

    @app.post("/api/ganaderia/contratos")
    def api_contrato_alta(data: dict = Body(...)):
        return _con(lambda cur, eid: {"ok": True, "id": guardar_contrato(cur, eid, data)}, commit=True)

    @app.put("/api/ganaderia/contratos/{contrato_id}")
    def api_contrato_editar(contrato_id: int, data: dict = Body(...)):
        return _con(lambda cur, eid: {"ok": True, "id": guardar_contrato(cur, eid, data, contrato_id)}, commit=True)

    @app.get("/api/ganaderia/contratos/cuotas/{cuota_id}/sugerido")
    def api_cuota_sugerido(cuota_id: int, fecha_pago: str = ""):
        def fn(cur, eid):
            q = _cuota(cur, eid, cuota_id)
            fecha = _iso(fecha_pago) or q["fecha_pago"]
            return {"cuota": q, "promedio": indice_promedio(cur, eid, q.get("indice_codigo") or "MAG", fecha)}
        return _con(fn)

    @app.post("/api/ganaderia/contratos/cuotas/{cuota_id}/liquidar")
    def api_cuota_liquidar(cuota_id: int, request: Request, data: dict = Body(...)):
        usuario = _usuario(request)
        return _con(lambda cur, eid: liquidar_cuota(cur, eid, cuota_id, data, usuario), commit=True)

    @app.post("/api/ganaderia/contratos/cuotas/{cuota_id}/anular")
    def api_cuota_anular(cuota_id: int):
        return _con(lambda cur, eid: anular_liquidacion(cur, eid, cuota_id), commit=True)
