# -*- coding: utf-8 -*-
"""
Padrón ARBA sin intervención manual:
- revisa cada pocas horas si el padrón cargado venció y, si es así, lo baja del servicio web;
- consulta online a ARBA los CUIT que no se pueden resolver con el padrón cargado (con caché mensual).
La CIT sale de configuracion_empresa (Configuración de Empresa) y nunca se devuelve al navegador.
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import threading
import time
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from arba_padron import (
    PADRONES_DIR,
    _PADRON_ROOT,
    asegurar_carpeta_padrones,
    estado_padron_arba,
    get_import_job_status,
    start_import_job,
)
from arba_ws import ArbaWSError, consultar_alicuotas, credenciales_validas, limpiar_cuit

CACHE_DB_PATH = os.path.join(_PADRON_ROOT, "padron_ws.db")
AUTO_STATUS_PATH = os.path.join(PADRONES_DIR, "_auto_arba.json")
INTERVALO_REVISION_SEG = 6 * 3600
REINTENTO_ERROR_SEG = 12 * 3600

_cache_lock = threading.Lock()
_auto_thread: Optional[threading.Thread] = None


def auto_habilitado() -> bool:
    por_defecto = "1" if os.environ.get("CAMPO_DATA_DIR", "").strip() else "0"
    return os.environ.get("ARBA_AUTO_PADRON", por_defecto).strip() == "1"


def bases_candidatas(raiz: str) -> List[str]:
    """Bases de empresas donde puede estar cargada la CIT (sin recorrer todo el disco)."""
    rutas: List[str] = []
    for patron in (
        os.path.join(raiz, "*.db"),
        os.path.join(raiz, "bases_empresas", "**", "*.db"),
        os.path.join(raiz, "datos_clientes", "**", "*.db"),
    ):
        rutas.extend(sorted(glob.glob(patron, recursive=True)))
    return [r for r in rutas if not os.path.basename(r).lower().startswith("padron")]


def buscar_credenciales(rutas: Iterable[str]) -> Optional[Tuple[str, str]]:
    """Primer (CUIT, CIT) válido en configuracion_empresa de las bases indicadas, en orden."""
    vistos = set()
    for ruta in rutas:
        if not ruta:
            continue
        clave = os.path.abspath(ruta)
        if clave in vistos or not os.path.isfile(ruta):
            continue
        vistos.add(clave)
        try:
            conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True, timeout=5)
            try:
                filas = conn.execute(
                    "SELECT cuit, cit_arba FROM configuracion_empresa ORDER BY id LIMIT 5;"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        for cuit, cit in filas:
            if credenciales_validas(cuit, cit):
                return limpiar_cuit(cuit), str(cit).strip()
    return None


def _leer_json(ruta: str) -> Dict[str, Any]:
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _escribir_auto(data: Dict[str, Any]) -> None:
    asegurar_carpeta_padrones()
    payload = {**_leer_json(AUTO_STATUS_PATH), **data}
    tmp = AUTO_STATUS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, AUTO_STATUS_PATH)


def estado_auto(rutas: Iterable[str]) -> Dict[str, Any]:
    data = _leer_json(AUTO_STATUS_PATH)
    return {
        "habilitado": auto_habilitado(),
        "credenciales": buscar_credenciales(rutas) is not None,
        "ultima_revision": data.get("ultima_revision"),
        "ultimo_resultado": data.get("ultimo_resultado"),
    }


def revisar_padron(rutas: Iterable[str], forzar: bool = False, automatico: bool = True) -> Dict[str, Any]:
    """Si el padrón no está vigente (o `forzar`), lanza la descarga + importación en segundo plano."""
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    job = get_import_job_status()
    if job.get("status") == "running":
        return {"status": "running", "accepted": False, "message": "Ya hay una carga del padrón en curso."}
    if not forzar:
        est = estado_padron_arba()
        if est.get("status") == "ok":
            _escribir_auto({"ultima_revision": ahora, "ultimo_resultado": est.get("message") or "Padrón vigente."})
            return {"status": "vigente", "accepted": False, "message": est.get("message") or "Padrón vigente."}
        ultimo_intento = _leer_json(AUTO_STATUS_PATH).get("ultimo_intento_ts") or 0
        if automatico and time.time() - float(ultimo_intento) < REINTENTO_ERROR_SEG and job.get("status") == "error":
            return {"status": "espera", "accepted": False, "message": "Se reintenta más tarde (último intento con error)."}
    cred = buscar_credenciales(rutas)
    if not cred:
        msg = "Falta la CIT de ARBA en Configuración de Empresa para bajar el padrón."
        _escribir_auto({"ultima_revision": ahora, "ultimo_resultado": msg})
        return {"status": "error", "accepted": False, "message": msg}
    _escribir_auto({
        "ultima_revision": ahora,
        "ultimo_resultado": "Descarga del padrón iniciada.",
        "ultimo_intento_ts": time.time(),
    })
    return start_import_job(
        auto_detect=False,
        descarga={"cuit": cred[0], "cit": cred[1], "fecha": date.today(), "automatico": automatico},
    )


def iniciar_actualizador(rutas_fn: Callable[[], List[str]], espera_inicial: float = 90) -> bool:
    """Hilo que revisa el padrón al arrancar y cada INTERVALO_REVISION_SEG."""
    global _auto_thread
    if not auto_habilitado() or (_auto_thread is not None and _auto_thread.is_alive()):
        return False

    def _loop():
        time.sleep(espera_inicial)
        while True:
            try:
                revisar_padron(rutas_fn())
            except Exception as e:
                try:
                    _escribir_auto({
                        "ultima_revision": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "ultimo_resultado": f"Error: {e}",
                    })
                except OSError:
                    pass
            time.sleep(INTERVALO_REVISION_SEG)

    _auto_thread = threading.Thread(target=_loop, name="arba-padron-auto", daemon=True)
    _auto_thread.start()
    return True


def _cache_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(CACHE_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS consultas_ws (
            cuit TEXT NOT NULL,
            periodo TEXT NOT NULL,
            ok INTEGER NOT NULL,
            datos TEXT,
            error TEXT,
            consultado_ts REAL NOT NULL,
            PRIMARY KEY (cuit, periodo)
        );
        """
    )
    return conn


