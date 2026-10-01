"""Unificación de nombres de campo y lote en las líneas de costo (margenes_access).

Los nombres vienen escritos a mano desde Access ("LAS ANGELICAS", "la angelicas", "las angerlicas").
Cada cambio queda como regla: se vuelve a aplicar al reimportar desde Access y se puede deshacer.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional
from uuid import uuid4

_VACIAS = {"el", "la", "los", "las", "de", "del", "y", "lote", "lotes"}
_ABREVIATURAS = {"sta": "santa", "sto": "santo", "sn": "san", "gral": "general"}
_ROMANOS = {"i", "ii", "iii", "iv", "v", "vi"}


def asegurar_schema_renombres(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS margenes_renombres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            operacion TEXT NOT NULL,
            tipo TEXT NOT NULL,
            campo TEXT,
            origen TEXT NOT NULL,
            destino TEXT NOT NULL,
            fecha TEXT,
            usuario TEXT,
            deshecho INTEGER DEFAULT 0
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS margenes_renombre_lineas (
            renombre_id INTEGER NOT NULL,
            margen_id INTEGER NOT NULL,
            anterior TEXT,
            PRIMARY KEY (renombre_id, margen_id)
        );
        """
    )


def _texto(txt) -> str:
    s = unicodedata.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)", " ", s)


def _tokens(txt, con_vacias: bool = False) -> List[str]:
    partes = [p for p in re.split(r"[^a-z0-9]+", _texto(txt)) if p]
    return [_ABREVIATURAS.get(p, p) for p in partes if con_vacias or p not in _VACIAS]


