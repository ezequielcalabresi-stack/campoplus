# -*- coding: utf-8 -*-
"""Sueldos fuera de convenio (legado Access «conformacion sueldos»).

Mecanismo mensual por empleado:
  nuevo sueldo base = sueldo base anterior x (1 + índice de actualización)
  total a cobrar    = nuevo sueldo base
                      - Depósito Bancario (neto del recibo oficial de convenio, con asiento)
                      - adelantos / descuentos
                      + otros créditos no bancarizados (sin asiento)
El «sueldo principal» del mes (fila principal=1) es la base del mes siguiente.
"""
from __future__ import annotations

import calendar
import json
import os
from datetime import date, datetime
from typing import Optional

MESES_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)

CONCEPTOS = [
    {"nombre": "Sueldo", "lado": "credito"},
    {"nombre": "Premio", "lado": "credito"},
    {"nombre": "Adelanto", "lado": "debito"},
    {"nombre": "Varios", "lado": "credito"},
    {"nombre": "Deposito Bancario", "lado": "debito"},
    {"nombre": "Descuentos", "lado": "debito"},
    {"nombre": "SAC", "lado": "credito"},
    {"nombre": "Aumento", "lado": "credito"},
    {"nombre": "Viaticos", "lado": "credito"},
    {"nombre": "Ahorro", "lado": "credito"},
    {"nombre": "Deposito Bancario SAC", "lado": "debito"},
]

CONVENIOS = ["UATRE", "Empleados de Comercio", "Camioneros", "Fuera de convenio"]

_SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos", "empleados_seed.json")
_FECHA_OK = "fecha GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'"


def _cols(cur, tabla: str) -> list[str]:
    cur.execute(f"PRAGMA table_info({tabla});")
    return [r[1] for r in cur.fetchall()]


def _add_col(cur, tabla: str, cols: list[str], nombre: str, ddl: str) -> bool:
    if nombre in cols:
        return False
    cur.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {ddl};")
    cols.append(nombre)
    return True


def init_sueldos_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS empleados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cuit TEXT,
            nombre TEXT,
            banco TEXT,
            cbu TEXT,
            haber_base REAL DEFAULT 0
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS conformacion_sueldos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            nombre TEXT,
            detalle TEXT,
            aumento REAL DEFAULT 0,
            debe REAL DEFAULT 0,
            haber REAL DEFAULT 0,
            saldo REAL DEFAULT 0
        );
        """
    )
    ce = _cols(cur, "empleados")
    _add_col(cur, "empleados", ce, "empresa_id", "INTEGER DEFAULT 1")
    _add_col(cur, "empleados", ce, "dni", "TEXT DEFAULT ''")
    _add_col(cur, "empleados", ce, "cuil", "TEXT DEFAULT ''")
    _add_col(cur, "empleados", ce, "activo", "INTEGER DEFAULT 1")
    _add_col(cur, "empleados", ce, "convenio", "TEXT DEFAULT ''")
    _add_col(cur, "empleados", ce, "centro_costo", "TEXT DEFAULT '2'")
    _add_col(cur, "empleados", ce, "excluir_indice", "INTEGER DEFAULT 0")
    _add_col(cur, "empleados", ce, "observaciones", "TEXT DEFAULT ''")
    _add_col(cur, "empleados", ce, "legajo", "TEXT DEFAULT ''")
    _add_col(cur, "empleados", ce, "categoria", "TEXT DEFAULT ''")
    _add_col(cur, "empleados", ce, "fecha_ingreso", "TEXT DEFAULT ''")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recibos_sueldo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL DEFAULT 1,
            periodo TEXT NOT NULL,
            tipo_liquidacion TEXT DEFAULT 'Mensual',
            es_sac INTEGER DEFAULT 0,
            legajo TEXT DEFAULT '',
            nombre TEXT DEFAULT '',
            cuil TEXT DEFAULT '',
            fecha_ingreso TEXT DEFAULT '',
            categoria TEXT DEFAULT '',
            convenio TEXT DEFAULT '',
            sueldo_basico REAL DEFAULT 0,
            remunerativo REAL DEFAULT 0,
            no_remunerativo REAL DEFAULT 0,
            descuentos REAL DEFAULT 0,
            neto REAL DEFAULT 0,
            contribuciones REAL DEFAULT 0,
            costo_total REAL DEFAULT 0,
            conceptos_json TEXT DEFAULT '[]',
            empleado_id INTEGER,
            movimiento_id INTEGER,
            archivo TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (empresa_id, periodo, cuil, tipo_liquidacion)
        );
        """
    )

    cc = _cols(cur, "conformacion_sueldos")
    _add_col(cur, "conformacion_sueldos", cc, "empresa_id", "INTEGER DEFAULT 1")
    _add_col(cur, "conformacion_sueldos", cc, "varios", "TEXT DEFAULT ''")
    _add_col(cur, "conformacion_sueldos", cc, "porcentaje", "REAL DEFAULT 0")
    nuevo_principal = _add_col(cur, "conformacion_sueldos", cc, "principal", "INTEGER DEFAULT 0")
    if nuevo_principal:
        cur.execute(
            """
            UPDATE conformacion_sueldos SET principal = 1
            WHERE COALESCE(debe, 0) > 0
              AND LOWER(COALESCE(detalle, '')) LIKE LOWER(TRIM(nombre)) || ' - %'
              AND LOWER(detalle) NOT LIKE '%sac%';
            """
        )
        cur.execute(
            """
            UPDATE conformacion_sueldos
            SET porcentaje = ROUND(aumento / (debe - aumento), 4)
            WHERE principal = 1 AND COALESCE(aumento, 0) > 0 AND debe > aumento;
            """
        )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_conf_sueldos_emp_nombre ON conformacion_sueldos(empresa_id, nombre, fecha);"
    )
    _seed_empleados(cur)


