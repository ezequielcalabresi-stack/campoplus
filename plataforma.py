# -*- coding: utf-8 -*-
"""
CAmpo+ — Control de clientes (grupos económicos).

Cada cuenta tiene su propia base SQLite, cupo de empresas y cupo de usuarios.
El administrador de la plataforma autoriza lo que exceda lo incluido:
  - 2 empresas incluidas
  - 3 usuarios incluidos
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextvars import ContextVar
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

EMPRESAS_INCLUIDAS = 2
USUARIOS_INCLUIDOS = 3

db_ctx: ContextVar[Optional[str]] = ContextVar("campo_db_path", default=None)
cuenta_ctx: ContextVar[Optional[int]] = ContextVar("campo_cuenta_id", default=None)

_TABLAS_REF = (
    "ref_provincias",
    "ref_tipos_documento",
    "ref_condiciones_iva",
    "ref_regimenes_ganancias",
)


def db_path_efectivo(default_path: str) -> str:
    return db_ctx.get() or default_path


def cuenta_id_actual() -> Optional[int]:
    return cuenta_ctx.get()


def master_connect(master_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(master_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _ensure_col(cursor, table: str, col: str, decl: str) -> None:
    try:
        cols = {r[1] for r in cursor.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return
    if cols and col not in cols:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl};")


def init_plataforma_schema(cursor: sqlite3.Cursor, master_path: str) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cuentas_cliente (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            contacto TEXT DEFAULT '',
            activo INTEGER DEFAULT 1,
            empresas_incluidas INTEGER DEFAULT 2,
            empresas_extra INTEGER DEFAULT 0,
            usuarios_incluidos INTEGER DEFAULT 3,
            usuarios_extra INTEGER DEFAULT 0,
            precio_empresa_extra REAL DEFAULT 0,
            precio_usuario_extra REAL DEFAULT 0,
            notas TEXT DEFAULT '',
            db_path TEXT NOT NULL,
            created_at TEXT
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS solicitudes_cupo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cuenta_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            detalle TEXT DEFAULT '',
            estado TEXT DEFAULT 'pendiente',
            created_at TEXT,
            resuelto_en TEXT
        );
        """
    )
    _ensure_col(cursor, "empresas", "cuenta_id", "INTEGER")
    _ensure_col(cursor, "usuarios_sistema", "cuenta_id", "INTEGER")

    cursor.execute("SELECT COUNT(*) FROM cuentas_cliente;")
    if cursor.fetchone()[0] == 0:
        ahora = datetime.now().isoformat(timespec="seconds")
        cursor.execute(
            """
            INSERT INTO cuentas_cliente (
                nombre, slug, contacto, activo,
                empresas_incluidas, empresas_extra,
                usuarios_incluidos, usuarios_extra,
                precio_empresa_extra, precio_usuario_extra,
                notas, db_path, created_at
            ) VALUES (?, ?, ?, 1, ?, 0, ?, 0, 0, 0, ?, ?, ?);
            """,
            (
                "Silo Chico",
                "silochico",
                "",
                EMPRESAS_INCLUIDAS,
                USUARIOS_INCLUIDOS,
                "Grupo inicial. Base histórica de Campo+.",
                master_path,
                ahora,
            ),
        )
    cursor.execute(
        "UPDATE empresas SET cuenta_id = 1 WHERE cuenta_id IS NULL;"
    )
    cursor.execute(
        "UPDATE usuarios_sistema SET cuenta_id = 1 WHERE cuenta_id IS NULL AND COALESCE(es_superadmin, 0) = 0;"
    )


def _token_from_headers(headers: dict) -> str:
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (headers.get("x-session-token") or "").strip()


def _headers_scope(scope) -> dict:
    out = {}
    for key, val in scope.get("headers") or []:
        out[key.decode("latin1").lower()] = val.decode("latin1")
    return out


def _ruta_base_cuenta(master_path: str, stored: Optional[str]) -> str:
    """Si la ruta se guardó en Windows y acá no existe, la cuenta maestra usa el archivo del servidor."""
    path = (stored or "").strip() or master_path
    if os.path.isfile(path):
        return path
    normal = path.replace("\\", "/")
    if "datos_clientes" not in normal:
        return master_path
    return path


def _nombre_empresa(razon: str) -> str:
    t = (razon or "").upper()
    for x in (".", ",", "-", "S.A.A. Y C.", "S.A.A.", "S.A.", "S.R.L.", "SRL"):
        t = t.replace(x, " ")
    return " ".join(t.split())