def parecidos(a: str, b: str) -> bool:
    """Mismo nombre con otra escritura (mayúsculas, artículos, orden, error de tipeo).
    Agregar una palabra ("el abrojo" / "el abrojo II", "guanaco" / "guanaco p") no cuenta."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    sa, sb = set(ta), set(tb)
    if sa == sb:
        return True
    if sa < sb or sb < sa:
        return False
    numeros = lambda s: {t for t in s if t.isdigit() or t in _ROMANOS}
    if numeros(sa) != numeros(sb):
        return False
    r1 = SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    r2 = SequenceMatcher(None, " ".join(_tokens(a, True)), " ".join(_tokens(b, True))).ratio()
    return max(r1, r2) >= 0.86


def _grupos(nombres: List[str]) -> List[List[str]]:
    padre = {n: n for n in nombres}

    def raiz(x):
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    for i, a in enumerate(nombres):
        for b in nombres[i + 1:]:
            if parecidos(a, b):
                padre[raiz(a)] = raiz(b)
    por_raiz: Dict[str, List[str]] = {}
    for n in nombres:
        por_raiz.setdefault(raiz(n), []).append(n)
    return [g for g in por_raiz.values() if len(g) > 1]


def clave_orden(v: str):
    s = _texto(v).strip()
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", s)]


def _maestro_parecido(nombres: List[str], maestros: List[str]) -> Optional[str]:
    for m in maestros:
        if any(_tokens(m) and set(_tokens(m)) == set(_tokens(n)) for n in nombres):
            return m
    for m in maestros:
        if any(parecidos(m, n) for n in nombres):
            return m
    return None


def _campos_maestro(cur, eid: int) -> List[dict]:
    if not cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='campos_agro';").fetchone():
        return []
    return [
        dict(r) for r in cur.execute(
            "SELECT id, nombre FROM campos_agro WHERE COALESCE(empresa_id,1)=? AND COALESCE(baja,0)=0 AND TRIM(COALESCE(nombre,''))!='';",
            (eid,),
        ).fetchall()
    ]


def _lotes_maestro(cur, eid: int, campo: str) -> List[str]:
    campos = _campos_maestro(cur, eid)
    m = _maestro_parecido([campo], [c["nombre"] for c in campos])
    if not m:
        return []
    cid = next(c["id"] for c in campos if c["nombre"] == m)
    return [
        r[0] for r in cur.execute(
            "SELECT TRIM(nombre) FROM lotes_agro WHERE campo_id=? AND COALESCE(baja,0)=0 AND TRIM(COALESCE(nombre,''))!='';",
            (cid,),
        ).fetchall()
    ]


def revision(cur, eid: int, tipo: str, campo: str = "") -> dict:
    asegurar_schema_renombres(cur)
    if tipo == "lote":
        if not campo:
            raise ValueError("Elegí el campo.")
        col, where, params = "lote", "AND UPPER(TRIM(campo))=UPPER(TRIM(?))", [campo]
        maestros = sorted(set(_lotes_maestro(cur, eid, campo)), key=clave_orden)
    else:
        col, where, params = "campo", "", []
        maestros = sorted({c["nombre"].strip() for c in _campos_maestro(cur, eid)}, key=clave_orden)
    filas = cur.execute(
        f"""
        SELECT TRIM({col}) AS nombre, COUNT(*) AS n,
               ROUND(COALESCE(SUM(costo_ars),0),2) AS ars, ROUND(COALESCE(SUM(costo_usd),0),2) AS usd,
               COUNT(DISTINCT UPPER(TRIM(lote))) AS lotes,
               MIN(NULLIF(TRIM(campania_codigo),'')) AS desde, MAX(NULLIF(TRIM(campania_codigo),'')) AS hasta
        FROM margenes_access
        WHERE empresa_id=? AND TRIM(COALESCE({col},''))!='' {where}
        GROUP BY TRIM({col});
        """,
        [eid] + params,
    ).fetchall()
    nombres = sorted((dict(r) for r in filas), key=lambda r: clave_orden(r["nombre"]))
    por_nombre = {r["nombre"]: r for r in nombres}
    grupos = []
    for g in _grupos([r["nombre"] for r in nombres]):
        g.sort(key=lambda n: -por_nombre[n]["n"])
        sugerido = _maestro_parecido(g, maestros) or g[0]
        grupos.append({"nombres": g, "sugerido": sugerido, "n": sum(por_nombre[n]["n"] for n in g)})
    grupos.sort(key=lambda x: clave_orden(x["sugerido"]))
    vistos: Dict[str, tuple] = {}
    for r in cur.execute(
        """
        SELECT TRIM(campo) AS v, COUNT(*) AS n FROM margenes_access
        WHERE empresa_id=? AND TRIM(COALESCE(campo,''))!='' GROUP BY TRIM(campo);
        """,
        (eid,),
    ).fetchall():
        k = r["v"].upper()
        if k not in vistos or r["n"] > vistos[k][1]:
            vistos[k] = (r["v"], r["n"])
    campos = sorted((v for v, _ in vistos.values()), key=clave_orden)
    return {
        "tipo": tipo, "campo": campo, "nombres": nombres, "grupos": grupos,
        "maestros": maestros, "campos": campos, "historial": historial(cur, eid),
    }


def historial(cur, eid: int, limite: int = 30) -> List[dict]:
    asegurar_schema_renombres(cur)
    _asegurar_eliminadas(cur)
    borrados = cur.execute(
        """
        SELECT operacion, 'eliminar' AS tipo, MIN(COALESCE(motivo,'')) AS motivo, MIN(fecha) AS fecha,
               MIN(COALESCE(usuario,'')) AS usuario, COUNT(*) AS lineas
        FROM margenes_access_eliminadas
        WHERE empresa_id=? AND operacion IS NOT NULL
        GROUP BY operacion
        ORDER BY MIN(fecha) DESC
        LIMIT ?;
        """,
        (eid, limite),
    ).fetchall()
    ops = cur.execute(
        """
        SELECT r.operacion, MIN(r.tipo) AS tipo, MIN(COALESCE(r.campo,'')) AS campo, MIN(r.destino) AS destino,
               GROUP_CONCAT(r.origen, ' | ') AS origenes, MIN(r.fecha) AS fecha, MIN(COALESCE(r.usuario,'')) AS usuario,
               (SELECT COUNT(*) FROM margenes_renombre_lineas l
                 WHERE l.renombre_id IN (SELECT id FROM margenes_renombres x WHERE x.operacion=r.operacion)) AS lineas
        FROM margenes_renombres r
        WHERE r.empresa_id=? AND COALESCE(r.deshecho,0)=0
        GROUP BY r.operacion
        ORDER BY MAX(r.id) DESC
        LIMIT ?;
        """,
        (eid, limite),
    ).fetchall()
    todo = [dict(r) for r in ops] + [dict(r) for r in borrados]
    todo.sort(key=lambda h: h["fecha"] or "", reverse=True)
    return todo[:limite]


def _asegurar_eliminadas(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS margenes_access_eliminadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER,
            margen_id INTEGER,
            id_access INTEGER,
            fecha TEXT,
            usuario TEXT,
            datos TEXT
        );
        """
    )
    cols = {r[1] for r in cur.execute("PRAGMA table_info(margenes_access_eliminadas);").fetchall()}
    for col in ("operacion", "motivo"):
        if col not in cols:
            cur.execute(f"ALTER TABLE margenes_access_eliminadas ADD COLUMN {col} TEXT;")