def _seed_empleados(cur) -> None:
    """Primera vez: padrón del Access + nombres presentes en la conformación migrada."""
    cur.execute("SELECT COUNT(*) FROM empleados;")
    if cur.fetchone()[0]:
        return
    cur.execute("SELECT COUNT(*) FROM conformacion_sueldos WHERE empresa_id = 1;")
    if not cur.fetchone()[0]:
        return
    seed = []
    if os.path.exists(_SEED_PATH):
        with open(_SEED_PATH, encoding="utf-8") as fh:
            seed = json.load(fh)
    vistos = set()
    for e in seed:
        nombre = (e.get("nombre") or "").strip()
        if not nombre or nombre.lower() in vistos:
            continue
        vistos.add(nombre.lower())
        cur.execute(
            """
            INSERT INTO empleados (empresa_id, nombre, dni, cuil, cuit, activo, centro_costo, haber_base)
            VALUES (1, ?, ?, ?, ?, ?, '2', 0);
            """,
            (nombre, e.get("dni") or "", e.get("cuil") or "", e.get("cuil") or "", int(e.get("activo") or 0)),
        )
    cur.execute(
        f"SELECT DISTINCT TRIM(nombre) FROM conformacion_sueldos WHERE empresa_id = 1 AND TRIM(COALESCE(nombre,'')) <> '';"
    )
    for (nombre,) in cur.fetchall():
        if nombre.lower() in vistos:
            continue
        vistos.add(nombre.lower())
        cur.execute(
            "INSERT INTO empleados (empresa_id, nombre, activo, centro_costo, haber_base) VALUES (1, ?, 0, '2', 0);",
            (nombre,),
        )


# ---------------------------------------------------------------- helpers

def mes_valido(mes: str) -> str:
    try:
        d = datetime.strptime((mes or "").strip()[:7], "%Y-%m")
    except ValueError as exc:
        raise ValueError("Mes inválido (formato AAAA-MM).") from exc
    return d.strftime("%Y-%m")


def fin_de_mes(mes: str) -> str:
    y, m = int(mes[:4]), int(mes[5:7])
    return date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def nombre_mes(mes: str) -> str:
    return MESES_ES[int(mes[5:7]) - 1]


def _r2(x) -> float:
    return round(float(x or 0), 2)


def _empleado(conn, emp_id: int, empleado_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM empleados WHERE id = ? AND COALESCE(empresa_id, 1) = ?;", (empleado_id, emp_id)
    ).fetchone()
    if not row:
        raise ValueError("Empleado inexistente.")
    return dict(row)


def _principal_antes(conn, emp_id: int, nombre: str, mes: str) -> Optional[dict]:
    row = conn.execute(
        f"""
        SELECT * FROM conformacion_sueldos
        WHERE COALESCE(empresa_id, 1) = ? AND TRIM(nombre) = ? AND principal = 1
          AND {_FECHA_OK} AND substr(fecha, 1, 7) < ?
        ORDER BY fecha DESC, id DESC LIMIT 1;
        """,
        (emp_id, nombre, mes),
    ).fetchone()
    return dict(row) if row else None


