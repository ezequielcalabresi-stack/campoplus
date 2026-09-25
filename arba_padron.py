# -*- coding: utf-8 -*-
"""
Carga mensual de padrones ARBA (Retenciones + Percepciones RGS).
Archivos típicos: PadronRGSRetMMYYYY.TXT / PadronRGSPerMMYYYY.TXT
Formato línea: R|P;pub;desde;hasta;CUIT;...;alicuota;grupo;
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import threading
import time
import zipfile
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.environ.get("CAMPO_DATA_DIR", "").strip()
_PADRON_ROOT = _DATA_DIR if _DATA_DIR else BASE_DIR
PADRON_DB_PATH = os.path.join(_PADRON_ROOT, "padron.db")
PADRON_TMP_PATH = os.path.join(_PADRON_ROOT, "padron_tmp.db")
PADRONES_DIR = os.path.join(_PADRON_ROOT, "padrones_arba")
JOB_STATUS_PATH = os.path.join(PADRONES_DIR, "_import_job.json")

ProgressCb = Optional[Callable[[Dict[str, Any]], None]]

_job_lock = threading.Lock()
_job_thread: Optional[threading.Thread] = None


def _parse_fecha_arba(s: str) -> str:
    """ddmmyyyy → YYYY-MM-DD"""
    s = (s or "").strip()
    if len(s) != 8 or not s.isdigit():
        return ""
    return f"{s[4:8]}-{s[2:4]}-{s[0:2]}"


def _parse_alicuota(s: str) -> float:
    try:
        return float(str(s or "0").strip().replace(",", "."))
    except ValueError:
        return 0.0


def _fmt_alicuota_txt(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def asegurar_carpeta_padrones() -> str:
    os.makedirs(PADRONES_DIR, exist_ok=True)
    return PADRONES_DIR


def detectar_archivos_padron(extra_dirs: Optional[List[str]] = None) -> Dict[str, Optional[str]]:
    """Busca Ret/Per más recientes en padrones_arba/ y raíz del proyecto."""
    dirs = [PADRONES_DIR, BASE_DIR]
    if extra_dirs:
        dirs.extend(extra_dirs)
    rets: List[str] = []
    pers: List[str] = []
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        rets.extend(glob.glob(os.path.join(d, "PadronRGSRet*.TXT")))
        rets.extend(glob.glob(os.path.join(d, "PadronRGSRet*.txt")))
        pers.extend(glob.glob(os.path.join(d, "PadronRGSPer*.TXT")))
        pers.extend(glob.glob(os.path.join(d, "PadronRGSPer*.txt")))
        # ZIPs
        for z in glob.glob(os.path.join(d, "*.zip")) + glob.glob(os.path.join(d, "*.ZIP")):
            name = os.path.basename(z).lower()
            if "ret" in name or "perc" in name or "padron" in name or "rgs" in name:
                # se descomprimen al importar
                pass
    rets = sorted(set(rets), key=lambda p: os.path.getmtime(p), reverse=True)
    pers = sorted(set(pers), key=lambda p: os.path.getmtime(p), reverse=True)
    return {
        "retenciones": rets[0] if rets else None,
        "percepciones": pers[0] if pers else None,
        "candidatos_ret": rets[:5],
        "candidatos_per": pers[:5],
    }


def _leer_meta_desde_primera_linea(ruta: str) -> Dict[str, str]:
    with open(ruta, "r", encoding="latin-1", errors="replace") as f:
        for linea in f:
            p = linea.strip().split(";")
            if len(p) >= 5 and p[0] in ("R", "P"):
                return {
                    "tipo": p[0],
                    "fecha_publicacion": _parse_fecha_arba(p[1]),
                    "vigencia_desde": _parse_fecha_arba(p[2]),
                    "vigencia_hasta": _parse_fecha_arba(p[3]),
                }
    return {}


def _crear_schema_padron(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS padron;")
    cur.execute("DROP TABLE IF EXISTS padron_meta;")
    cur.execute(
        """
        CREATE TABLE padron (
            cuit TEXT PRIMARY KEY,
            estado TEXT,
            ret_alicuota TEXT,
            ret_grupo TEXT,
            perc_alicuota TEXT,
            perc_grupo TEXT,
            ret_alicuota_num REAL DEFAULT 0,
            perc_alicuota_num REAL DEFAULT 0
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE padron_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            vigencia_desde TEXT,
            vigencia_hasta TEXT,
            fecha_publicacion TEXT,
            archivo_ret TEXT,
            archivo_per TEXT,
            registros INTEGER DEFAULT 0,
            fecha_carga TEXT,
            periodo_label TEXT
        );
        """
    )
    conn.commit()


def _cargar_archivo(
    conn: sqlite3.Connection,
    ruta: str,
    tipo: str,
    batch_size: int = 40000,
    on_progress: ProgressCb = None,
    fase: str = "",
) -> int:
    """tipo='R' o 'P'. Inserta/actualiza en lotes."""
    cur = conn.cursor()
    batch: List[Tuple] = []
    count = 0
    tipo = tipo.upper()
    t0 = time.time()

    def flush():
        nonlocal batch, count
        if not batch:
            return
        if tipo == "R":
            cur.executemany(
                """
                INSERT INTO padron (cuit, estado, ret_alicuota, ret_grupo, perc_alicuota, perc_grupo, ret_alicuota_num, perc_alicuota_num)
                VALUES (?, 'Oficial ARBA', ?, ?, '0,00', '00', ?, 0)
                ON CONFLICT(cuit) DO UPDATE SET
                    ret_alicuota = excluded.ret_alicuota,
                    ret_grupo = excluded.ret_grupo,
                    ret_alicuota_num = excluded.ret_alicuota_num,
                    estado = 'Oficial ARBA';
                """,
                batch,
            )
        else:
            cur.executemany(
                """
                INSERT INTO padron (cuit, estado, ret_alicuota, ret_grupo, perc_alicuota, perc_grupo, ret_alicuota_num, perc_alicuota_num)
                VALUES (?, 'Oficial ARBA', '0,00', '00', ?, ?, 0, ?)
                ON CONFLICT(cuit) DO UPDATE SET
                    perc_alicuota = excluded.perc_alicuota,
                    perc_grupo = excluded.perc_grupo,
                    perc_alicuota_num = excluded.perc_alicuota_num,
                    estado = 'Oficial ARBA';
                """,
                batch,
            )
        count += len(batch)
        batch = []
        if on_progress:
            on_progress({
                "fase": fase or ("Retenciones" if tipo == "R" else "Percepciones"),
                "procesados": count,
                "elapsed_sec": round(time.time() - t0, 1),
            })

    with open(ruta, "r", encoding="latin-1", errors="replace") as f:
        for linea in f:
            partes = linea.strip().split(";")
            if len(partes) < 10:
                continue
            if partes[0].upper() != tipo:
                continue
            cuit = re.sub(r"\D", "", partes[4] or "")
            if len(cuit) < 10:
                continue
            ali = _parse_alicuota(partes[8])
            grupo = (partes[9] or "00").strip().zfill(2)[:2]
            ali_txt = _fmt_alicuota_txt(ali)
            if tipo == "R":
                batch.append((cuit, ali_txt, grupo, ali))
            else:
                batch.append((cuit, ali_txt, grupo, ali))
            if len(batch) >= batch_size:
                flush()
                conn.commit()
    flush()
    conn.commit()
    return count


def _extraer_zip_si_hace_falta(ruta: str) -> List[str]:
    if not ruta.lower().endswith(".zip"):
        return [ruta]
    asegurar_carpeta_padrones()
    out = []
    with zipfile.ZipFile(ruta, "r") as zf:
        for name in zf.namelist():
            base = os.path.basename(name)
            if not base:
                continue
            low = base.lower()
            if not (low.endswith(".txt") and ("padron" in low or "rgs" in low or "ret" in low or "per" in low)):
                continue
            dest = os.path.join(PADRONES_DIR, base)
            with zf.open(name) as src, open(dest, "wb") as dst:
                dst.write(src.read())
            out.append(dest)
    return out


def importar_padrones_arba(
    ruta_ret: Optional[str] = None,
    ruta_per: Optional[str] = None,
    auto_detect: bool = True,
    on_progress: ProgressCb = None,
) -> Dict[str, Any]:
    """Importa Ret+Per a padron.db (reemplazo atómico)."""
    def prog(msg: str, **extra):
        if on_progress:
            payload = {"message": msg, **extra}
            on_progress(payload)

    asegurar_carpeta_padrones()
    if auto_detect and (not ruta_ret or not ruta_per):
        det = detectar_archivos_padron()
        ruta_ret = ruta_ret or det.get("retenciones")
        ruta_per = ruta_per or det.get("percepciones")

    if ruta_ret:
        extra = _extraer_zip_si_hace_falta(ruta_ret)
        for p in extra:
            if "ret" in os.path.basename(p).lower():
                ruta_ret = p
    if ruta_per:
        extra = _extraer_zip_si_hace_falta(ruta_per)
        for p in extra:
            if "per" in os.path.basename(p).lower():
                ruta_per = p

    if not ruta_ret or not os.path.exists(ruta_ret):
        return {"status": "error", "message": "No se encontró archivo de Retenciones (PadronRGSRet*.TXT)."}
    if not ruta_per or not os.path.exists(ruta_per):
        return {"status": "error", "message": "No se encontró archivo de Percepciones (PadronRGSPer*.TXT)."}

    meta = _leer_meta_desde_primera_linea(ruta_ret) or _leer_meta_desde_primera_linea(ruta_per)
    t0 = datetime.now()
    prog("Preparando base temporal…", fase="inicio", pct=1)

    if os.path.exists(PADRON_TMP_PATH):
        try:
            os.remove(PADRON_TMP_PATH)
        except OSError:
            pass

    conn = sqlite3.connect(PADRON_TMP_PATH)
    conn.execute("PRAGMA journal_mode=OFF;")
    conn.execute("PRAGMA synchronous=OFF;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    _crear_schema_padron(conn)

    def cb_ret(info):
        prog(
            f"Retenciones: {info.get('procesados', 0):,} líneas…".replace(",", "."),
            fase="retenciones",
            pct=min(45, 5 + int((info.get("procesados") or 0) / 60000)),
            procesados=info.get("procesados"),
            elapsed_sec=info.get("elapsed_sec"),
        )

    def cb_per(info):
        prog(
            f"Percepciones: {info.get('procesados', 0):,} líneas…".replace(",", "."),
            fase="percepciones",
            pct=min(90, 50 + int((info.get("procesados") or 0) / 80000)),
            procesados=info.get("procesados"),
            elapsed_sec=info.get("elapsed_sec"),
        )

    prog("Cargando Retenciones…", fase="retenciones", pct=5)
    n_ret = _cargar_archivo(conn, ruta_ret, "R", on_progress=cb_ret, fase="retenciones")
    prog("Cargando Percepciones…", fase="percepciones", pct=50)
    n_per = _cargar_archivo(conn, ruta_per, "P", on_progress=cb_per, fase="percepciones")
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM padron;")
    total = int(cur.fetchone()[0] or 0)

    desde = meta.get("vigencia_desde") or ""
    hasta = meta.get("vigencia_hasta") or ""
    pub = meta.get("fecha_publicacion") or ""
    label = ""
    if desde and hasta:
        try:
            d = datetime.strptime(desde, "%Y-%m-%d")
            label = d.strftime("%m/%Y")
        except ValueError:
            label = f"{desde} → {hasta}"

    prog("Finalizando y activando padrón…", fase="cierre", pct=95)
    cur.execute(
        """
        INSERT INTO padron_meta (
            id, vigencia_desde, vigencia_hasta, fecha_publicacion,
            archivo_ret, archivo_per, registros, fecha_carga, periodo_label
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            desde, hasta, pub,
            os.path.basename(ruta_ret), os.path.basename(ruta_per),
            total, t0.strftime("%Y-%m-%d %H:%M:%S"), label,
        ),
    )
    conn.commit()
    conn.close()

    # Reemplazo atómico
    if os.path.exists(PADRON_DB_PATH):
        bak = PADRON_DB_PATH + ".bak"
        try:
            if os.path.exists(bak):
                os.remove(bak)
            os.replace(PADRON_DB_PATH, bak)
        except OSError:
            try:
                os.remove(PADRON_DB_PATH)
            except OSError:
                pass
    os.replace(PADRON_TMP_PATH, PADRON_DB_PATH)

    elapsed = (datetime.now() - t0).total_seconds()
    result = {
        "status": "success",
        "message": f"Padrón ARBA cargado ({label or 'vigente'}).",
        "registros": total,
        "lineas_ret": n_ret,
        "lineas_per": n_per,
        "vigencia_desde": desde,
        "vigencia_hasta": hasta,
        "fecha_publicacion": pub,
        "periodo_label": label,
        "archivo_ret": os.path.basename(ruta_ret),
        "archivo_per": os.path.basename(ruta_per),
        "segundos": round(elapsed, 1),
    }
    prog(result["message"], fase="listo", pct=100, **{k: v for k, v in result.items() if k != "message"})
    return result