def eliminar(cur, eid: int, tipo: str, nombres: List[str], campo: str = "", usuario: str = "") -> dict:
    """Borra las líneas de costo con esos nombres de campo (o de lote dentro de un campo).
    Guarda copia (la reimportación desde Access no las vuelve a traer) y no toca líneas de OT emitidas en Campo+."""
    import json

    _asegurar_eliminadas(cur)
    asegurar_schema_renombres(cur)
    tipo = "lote" if tipo == "lote" else "campo"
    nombres = sorted({(n or "").strip() for n in nombres if (n or "").strip()})
    if not nombres:
        raise ValueError("Marcá los nombres a eliminar.")
    if tipo == "lote" and not (campo or "").strip():
        raise ValueError("Elegí el campo de los lotes.")
    col = "lote" if tipo == "lote" else "campo"
    where = f"empresa_id=? AND TRIM({col}) IN ({','.join('?' * len(nombres))})"
    params: list = [eid] + nombres
    if tipo == "lote":
        fam = _familia_campo(cur, eid, campo)
        where += f" AND UPPER(TRIM(campo)) IN ({','.join('?' * len(fam))})"
        params += fam
    cols = {r[1] for r in cur.execute("PRAGMA table_info(margenes_access);").fetchall()}
    operacion = datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:6]
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    motivo = ("Lotes de " + campo.strip() + ": " if tipo == "lote" else "Campos: ") + " | ".join(nombres)
    eliminadas = con_ot = 0
    for r in cur.execute(f"SELECT * FROM margenes_access WHERE {where};", params).fetchall():
        fila = dict(r)
        if "ot_id" in cols and fila.get("ot_id"):
            con_ot += 1
            continue
        cur.execute(
            """
            INSERT INTO margenes_access_eliminadas (empresa_id, margen_id, id_access, fecha, usuario, datos, operacion, motivo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (eid, fila["id"], fila.get("id_access"), ahora, usuario, json.dumps(fila, ensure_ascii=False, default=str), operacion, motivo),
        )
        cur.execute("DELETE FROM margenes_access WHERE id=?;", (fila["id"],))
        eliminadas += 1
    return {"operacion": operacion, "eliminadas": eliminadas, "con_ot": con_ot, "nombres": nombres}


def restaurar(cur, eid: int, operacion: str) -> dict:
    import json

    _asegurar_eliminadas(cur)
    filas = cur.execute(
        "SELECT id, datos FROM margenes_access_eliminadas WHERE empresa_id=? AND operacion=?;", (eid, operacion)
    ).fetchall()
    if not filas:
        raise ValueError("Esa eliminación no existe o ya se restauró.")
    cols = {r[1] for r in cur.execute("PRAGMA table_info(margenes_access);").fetchall()}
    n = 0
    for f in filas:
        datos = {k: v for k, v in json.loads(f["datos"] or "{}").items() if k in cols}
        if datos.get("id") and cur.execute("SELECT 1 FROM margenes_access WHERE id=?;", (datos["id"],)).fetchone():
            datos.pop("id")
        if datos.get("id_access") and cur.execute(
            "SELECT 1 FROM margenes_access WHERE empresa_id=? AND id_access=?;", (eid, datos["id_access"])
        ).fetchone():
            cur.execute("DELETE FROM margenes_access_eliminadas WHERE id=?;", (f["id"],))
            continue
        claves = list(datos)
        cur.execute(f"INSERT INTO margenes_access ({', '.join(claves)}) VALUES ({','.join('?' * len(claves))});",
                    [datos[k] for k in claves])
        cur.execute("DELETE FROM margenes_access_eliminadas WHERE id=?;", (f["id"],))
        n += 1
    return {"restauradas": n}


def _familia_campo(cur, eid: int, campo: str) -> List[str]:
    """El campo de una regla de lote y los nombres que tuvo o tiene por renombres de campo."""
    fam = {campo.strip().upper()}
    for _ in range(5):
        antes = len(fam)
        marcas = ",".join("?" * len(fam))
        for r in cur.execute(
            f"""
            SELECT UPPER(TRIM(origen)), UPPER(TRIM(destino)) FROM margenes_renombres
            WHERE empresa_id=? AND tipo='campo'
              AND (UPPER(TRIM(destino)) IN ({marcas}) OR UPPER(TRIM(origen)) IN ({marcas}));
            """,
            [eid] + list(fam) * 2,
        ).fetchall():
            fam.update(r)
        if len(fam) == antes:
            break
    return sorted(fam)


def _aplicar_regla(cur, eid: int, regla) -> int:
    col = "lote" if regla["tipo"] == "lote" else "campo"
    where = f"empresa_id=? AND TRIM({col})=?"
    params: list = [eid, regla["origen"]]
    if col == "lote":
        fam = _familia_campo(cur, eid, regla["campo"] or "")
        where += f" AND UPPER(TRIM(campo)) IN ({','.join('?' * len(fam))})"
        params += fam
    cur.execute(
        f"""
        INSERT OR IGNORE INTO margenes_renombre_lineas (renombre_id, margen_id, anterior)
        SELECT ?, id, {col} FROM margenes_access WHERE {where};
        """,
        [regla["id"]] + params,
    )
    cur.execute(f"UPDATE margenes_access SET {col}=? WHERE {where};", [regla["destino"]] + params)
    return cur.rowcount or 0


def aplicar_renombres(cur, eid: int) -> int:
    """Vuelve a aplicar todas las reglas vigentes, en el orden en que se crearon (tras reimportar desde Access)."""
    asegurar_schema_renombres(cur)
    total = 0
    for regla in cur.execute(
        "SELECT * FROM margenes_renombres WHERE empresa_id=? AND COALESCE(deshecho,0)=0 ORDER BY id;", (eid,)
    ).fetchall():
        total += _aplicar_regla(cur, eid, dict(regla))
    return total


def renombrar(cur, eid: int, tipo: str, origenes: List[str], destino: str, campo: str = "", usuario: str = "") -> dict:
    asegurar_schema_renombres(cur)
    tipo = "lote" if tipo == "lote" else "campo"
    destino = re.sub(r"\s+", " ", (destino or "").strip())
    if not destino:
        raise ValueError("Escribí el nombre final.")
    if tipo == "lote" and not (campo or "").strip():
        raise ValueError("Elegí el campo de los lotes.")
    origenes = sorted({(o or "").strip() for o in origenes if (o or "").strip()} - {destino})
    if not origenes:
        raise ValueError("No hay nombres distintos del final para cambiar.")
    operacion = datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:6]
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cambiadas = 0
    for o in origenes:
        cur.execute(
            """
            INSERT INTO margenes_renombres (empresa_id, operacion, tipo, campo, origen, destino, fecha, usuario)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (eid, operacion, tipo, (campo or "").strip() if tipo == "lote" else None, o, destino, ahora, usuario),
        )
        regla = dict(cur.execute("SELECT * FROM margenes_renombres WHERE id=?;", (cur.lastrowid,)).fetchone())
        cambiadas += _aplicar_regla(cur, eid, regla)
    return {"operacion": operacion, "lineas": cambiadas, "origenes": origenes, "destino": destino}