def _principal_mes(conn, emp_id: int, nombre: str, mes: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT * FROM conformacion_sueldos
        WHERE COALESCE(empresa_id, 1) = ? AND TRIM(nombre) = ? AND principal = 1
          AND substr(fecha, 1, 7) = ?
        ORDER BY id DESC LIMIT 1;
        """,
        (emp_id, nombre, mes),
    ).fetchone()
    return dict(row) if row else None


def _ultimo_principal(conn, emp_id: int, nombre: str) -> Optional[dict]:
    return _principal_antes(conn, emp_id, nombre, "9999-12")


# ---------------------------------------------------------------- empleados

def listar_empleados(conn, emp_id: int, incluir_inactivos: bool = False) -> list[dict]:
    sql = "SELECT * FROM empleados WHERE COALESCE(empresa_id, 1) = ?"
    if not incluir_inactivos:
        sql += " AND COALESCE(activo, 1) = 1"
    sql += " ORDER BY LOWER(nombre);"
    out = []
    for row in conn.execute(sql, (emp_id,)).fetchall():
        e = dict(row)
        ult = _ultimo_principal(conn, emp_id, (e["nombre"] or "").strip())
        e["ult_sueldo"] = _r2(ult["debe"]) if ult else _r2(e.get("haber_base"))
        e["ult_fecha"] = ult["fecha"] if ult else ""
        out.append(e)
    return out


def guardar_empleado(conn, emp_id: int, data: dict, empleado_id: Optional[int] = None) -> dict:
    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("El nombre es obligatorio.")
    dup = conn.execute(
        "SELECT id FROM empleados WHERE COALESCE(empresa_id,1) = ? AND LOWER(TRIM(nombre)) = LOWER(?) AND id <> COALESCE(?, -1);",
        (emp_id, nombre, empleado_id),
    ).fetchone()
    if dup:
        raise ValueError("Ya existe un empleado con ese nombre.")
    campos = {
        "nombre": nombre,
        "dni": (data.get("dni") or "").strip(),
        "cuil": (data.get("cuil") or "").strip(),
        "cuit": (data.get("cuil") or "").strip(),
        "banco": (data.get("banco") or "").strip(),
        "cbu": (data.get("cbu") or "").strip(),
        "convenio": (data.get("convenio") or "").strip(),
        "centro_costo": (data.get("centro_costo") or "2").strip(),
        "activo": 1 if int(data.get("activo", 1) or 0) else 0,
        "excluir_indice": 1 if int(data.get("excluir_indice", 0) or 0) else 0,
        "observaciones": (data.get("observaciones") or "").strip(),
        "haber_base": float(data.get("haber_base") or 0),
    }
    if empleado_id:
        anterior = _empleado(conn, emp_id, empleado_id)
        sets = ", ".join(f"{k} = ?" for k in campos)
        conn.execute(f"UPDATE empleados SET {sets} WHERE id = ?;", (*campos.values(), empleado_id))
        viejo = (anterior["nombre"] or "").strip()
        if viejo != nombre:
            conn.execute(
                "UPDATE conformacion_sueldos SET nombre = ? WHERE COALESCE(empresa_id,1) = ? AND TRIM(nombre) = ?;",
                (nombre, emp_id, viejo),
            )
    else:
        cols = ", ".join(["empresa_id", *campos])
        qs = ", ".join("?" for _ in range(len(campos) + 1))
        cur = conn.execute(f"INSERT INTO empleados ({cols}) VALUES ({qs});", (emp_id, *campos.values()))
        empleado_id = cur.lastrowid
    conn.commit()
    return _empleado(conn, emp_id, empleado_id)


# ---------------------------------------------------------------- conformación masiva

def preview_conformacion(conn, emp_id: int, mes: str, porcentaje: float) -> list[dict]:
    mes = mes_valido(mes)
    out = []
    for e in listar_empleados(conn, emp_id):
        nombre = (e["nombre"] or "").strip()
        ant = _principal_antes(conn, emp_id, nombre, mes)
        base = _r2(ant["debe"]) if ant else _r2(e.get("haber_base"))
        pct = 0.0 if int(e.get("excluir_indice") or 0) else float(porcentaje or 0)
        existente = _principal_mes(conn, emp_id, nombre, mes)
        nuevo = _r2(base * (1 + pct / 100))
        out.append({
            "empleado_id": e["id"],
            "nombre": nombre,
            "convenio": e.get("convenio") or "",
            "sueldo_anterior": base,
            "fecha_anterior": ant["fecha"] if ant else "",
            "porcentaje": pct,
            "excluir_indice": int(e.get("excluir_indice") or 0),
            "nuevo_sueldo": nuevo,
            "aumento": _r2(nuevo - base),
            "ya_conformado": bool(existente),
            "sueldo_conformado": _r2(existente["debe"]) if existente else None,
        })
    return out


def conformar_sueldos(conn, emp_id: int, mes: str, detalle_indice: str, items: list[dict],
                      fecha: Optional[str] = None, reemplazar: bool = False) -> dict:
    mes = mes_valido(mes)
    fecha = (fecha or "").strip()[:10] or fin_de_mes(mes)
    if fecha[:7] != mes:
        raise ValueError("La fecha tiene que caer dentro del mes a conformar.")
    creados, actualizados, omitidos = 0, 0, []
    for it in items:
        e = _empleado(conn, emp_id, int(it["empleado_id"]))
        nombre = (e["nombre"] or "").strip()
        ant = _principal_antes(conn, emp_id, nombre, mes)
        base = _r2(ant["debe"]) if ant else _r2(e.get("haber_base"))
        if base <= 0 and it.get("sueldo") is None:
            omitidos.append(f"{nombre} (sin sueldo anterior)")
            continue
        pct = float(it.get("porcentaje") or 0)
        nuevo = _r2(it["sueldo"]) if it.get("sueldo") is not None else _r2(base * (1 + pct / 100))
        aumento = _r2(nuevo - base)
        frac = round(pct / 100, 4)
        detalle = f"{nombre} - {nombre_mes(mes)}"
        existente = _principal_mes(conn, emp_id, nombre, mes)
        if existente:
            if not reemplazar:
                omitidos.append(f"{nombre} (ya conformado)")
                continue
            conn.execute(
                """
                UPDATE conformacion_sueldos
                SET fecha = ?, detalle = ?, aumento = ?, debe = ?, haber = 0, varios = ?, porcentaje = ?
                WHERE id = ?;
                """,
                (fecha, detalle, aumento, nuevo, detalle_indice or "", frac, existente["id"]),
            )
            actualizados += 1
        else:
            conn.execute(
                """
                INSERT INTO conformacion_sueldos
                    (empresa_id, fecha, nombre, detalle, aumento, debe, haber, varios, porcentaje, principal)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 1);
                """,
                (emp_id, fecha, nombre, detalle, aumento, nuevo, detalle_indice or "", frac),
            )
            creados += 1
        conn.execute("UPDATE empleados SET haber_base = ? WHERE id = ?;", (nuevo, e["id"]))
    conn.commit()
    return {"creados": creados, "actualizados": actualizados, "omitidos": omitidos}


# ---------------------------------------------------------------- movimientos / consulta

def _fila(r) -> dict:
    d = dict(r)
    d["debe"] = _r2(d.get("debe"))
    d["haber"] = _r2(d.get("haber"))
    d["aumento"] = _r2(d.get("aumento"))
    d["principal"] = int(d.get("principal") or 0)
    return d


def _es_deposito(detalle: str) -> bool:
    return (detalle or "").strip().lower().startswith("deposito bancario")


def resumen_empleado_mes(conn, emp_id: int, empleado_id: int, mes: str) -> dict:
    mes = mes_valido(mes)
    e = _empleado(conn, emp_id, empleado_id)
    nombre = (e["nombre"] or "").strip()
    filas = [
        _fila(r) for r in conn.execute(
            """
            SELECT * FROM conformacion_sueldos
            WHERE COALESCE(empresa_id, 1) = ? AND TRIM(nombre) = ? AND substr(fecha, 1, 7) = ?
            ORDER BY principal DESC, fecha, id;
            """,
            (emp_id, nombre, mes),
        ).fetchall()
    ]
    principal = next((f for f in filas if f["principal"]), None)
    ant = _principal_antes(conn, emp_id, nombre, mes)
    creditos = _r2(sum(f["debe"] for f in filas))
    debitos = _r2(sum(f["haber"] for f in filas))
    depositos = _r2(sum(f["haber"] for f in filas if _es_deposito(f["detalle"])))
    return {
        "empleado": e,
        "mes": mes,
        "filas": filas,
        "haber_base": principal["debe"] if principal else (_r2(ant["debe"]) if ant else _r2(e.get("haber_base"))),
        "sueldo_anterior": _r2(ant["debe"]) if ant else 0.0,
        "aumento": principal["aumento"] if principal else 0.0,
        "porcentaje": round(float(principal.get("porcentaje") or 0) * 100, 2) if principal else 0.0,
        "detalle_indice": (principal.get("varios") or "") if principal else "",
        "creditos": creditos,
        "debitos": debitos,
        "depositos_bancarios": depositos,
        "total_cobrar": _r2(creditos - debitos),
        "conformado": bool(principal),
    }


def guardar_movimiento(conn, emp_id: int, data: dict, mov_id: Optional[int] = None) -> dict:
    e = _empleado(conn, emp_id, int(data["empleado_id"]))
    fecha = (data.get("fecha") or "").strip()[:10]
    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Fecha inválida.") from exc
    detalle = (data.get("detalle") or "").strip()
    if not detalle:
        raise ValueError("El detalle es obligatorio.")
    debe = _r2(data.get("debe"))
    haber = _r2(data.get("haber"))
    if debe < 0 or haber < 0 or (debe == 0 and haber == 0):
        raise ValueError("Cargá un importe en créditos o en débitos.")
    nombre = (e["nombre"] or "").strip()
    if mov_id:
        row = conn.execute(
            "SELECT * FROM conformacion_sueldos WHERE id = ? AND COALESCE(empresa_id,1) = ?;", (mov_id, emp_id)
        ).fetchone()
        if not row:
            raise ValueError("Movimiento inexistente.")
        aumento = row["aumento"] or 0
        if int(row["principal"] or 0):
            ant = _principal_antes(conn, emp_id, nombre, fecha[:7])
            aumento = _r2(debe - ant["debe"]) if ant else 0
        conn.execute(
            "UPDATE conformacion_sueldos SET fecha = ?, detalle = ?, debe = ?, haber = ?, aumento = ? WHERE id = ?;",
            (fecha, detalle, debe, haber, aumento, mov_id),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO conformacion_sueldos (empresa_id, fecha, nombre, detalle, aumento, debe, haber, principal)
            VALUES (?, ?, ?, ?, 0, ?, ?, 0);
            """,
            (emp_id, fecha, nombre, detalle, debe, haber),
        )
        mov_id = cur.lastrowid
    conn.commit()
    return _fila(conn.execute("SELECT * FROM conformacion_sueldos WHERE id = ?;", (mov_id,)).fetchone())


