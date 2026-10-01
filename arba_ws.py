# -*- coding: utf-8 -*-
"""
Servicios web de ARBA — Régimen de Recaudación por Sujeto.

- dfeServicioConsulta.do: alícuotas Ret/Perc de un CUIT para un período.
- dfeServicioDescargaPadron.do: ZIP del padrón completo (PadronRGSRet + PadronRGSPer).

Ambos reciben un POST multipart con user (CUIT del agente), password (CIT)
y un XML llamado <prefijo>_<md5 del XML>.xml. Los errores vuelven como <DFEError>.
"""
from __future__ import annotations

import calendar
import hashlib
import os
import re
import ssl
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any, Dict, Optional, Tuple

URL_BASE = os.environ.get(
    "ARBA_WS_URL_BASE", "https://dfe.arba.gov.ar/DomicilioElectronico/SeguridadCliente"
).rstrip("/")
URL_CONSULTA = URL_BASE + "/dfeServicioConsulta.do"
URL_PADRON = URL_BASE + "/dfeServicioDescargaPadron.do"


class ArbaWSError(Exception):
    pass


def limpiar_cuit(cuit: Any) -> str:
    return re.sub(r"\D", "", str(cuit or ""))


def credenciales_validas(cuit: Any, cit: Any) -> bool:
    c = limpiar_cuit(cuit)
    k = str(cit or "").strip()
    return len(c) == 11 and bool(k) and not k.upper().startswith("CIT-ARBA-")


def rango_mes(fecha: Optional[date] = None) -> Tuple[str, str]:
    """(AAAAMMDD primer día, AAAAMMDD último día) del mes de `fecha`."""
    f = fecha or date.today()
    ultimo = calendar.monthrange(f.year, f.month)[1]
    return f"{f.year:04d}{f.month:02d}01", f"{f.year:04d}{f.month:02d}{ultimo:02d}"


def _texto(nodo: Optional[ET.Element], tag: str) -> str:
    if nodo is None:
        return ""
    el = nodo.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""


def _limpiar_mensaje(msg: str) -> str:
    m = re.sub(r"<!\[CDATA\[|\]\]/?>?", "", msg or "")
    return m.strip()


def error_dfe(contenido: bytes) -> Optional[str]:
    """Mensaje de un <DFEError> o None si la respuesta no es un error de ARBA."""
    cabeza = contenido[:400].lstrip()
    if not cabeza.startswith(b"<"):
        return None
    try:
        raiz = ET.fromstring(contenido)
    except ET.ParseError:
        return None
    if raiz.tag != "DFEError" and raiz.find("tipoError") is None:
        return None
    tipo = _texto(raiz, "tipoError")
    codigo = _texto(raiz, "codigoError")
    msg = _limpiar_mensaje(_texto(raiz, "mensajeError"))
    return f"ARBA ({tipo} {codigo}): {msg}".strip()


def _multipart(campos: Dict[str, str], nombre_archivo: str, xml: bytes) -> Tuple[bytes, str]:
    limite = "----CampoPlus" + uuid.uuid4().hex
    partes = []
    for k, v in campos.items():
        partes.append(
            f"--{limite}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("latin-1")
        )
    partes.append(
        (
            f"--{limite}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{nombre_archivo}\"\r\n"
            "Content-Type: application/xml\r\n\r\n"
        ).encode("latin-1")
        + xml
        + b"\r\n"
    )
    partes.append(f"--{limite}--\r\n".encode("latin-1"))
    return b"".join(partes), f"multipart/form-data; boundary={limite}"


def _post(url: str, cuit_agente: str, cit: str, prefijo: str, xml_txt: str, timeout: float):
    xml = xml_txt.encode("iso-8859-1")
    nombre = f"{prefijo}_{hashlib.md5(xml).hexdigest()}.xml"
    cuerpo, tipo = _multipart({"user": limpiar_cuit(cuit_agente), "password": str(cit).strip()}, nombre, xml)
    req = urllib.request.Request(url, data=cuerpo, method="POST", headers={"Content-Type": tipo})
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context())
    except urllib.error.HTTPError as e:
        detalle = error_dfe(e.read() or b"") or f"HTTP {e.code}"
        raise ArbaWSError(detalle) from e
    except (urllib.error.URLError, OSError) as e:
        raise ArbaWSError(f"No se pudo conectar con ARBA: {e}") from e


def _alicuota(txt: str) -> float:
    try:
        return float(str(txt or "0").strip().replace(",", "."))
    except ValueError:
        return 0.0