def consulta_online(cuit: str, rutas: Iterable[str], fecha: Optional[date] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """(datos, error). Las respuestas buenas valen todo el mes; los errores se reintentan a las 12 h."""
    objetivo = limpiar_cuit(cuit)
    if len(objetivo) != 11:
        return None, "CUIT inválido"
    f = fecha or date.today()
    periodo = f"{f.year:04d}{f.month:02d}"
    with _cache_lock:
        conn = _cache_conn()
        try:
            fila = conn.execute(
                "SELECT ok, datos, error, consultado_ts FROM consultas_ws WHERE cuit=? AND periodo=?;",
                (objetivo, periodo),
            ).fetchone()
        finally:
            conn.close()
    if fila:
        if fila["ok"]:
            return json.loads(fila["datos"]), ""
        if time.time() - float(fila["consultado_ts"]) < REINTENTO_ERROR_SEG:
            return None, fila["error"] or "Sin datos de ARBA"
    cred = buscar_credenciales(rutas)
    if not cred:
        return None, "Falta la CIT de ARBA en Configuración de Empresa"
    try:
        datos, error = consultar_alicuotas(cred[0], cred[1], objetivo, fecha=f), ""
    except ArbaWSError as e:
        datos, error = None, str(e)
    if error and (not error.startswith("ARBA") or "contrase" in error.lower()):
        return None, error
    with _cache_lock:
        conn = _cache_conn()
        try:
            conn.execute(
                """
                INSERT INTO consultas_ws (cuit, periodo, ok, datos, error, consultado_ts)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cuit, periodo) DO UPDATE SET
                    ok=excluded.ok, datos=excluded.datos, error=excluded.error, consultado_ts=excluded.consultado_ts;
                """,
                (objetivo, periodo, 1 if datos else 0, json.dumps(datos) if datos else None, error, time.time()),
            )
            conn.commit()
        finally:
            conn.close()
    return datos, error