def buscar_empresa_existente(master_path: str, cuit: str, razon: str) -> Optional[str]:
    """Si el CUIT o la razón social ya están en alguna cuenta, devuelve el aviso."""
    objetivo = _nombre_empresa(razon)
    cuit_d = "".join(c for c in (cuit or "") if c.isdigit())
    conn = master_connect(master_path)
    cur = conn.cursor()
    cur.execute("SELECT nombre, db_path FROM cuentas_cliente;")
    cuentas = [dict(r) for r in cur.fetchall()]
    conn.close()
    if not cuentas:
        cuentas = [{"nombre": "Silo Chico", "db_path": master_path}]
    vistos = set()
    for cta in cuentas:
        path = _ruta_base_cuenta(master_path, cta.get("db_path"))
        if not path or not os.path.isfile(path) or path in vistos:
            continue
        vistos.add(path)
        try:
            base = sqlite3.connect(path)
            base.row_factory = sqlite3.Row
            rows = base.execute(
                "SELECT razon_social, cuit FROM empresas;"
            ).fetchall()
            base.close()
        except sqlite3.Error:
            continue
        for row in rows:
            otro_cuit = "".join(c for c in (row["cuit"] or "") if c.isdigit())
            mismo_cuit = len(cuit_d) == 11 and otro_cuit == cuit_d
            mismo_nombre = objetivo and _nombre_empresa(row["razon_social"]) == objetivo
            if mismo_cuit or mismo_nombre:
                return (
                    f"{row['razon_social']} ya existe en la cuenta {cta.get('nombre') or 'Silo Chico'} "
                    f"(CUIT {row['cuit']}). No se creó una empresa nueva."
                )
    return None