def deshacer(cur, eid: int, operacion: str) -> dict:
    asegurar_schema_renombres(cur)
    reglas = cur.execute(
        "SELECT * FROM margenes_renombres WHERE empresa_id=? AND operacion=? AND COALESCE(deshecho,0)=0 ORDER BY id DESC;",
        (eid, operacion),
    ).fetchall()
    if not reglas:
        raise ValueError("Ese cambio no existe o ya se deshizo.")
    restauradas = 0
    total = 0
    for r in reglas:
        col = "lote" if r["tipo"] == "lote" else "campo"
        cur.execute(
            f"""
            UPDATE margenes_access SET {col} = (
                SELECT l.anterior FROM margenes_renombre_lineas l WHERE l.renombre_id=? AND l.margen_id=margenes_access.id)
            WHERE empresa_id=? AND TRIM({col})=?
              AND id IN (SELECT margen_id FROM margenes_renombre_lineas WHERE renombre_id=?);
            """,
            (r["id"], eid, r["destino"], r["id"]),
        )
        restauradas += cur.rowcount or 0
        total += cur.execute("SELECT COUNT(*) FROM margenes_renombre_lineas WHERE renombre_id=?;", (r["id"],)).fetchone()[0]
        cur.execute("DELETE FROM margenes_renombre_lineas WHERE renombre_id=?;", (r["id"],))
        cur.execute("UPDATE margenes_renombres SET deshecho=1 WHERE id=?;", (r["id"],))
    # Las que no vuelven es porque después se les cambió el nombre otra vez.
    return {"restauradas": restauradas, "no_restauradas": max(0, total - restauradas)}
