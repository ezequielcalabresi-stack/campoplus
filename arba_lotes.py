# -*- coding: utf-8 -*-
"""
Retenciones y percepciones de Ingresos Brutos (ARBA): detalle por período y lotes de importación.

Percepciones (AR-Web, actividad 7 — diseño 1.2 "método Percibido", 81 caracteres):
  ZIP  AR-CUIT-AAAAMMQ-P7-LOTE_MD5.zip  (MD5 del .zip) con el TXT AR-CUIT-AAAAMMQ-P7-LOTE.txt
Retenciones (Emisión de Comprobantes de Retención Web A-122R, RN 22/25, 67 caracteres):
  ZIP  ER-CUIT-AAAAMMQ-ACTIVIDAD-LOTEXXXXX.zip con el TXT del mismo nombre. ARBA calcula el
  importe y emite el comprobante; el número de transacción del agente no puede repetirse en el período.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from calendar import monthrange
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import Response

_TIPO_CBTE = (
    ("NOTA DE CRÉDITO", "C"), ("NOTA DE CREDITO", "C"),
    ("NOTA DE DÉBITO", "D"), ("NOTA DE DEBITO", "D"),
    ("RECIBO", "R"), ("FACTURA", "F"),
)

_TIPOS_PERCEPCION = ("PERCEPCION_IIBB", "PERCEPCION IIBB", "PERC_IIBB")
_TIPOS_RETENCION = ("IIBB", "RET_IIBB", "ARBA")


def _digitos(s: Any) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _cuit_guiones(s: Any) -> str:
    d = _digitos(s).zfill(11)[-11:]
    return f"{d[:2]}-{d[2:10]}-{d[10]}"


def _pesos(n: float) -> str:
    return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fecha_ar(iso: str) -> str:
    y, m, d = iso[:10].split("-")
    return f"{d}/{m}/{y}"


def _rango(anio: int, mes: int, quincena: int) -> Tuple[str, str]:
    if quincena == 1:
        return f"{anio:04d}-{mes:02d}-01", f"{anio:04d}-{mes:02d}-15"
    fin = monthrange(anio, mes)[1]
    ini = 16 if quincena == 2 else 1
    return f"{anio:04d}-{mes:02d}-{ini:02d}", f"{anio:04d}-{mes:02d}-{fin:02d}"


def _alicuota(regimen: Any, base: float, importe: float) -> float:
    m = re.search(r"([\d.,]+)\s*%", str(regimen or ""))
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            pass
    return round(importe / base * 100, 2) if base > 0.009 else 0.0


def _comprobante(origen: Any) -> Tuple[str, str, str, str]:
    """'Factura A 00005-00000101' -> ('F', 'A', '00005', '00000101')."""
    txt = str(origen or "").strip()
    up = txt.upper()
    tipo = next((letra for clave, letra in _TIPO_CBTE if up.startswith(clave)), "")
    m_letra = re.search(r"\b([ABC])\b", up)
    m_nro = re.search(r"(\d{1,5})\s*-\s*(\d{1,8})\s*$", txt)
    if not tipo or not m_nro:
        return "", "", "", ""
    return tipo, (m_letra.group(1) if m_letra else " "), m_nro.group(1).zfill(5), m_nro.group(2).zfill(8)


def _cuit_agente(cursor) -> str:
    cursor.execute("SELECT cuit FROM configuracion_empresa WHERE id = 1;")
    r = cursor.fetchone()
    cuit = _digitos(r[0] if r else "")
    if len(cuit) != 11:
        raise HTTPException(400, "Cargá el CUIT de la empresa en Configuración de Empresa.")
    return cuit


def _filas(cursor, tipos: Tuple[str, ...], desde: str, hasta: str) -> list:
    cursor.execute("PRAGMA table_info(retenciones_sicore);")
    con_origen = "comprobante_origen" in {c[1] for c in cursor.fetchall()}
    ph = ",".join("?" for _ in tipos)
    cursor.execute(
        f"""
        SELECT r.id, r.fecha, r.cuit, r.razon_social, r.base_imponible, r.importe_retenido, r.regimen,
               r.nro_comprobante, {"r.comprobante_origen" if con_origen else "''"} AS comprobante_origen,
               (SELECT e.domicilio FROM entidades e
                 WHERE REPLACE(e.cuit, '-', '') = REPLACE(r.cuit, '-', '') AND COALESCE(e.domicilio, '') <> '' LIMIT 1) AS domicilio,
               (SELECT e.localidad FROM entidades e
                 WHERE REPLACE(e.cuit, '-', '') = REPLACE(r.cuit, '-', '') AND COALESCE(e.localidad, '') <> '' LIMIT 1) AS localidad
        FROM retenciones_sicore r
        WHERE UPPER(COALESCE(r.tipo_retencion, '')) IN ({ph})
          AND substr(r.fecha, 1, 10) BETWEEN ? AND ?
        ORDER BY r.fecha, r.id;
        """,
        (*tipos, desde, hasta),
    )
    return cursor.fetchall()


def _base_registro(r, base: float, alic: float, imp: float) -> Dict[str, Any]:
    return {
        "id": r["id"], "fecha": r["fecha"][:10], "cuit": r["cuit"], "nombre": r["razon_social"],
        "domicilio": r["domicilio"] or "", "localidad": r["localidad"] or "",
        "certificado": r["nro_comprobante"] or "", "base": base, "alicuota": alic, "importe": imp,
        "linea": "", "error": "", "aviso": "",
    }


def registros_percepciones(cursor, desde: str, hasta: str) -> List[Dict[str, Any]]:
    regs = []
    for r in _filas(cursor, _TIPOS_PERCEPCION, desde, hasta):
        base = float(r["base_imponible"] or 0)
        imp = float(r["importe_retenido"] or 0)
        alic = _alicuota(r["regimen"], base, imp)
        reg = _base_registro(r, base, alic, imp)
        reg["comprobante"] = r["comprobante_origen"] or ""
        tipo, letra, suc, nro = _comprobante(r["comprobante_origen"])
        if not tipo:
            reg["error"] = (f"Certificado {reg['certificado']} ({r['razon_social']}): no tiene la factura asociada; "
                            "cargala de nuevo desde Facturas de venta.")
        else:
            if tipo == "C":
                base, imp = -abs(base), -abs(imp)
            fecha = _fecha_ar(r["fecha"])
            reg["linea"] = (
                f"{_cuit_guiones(r['cuit'])}{fecha}{tipo}{letra}{suc}{nro}"
                f"{base:014.2f}{alic:05.2f}{imp:013.2f}{fecha}A"
            )
        regs.append(reg)
    return regs


def registros_retenciones(cursor, desde: str, hasta: str, sucursal: int = 1) -> List[Dict[str, Any]]:
    regs, usados = [], set()
    for r in _filas(cursor, _TIPOS_RETENCION, desde, hasta):
        base = float(r["base_imponible"] or 0)
        imp = float(r["importe_retenido"] or 0)
        alic = _alicuota(r["regimen"], base, imp)
        reg = _base_registro(r, base, alic, imp)
        cert = reg["certificado"]
        transaccion = _digitos(cert.split("-")[-1]) or str(r["id"])
        if transaccion in usados:
            transaccion = str(r["id"])
        usados.add(transaccion)
        reg["transaccion"] = transaccion
        if base <= 0.009 or alic <= 0:
            reg["error"] = f"Retención {cert} ({r['razon_social']}): falta la base imponible o la alícuota."
        else:
            calculado = round(base * alic / 100, 2)
            if abs(calculado - imp) > 1:
                reg["aviso"] = (f"Retención {cert} ({r['razon_social']}): se retuvo $ {_pesos(imp)} pero ARBA va a calcular "
                                f"$ {_pesos(calculado)} ({_pesos(alic)}% sobre $ {_pesos(base)}). Revisá la alícuota antes de subir el lote.")
            reg["linea"] = (
                f"{transaccion.zfill(20)[-20:]}{_digitos(r['cuit']).zfill(11)[-11:]}{int(sucursal):05d}"
                f"{_fecha_ar(r['fecha'])}{alic:05.2f}{base:016.2f}"
            )
        regs.append(reg)
    return regs


def _zip(nombre_txt: str, contenido: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo(nombre_txt, date_time=date.today().timetuple()[:6])
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, contenido.encode("cp1252", errors="replace"))
    return buf.getvalue()


def _validar_periodo(anio: int, mes: int, quincena: int) -> None:
    if not (2000 <= anio <= 2100 and 1 <= mes <= 12 and quincena in (0, 1, 2)):
        raise HTTPException(400, "Período inválido.")


def _validar_fecha(s: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s or ""):
        raise HTTPException(400, "Fecha inválida.")
    return s


def _resumen(regs: List[Dict[str, Any]]) -> Dict[str, Any]:
    validos = [r for r in regs if r["linea"]]
    return {
        "registros": regs,
        "errores": [r["error"] for r in regs if r["error"]],
        "avisos": [r["aviso"] for r in regs if r["aviso"]],
        "en_lote": len(validos),
        "total_base": round(sum(r["base"] for r in regs), 2),
        "total_importe": round(sum(r["importe"] for r in regs), 2),
    }


def register_arba_lotes_routes(app, get_db, huerfanas: Optional[Callable[[Any], list]] = None):
    def _datos(tipo: str, desde: str, hasta: str, sucursal: int):
        conn = get_db()
        try:
            cursor = conn.cursor()
            cuit = _cuit_agente(cursor)
            if tipo == "percepciones":
                regs = registros_percepciones(cursor, desde, hasta)
            elif tipo == "retenciones":
                regs = registros_retenciones(cursor, desde, hasta, sucursal)
            else:
                raise HTTPException(400, "Tipo de lote inválido.")
        finally:
            conn.close()
        return cuit, regs

    @app.get("/api/arba/iibb")
    def api_arba_iibb(desde: str, hasta: str, sucursal: int = 1):
        """Retenciones y percepciones de IIBB entre dos fechas, con la línea de lote de cada una."""
        desde, hasta = _validar_fecha(desde), _validar_fecha(hasta)
        conn = get_db()
        try:
            cursor = conn.cursor()
            ret = registros_retenciones(cursor, desde, hasta, sucursal)
            perc = registros_percepciones(cursor, desde, hasta)
            sueltas = [h for h in (huerfanas(cursor) if huerfanas else [])
                       if str(h.get("tipo_retencion") or "").upper() == "IIBB"]
        finally:
            conn.close()
        ids_sueltas = {h["id"] for h in sueltas}
        for r in ret:
            r["sin_cta_cte"] = r["id"] in ids_sueltas
        return {"retenciones": _resumen(ret), "percepciones": _resumen(perc), "sin_cta_cte": sueltas}

    @app.get("/api/arba/lotes/previa")
    def api_arba_lote_previa(tipo: str, anio: int, mes: int, quincena: int = 0, sucursal: int = 1):
        _validar_periodo(anio, mes, quincena)
        _, regs = _datos(tipo, *_rango(anio, mes, quincena), sucursal)
        return _resumen(regs)

    @app.get("/api/arba/lotes/descargar")
    def api_arba_lote_descargar(tipo: str, anio: int, mes: int, quincena: int = 0, actividad: str = "6",
                                lote: str = "1", sucursal: int = 1):
        _validar_periodo(anio, mes, quincena)
        cuit, regs = _datos(tipo, *_rango(anio, mes, quincena), sucursal)
        regs = [r for r in regs if r["linea"]]
        if not regs:
            raise HTTPException(404, "No hay operaciones para informar en ese período.")
        periodo = f"{anio:04d}{mes:02d}{quincena}"
        lote_id = re.sub(r"[^0-9A-Za-z]", "", lote or "1") or "1"
        contenido = "\r\n".join(r["linea"] for r in regs) + "\r\n"
        if tipo == "percepciones":
            base = f"AR-{cuit}-{periodo}-P7-LOTE{lote_id}"
            datos = _zip(base + ".txt", contenido)
            nombre = f"{base}_{hashlib.md5(datos).hexdigest()}.zip"
        else:
            act = re.sub(r"\D", "", actividad or "6") or "6"
            base = f"ER-{cuit}-{periodo}-{act}-LOTE{lote_id.zfill(5)[-5:]}"
            datos = _zip(base + ".txt", contenido)
            nombre = base + ".zip"
        return Response(
            content=datos,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
        )