def consultar_alicuotas(
    cuit_agente: str, cit: str, cuit: str, fecha: Optional[date] = None, timeout: float = 12
) -> Dict[str, Any]:
    """Alícuotas vigentes de `cuit` en el mes de `fecha`. Lanza ArbaWSError si ARBA rechaza."""
    objetivo = limpiar_cuit(cuit)
    if len(objetivo) != 11:
        raise ArbaWSError("CUIT inválido")
    desde, hasta = rango_mes(fecha)
    xml_txt = (
        '<?xml version="1.0" encoding="ISO-8859-1"?>\n'
        "<CONSULTA-ALICUOTA>\n"
        f"<fechaDesde>{desde}</fechaDesde>\n"
        f"<fechaHasta>{hasta}</fechaHasta>\n"
        "<cantidadContribuyentes>1</cantidadContribuyentes>\n"
        '<contribuyentes class="list">\n'
        f"<contribuyente><cuitContribuyente>{objetivo}</cuitContribuyente></contribuyente>\n"
        "</contribuyentes>\n"
        "</CONSULTA-ALICUOTA>\n"
    )
    with _post(URL_CONSULTA, cuit_agente, cit, "DFEServicioConsulta", xml_txt, timeout) as resp:
        contenido = resp.read()
    err = error_dfe(contenido)
    if err:
        raise ArbaWSError(err)
    try:
        raiz = ET.fromstring(contenido)
    except ET.ParseError as e:
        raise ArbaWSError("Respuesta de ARBA ilegible") from e
    contrib = raiz.find(".//contribuyente")
    if contrib is None:
        raise ArbaWSError("ARBA no devolvió datos para el CUIT")
    return {
        "cuit": _texto(contrib, "cuitContribuyente") or objetivo,
        "alicuota_retencion": _alicuota(_texto(contrib, "alicuotaRetencion")),
        "alicuota_percepcion": _alicuota(_texto(contrib, "alicuotaPercepcion")),
        "ret_grupo": (_texto(contrib, "grupoRetencion") or "00").zfill(2)[:2],
        "perc_grupo": (_texto(contrib, "grupoPercepcion") or "00").zfill(2)[:2],
        "numero_comprobante": _texto(raiz, "numeroComprobante"),
        "vigencia_desde": f"{desde[:4]}-{desde[4:6]}-{desde[6:]}",
        "vigencia_hasta": f"{hasta[:4]}-{hasta[4:6]}-{hasta[6:]}",
    }


def descargar_padron(
    cuit_agente: str,
    cit: str,
    destino_dir: str,
    fecha: Optional[date] = None,
    timeout: float = 120,
    on_progress=None,
) -> str:
    """Descarga el ZIP del padrón del mes de `fecha` a `destino_dir` y devuelve su ruta."""
    desde, hasta = rango_mes(fecha)
    xml_txt = (
        '<?xml version="1.0" encoding="ISO-8859-1"?>\n'
        "<DESCARGA-PADRON>\n"
        f"<fechaDesde>{desde}</fechaDesde>\n"
        f"<fechaHasta>{hasta}</fechaHasta>\n"
        "</DESCARGA-PADRON>\n"
    )
    os.makedirs(destino_dir, exist_ok=True)
    destino = os.path.join(destino_dir, f"PadronRGS{desde[4:6]}{desde[:4]}.zip")
    parcial = destino + ".part"
    total = 0
    try:
        with _post(URL_PADRON, cuit_agente, cit, "DFEServicioDescargaPadron", xml_txt, timeout) as resp:
            primero = resp.read(64 * 1024)
            if not primero.startswith(b"PK"):
                resto = resp.read(256 * 1024)
                raise ArbaWSError(error_dfe(primero + resto) or "ARBA no devolvió un ZIP de padrón")
            with open(parcial, "wb") as f:
                f.write(primero)
                total = len(primero)
                while True:
                    bloque = resp.read(1024 * 1024)
                    if not bloque:
                        break
                    f.write(bloque)
                    total += len(bloque)
                    if on_progress:
                        on_progress(total)
        os.replace(parcial, destino)
    finally:
        if os.path.exists(parcial):
            try:
                os.remove(parcial)
            except OSError:
                pass
    return destino


if __name__ == "__main__":
    import getpass
    import json

    agente = input("CUIT agente: ").strip()
    clave = getpass.getpass("CIT ARBA: ").strip()
    objetivo = input("CUIT a consultar: ").strip()
    print(json.dumps(consultar_alicuotas(agente, clave, objetivo), indent=2, ensure_ascii=False))