def borrar_movimiento(conn, emp_id: int, mov_id: int) -> None:
    cur = conn.execute(
        "DELETE FROM conformacion_sueldos WHERE id = ? AND COALESCE(empresa_id,1) = ?;", (mov_id, emp_id)
    )
    if not cur.rowcount:
        raise ValueError("Movimiento inexistente.")
    conn.commit()


def detalle_sueldos_mes(conn, emp_id: int, mes: str, solo_activos: bool = True) -> dict:
    mes = mes_valido(mes)
    empleados = []
    tot = {"creditos": 0.0, "debitos": 0.0, "depositos_bancarios": 0.0, "total_cobrar": 0.0, "aumentos": 0.0}
    for e in listar_empleados(conn, emp_id, incluir_inactivos=not solo_activos):
        r = resumen_empleado_mes(conn, emp_id, e["id"], mes)
        if not r["filas"]:
            continue
        empleados.append({
            "empleado_id": e["id"],
            "nombre": e["nombre"],
            "convenio": e.get("convenio") or "",
            "centro_costo": e.get("centro_costo") or "",
            "filas": r["filas"],
            "actualizacion": r["aumento"],
            "creditos": r["creditos"],
            "debitos": r["debitos"],
            "depositos_bancarios": r["depositos_bancarios"],
            "total_cobrar": r["total_cobrar"],
        })
        for k in ("creditos", "debitos", "depositos_bancarios", "total_cobrar"):
            tot[k] += r[k]
        tot["aumentos"] += r["aumento"]
    return {"mes": mes, "empleados": empleados, "totales": {k: _r2(v) for k, v in tot.items()}}


