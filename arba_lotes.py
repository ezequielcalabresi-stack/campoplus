# -*- coding: utf-8 -*-
"""
Lotes de importación de ARBA para agentes de recaudación de Ingresos Brutos.

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
from typing import Any, Dict, List, Tuple

from fastapi import HTTPException
from fastapi.responses import Response

_TIPO_CBTE = (
    ("NOTA DE CRÉDITO", "C"), ("NOTA DE CREDITO", "C"),
    ("NOTA DE DÉBITO", "D"), ("NOTA DE DEBITO", "D"),
    ("RECIBO", "R"), ("FACTURA", "F"),
)


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


def registros_percepciones(cursor, anio: int, mes: int, quincena: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    desde, hasta = _rango(anio, mes, quincena)
    cursor.execute("PRAGMA table_info(retenciones_sicore);")
    con_origen = "comprobante_origen" in {c[1] for c in cursor.fetchall()}
    cursor.execute(
        f"""
        SELECT id, fecha, cuit, razon_social, base_imponible, importe_retenido, regimen, nro_comprobante,
               {"comprobante_origen" if con_origen else "''"} AS comprobante_origen
        FROM retenciones_sicore
        WHERE UPPER(COALESCE(tipo_retencion, '')) IN ('PERCEPCION_IIBB', 'PERCEPCION IIBB', 'PERC_IIBB')
          AND substr(fecha, 1, 10) BETWEEN ? AND ?
        ORDER BY fecha, id;
        """,
        (desde, hasta),
    )
    regs, errores = [], []
    for r in cursor.fetchall():
        base = float(r["base_imponible"] or 0)
        imp = float(r["importe_retenido"] or 0)
        alic = _alicuota(r["regimen"], base, imp)
        tipo, letra, suc, nro = _comprobante(r["comprobante_origen"])
        cert = r["nro_comprobante"] or ""
        if not tipo:
            msg = f"Certificado {cert} ({r['razon_social']}): no tiene la factura asociada; cargala de nuevo desde Facturas de venta o informala a mano."
            errores.append(msg)
            regs.append({"id": r["id"], "fecha": r["fecha"][:10], "cuit": r["cuit"], "nombre": r["razon_social"],
                         "comprobante": "", "certificado": cert, "base": base, "alicuota": alic, "importe": imp,
                         "linea": "", "error": msg})
            continue
        if tipo == "C":
            base, imp = -abs(base), -abs(imp)
        fecha = _fecha_ar(r["fecha"])
        linea = (
            f"{_cuit_guiones(r['cuit'])}{fecha}{tipo}{letra}{suc}{nro}"
            f"{base:014.2f}{alic:05.2f}{imp:013.2f}{fecha}A"
        )
        regs.append({
            "id": r["id"], "fecha": r["fecha"][:10], "cuit": r["cuit"], "nombre": r["razon_social"], "comprobante": r["comprobante_origen"],
            "certificado": cert, "base": base, "alicuota": alic, "importe": imp, "linea": linea,
        })
    return regs, errores


def registros_retenciones(cursor, anio: int, mes: int, quincena: int, sucursal: int = 1) -> Tuple[List[Dict[str, Any]], List[str]]:
    desde, hasta = _rango(anio, mes, quincena)
    cursor.execute(
        """
        SELECT id, fecha, cuit, razon_social, base_imponible, importe_retenido, regimen, nro_comprobante
        FROM retenciones_sicore
        WHERE UPPER(COALESCE(tipo_retencion, '')) IN ('IIBB', 'RET_IIBB', 'ARBA')
          AND substr(fecha, 1, 10) BETWEEN ? AND ?
        ORDER BY fecha, id;
        """,
        (desde, hasta),
    )
    regs, errores, usados = [], [], set()
    for r in cursor.fetchall():
        base = float(r["base_imponible"] or 0)
        imp = float(r["importe_retenido"] or 0)
        alic = _alicuota(r["regimen"], base, imp)
        cert = str(r["nro_comprobante"] or "")
        transaccion = _digitos(cert.split("-")[-1]) or str(r["id"])
        if transaccion in usados:
            transaccion = str(r["id"])
        usados.add(transaccion)
        if base <= 0.009 or alic <= 0:
            msg = f"Retención {cert} ({r['razon_social']}): falta la base imponible o la alícuota."
            errores.append(msg)
            regs.append({"id": r["id"], "fecha": r["fecha"][:10], "cuit": r["cuit"], "nombre": r["razon_social"],
                         "certificado": cert, "transaccion": transaccion, "base": base, "alicuota": alic,
                         "importe": imp, "linea": "", "error": msg})
            continue
        linea = (
            f"{transaccion.zfill(20)[-20:]}{_digitos(r['cuit']).zfill(11)[-11:]}{int(sucursal):05d}"
            f"{_fecha_ar(r['fecha'])}{alic:05.2f}{base:016.2f}"
        )
        calculado = round(base * alic / 100, 2)
        aviso = ""
        if abs(calculado - imp) > 1:
            aviso = (f"Retención {cert} ({r['razon_social']}): se retuvo $ {_pesos(imp)} pero ARBA va a calcular "
                     f"$ {_pesos(calculado)} ({_pesos(alic)}% sobre $ {_pesos(base)}). Revisá la alícuota antes de subir el lote.")
        regs.append({
            "id": r["id"], "fecha": r["fecha"][:10], "cuit": r["cuit"], "nombre": r["razon_social"], "certificado": cert,
            "transaccion": transaccion, "base": base, "alicuota": alic, "importe": imp, "linea": linea,
            "aviso": aviso,
        })
    return regs, errores


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


def register_arba_lotes_routes(app, get_db):
    def _datos(tipo: str, anio: int, mes: int, quincena: int, sucursal: int):
        _validar_periodo(anio, mes, quincena)
        conn = get_db()
        try:
            cursor = conn.cursor()
            cuit = _cuit_agente(cursor)
            if tipo == "percepciones":
                regs, errores = registros_percepciones(cursor, anio, mes, quincena)
            elif tipo == "retenciones":
                regs, errores = registros_retenciones(cursor, anio, mes, quincena, sucursal)
            else:
                raise HTTPException(400, "Tipo de lote inválido.")
        finally:
            conn.close()
        return cuit, regs, errores

    @app.get("/api/arba/lotes/previa")
    def api_arba_lote_previa(tipo: str, anio: int, mes: int, quincena: int = 0, sucursal: int = 1):
        _, regs, errores = _datos(tipo, anio, mes, quincena, sucursal)
        validos = [r for r in regs if r["linea"]]
        return {
            "registros": regs,
            "errores": errores,
            "avisos": [r["aviso"] for r in regs if r.get("aviso")],
            "en_lote": len(validos),
            "total_base": round(sum(r["base"] for r in validos), 2),
            "total_importe": round(sum(r["importe"] for r in validos), 2),
        }

    @app.get("/api/arba/lotes/descargar")
    def api_arba_lote_descargar(tipo: str, anio: int, mes: int, quincena: int = 0, actividad: str = "6",
                                lote: str = "1", sucursal: int = 1):
        cuit, regs, _ = _datos(tipo, anio, mes, quincena, sucursal)
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