def resolver_contexto(master_path: str, token: str, cuenta_header: str):
    """Devuelve (db_path, cuenta_id, bloquear)."""
    if not token:
        return master_path, None, False
    conn = master_connect(master_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.empresa_id, u.es_superadmin, u.cuenta_id, u.activo
        FROM sesiones_usuario s
        JOIN usuarios_sistema u ON u.id = s.usuario_id
        WHERE s.token = ?;
        """,
        (token,),
    )
    ses = cur.fetchone()
    if not ses or not int(ses["activo"] or 0):
        conn.close()
        return master_path, None, False

    superadmin = int(ses["es_superadmin"] or 0) == 1
    cuenta_id = None
    if superadmin and str(cuenta_header or "").strip().isdigit():
        cuenta_id = int(cuenta_header)
    elif ses["cuenta_id"]:
        cuenta_id = int(ses["cuenta_id"])
    elif not superadmin:
        cuenta_id = 1
    else:
        # Admin de plataforma, sin impersonar: trabaja sobre la base maestra (Silo Chico).
        cuenta_id = 1

    cur.execute("SELECT * FROM cuentas_cliente WHERE id = ?;", (cuenta_id,))
    cuenta = cur.fetchone()
    conn.close()
    if not cuenta:
        return master_path, None, False
    if not int(cuenta["activo"] or 0) and not superadmin:
        return master_path, int(cuenta["id"]), True
    return _ruta_base_cuenta(master_path, cuenta["db_path"]), int(cuenta["id"]), False


class TenantDBMiddleware:
    def __init__(self, app, master_path: str):
        self.app = app
        self.master_path = master_path

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = _headers_scope(scope)
        token = _token_from_headers(headers)
        path, cuenta_id, bloquear = resolver_contexto(
            self.master_path, token, headers.get("x-cuenta-id", "")
        )
        if bloquear:
            body = b'{"detail":"Cuenta suspendida. Contacta a Campo+."}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        t_db = db_ctx.set(path)
        t_cta = cuenta_ctx.set(cuenta_id)
        try:
            await self.app(scope, receive, send)
        finally:
            db_ctx.reset(t_db)
            cuenta_ctx.reset(t_cta)


def _cuenta_row(master_path: str, cuenta_id: int):
    conn = master_connect(master_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM cuentas_cliente WHERE id = ?;", (cuenta_id,))
    row = cur.fetchone()
    conn.close()
    return row


def abrir_base_cuenta(master_path: str, cuenta_id: Optional[int] = None) -> sqlite3.Connection:
    cid = cuenta_id if cuenta_id is not None else cuenta_id_actual()
    if not cid:
        return master_connect(master_path)
    row = _cuenta_row(master_path, cid)
    path = _ruta_base_cuenta(master_path, row["db_path"] if row else None)
    if os.path.abspath(path) == os.path.abspath(master_path):
        return master_connect(master_path)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def resumen_cupo(master_path: str, cuenta_id: int) -> dict:
    cuenta = _cuenta_row(master_path, cuenta_id)
    if not cuenta:
        raise HTTPException(404, "Cuenta cliente no encontrada")
    conn = abrir_base_cuenta(master_path, cuenta_id)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM empresas;")
    n_emp = int(cur.fetchone()["n"])
    conn.close()
    mconn = master_connect(master_path)
    mcur = mconn.cursor()
    mcur.execute(
        """
        SELECT COUNT(*) AS n FROM usuarios_sistema
        WHERE COALESCE(activo, 1) = 1
          AND COALESCE(es_superadmin, 0) = 0
          AND cuenta_id = ?;
        """,
        (cuenta_id,),
    )
    n_usr = int(mcur.fetchone()["n"])
    mconn.close()
    max_emp = int(cuenta["empresas_incluidas"] or 0) + int(cuenta["empresas_extra"] or 0)
    max_usr = int(cuenta["usuarios_incluidos"] or 0) + int(cuenta["usuarios_extra"] or 0)
    return {
        "cuenta_id": int(cuenta["id"]),
        "nombre": cuenta["nombre"],
        "slug": cuenta["slug"],
        "activo": int(cuenta["activo"] or 0),
        "db_path": cuenta["db_path"],
        "empresas_usadas": n_emp,
        "empresas_incluidas": int(cuenta["empresas_incluidas"] or 0),
        "empresas_extra": int(cuenta["empresas_extra"] or 0),
        "empresas_max": max_emp,
        "puede_crear_empresa": n_emp < max_emp,
        "usuarios_usados": n_usr,
        "usuarios_incluidos": int(cuenta["usuarios_incluidos"] or 0),
        "usuarios_extra": int(cuenta["usuarios_extra"] or 0),
        "usuarios_max": max_usr,
        "puede_crear_usuario": n_usr < max_usr,
        "precio_empresa_extra": float(cuenta["precio_empresa_extra"] or 0),
        "precio_usuario_extra": float(cuenta["precio_usuario_extra"] or 0),
        "notas": cuenta["notas"] or "",
        "contacto": cuenta["contacto"] or "",
    }


def exigir_cupo_empresa(master_path: str) -> None:
    cid = cuenta_id_actual() or 1
    cupo = resumen_cupo(master_path, cid)
    if cupo["puede_crear_empresa"]:
        return
    raise HTTPException(
        403,
        (
            f"Cupo de empresas agotado para {cupo['nombre']}: "
            f"{cupo['empresas_usadas']} de {cupo['empresas_max']} "
            f"({cupo['empresas_incluidas']} incluidas). "
            "Campo+ debe autorizar una empresa adicional."
        ),
    )


def exigir_cupo_usuario(master_path: str) -> None:
    cid = cuenta_id_actual() or 1
    cupo = resumen_cupo(master_path, cid)
    if cupo["puede_crear_usuario"]:
        return
    extra = cupo["precio_usuario_extra"]
    precio = f" (costo extra ${extra:,.0f})".replace(",", ".") if extra else ""
    raise HTTPException(
        403,
        (
            f"Cupo de usuarios agotado para {cupo['nombre']}: "
            f"{cupo['usuarios_usados']} de {cupo['usuarios_max']} "
            f"({cupo['usuarios_incluidos']} incluidos){precio}. "
            "Campo+ debe autorizar un usuario adicional."
        ),
    )


def _slug(texto: str) -> str:
    s = (texto or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "cliente"


def _clonar_esquema(origen: str, destino: str) -> None:
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    src = sqlite3.connect(origen)
    dst = sqlite3.connect(destino)
    src.row_factory = sqlite3.Row
    stmts = []
    for row in src.execute(
        """
        SELECT type, name, sql FROM sqlite_master
        WHERE sql IS NOT NULL
          AND name NOT LIKE 'sqlite_%'
          AND type IN ('table', 'index')
        ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name;
        """
    ):
        stmts.append(row["sql"])
    for sql in stmts:
        try:
            dst.execute(sql)
        except sqlite3.OperationalError:
            pass
    for tabla in _TABLAS_REF:
        try:
            cols = [r[1] for r in src.execute(f"PRAGMA table_info({tabla})")]
            if not cols:
                continue
            qmarks = ",".join("?" for _ in cols)
            colsql = ",".join(cols)
            filas = src.execute(f"SELECT {colsql} FROM {tabla}").fetchall()
            dst.executemany(
                f"INSERT OR IGNORE INTO {tabla} ({colsql}) VALUES ({qmarks});",
                [tuple(f) for f in filas],
            )
        except sqlite3.OperationalError:
            continue
    dst.commit()
    src.close()
    dst.close()


def provisionar_base(master_path: str, slug: str, nombre: str) -> str:
    """Crea la base vacía del cliente (no copia datos de otros clientes)."""
    base = os.path.dirname(os.path.abspath(master_path))
    dest = os.path.join(base, "datos_clientes", slug, "campoplus.db")
    if os.path.abspath(dest) == os.path.abspath(master_path):
        return master_path
    if not os.path.exists(dest):
        _clonar_esquema(master_path, dest)
    conn = sqlite3.connect(dest)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM configuracion_empresa;")
    if int(cur.fetchone()["n"]) == 0:
        cur.execute(
            """
            INSERT INTO configuracion_empresa
            (id, razon_social, cuit, condicion_iva, localidad, contacto_email, cit_arba, empresa_activa_id)
            VALUES (1, ?, '', 'Responsable Inscripto', '', '', '', NULL);
            """,
            (nombre,),
        )
    conn.commit()
    conn.close()
    return dest


def _es_superadmin(master_path: str, request: Request) -> bool:
    headers = {k.lower(): v for k, v in request.headers.items()}
    token = _token_from_headers(headers)
    if not token:
        return False
    conn = master_connect(master_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.es_superadmin
        FROM sesiones_usuario s
        JOIN usuarios_sistema u ON u.id = s.usuario_id
        WHERE s.token = ?;
        """,
        (token,),
    )
    row = cur.fetchone()
    conn.close()
    return bool(row and int(row["es_superadmin"] or 0))