# ---------------------------------------------------------------- recibos de convenio

def _en_conformacion(conn, emp_id: int, nombre: str, mes: str) -> bool:
    """El empleado cobra una parte fuera de convenio si tiene sueldo base este mes o el anterior."""
    y, m = int(mes[:4]), int(mes[5:7])
    ant = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
    row = conn.execute(
        """
        SELECT 1 FROM conformacion_sueldos
        WHERE COALESCE(empresa_id,1) = ? AND TRIM(nombre) = ? AND principal = 1
          AND substr(fecha, 1, 7) IN (?, ?) LIMIT 1;
        """,
        (emp_id, nombre, mes, ant),
    ).fetchone()
    return bool(row)


def _buscar_empleado_para_recibo(conn, emp_id: int, rec: dict, usados: set) -> Optional[int]:
    from recibos_sueldo import puntaje_nombre, solo_digitos

    empleados = [dict(r) for r in conn.execute(
        "SELECT id, nombre, cuil, cuit, dni, legajo, activo FROM empleados WHERE COALESCE(empresa_id,1) = ?;", (emp_id,)
    ).fetchall()]
    cuil = solo_digitos(rec.get("cuil"))
    dni = cuil[2:10] if len(cuil) == 11 else ""

    def _dni_de(e):
        propio = solo_digitos(e.get("cuil")) or solo_digitos(e.get("cuit"))
        return propio[2:10] if len(propio) == 11 else solo_digitos(e.get("dni")).zfill(8)

    if cuil:
        for e in empleados:
            if e["id"] not in usados and cuil in (solo_digitos(e.get("cuil")), solo_digitos(e.get("cuit"))):
                return e["id"]
        for e in empleados:
            if e["id"] not in usados and dni and _dni_de(e) == dni:
                return e["id"]
    candidatos = []
    for e in empleados:
        if e["id"] in usados:
            continue
        if dni and _dni_de(e).strip("0") and _dni_de(e) != dni:
            continue
        p = puntaje_nombre(e["nombre"], rec.get("nombre", ""))
        if p >= 1.0:
            candidatos.append((int(e.get("activo") or 0), len(e["nombre"] or ""), e["id"]))
    if not candidatos:
        return None
    candidatos.sort(reverse=True)
    if len(candidatos) > 1 and candidatos[0][:2] == candidatos[1][:2]:
        return None
    return candidatos[0][2]