def estado_padron_arba() -> Dict[str, Any]:
    if not os.path.exists(PADRON_DB_PATH):
        return {
            "status": "empty",
            "cargado": False,
            "message": "Aún no hay padrón cargado. Importá Ret + Perc del mes.",
            "detectados": detectar_archivos_padron(),
        }
    conn = sqlite3.connect(PADRON_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='padron_meta';")
        if not cur.fetchone():
            cur.execute("SELECT COUNT(*) AS n FROM padron;")
            n = int(cur.fetchone()["n"] or 0)
            conn.close()
            return {
                "status": "ok_legacy",
                "cargado": n > 0,
                "registros": n,
                "message": "Padrón legacy sin metadatos de vigencia. Reimportá el TXT del mes.",
                "detectados": detectar_archivos_padron(),
            }
        cur.execute("SELECT * FROM padron_meta WHERE id=1;")
        meta = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n FROM padron;")
        n = int(cur.fetchone()["n"] or 0)
        conn.close()
        meta_d = dict(meta) if meta else {}
        vigente = True
        hoy = datetime.now().strftime("%Y-%m-%d")
        hasta = meta_d.get("vigencia_hasta") or ""
        if hasta and hoy > hasta:
            vigente = False
        return {
            "status": "ok" if vigente else "vencido",
            "cargado": n > 0,
            "vigente": vigente,
            "registros": n,
            **meta_d,
            "detectados": detectar_archivos_padron(),
            "message": (
                f"Padrón {meta_d.get('periodo_label') or ''} vigente hasta {hasta}."
                if vigente
                else f"Padrón vencido (hasta {hasta}). Cargá el del mes actual."
            ),
        }
    except Exception as e:
        conn.close()
        return {"status": "error", "cargado": False, "message": str(e)}


def consultar_cuit_padron(cuit: str) -> Dict[str, Any]:
    cuit_clean = re.sub(r"\D", "", cuit or "")
    if len(cuit_clean) < 10:
        return {"encontrado": False, "cuit": cuit, "detail": "CUIT inválido"}
    if not os.path.exists(PADRON_DB_PATH):
        return {
            "encontrado": False,
            "cuit": cuit_clean,
            "alicuota_retencion": 3.0,
            "alicuota_percepcion": 1.75,
            "ret_grupo": "00",
            "perc_grupo": "00",
            "estado": "Sin padrón cargado — alícuotas por defecto",
        }
    conn = sqlite3.connect(PADRON_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # Compat: tablas viejas sin *_num
    cur.execute("PRAGMA table_info(padron);")
    cols = {r[1] for r in cur.fetchall()}
    cur.execute("SELECT * FROM padron WHERE cuit = ?;", (cuit_clean,))
    row = cur.fetchone()
    meta = None
    try:
        cur.execute("SELECT vigencia_desde, vigencia_hasta, periodo_label FROM padron_meta WHERE id=1;")
        meta = cur.fetchone()
    except Exception:
        pass
    conn.close()
    if not row:
        return {
            "encontrado": False,
            "cuit": cuit_clean,
            "razon_social": "",
            "alicuota_retencion": 0.0,
            "alicuota_percepcion": 0.0,
            "ret_grupo": "00",
            "perc_grupo": "00",
            "estado": "No figura en padrón (régimen general / sin alícuota diferencial)",
            "periodo": dict(meta) if meta else {},
        }
    d = dict(row)
    ret_num = d.get("ret_alicuota_num")
    perc_num = d.get("perc_alicuota_num")
    if ret_num is None:
        ret_num = _parse_alicuota(d.get("ret_alicuota") or "0")
    if perc_num is None:
        perc_num = _parse_alicuota(d.get("perc_alicuota") or "0")
    return {
        "encontrado": True,
        "cuit": cuit_clean,
        "razon_social": "",
        "alicuota_retencion": float(ret_num or 0),
        "alicuota_percepcion": float(perc_num or 0),
        "ret_alicuota": d.get("ret_alicuota") or _fmt_alicuota_txt(ret_num or 0),
        "perc_alicuota": d.get("perc_alicuota") or _fmt_alicuota_txt(perc_num or 0),
        "ret_grupo": d.get("ret_grupo") or "00",
        "perc_grupo": d.get("perc_grupo") or "00",
        "estado": d.get("estado") or "Oficial ARBA",
        "periodo": dict(meta) if meta else {},
    }


def _escribir_job(data: Dict[str, Any]) -> None:
    asegurar_carpeta_padrones()
    payload = dict(data)
    payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp = JOB_STATUS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, JOB_STATUS_PATH)


def get_import_job_status() -> Dict[str, Any]:
    """Estado del job de importación (running / done / error / idle)."""
    if not os.path.exists(JOB_STATUS_PATH):
        return {"status": "idle", "message": "Sin importación en curso."}
    try:
        with open(JOB_STATUS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"status": "idle", "message": "Sin importación en curso."}
        # Reloj servidor por si el cliente se fue y volvió
        started = data.get("started_at_ts")
        if started and data.get("status") == "running":
            data["elapsed_sec"] = round(time.time() - float(started), 1)
        return data
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {"status": "idle", "message": "Sin importación en curso."}


def start_import_job(
    ruta_ret: Optional[str] = None,
    ruta_per: Optional[str] = None,
    auto_detect: bool = True,
) -> Dict[str, Any]:
    """
    Lanza la importación en un hilo de fondo.
    El usuario puede salir de la pantalla; al volver, GET /job muestra el estado.
    """
    global _job_thread
    with _job_lock:
        if _job_thread is not None and _job_thread.is_alive():
            return {
                "status": "running",
                "accepted": False,
                "message": "Ya hay una importación en curso. Podés seguir usándola en segundo plano.",
                **{k: v for k, v in get_import_job_status().items() if k not in ("status", "message")},
            }

        started_ts = time.time()
        job0 = {
            "status": "running",
            "accepted": True,
            "message": "Importación iniciada en segundo plano…",
            "fase": "inicio",
            "pct": 0,
            "procesados": 0,
            "elapsed_sec": 0,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "started_at_ts": started_ts,
            "ruta_ret": os.path.basename(ruta_ret) if ruta_ret else None,
            "ruta_per": os.path.basename(ruta_per) if ruta_per else None,
        }
        _escribir_job(job0)

        def _run():
            def on_progress(info: Dict[str, Any]):
                cur = get_import_job_status()
                if cur.get("status") not in ("running", "idle"):
                    # No pisar done/error con updates tardíos
                    if cur.get("status") in ("done", "error"):
                        return
                patch = {
                    "status": "running",
                    "accepted": True,
                    "message": info.get("message") or cur.get("message") or "Importando…",
                    "fase": info.get("fase") or cur.get("fase"),
                    "pct": info.get("pct", cur.get("pct") or 0),
                    "procesados": info.get("procesados", cur.get("procesados") or 0),
                    "elapsed_sec": info.get("elapsed_sec")
                    if info.get("elapsed_sec") is not None
                    else round(time.time() - started_ts, 1),
                    "started_at": job0["started_at"],
                    "started_at_ts": started_ts,
                    "ruta_ret": job0.get("ruta_ret"),
                    "ruta_per": job0.get("ruta_per"),
                }
                # Mantener campos de resultado si vienen en progress final
                for k in (
                    "registros", "lineas_ret", "lineas_per", "vigencia_desde",
                    "vigencia_hasta", "periodo_label", "archivo_ret", "archivo_per", "segundos",
                ):
                    if k in info:
                        patch[k] = info[k]
                _escribir_job(patch)

            try:
                result = importar_padrones_arba(
                    ruta_ret=ruta_ret,
                    ruta_per=ruta_per,
                    auto_detect=auto_detect,
                    on_progress=on_progress,
                )
                if result.get("status") == "error":
                    _escribir_job({
                        "status": "error",
                        "accepted": True,
                        "message": result.get("message") or "Error al importar padrón",
                        "fase": "error",
                        "pct": 0,
                        "elapsed_sec": round(time.time() - started_ts, 1),
                        "started_at": job0["started_at"],
                        "started_at_ts": started_ts,
                    })
                else:
                    _escribir_job({
                        "status": "done",
                        "accepted": True,
                        "message": result.get("message") or "Listo",
                        "fase": "listo",
                        "pct": 100,
                        "elapsed_sec": result.get("segundos") or round(time.time() - started_ts, 1),
                        "started_at": job0["started_at"],
                        "started_at_ts": started_ts,
                        **{k: v for k, v in result.items() if k not in ("status", "message")},
                    })
            except Exception as e:
                _escribir_job({
                    "status": "error",
                    "accepted": True,
                    "message": str(e),
                    "fase": "error",
                    "pct": 0,
                    "elapsed_sec": round(time.time() - started_ts, 1),
                    "started_at": job0["started_at"],
                    "started_at_ts": started_ts,
                })

        _job_thread = threading.Thread(target=_run, name="arba-padron-import", daemon=True)
        _job_thread.start()
        return dict(job0)


def liberar_temporal_padron() -> None:
    """Borra la base a medio armar. Si queda, llena el disco y el sitio no arranca."""
    for path in (PADRON_TMP_PATH, PADRON_TMP_PATH + "-journal", PADRON_TMP_PATH + "-wal", PADRON_TMP_PATH + "-shm"):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


liberar_temporal_padron()


if __name__ == "__main__":
    print(detectar_archivos_padron())
    print(estado_padron_arba())
