# -*- coding: utf-8 -*-
"""
CAmpo+ — Auth comercial, planes/módulos por empresa y logos.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.environ.get("CAMPO_DATA_DIR", "").strip()
LOGOS_DIR = os.path.join(_DATA_DIR, "logos") if _DATA_DIR else os.path.join(BASE_DIR, "logos")

# Paquetes comerciales → módulos habilitados
PLANES: Dict[str, Dict[str, int]] = {
    "full": {
        "mod_bancos": 1,
        "mod_agro": 1,
        "mod_almacen": 1,
        "mod_ganaderia": 1,
        "mod_tambo": 1,
        "mod_sicore": 1,
        "mod_arba": 1,
        "mod_contabilidad": 1,
        "mod_liquidaciones": 1,
        "es_agente_retencion": 1,
    },
    "agro": {
        "mod_bancos": 1,
        "mod_agro": 1,
        "mod_almacen": 1,
        "mod_ganaderia": 0,
        "mod_tambo": 0,
        "mod_sicore": 1,
        "mod_arba": 1,
        "mod_contabilidad": 1,
        "mod_liquidaciones": 1,
        "es_agente_retencion": 1,
    },
    "ganadero": {
        "mod_bancos": 1,
        "mod_agro": 0,
        "mod_almacen": 0,
        "mod_ganaderia": 1,
        "mod_tambo": 1,
        "mod_sicore": 0,
        "mod_arba": 0,
        "mod_contabilidad": 1,
        "mod_liquidaciones": 1,
        "es_agente_retencion": 0,
    },
    "admin": {
        "mod_bancos": 1,
        "mod_agro": 0,
        "mod_almacen": 0,
        "mod_ganaderia": 0,
        "mod_tambo": 0,
        "mod_sicore": 1,
        "mod_arba": 1,
        "mod_contabilidad": 1,
        "mod_liquidaciones": 0,
        "es_agente_retencion": 1,
    },
    "trial": {
        "mod_bancos": 1,
        "mod_agro": 1,
        "mod_almacen": 1,
        "mod_ganaderia": 1,
        "mod_tambo": 1,
        "mod_sicore": 1,
        "mod_arba": 1,
        "mod_contabilidad": 1,
        "mod_liquidaciones": 1,
        "es_agente_retencion": 1,
    },
}

MOD_KEYS = [
    "mod_bancos",
    "mod_agro",
    "mod_almacen",
    "mod_ganaderia",
    "mod_tambo",
    "mod_sicore",
    "mod_arba",
    "mod_contabilidad",
    "mod_liquidaciones",
    "es_agente_retencion",
]


def _hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    dig = hashlib.pbkdf2_hmac(
        "sha256",
        (password or "").encode("utf-8"),
        salt.encode("utf-8"),
        120000,
    )
    return f"{salt}${dig.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    salt, _ = stored.split("$", 1)
    return secrets.compare_digest(_hash_password(password, salt), stored)


def _ensure_col(cursor, table: str, col: str, decl: str) -> None:
    cols = {r[1] for r in cursor.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl};")
        except Exception:
            pass


def init_saas_schema(cursor: sqlite3.Cursor) -> None:
    os.makedirs(LOGOS_DIR, exist_ok=True)

    # Extender empresas
    for col, decl in (
        ("plan", "TEXT DEFAULT 'full'"),
        ("acceso_habilitado", "INTEGER DEFAULT 1"),
        ("vencimiento_licencia", "TEXT"),
        ("logo_path", "TEXT"),
        ("notas_comerciales", "TEXT"),
        ("mod_bancos", "INTEGER DEFAULT 1"),
        ("mod_agro", "INTEGER DEFAULT 1"),
        ("mod_almacen", "INTEGER DEFAULT 1"),
        ("mod_ganaderia", "INTEGER DEFAULT 1"),
        ("mod_tambo", "INTEGER DEFAULT 1"),
        ("mod_sicore", "INTEGER DEFAULT 1"),
        ("mod_arba", "INTEGER DEFAULT 1"),
        ("mod_contabilidad", "INTEGER DEFAULT 1"),
        ("mod_liquidaciones", "INTEGER DEFAULT 1"),
        ("es_agente_retencion", "INTEGER DEFAULT 1"),
    ):
        _ensure_col(cursor, "empresas", col, decl)

    # Tabla usuarios (si audit aún no corrió)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios_sistema (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            email TEXT,
            rol TEXT,
            activo INTEGER DEFAULT 1,
            created_at TEXT
        );
        """
    )

    # Extender usuarios
    for col, decl in (
        ("password_hash", "TEXT"),
        ("empresa_id", "INTEGER"),
        ("es_superadmin", "INTEGER DEFAULT 0"),
        ("login", "TEXT"),
    ):
        _ensure_col(cursor, "usuarios_sistema", col, decl)

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sesiones_usuario (
            token TEXT PRIMARY KEY,
            usuario_id INTEGER NOT NULL,
            empresa_id INTEGER,
            creado_en TEXT,
            expira_en TEXT,
            ip TEXT
        );
        """
    )

    # Passwords por defecto si faltan (solo primera vez)
    cursor.execute(
        "SELECT id, email, nombre, password_hash, es_superadmin FROM usuarios_sistema;"
    )
    for row in cursor.fetchall():
        rid = row[0] if not isinstance(row, sqlite3.Row) else row["id"]
        ph = row[3] if not isinstance(row, sqlite3.Row) else row["password_hash"]
        email = (row[1] if not isinstance(row, sqlite3.Row) else row["email"]) or ""
        nombre = (row[2] if not isinstance(row, sqlite3.Row) else row["nombre"]) or ""
        if not ph:
            # login = email o nombre normalizado
            login = (email or nombre).strip().lower()
            cursor.execute(
                """
                UPDATE usuarios_sistema
                SET password_hash = ?, login = COALESCE(NULLIF(TRIM(login),''), ?),
                    es_superadmin = CASE WHEN ? = 1 THEN 1 ELSE COALESCE(es_superadmin, 0) END
                WHERE id = ?;
                """,
                (_hash_password("campo+"), login, 1 if "calabresi" in login or "ezequiel" in login else 0, rid),
            )
        # Primer usuario admin total
        rol = None
        try:
            cursor.execute("SELECT rol FROM usuarios_sistema WHERE id=?;", (rid,))
            r2 = cursor.fetchone()
            rol = (r2[0] if not isinstance(r2, sqlite3.Row) else r2["rol"]) if r2 else ""
        except Exception:
            pass

    cursor.execute(
        """
        UPDATE usuarios_sistema
        SET es_superadmin = 0
        WHERE LOWER(TRIM(COALESCE(login, ''))) <> 'eze'
          AND LOWER(TRIM(COALESCE(email, ''))) <> 'ezequielcalabresi@gmail.com';
        """
    )


def empresa_to_dict(row) -> dict:
    d = dict(row)
    logo = d.get("logo_path") or ""
    if logo and not logo.startswith("/"):
        logo = "/" + logo.replace("\\", "/")
    d["logo_url"] = logo if logo else ""
    d["plan"] = d.get("plan") or "full"
    d["acceso_habilitado"] = int(d.get("acceso_habilitado") if d.get("acceso_habilitado") is not None else 1)
    for k in MOD_KEYS:
        d[k] = int(d.get(k) if d.get(k) is not None else 1)
    return d


def aplicar_plan(data: dict) -> dict:
    plan = (data.get("plan") or "full").strip().lower()
    presets = PLANES.get(plan)
    out = dict(data)
    out["plan"] = plan if plan in PLANES else "full"
    if presets and data.get("aplicar_plan_modulos", True):
        for k, v in presets.items():
            # Solo aplicar si no vino override explícito en el payload
            if k not in data or data.get("_force_plan"):
                out[k] = v
            elif data.get(k) is None:
                out[k] = v
    return out


class LoginModel(BaseModel):
    usuario: str
    password: str
    empresa_id: Optional[int] = None


class EmpresaSaasModel(BaseModel):
    razon_social: str
    cuit: str
    tenant_id: str
    localidad: Optional[str] = ""
    plan: str = "full"
    acceso_habilitado: int = 1
    vencimiento_licencia: Optional[str] = ""
    notas_comerciales: Optional[str] = ""
    mod_bancos: Optional[int] = None
    mod_agro: Optional[int] = None
    mod_almacen: Optional[int] = None
    mod_ganaderia: Optional[int] = None
    mod_tambo: Optional[int] = None
    mod_sicore: Optional[int] = None
    mod_arba: Optional[int] = None
    mod_contabilidad: Optional[int] = None
    mod_liquidaciones: Optional[int] = None
    es_agente_retencion: Optional[int] = None
    aplicar_plan_modulos: bool = False


class UsuarioAuthModel(BaseModel):
    nombre: str
    email: Optional[str] = ""
    login: Optional[str] = ""
    password: Optional[str] = ""
    rol: Optional[str] = "Administración / Carga"
    empresa_id: Optional[int] = None
    es_superadmin: int = 0
    activo: int = 1


class PasswordModel(BaseModel):
    password: str


def _token_from_request(request: Request) -> str:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("X-Session-Token") or "").strip()


def sesion_actual(get_db: Callable, request: Request) -> Optional[dict]:
    token = _token_from_request(request)
    if not token:
        return None
    import main as _main
    from plataforma import master_connect

    conn = master_connect(_main.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.*, u.nombre, u.email, u.login, u.rol, u.es_superadmin, u.empresa_id AS user_empresa_id,
               u.activo AS user_activo
        FROM sesiones_usuario s
        JOIN usuarios_sistema u ON u.id = s.usuario_id
        WHERE s.token = ?;
        """,
        (token,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    if d.get("expira_en") and d["expira_en"] < datetime.now().isoformat(timespec="seconds"):
        return None
    if not int(d.get("user_activo") or 0):
        return None
    return d


def register_saas_routes(app: FastAPI, get_db: Callable, get_empresa_activa_id: Callable) -> None:
    try:
        conn = get_db()
        init_saas_schema(conn.cursor())
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"AVISO init saas: {exc}")

    @app.get("/api/auth/planes")
    def api_planes():
        return {
            "planes": list(PLANES.keys()),
            "modulos": MOD_KEYS,
            "descripcion": {
                "full": "Todos los módulos",
                "agro": "Agricultura + admin/fiscal (sin ganadería/tambo)",
                "ganadero": "Ganadería/tambo + bancos (sin agro/fiscal avanzado)",
                "admin": "Administración / bancos / fiscal",
                "trial": "Prueba completa",
            },
        }

    @app.post("/api/auth/login")
    def api_login(data: LoginModel, request: Request):
        user_key = (data.usuario or "").strip().lower()
        if not user_key or not data.password:
            raise HTTPException(400, "Usuario y contraseña obligatorios")
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM usuarios_sistema
            WHERE activo = 1 AND (
                LOWER(TRIM(COALESCE(login,''))) = ?
                OR LOWER(TRIM(COALESCE(email,''))) = ?
                OR LOWER(TRIM(COALESCE(nombre,''))) = ?
            )
            LIMIT 1;
            """,
            (user_key, user_key, user_key),
        )
        user = cur.fetchone()
        if not user or not _verify_password(data.password, user["password_hash"] or ""):
            conn.close()
            raise HTTPException(401, "Usuario o contraseña incorrectos")

        user = dict(user)

        from plataforma import abrir_base_cuenta, master_connect
        import main as _main
        try:
            conn.commit()
            conn.close()
            conn = None
            cuenta_user = user.get("cuenta_id") or (None if int(user.get("es_superadmin") or 0) else 1)
            base_emp = abrir_base_cuenta(_main.DB_PATH, int(cuenta_user) if cuenta_user else 1)
            cur_emp = base_emp.cursor()
            if data.empresa_id:
                empresa_id = int(data.empresa_id)
            elif user.get("empresa_id"):
                empresa_id = int(user["empresa_id"])
            else:
                cur_emp.execute("SELECT empresa_activa_id FROM configuracion_empresa WHERE id = 1;")
                cfg = cur_emp.fetchone()
                empresa_id = int(cfg["empresa_activa_id"]) if cfg and cfg["empresa_activa_id"] else None
                if not empresa_id:
                    cur_emp.execute("SELECT id FROM empresas ORDER BY id ASC LIMIT 1;")
                    first = cur_emp.fetchone()
                    empresa_id = int(first["id"]) if first else None
            if not empresa_id:
                base_emp.close()
                raise HTTPException(400, "El cliente todavía no tiene empresas cargadas")
            cur_emp.execute("SELECT * FROM empresas WHERE id = ?;", (empresa_id,))
            emp = cur_emp.fetchone()
            if not emp:
                base_emp.close()
                raise HTTPException(400, "Empresa no válida")
            emp = empresa_to_dict(emp)
            base_emp.execute(
                "UPDATE configuracion_empresa SET empresa_activa_id = ? WHERE id = 1;",
                (empresa_id,),
            )
            base_emp.commit()
            base_emp.close()
            base_emp = None

            if not int(user.get("es_superadmin") or 0) and not emp["acceso_habilitado"]:
                raise HTTPException(
                    403,
                    "Acceso suspendido por falta de pago o licencia. Contactá a CAmpo+.",
                )

            if not int(user.get("es_superadmin") or 0) and user.get("empresa_id"):
                if int(user["empresa_id"]) != int(empresa_id):
                    raise HTTPException(403, "No tenés acceso a esa empresa")

            token = secrets.token_urlsafe(32)
            ahora = datetime.now()
            expira = (ahora + timedelta(days=7)).isoformat(timespec="seconds")
            ip = request.client.host if request.client else ""
            conn = _main.get_db()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO sesiones_usuario (token, usuario_id, empresa_id, creado_en, expira_en, ip)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (token, user["id"], empresa_id, ahora.isoformat(timespec="seconds"), expira, ip),
            )
            conn.commit()
            conn.close()
            conn = None
            return {
                "status": "ok",
                "token": token,
                "expira_en": expira,
                "usuario": {
                    "id": user["id"],
                    "nombre": user["nombre"],
                    "email": user["email"],
                    "login": user.get("login") or user.get("email"),
                    "rol": user["rol"],
                    "es_superadmin": int(user.get("es_superadmin") or 0),
                },
                "empresa": emp,
            }

        except HTTPException:
            if conn is not None:
                conn.close()
            raise
        except Exception as exc:
            if conn is not None:
                conn.close()
            raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/auth/logout")
    def api_logout(request: Request):
        token = _token_from_request(request)
        if token:
            import main as _main
            from plataforma import master_connect

            conn = master_connect(_main.DB_PATH)
            conn.execute("DELETE FROM sesiones_usuario WHERE token = ?;", (token,))
            conn.commit()
            conn.close()
        return {"status": "ok"}

    @app.get("/api/auth/me")
    def api_me(request: Request):
        ses = sesion_actual(get_db, request)
        if not ses:
            raise HTTPException(401, "Sesión no válida o expirada")
        conn = get_db()
        cur = conn.cursor()
        emp_id = ses.get("empresa_id") or get_empresa_activa_id()
        cur.execute("SELECT * FROM empresas WHERE id = ?;", (emp_id,))
        emp = cur.fetchone()
        conn.close()
        return {
            "usuario": {
                "id": ses["usuario_id"],
                "nombre": ses["nombre"],
                "email": ses["email"],
                "login": ses.get("login"),
                "rol": ses["rol"],
                "es_superadmin": int(ses.get("es_superadmin") or 0),
            },
            "empresa": empresa_to_dict(emp) if emp else None,
            "token_ok": True,
        }

    @app.get("/api/saas/empresas")
    def api_empresas_detalle(request: Request):
        """Listado ampliado (planes/módulos/logo). Superadmin ve todas."""
        ses = sesion_actual(get_db, request)
        conn = get_db()
        cur = conn.cursor()
        if ses and int(ses.get("es_superadmin") or 0):
            cur.execute("SELECT * FROM empresas ORDER BY razon_social COLLATE NOCASE;")
        elif ses and ses.get("user_empresa_id"):
            cur.execute(
                "SELECT * FROM empresas WHERE id = ?;",
                (ses["user_empresa_id"],),
            )
        else:
            conn.close()
            raise HTTPException(401, "Tenés que iniciar sesión")
        rows = [empresa_to_dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @app.put("/api/empresas/{empresa_id}/saas")
    def api_upd_empresa_saas(empresa_id: int, data: EmpresaSaasModel, request: Request):
        ses = sesion_actual(get_db, request)
        if ses and not int(ses.get("es_superadmin") or 0):
            # Solo superadmin cambia bloqueo/plan
            if data.acceso_habilitado == 0 or data.plan:
                pass  # allow módulo tweaks for now; block only superadmin for acceso
            if int(data.acceso_habilitado) == 0:
                raise HTTPException(403, "Solo superadmin puede suspender acceso")

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM empresas WHERE id = ?;", (empresa_id,))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(404, "Empresa no encontrada")

        payload = data.model_dump()
        if data.aplicar_plan_modulos:
            payload["_force_plan"] = True
            payload = aplicar_plan(payload)

        cuit = "".join(c for c in (data.cuit or "") if c.isdigit())
        fields = [
            "razon_social=?",
            "cuit=?",
            "tenant_id=?",
            "localidad=?",
            "plan=?",
            "acceso_habilitado=?",
            "vencimiento_licencia=?",
            "notas_comerciales=?",
        ]
        vals: list = [
            data.razon_social.strip(),
            cuit,
            data.tenant_id.strip().lower(),
            (data.localidad or "").strip(),
            (payload.get("plan") or "full"),
            int(payload.get("acceso_habilitado", 1)),
            data.vencimiento_licencia or "",
            data.notas_comerciales or "",
        ]
        for k in MOD_KEYS:
            v = payload.get(k)
            if v is None:
                v = 1
            fields.append(f"{k}=?")
            vals.append(int(v))
        vals.append(empresa_id)
        cur.execute(
            f"UPDATE empresas SET {', '.join(fields)} WHERE id = ?;",
            vals,
        )
        conn.commit()
        cur.execute("SELECT * FROM empresas WHERE id = ?;", (empresa_id,))
        emp = empresa_to_dict(cur.fetchone())
        conn.close()
        return {"status": "ok", "empresa": emp}

    @app.post("/api/empresas/{empresa_id}/logo")
    async def api_logo(empresa_id: int, file: UploadFile = File(...)):
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM empresas WHERE id = ?;", (empresa_id,))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(404, "Empresa no encontrada")
        raw = await file.read()
        if len(raw) > 2_500_000:
            conn.close()
            raise HTTPException(400, "Logo demasiado grande (máx 2.5 MB)")
        ext = os.path.splitext(file.filename or "")[1].lower() or ".png"
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            ext = ".png"
        fname = f"empresa_{empresa_id}{ext}"
        path = os.path.join(LOGOS_DIR, fname)
        with open(path, "wb") as f:
            f.write(raw)
        rel = f"logos/{fname}"
        cur.execute("UPDATE empresas SET logo_path = ? WHERE id = ?;", (rel, empresa_id))
        conn.commit()
        conn.close()
        return {"status": "ok", "logo_url": "/" + rel}

    @app.post("/api/empresas/{empresa_id}/acceso")
    def api_toggle_acceso(empresa_id: int, request: Request, habilitar: int = 1):
        ses = sesion_actual(get_db, request)
        if not ses or not int(ses.get("es_superadmin") or 0):
            raise HTTPException(403, "Solo superadmin")
        conn = get_db()
        conn.execute(
            "UPDATE empresas SET acceso_habilitado = ? WHERE id = ?;",
            (1 if habilitar else 0, empresa_id),
        )
        conn.commit()
        conn.close()
        return {"status": "ok", "acceso_habilitado": 1 if habilitar else 0}

    @app.post("/api/auth/usuarios")
    def api_crear_usuario_auth(data: UsuarioAuthModel, request: Request):
        from plataforma import exigir_cupo_usuario
        import main as _main

        ses = sesion_actual(get_db, request)
        if ses and not int(ses.get("es_superadmin") or 0):
            # admin de empresa puede crear usuarios de su empresa
            if data.es_superadmin:
                raise HTTPException(403, "No podés crear superadmin")
        if not int(data.es_superadmin or 0):
            exigir_cupo_usuario(_main.DB_PATH)
        from plataforma import cuenta_id_actual, master_connect

        conn = master_connect(_main.DB_PATH)
        cur = conn.cursor()
        login = (data.login or data.email or data.nombre).strip().lower()
        ph = _hash_password(data.password) if data.password else _hash_password("campo+")
        cuenta_id = None if int(data.es_superadmin or 0) else (cuenta_id_actual() or 1)
        cur.execute(
            """
            INSERT INTO usuarios_sistema
            (nombre, email, rol, activo, created_at, password_hash, empresa_id, es_superadmin, login, cuenta_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                data.nombre.strip(),
                (data.email or "").strip(),
                data.rol or "Administración / Carga",
                1 if data.activo else 0,
                datetime.now().isoformat(timespec="seconds"),
                ph,
                data.empresa_id,
                int(data.es_superadmin or 0),
                login,
                cuenta_id,
            ),
        )
        uid = cur.lastrowid
        conn.commit()
        conn.close()
        return {"id": uid, "status": "ok", "login": login}

    @app.put("/api/auth/usuarios/{uid}/password")
    def api_set_password(uid: int, data: PasswordModel, request: Request):
        if not data.password or len(data.password) < 4:
            raise HTTPException(400, "Password mínimo 4 caracteres")
        import main as _main
        from plataforma import master_connect

        conn = master_connect(_main.DB_PATH)
        conn.execute(
            "UPDATE usuarios_sistema SET password_hash = ? WHERE id = ?;",
            (_hash_password(data.password), uid),
        )
        conn.commit()
        conn.close()
        return {"status": "ok"}