def _completar_empleado_desde_recibo(conn, empleado_id: int, rec: dict) -> None:
    conn.execute(
        """
        UPDATE empleados SET
            cuil = COALESCE(NULLIF(?, ''), cuil),
            cuit = COALESCE(NULLIF(?, ''), cuit),
            dni = CASE WHEN LENGTH(?) = 13 THEN CAST(CAST(SUBSTR(?, 4, 8) AS INTEGER) AS TEXT) ELSE dni END,
            legajo = ?, categoria = ?, fecha_ingreso = ?, convenio = ?
        WHERE id = ?;
        """,
        (rec["cuil"], rec["cuil"], rec["cuil"], rec["cuil"], rec["legajo"], rec["categoria"], rec["fecha_ingreso"],
         rec["convenio"], empleado_id),
    )


def importar_recibos(conn, emp_id: int, archivos: list[tuple[str, bytes]]) -> dict:
    from recibos_sueldo import parsear_pdf

    leidos, periodos, errores = 0, set(), []
    for nombre_archivo, contenido in archivos:
        try:
            recibos = parsear_pdf(contenido)
        except Exception as exc:
            errores.append(f"{nombre_archivo}: {exc}")
            continue
        if not recibos:
            errores.append(f"{nombre_archivo}: no se encontraron recibos")
            continue
        for rec in recibos:
            leidos += 1
            periodos.add(rec["periodo"])
            previo = conn.execute(
                """
                SELECT id, empleado_id, movimiento_id FROM recibos_sueldo
                WHERE empresa_id = ? AND periodo = ? AND cuil = ? AND tipo_liquidacion = ?;
                """,
                (emp_id, rec["periodo"], rec["cuil"], rec["tipo_liquidacion"]),
            ).fetchone()
            valores = (
                rec["es_sac"], rec["legajo"], rec["nombre"], rec.get("fecha_ingreso", ""), rec.get("categoria", ""),
                rec["convenio"], rec["sueldo_basico"], rec["remunerativo"], rec["no_remunerativo"], rec["descuentos"],
                rec["neto"], rec["contribuciones"], rec["costo_total"], json.dumps(rec["conceptos"], ensure_ascii=False),
                nombre_archivo,
            )
            if previo:
                conn.execute(
                    """
                    UPDATE recibos_sueldo SET es_sac=?, legajo=?, nombre=?, fecha_ingreso=?, categoria=?, convenio=?,
                        sueldo_basico=?, remunerativo=?, no_remunerativo=?, descuentos=?, neto=?, contribuciones=?,
                        costo_total=?, conceptos_json=?, archivo=?
                    WHERE id = ?;
                    """,
                    (*valores, previo["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO recibos_sueldo (empresa_id, periodo, tipo_liquidacion, cuil, es_sac, legajo, nombre,
                        fecha_ingreso, categoria, convenio, sueldo_basico, remunerativo, no_remunerativo, descuentos,
                        neto, contribuciones, costo_total, conceptos_json, archivo)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (emp_id, rec["periodo"], rec["tipo_liquidacion"], rec["cuil"], *valores),
                )
    for periodo in periodos:
        _vincular_automatico(conn, emp_id, periodo)
    conn.commit()
    return {"leidos": leidos, "periodos": sorted(periodos), "errores": errores}


def _vincular_automatico(conn, emp_id: int, periodo: str) -> None:
    filas = [dict(r) for r in conn.execute(
        "SELECT * FROM recibos_sueldo WHERE empresa_id = ? AND periodo = ? ORDER BY empleado_id IS NULL, id;",
        (emp_id, periodo),
    ).fetchall()]
    usados = {f["empleado_id"] for f in filas if f["empleado_id"]}
    for f in filas:
        if f["empleado_id"]:
            continue
        eid = _buscar_empleado_para_recibo(conn, emp_id, f, usados)
        if eid:
            usados.add(eid)
            conn.execute("UPDATE recibos_sueldo SET empleado_id = ? WHERE id = ?;", (eid, f["id"]))
            _completar_empleado_desde_recibo(conn, eid, f)


def _detalle_deposito(es_sac) -> str:
    return "Deposito Bancario SAC" if int(es_sac or 0) else "Deposito Bancario"


def _movimiento_deposito(conn, emp_id: int, nombre: str, mes: str, recibo: dict) -> Optional[dict]:
    if recibo.get("movimiento_id"):
        row = conn.execute(
            "SELECT * FROM conformacion_sueldos WHERE id = ? AND COALESCE(empresa_id,1) = ?;",
            (recibo["movimiento_id"], emp_id),
        ).fetchone()
        if row:
            return dict(row)
    row = conn.execute(
        """
        SELECT * FROM conformacion_sueldos
        WHERE COALESCE(empresa_id,1) = ? AND TRIM(nombre) = ? AND substr(fecha,1,7) = ?
          AND LOWER(TRIM(detalle)) = LOWER(?) AND COALESCE(principal,0) = 0
        ORDER BY id LIMIT 1;
        """,
        (emp_id, nombre, mes, _detalle_deposito(recibo.get("es_sac"))),
    ).fetchone()
    return dict(row) if row else None


def listar_recibos(conn, emp_id: int, mes: str) -> dict:
    mes = mes_valido(mes)
    filas = []
    tot: dict[str, dict] = {}
    for r in conn.execute(
        """
        SELECT r.*, e.nombre AS empleado_nombre FROM recibos_sueldo r
        LEFT JOIN empleados e ON e.id = r.empleado_id
        WHERE r.empresa_id = ? AND r.periodo = ?
        ORDER BY r.convenio, r.nombre;
        """,
        (emp_id, mes),
    ).fetchall():
        d = dict(r)
        d["conceptos"] = json.loads(d.pop("conceptos_json") or "[]")
        d["en_conformacion"] = False
        d["deposito_cargado"] = None
        d["deposito_mes_anterior"] = False
        if d["empleado_nombre"]:
            nombre = d["empleado_nombre"].strip()
            d["en_conformacion"] = _en_conformacion(conn, emp_id, nombre, mes)
            mov = _movimiento_deposito(conn, emp_id, nombre, mes, d)
            d["deposito_cargado"] = _r2(mov["haber"]) if mov else None
            y, m = int(mes[:4]), int(mes[5:7])
            ant = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
            d["deposito_mes_anterior"] = bool(conn.execute(
                """
                SELECT 1 FROM conformacion_sueldos
                WHERE COALESCE(empresa_id,1) = ? AND TRIM(nombre) = ? AND substr(fecha,1,7) = ?
                  AND LOWER(TRIM(detalle)) LIKE 'deposito bancario%' LIMIT 1;
                """,
                (emp_id, nombre, ant),
            ).fetchone())
        filas.append(d)
        t = tot.setdefault(d["convenio"] or "Sin convenio", {
            "convenio": d["convenio"] or "Sin convenio", "cantidad": 0, "remunerativo": 0.0, "no_remunerativo": 0.0,
            "descuentos": 0.0, "neto": 0.0, "contribuciones": 0.0, "costo_total": 0.0,
        })
        t["cantidad"] += 1
        for k in ("remunerativo", "no_remunerativo", "descuentos", "neto", "contribuciones", "costo_total"):
            t[k] += float(d[k] or 0)
    por_convenio = [{k: (_r2(v) if isinstance(v, float) else v) for k, v in t.items()} for t in tot.values()]
    total = {k: _r2(sum(t[k] for t in por_convenio)) for k in
             ("remunerativo", "no_remunerativo", "descuentos", "neto", "contribuciones", "costo_total")}
    total["cantidad"] = len(filas)
    return {"mes": mes, "recibos": filas, "por_convenio": por_convenio, "total": total}


def vincular_recibo(conn, emp_id: int, recibo_id: int, empleado_id: Optional[int]) -> dict:
    rec = conn.execute(
        "SELECT * FROM recibos_sueldo WHERE id = ? AND empresa_id = ?;", (recibo_id, emp_id)
    ).fetchone()
    if not rec:
        raise ValueError("Recibo inexistente.")
    if empleado_id:
        _empleado(conn, emp_id, empleado_id)
        otro = conn.execute(
            "SELECT id FROM recibos_sueldo WHERE empresa_id = ? AND periodo = ? AND empleado_id = ? AND id <> ? AND tipo_liquidacion = ?;",
            (emp_id, rec["periodo"], empleado_id, recibo_id, rec["tipo_liquidacion"]),
        ).fetchone()
        if otro:
            raise ValueError("Ese empleado ya tiene otro recibo asociado en el mes.")
        conn.execute(
            "UPDATE recibos_sueldo SET empleado_id = ?, movimiento_id = NULL WHERE id = ?;", (empleado_id, recibo_id)
        )
        _completar_empleado_desde_recibo(conn, empleado_id, dict(rec))
    else:
        conn.execute("UPDATE recibos_sueldo SET empleado_id = NULL, movimiento_id = NULL WHERE id = ?;", (recibo_id,))
    conn.commit()
    return {"status": "ok"}


def aplicar_depositos(conn, emp_id: int, mes: str, recibo_ids: Optional[list[int]] = None) -> dict:
    """Carga el neto de cada recibo como Depósito Bancario en la cuenta fuera de convenio."""
    mes = mes_valido(mes)
    fecha = fin_de_mes(mes)
    elegidos = set(recibo_ids) if recibo_ids is not None else None
    creados, actualizados, sin_cambios, omitidos = 0, 0, 0, []
    for r in conn.execute(
        """
        SELECT r.*, e.nombre AS empleado_nombre FROM recibos_sueldo r
        LEFT JOIN empleados e ON e.id = r.empleado_id
        WHERE r.empresa_id = ? AND r.periodo = ?;
        """,
        (emp_id, mes),
    ).fetchall():
        d = dict(r)
        if elegidos is not None and d["id"] not in elegidos:
            continue
        if not d["empleado_nombre"]:
            omitidos.append(f"{d['nombre']} (sin empleado asociado)")
            continue
        nombre = d["empleado_nombre"].strip()
        if not _en_conformacion(conn, emp_id, nombre, mes):
            omitidos.append(f"{d['nombre']} (cobra solo por recibo)")
            continue
        neto = _r2(d["neto"])
        mov = _movimiento_deposito(conn, emp_id, nombre, mes, d)
        if mov:
            if _r2(mov["haber"]) == neto and _r2(mov["debe"]) == 0:
                sin_cambios += 1
            else:
                conn.execute(
                    "UPDATE conformacion_sueldos SET haber = ?, debe = 0 WHERE id = ?;", (neto, mov["id"])
                )
                actualizados += 1
            mov_id = mov["id"]
        else:
            cur = conn.execute(
                """
                INSERT INTO conformacion_sueldos (empresa_id, fecha, nombre, detalle, aumento, debe, haber, principal)
                VALUES (?, ?, ?, ?, 0, 0, ?, 0);
                """,
                (emp_id, fecha, nombre, _detalle_deposito(d["es_sac"]), neto),
            )
            mov_id = cur.lastrowid
            creados += 1
        conn.execute("UPDATE recibos_sueldo SET movimiento_id = ? WHERE id = ?;", (mov_id, d["id"]))
    conn.commit()
    return {"creados": creados, "actualizados": actualizados, "sin_cambios": sin_cambios, "omitidos": omitidos}


def meses_con_movimientos(conn, emp_id: int) -> list[str]:
    return [
        r[0] for r in conn.execute(
            f"""
            SELECT DISTINCT substr(fecha, 1, 7) FROM conformacion_sueldos
            WHERE COALESCE(empresa_id, 1) = ? AND {_FECHA_OK}
            ORDER BY 1 DESC LIMIT 36;
            """,
            (emp_id,),
        ).fetchall()
    ]
