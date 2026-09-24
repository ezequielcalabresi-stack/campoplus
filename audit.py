# -*- coding: utf-8 -*-
"""Firma de usuarios y log de auditoría (trazabilidad de altas/modificaciones)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

# Rutas que no generan ruido en el log (polling / lecturas disfrazadas)
_SKIP_PREFIXES = (
    "/api/audit",
    "/api/usuarios",
    "/api/arba/padron/job",
    "/api/dashboard",
)
_SKIP_EXACT = {"/", "/favicon.ico"}
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def init_audit_schema(cursor: sqlite3.Cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios_sistema (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            email TEXT,
            rol TEXT DEFAULT 'Administración / Carga',
            activo INTEGER DEFAULT 1,
            created_at TEXT
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            usuario_id INTEGER,
            usuario_nombre TEXT NOT NULL,
            accion TEXT,
            modulo TEXT,
            entidad TEXT,
            entidad_id TEXT,
            detalle TEXT,
            metodo TEXT,
            ruta TEXT,
            status_code INTEGER,
            empresa_id INTEGER,
            ip TEXT
        );
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(ts DESC);"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_usuario ON audit_log(usuario_nombre);"
    )

    cursor.execute("SELECT COUNT(*) AS n FROM usuarios_sistema;")
    if int(cursor.fetchone()[0] or 0) == 0:
        ahora = datetime.now().isoformat(timespec="seconds")
        seeds = [
            ("Raul Ezequiel Calabresi", "ezequiel@campoplus.com", "Administrador Total"),
            ("Administración CAmpo+", "admin@campoplus.com", "Administración / Carga"),
            ("Taty", "taty@campoplus.com", "Administración / Carga"),
            ("Operativo Campo", "campo@campoplus.com", "Operativo / Campo"),
        ]
        for nombre, email, rol in seeds:
            cursor.execute(
                """
                INSERT INTO usuarios_sistema (nombre, email, rol, activo, created_at)
                VALUES (?, ?, ?, 1, ?);
                """,
                (nombre, email, rol, ahora),
            )


def usuario_desde_headers(request: Request) -> dict:
    """Lee la firma enviada por el cliente (X-Usuario-*)."""
    nombre = (request.headers.get("X-Usuario-Nombre") or "").strip()
    uid_raw = (request.headers.get("X-Usuario-Id") or "").strip()
    rol = (request.headers.get("X-Usuario-Rol") or "").strip()
    uid = None
    if uid_raw.isdigit():
        uid = int(uid_raw)
    if not nombre:
        nombre = "Sin firmar"
    return {"id": uid, "nombre": nombre, "rol": rol}


def registrar_firma(
    get_db: Callable,
    *,
    usuario_nombre: str,
    accion: str,
    modulo: str = "",
    entidad: str = "",
    entidad_id: str = "",
    detalle: Any = None,
    metodo: str = "",
    ruta: str = "",
    status_code: Optional[int] = None,
    empresa_id: Optional[int] = None,
    usuario_id: Optional[int] = None,
    ip: str = "",
) -> None:
    """Inserta una fila en audit_log. Nunca lanza (no debe romper la operación)."""
    try:
        det = detalle
        if det is not None and not isinstance(det, str):
            try:
                det = json.dumps(det, ensure_ascii=False, default=str)[:4000]
            except Exception:
                det = str(det)[:4000]
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO audit_log (
                ts, usuario_id, usuario_nombre, accion, modulo, entidad, entidad_id,
                detalle, metodo, ruta, status_code, empresa_id, ip
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                usuario_id,
                (usuario_nombre or "Sin firmar")[:200],
                (accion or "")[:80],
                (modulo or "")[:120],
                (entidad or "")[:120],
                str(entidad_id or "")[:80],
                det,
                (metodo or "")[:12],
                (ruta or "")[:300],
                status_code,
                empresa_id,
                (ip or "")[:80],
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"AVISO audit_log: {exc}")


def _modulo_desde_ruta(path: str) -> str:
    parts = [p for p in (path or "").split("/") if p]
    if len(parts) >= 2 and parts[0] == "api":
        return parts[1]
    return parts[0] if parts else "sistema"


def _resumen_body(raw: bytes, content_type: str) -> str:
    if not raw:
        return ""
    ct = (content_type or "").lower()
    if "multipart" in ct:
        return f"[multipart {len(raw)} bytes]"
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return f"[binario {len(raw)} bytes]"
    if "json" in ct or text[:1] in "{[":
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                # No guardar secretos obvios
                safe = {
                    k: v
                    for k, v in data.items()
                    if k.lower() not in ("password", "pass", "clave", "token", "cit")
                }
                return json.dumps(safe, ensure_ascii=False, default=str)[:3500]
            return json.dumps(data, ensure_ascii=False, default=str)[:3500]
        except Exception:
            pass
    return text[:1500]


class AuditMiddleware(BaseHTTPMiddleware):
    """Registra automáticamente POST/PUT/PATCH/DELETE a /api/* con la firma del usuario."""

    def __init__(self, app, get_db: Callable, get_empresa_activa_id: Optional[Callable] = None):
        super().__init__(app)
        self.get_db = get_db
        self.get_empresa_activa_id = get_empresa_activa_id

    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()
        path = request.url.path or ""

        if method not in _MUTATING or not path.startswith("/api/"):
            return await call_next(request)

        if path in _SKIP_EXACT or any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        body = b""
        try:
            body = await request.body()
        except Exception:
            body = b""

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(request.scope, receive)

        response: Response = await call_next(request)

        # Solo loguear escrituras "exitosas" o errores de negocio (4xx/5xx también útiles)
        user = usuario_desde_headers(request)
        empresa_id = None
        if self.get_empresa_activa_id:
            try:
                empresa_id = self.get_empresa_activa_id()
            except Exception:
                empresa_id = None

        ip = ""
        if request.client:
            ip = request.client.host or ""

        detalle = _resumen_body(body, request.headers.get("content-type", ""))
        registrar_firma(
            self.get_db,
            usuario_nombre=user["nombre"],
            usuario_id=user["id"],
            accion=method,
            modulo=_modulo_desde_ruta(path),
            entidad="",
            entidad_id="",
            detalle=detalle,
            metodo=method,
            ruta=path,
            status_code=response.status_code,
            empresa_id=empresa_id,
            ip=ip,
        )
        # Echo firma en respuesta para UI
        response.headers["X-Firma-Usuario"] = user["nombre"]
        return response


class UsuarioCreate(BaseModel):
    nombre: str
    email: Optional[str] = ""
    rol: Optional[str] = "Administración / Carga"
    activo: int = 1


class UsuarioUpdate(BaseModel):
    nombre: Optional[str] = None
    email: Optional[str] = None
    rol: Optional[str] = None
    activo: Optional[int] = None
    login: Optional[str] = None


def register_audit_routes(app: FastAPI, get_db: Callable, get_empresa_activa_id: Callable) -> None:
    @app.get("/api/usuarios")
    def listar_usuarios(solo_activos: int = 1):
        import main as _main
        from plataforma import cuenta_id_actual, master_connect

        cid = cuenta_id_actual() or 1
        conn = master_connect(_main.DB_PATH)
        cur = conn.cursor()
        where = ["COALESCE(cuenta_id, 1) = ?"]
        params = [cid]
        if solo_activos:
            where.append("activo = 1")
        cur.execute(
            f"SELECT * FROM usuarios_sistema WHERE {' AND '.join(where)} ORDER BY nombre COLLATE NOCASE;",
            params,
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.post("/api/usuarios")
    def crear_usuario(data: UsuarioCreate, request: Request):
        nombre = (data.nombre or "").strip()
        if not nombre:
            raise HTTPException(400, "Nombre obligatorio")
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO usuarios_sistema (nombre, email, rol, activo, created_at)
            VALUES (?, ?, ?, ?, ?);
            """,
            (
                nombre,
                (data.email or "").strip(),
                (data.rol or "Administración / Carga").strip(),
                1 if data.activo else 0,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        uid = cur.lastrowid
        conn.commit()
        conn.close()
        firmante = usuario_desde_headers(request)
        registrar_firma(
            get_db,
            usuario_nombre=firmante["nombre"],
            usuario_id=firmante["id"],
            accion="ALTA_USUARIO",
            modulo="usuarios",
            entidad="usuarios_sistema",
            entidad_id=str(uid),
            detalle={"nombre": nombre, "rol": data.rol},
            metodo="POST",
            ruta="/api/usuarios",
            status_code=200,
            empresa_id=get_empresa_activa_id(),
        )
        return {"id": uid, "status": "ok"}

    @app.put("/api/usuarios/{uid}")
    def actualizar_usuario(uid: int, data: UsuarioUpdate, request: Request):
        import main as _main
        from plataforma import master_connect

        conn = master_connect(_main.DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios_sistema WHERE id = ?;", (uid,))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise HTTPException(404, "Usuario no encontrado")
        fields = []
        vals = []
        payload = data.model_dump(exclude_unset=True)
        for key in ("nombre", "email", "rol", "activo", "login"):
            if key in payload and payload[key] is not None:
                val = payload[key]
                if key in ("nombre", "email", "login"):
                    val = str(val).strip()
                    if key == "login":
                        val = val.lower()
                fields.append(f"{key} = ?")
                vals.append(val)
        if not fields:
            conn.close()
            raise HTTPException(400, "Sin cambios")
        vals.append(uid)
        cur.execute(f"UPDATE usuarios_sistema SET {', '.join(fields)} WHERE id = ?;", vals)
        conn.commit()
        conn.close()
        firmante = usuario_desde_headers(request)
        registrar_firma(
            get_db,
            usuario_nombre=firmante["nombre"],
            usuario_id=firmante["id"],
            accion="MOD_USUARIO",
            modulo="usuarios",
            entidad="usuarios_sistema",
            entidad_id=str(uid),
            detalle=payload,
            metodo="PUT",
            ruta=f"/api/usuarios/{uid}",
            status_code=200,
            empresa_id=get_empresa_activa_id(),
        )
        return {"status": "ok"}

    @app.get("/api/audit/log")
    def listar_audit_log(
        limit: int = 200,
        offset: int = 0,
        usuario: Optional[str] = None,
        modulo: Optional[str] = None,
        q: Optional[str] = None,
    ):
        limit = max(1, min(int(limit or 200), 1000))
        offset = max(0, int(offset or 0))
        conn = get_db()
        cur = conn.cursor()
        where = []
        params: list = []
        if usuario:
            where.append("usuario_nombre LIKE ?")
            params.append(f"%{usuario}%")
        if modulo:
            where.append("modulo LIKE ?")
            params.append(f"%{modulo}%")
        if q:
            where.append("(ruta LIKE ? OR detalle LIKE ? OR accion LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        sql_where = (" WHERE " + " AND ".join(where)) if where else ""
        cur.execute(
            f"SELECT COUNT(*) AS n FROM audit_log{sql_where};",
            params,
        )
        total = int(cur.fetchone()["n"] or 0)
        cur.execute(
            f"""
            SELECT * FROM audit_log
            {sql_where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?;
            """,
            params + [limit, offset],
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return {"total": total, "items": rows, "limit": limit, "offset": offset}

    @app.post("/api/audit/firmar")
    async def firmar_evento(request: Request):
        """Endpoint opcional para eventos de UI que no pasan por API de negocio."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        firmante = usuario_desde_headers(request)
        nombre = (body.get("usuario_nombre") or firmante["nombre"] or "Sin firmar").strip()
        registrar_firma(
            get_db,
            usuario_nombre=nombre,
            usuario_id=firmante["id"] or body.get("usuario_id"),
            accion=str(body.get("accion") or "EVENTO")[:80],
            modulo=str(body.get("modulo") or "ui")[:120],
            entidad=str(body.get("entidad") or "")[:120],
            entidad_id=str(body.get("entidad_id") or "")[:80],
            detalle=body.get("detalle"),
            metodo="POST",
            ruta="/api/audit/firmar",
            status_code=200,
            empresa_id=get_empresa_activa_id(),
        )
        return {"status": "ok", "firma": nombre}