def _exigir_superadmin(master_path: str, request: Request) -> None:
    if not _es_superadmin(master_path, request):
        raise HTTPException(403, "Solo el administrador de Campo+ puede hacer esto.")


class CuentaCreate(BaseModel):
    nombre: str
    slug: Optional[str] = ""
    contacto: Optional[str] = ""
    notas: Optional[str] = ""
    precio_empresa_extra: float = 0
    precio_usuario_extra: float = 0


class CuentaCupoUpdate(BaseModel):
    activo: Optional[int] = None
    empresas_incluidas: Optional[int] = None
    empresas_extra: Optional[int] = None
    usuarios_incluidos: Optional[int] = None
    usuarios_extra: Optional[int] = None
    precio_empresa_extra: Optional[float] = None
    precio_usuario_extra: Optional[float] = None
    notas: Optional[str] = None
    contacto: Optional[str] = None


class SolicitudCreate(BaseModel):
    tipo: str
    detalle: Optional[str] = ""


def register_plataforma_routes(app: FastAPI, master_path: str) -> None:
    @app.get("/api/plataforma/mi-cupo")
    def mi_cupo(request: Request):
        if not _token_from_headers({k.lower(): v for k, v in request.headers.items()}):
            raise HTTPException(401, "Tenés que iniciar sesión")
        cid = cuenta_id_actual() or 1
        return resumen_cupo(master_path, cid)

    @app.get("/api/plataforma/cuentas")
    def listar_cuentas(request: Request):
        _exigir_superadmin(master_path, request)
        conn = master_connect(master_path)
        cur = conn.cursor()
        cur.execute("SELECT id FROM cuentas_cliente ORDER BY nombre COLLATE NOCASE;")
        ids = [int(r["id"]) for r in cur.fetchall()]
        conn.close()
        return [resumen_cupo(master_path, i) for i in ids]

    @app.post("/api/plataforma/cuentas")
    def crear_cuenta(data: CuentaCreate, request: Request):
        _exigir_superadmin(master_path, request)
        nombre = (data.nombre or "").strip()
        if not nombre:
            raise HTTPException(400, "El nombre del cliente es obligatorio")
        slug = _slug(data.slug or nombre)
        db_path = provisionar_base(master_path, slug, nombre)
        conn = master_connect(master_path)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO cuentas_cliente (
                    nombre, slug, contacto, activo,
                    empresas_incluidas, empresas_extra,
                    usuarios_incluidos, usuarios_extra,
                    precio_empresa_extra, precio_usuario_extra,
                    notas, db_path, created_at
                ) VALUES (?, ?, ?, 1, ?, 0, ?, 0, ?, ?, ?, ?, ?);
                """,
                (
                    nombre,
                    slug,
                    (data.contacto or "").strip(),
                    EMPRESAS_INCLUIDAS,
                    USUARIOS_INCLUIDOS,
                    float(data.precio_empresa_extra or 0),
                    float(data.precio_usuario_extra or 0),
                    (data.notas or "").strip(),
                    db_path,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            nuevo = cur.lastrowid
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            raise HTTPException(400, "Ya existe un cliente con ese identificador")
        conn.close()
        return resumen_cupo(master_path, int(nuevo))

    @app.put("/api/plataforma/cuentas/{cuenta_id}")
    def actualizar_cupo(cuenta_id: int, data: CuentaCupoUpdate, request: Request):
        _exigir_superadmin(master_path, request)
        payload = data.model_dump(exclude_unset=True)
        if not payload:
            raise HTTPException(400, "Sin cambios")
        permitidos = [
            "activo",
            "empresas_incluidas",
            "empresas_extra",
            "usuarios_incluidos",
            "usuarios_extra",
            "precio_empresa_extra",
            "precio_usuario_extra",
            "notas",
            "contacto",
        ]
        campos = []
        vals = []
        for k in permitidos:
            if k in payload and payload[k] is not None:
                campos.append(f"{k} = ?")
                vals.append(payload[k])
        if not campos:
            raise HTTPException(400, "Sin cambios")
        vals.append(cuenta_id)
        conn = master_connect(master_path)
        cur = conn.cursor()
        cur.execute("SELECT id FROM cuentas_cliente WHERE id = ?;", (cuenta_id,))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(404, "Cuenta no encontrada")
        cur.execute(
            f"UPDATE cuentas_cliente SET {', '.join(campos)} WHERE id = ?;",
            vals,
        )
        conn.commit()
        conn.close()
        return resumen_cupo(master_path, cuenta_id)

    @app.post("/api/plataforma/solicitudes")
    def crear_solicitud(data: SolicitudCreate, request: Request):
        if not _token_from_headers({k.lower(): v for k, v in request.headers.items()}):
            raise HTTPException(401, "Tenés que iniciar sesión")
        tipo = (data.tipo or "").strip().lower()
        if tipo not in ("empresa", "usuario"):
            raise HTTPException(400, "El tipo debe ser empresa o usuario")
        cid = cuenta_id_actual() or 1
        conn = master_connect(master_path)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO solicitudes_cupo (cuenta_id, tipo, detalle, estado, created_at)
            VALUES (?, ?, ?, 'pendiente', ?);
            """,
            (
                cid,
                tipo,
                (data.detalle or "").strip(),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        sid = cur.lastrowid
        conn.close()
        return {"id": sid, "status": "pendiente"}

    @app.get("/api/plataforma/solicitudes")
    def listar_solicitudes(request: Request, estado: str = "pendiente"):
        _exigir_superadmin(master_path, request)
        conn = master_connect(master_path)
        cur = conn.cursor()
        if estado == "todas":
            cur.execute(
                """
                SELECT s.*, c.nombre AS cuenta
                FROM solicitudes_cupo s
                JOIN cuentas_cliente c ON c.id = s.cuenta_id
                ORDER BY s.id DESC;
                """
            )
        else:
            cur.execute(
                """
                SELECT s.*, c.nombre AS cuenta
                FROM solicitudes_cupo s
                JOIN cuentas_cliente c ON c.id = s.cuenta_id
                WHERE s.estado = ?
                ORDER BY s.id DESC;
                """,
                (estado,),
            )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.post("/api/plataforma/solicitudes/{sid}/resolver")
    def resolver_solicitud(sid: int, request: Request, aprobar: int = 1):
        _exigir_superadmin(master_path, request)
        conn = master_connect(master_path)
        cur = conn.cursor()
        cur.execute("SELECT * FROM solicitudes_cupo WHERE id = ?;", (sid,))
        sol = cur.fetchone()
        if not sol:
            conn.close()
            raise HTTPException(404, "Solicitud no encontrada")
        if sol["estado"] != "pendiente":
            conn.close()
            raise HTTPException(400, "La solicitud ya fue resuelta")
        ahora = datetime.now().isoformat(timespec="seconds")
        if aprobar:
            col = "empresas_extra" if sol["tipo"] == "empresa" else "usuarios_extra"
            cur.execute(
                f"UPDATE cuentas_cliente SET {col} = COALESCE({col}, 0) + 1 WHERE id = ?;",
                (sol["cuenta_id"],),
            )
            cur.execute(
                "UPDATE solicitudes_cupo SET estado = 'aprobada', resuelto_en = ? WHERE id = ?;",
                (ahora, sid),
            )
        else:
            cur.execute(
                "UPDATE solicitudes_cupo SET estado = 'rechazada', resuelto_en = ? WHERE id = ?;",
                (ahora, sid),
            )
        conn.commit()
        conn.close()
        return {"status": "aprobada" if aprobar else "rechazada"}
