from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
import uvicorn
import pandas as pd
import os
import re
import io
from datetime import datetime, timedelta
from motor_contable import (
    init_contabilidad,
    asiento_para_movimiento_banco,
    asiento_para_comprobante_compra,
    asiento_para_factura_venta,
    eliminar_cascada_movimiento_banco,
    listar_asientos_plano,
    recalcular_asientos_movimientos_bancarios,
    ejercicios_disponibles,
    ejercicio_desde_fecha,
    _fecha_iso_contable,
)
from agro_campania import init_agro_schema
from audit import AuditMiddleware, init_audit_schema, register_audit_routes
from arba_padron import (
    asegurar_carpeta_padrones,
    detectar_archivos_padron,
    estado_padron_arba,
    consultar_cuit_padron,
    start_import_job,
    get_import_job_status,
    PADRONES_DIR,
)

app = FastAPI(title="CAmpo+ Backend - Gestión Total & Bancaria (Motor Normativo Relacional)", version="12.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Firma-Usuario"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _copiar_sqlite(origen: str, destino: str) -> None:
    os.makedirs(os.path.dirname(destino) or ".", exist_ok=True)
    src = sqlite3.connect(origen)
    dst = sqlite3.connect(destino)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _resolver_db_path() -> str:
    """Sin CAMPO_DATA_DIR sigue el archivo de esta carpeta (beta local).
    Con CAMPO_DATA_DIR usa el disco del servidor y lo llena una sola vez."""
    data = os.environ.get("CAMPO_DATA_DIR", "").strip()
    local = os.path.join(BASE_DIR, "campoplus.db")
    if not data:
        return local
    os.makedirs(data, exist_ok=True)
    dest = os.path.join(data, "campoplus.db")
    if not os.path.exists(dest) and os.path.exists(local):
        _copiar_sqlite(local, dest)
        src_cli = os.path.join(BASE_DIR, "datos_clientes")
        dest_cli = os.path.join(data, "datos_clientes")
        if os.path.isdir(src_cli) and not os.path.exists(dest_cli):
            for dirpath, _dirs, files in os.walk(src_cli):
                for name in files:
                    if not name.endswith(".db"):
                        continue
                    src_db = os.path.join(dirpath, name)
                    rel = os.path.relpath(src_db, src_cli)
                    dst_db = os.path.join(dest_cli, rel)
                    _copiar_sqlite(src_db, dst_db)
    return dest


DB_PATH = _resolver_db_path()
EXCEL_RETENCIONES = "retenciones.xlsx"
EXCEL_BANCOS = os.path.join("tablas", "movimientos bancarios.xlsx")

def _es_nombre_banco_real(banco: str) -> bool:
    """Heurística para separar bancos reales de clientes usados como cta de pago (legado Access)."""
    b = (banco or "").strip().lower()
    if not b or b in ("nan", "none", "-"):
        return False
    tokens = (
        "bco", "banco", "galicia", "macro", "frances", "franç", "patagonia",
        "hsbc", "nacion", "nación", "comafi", "santander", "pampa", "provincia",
        "ciudad", "icbc", "bbva", "credicoop", "hipotecario", "supervielle",
        "itau", "itaú", "bind", "brubank", "industrial",
    )
    return any(t in b for t in tokens)


def _clasificar_cuentas_bancarias(cursor) -> None:
    """Marca cuentas reales vs cuentas de pago (clientes) en datos migrados."""
    cursor.execute("PRAGMA table_info(ctas_ctes_bancarias);")
    cols = [col[1] for col in cursor.fetchall()]
    if "es_cuenta_bancaria" not in cols:
        cursor.execute(
            "ALTER TABLE ctas_ctes_bancarias ADD COLUMN es_cuenta_bancaria INTEGER DEFAULT 1;"
        )
        cursor.execute("SELECT id, banco, nro_cta_cte FROM ctas_ctes_bancarias;")
        for row in cursor.fetchall():
            banco = row["banco"] if isinstance(row, sqlite3.Row) else row[1]
            nro = row["nro_cta_cte"] if isinstance(row, sqlite3.Row) else row[2]
            row_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
            es_banco = 1 if _es_nombre_banco_real(banco) else 0
            # Patrón Access: banco == nro_cta_cte (cliente usado como cuenta)
            if banco and nro and str(banco).strip().upper() == str(nro).strip().upper():
                es_banco = 0
            cursor.execute(
                "UPDATE ctas_ctes_bancarias SET es_cuenta_bancaria = ? WHERE id = ?;",
                (es_banco, row_id),
            )


def get_db():
    from plataforma import db_path_efectivo

    path = db_path_efectivo(DB_PATH)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _row_to_empresa(row) -> dict:
    d = {
        "id": int(row["id"]),
        "razon_social": row["razon_social"] or "",
        "cuit": row["cuit"] or "",
        "tenant_id": row["tenant_id"] or "",
        "localidad": row["localidad"] or "",
    }
    # Campos SaaS (si existen en el row)
    keys = row.keys() if hasattr(row, "keys") else []
    for k in (
        "plan", "acceso_habilitado", "vencimiento_licencia", "logo_path", "notas_comerciales",
        "mod_bancos", "mod_agro", "mod_almacen", "mod_ganaderia", "mod_tambo",
        "mod_sicore", "mod_arba", "mod_contabilidad", "mod_liquidaciones", "es_agente_retencion",
    ):
        if k in keys:
            d[k] = row[k]
    logo = d.get("logo_path") or ""
    if logo and not str(logo).startswith("/"):
        logo = "/" + str(logo).replace("\\", "/")
    d["logo_url"] = logo
    d["plan"] = d.get("plan") or "full"
    d["acceso_habilitado"] = int(d["acceso_habilitado"]) if d.get("acceso_habilitado") is not None else 1
    return d


def get_empresa_activa_id() -> int:
    """Devuelve el id de la empresa activa validado contra la tabla empresas."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT empresa_activa_id FROM configuracion_empresa WHERE id = 1;")
        cfg = cursor.fetchone()
        emp_id = int(cfg["empresa_activa_id"]) if cfg and cfg["empresa_activa_id"] is not None else None

        if emp_id is not None:
            cursor.execute("SELECT id FROM empresas WHERE id = ?;", (emp_id,))
            if cursor.fetchone():
                conn.close()
                return emp_id

        cursor.execute("SELECT id FROM empresas ORDER BY id ASC LIMIT 1;")
        first = cursor.fetchone()
        if first:
            emp_id = int(first["id"])
            cursor.execute(
                "UPDATE configuracion_empresa SET empresa_activa_id = ? WHERE id = 1;",
                (emp_id,),
            )
            conn.commit()
            conn.close()
            return emp_id
        conn.close()
    except Exception as e:
        print(f"⚠️ Error obteniendo empresa activa: {e}")
    return 1
def safe_date(val) -> str:
    if pd.isnull(val):
        return ''
    try:
        dt = pd.to_datetime(val, errors='coerce')
        if pd.notnull(dt) and 1950 <= dt.year <= 2100:
            return dt.strftime('%Y-%m-%d')
    except Exception:
        pass
    return ''

def obtener_codigo_regimen(descripcion: str) -> str:
    desc = str(descripcion).lower()
    if 'muebles' in desc:
        return '06'
    elif 'inmuebles' in desc:
        return '30'
    elif 'locaciones' in desc or 'servicios' in desc:
        return '16'
    elif 'transporte' in desc:
        return '78'
    elif 'comisiones' in desc:
        return '21'
    elif desc.strip().isdigit():
        num = int(desc.strip())
        return f"{num:02d}" if num > 0 else '06'
    return '06'

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Tabla maestra de empresas (fuente única para el selector multitenant)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS empresas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            razon_social TEXT NOT NULL,
            cuit TEXT NOT NULL,
            tenant_id TEXT NOT NULL UNIQUE,
            localidad TEXT DEFAULT ''
        );
    """)
    cursor.execute("SELECT COUNT(*) FROM empresas;")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            "INSERT INTO empresas (razon_social, cuit, tenant_id, localidad) VALUES (?, ?, ?, ?);",
            [
                ("Silo Chico S.A.", "30717802868", "silochico", "Chivilcoy"),
                ("Ezequiel Calabresi", "20270684271", "cala", "Chivilcoy"),
            ],
        )

    # 0. Tablas Maestras de Referencia AFIP / SICORE (Normalizadas)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ref_provincias (
            codigo TEXT PRIMARY KEY,
            descripcion TEXT NOT NULL
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ref_tipos_documento (
            codigo TEXT PRIMARY KEY,
            descripcion TEXT NOT NULL
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ref_condiciones_iva (
            codigo TEXT PRIMARY KEY,
            descripcion TEXT NOT NULL
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ref_regimenes_ganancias (
            codigo TEXT PRIMARY KEY,
            descripcion TEXT NOT NULL,
            porcentaje_insc REAL,
            porcentaje_no_insc REAL,
            monto_no_sujeto REAL
        );
    """)

    # Poblar tablas maestras con normativa oficial AFIP
    cursor.executemany("INSERT OR IGNORE INTO ref_provincias (codigo, descripcion) VALUES (?, ?);", [
        ('00', 'Ciudad Autónoma de Buenos Aires'), ('01', 'Buenos Aires'), ('02', 'Catamarca'),
        ('03', 'Córdoba'), ('04', 'Corrientes'), ('05', 'Entre Ríos'), ('06', 'Jujuy'),
        ('07', 'Mendoza'), ('08', 'La Rioja'), ('09', 'Salta'), ('10', 'San Juan'),
        ('11', 'San Luis'), ('12', 'Santa Fe'), ('13', 'Santiago del Estero'), ('14', 'Tucumán'),
        ('15', 'Chaco'), ('16', 'Chubut'), ('17', 'Formosa'), ('18', 'Misiones'),
        ('19', 'Neuquén'), ('20', 'La Pampa'), ('21', 'Río Negro'), ('22', 'Santa Cruz'),
        ('23', 'Tierra del Fuego')
    ])

    cursor.executemany("INSERT OR IGNORE INTO ref_tipos_documento (codigo, descripcion) VALUES (?, ?);", [
        ('80', 'CUIT'), ('86', 'CUIL'), ('87', 'CDI'), ('96', 'DNI'), ('99', 'Sin identificar')
    ])

    cursor.executemany("INSERT OR IGNORE INTO ref_condiciones_iva (codigo, descripcion) VALUES (?, ?);", [
        ('01', 'IVA Responsable Inscripto'), ('02', 'IVA Responsable no Inscripto'),
        ('03', 'IVA no Responsable'), ('04', 'IVA Sujeto Exento'),
        ('05', 'Consumidor Final'), ('08', 'Responsable Monotributo')
    ])

  # 0.1 Tabla de Configuración de la Empresa
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS configuracion_empresa (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            razon_social TEXT, cuit TEXT, condicion_iva TEXT, localidad TEXT, contacto_email TEXT, cit_arba TEXT,
            empresa_activa_id INTEGER DEFAULT 1
        );
    """)
    cursor.execute("SELECT COUNT(*) FROM configuracion_empresa;")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO configuracion_empresa (id, razon_social, cuit, condicion_iva, localidad, contacto_email, cit_arba)
            VALUES (1, 'Silo Chico S.A.', '30711166250', 'Responsable Inscripto', 'Chivilcoy', 'contacto@silochico.com.ar', '');
        """)

    cursor.execute("PRAGMA table_info(configuracion_empresa);")
    cols_conf = [col[1] for col in cursor.fetchall()]
    if 'empresa_activa_id' not in cols_conf:
        cursor.execute("ALTER TABLE configuracion_empresa ADD COLUMN empresa_activa_id INTEGER DEFAULT 1;")
    # Perfil fiscal: empresa alcanzada por magnitud → agente de estos regímenes
    for col_flag in (
        "agente_retencion_iibb",
        "agente_retencion_ganancias",
        "agente_percepcion_iibb",
    ):
        if col_flag not in cols_conf:
            cursor.execute(f"ALTER TABLE configuracion_empresa ADD COLUMN {col_flag} INTEGER DEFAULT 1;")
    cursor.execute("""
        UPDATE configuracion_empresa
        SET agente_retencion_iibb = COALESCE(agente_retencion_iibb, 1),
            agente_retencion_ganancias = COALESCE(agente_retencion_ganancias, 1),
            agente_percepcion_iibb = COALESCE(agente_percepcion_iibb, 1)
        WHERE id = 1;
    """)
 
    cursor.execute("PRAGMA table_info(ordenes_pago);")
    cols_op = [col[1] for col in cursor.fetchall()]
    if 'empresa_id' not in cols_op:
        cursor.execute("ALTER TABLE ordenes_pago ADD COLUMN empresa_id INTEGER DEFAULT 1;")

    cursor.execute("PRAGMA table_info(entidades);")
    cols_entidades = [col[1] for col in cursor.fetchall()]
    if 'provincia_codigo' not in cols_entidades:
        cursor.execute("ALTER TABLE entidades ADD COLUMN provincia_codigo TEXT DEFAULT '01';")
    if 'tipo_documento' not in cols_entidades:
        cursor.execute("ALTER TABLE entidades ADD COLUMN tipo_documento TEXT DEFAULT '80';")
    if 'condicion_iva_codigo' not in cols_entidades:
        cursor.execute("ALTER TABLE entidades ADD COLUMN condicion_iva_codigo TEXT DEFAULT '01';")
    if 'condicion_iva' not in cols_entidades:
        cursor.execute("ALTER TABLE entidades ADD COLUMN condicion_iva TEXT DEFAULT 'Responsable Inscripto';")
    # Centro de costos: '1' = contabilidad oficial; 'SP' = Sin Partida (cta cte sí, asiento oficial no)
    if 'centro_costo' not in cols_entidades:
        cursor.execute("ALTER TABLE entidades ADD COLUMN centro_costo TEXT DEFAULT '1';")
        cursor.execute("""
            UPDATE entidades
            SET centro_costo = 'SP'
            WHERE UPPER(COALESCE(razon_social, '')) LIKE '%(S/P)%'
               OR UPPER(COALESCE(nombre_fantasia, '')) LIKE '%(S/P)%'
               OR UPPER(COALESCE(razon_social, '')) LIKE '% S/P%'
               OR UPPER(COALESCE(nombre_fantasia, '')) LIKE '% S/P%';
        """)
    # Régimen SICORE por defecto del proveedor (RG 830). Arrendadores/prop. inmueble → 032
    if 'regimen_sicore' not in cols_entidades:
        cursor.execute("ALTER TABLE entidades ADD COLUMN regimen_sicore TEXT DEFAULT '';")
    if 'es_propietario_inmueble' not in cols_entidades:
        cursor.execute("ALTER TABLE entidades ADD COLUMN es_propietario_inmueble INTEGER DEFAULT 0;")

    cursor.executemany(
        """
        INSERT OR IGNORE INTO ref_regimenes_ganancias
        (codigo, descripcion, porcentaje_insc, porcentaje_no_insc, monto_no_sujeto)
        VALUES (?, ?, ?, ?, ?);
        """,
        [
            ("032", "Bienes Inmuebles Rurales (incluye leasing) — RG 830", 6.0, 28.0, 11200.0),
            ("031", "Bienes Inmuebles Urbanos (incluye leasing) — RG 830", 6.0, 28.0, 11200.0),
            ("030", "Alquileres o arrendamientos de bienes muebles — RG 830", 6.0, 28.0, 11200.0),
            ("078", "Enajenación de bienes muebles y de cambio", 2.0, 10.0, 224000.0),
            ("094", "Locaciones de obra y/o servicios", 2.0, 10.0, 67170.0),
            ("095", "Transporte de carga", 0.25, 0.25, 0.0),
            ("025", "Comisiones y auxiliares de comercio", 6.0, 28.0, 0.0),
            ("116", "Honorarios director / síndico / fiduciario", 6.0, 28.0, 0.0),
            ("119", "Profesiones liberales / oficios", 6.0, 28.0, 160000.0),
        ],
    )
    # Actualizar descripción 032 si ya existía con texto viejo
    cursor.execute(
        """
        UPDATE ref_regimenes_ganancias
        SET descripcion=?, porcentaje_insc=6.0, porcentaje_no_insc=28.0, monto_no_sujeto=11200.0
        WHERE codigo='032';
        """,
        ("Bienes Inmuebles Rurales (incluye leasing) — RG 830",),
    )

    # 2. Cuentas Corrientes Bancarias
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ctas_ctes_bancarias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nro_cta_cte TEXT, banco TEXT, titular TEXT, cbu TEXT, moneda TEXT DEFAULT 'ARS',
            acuerdo_cta_cte REAL DEFAULT 0, visa_business REAL DEFAULT 0, tarjeta_rural REAL DEFAULT 0,
            vta_cpd REAL DEFAULT 0, leasing_sgr REAL DEFAULT 0, fw_pesos REAL DEFAULT 0,
            sola_firma REAL DEFAULT 0, credito_aval_sgr REAL DEFAULT 0, baja INTEGER DEFAULT 0,
            empresa_id INTEGER DEFAULT 1
        );
    """)
    cursor.execute("PRAGMA table_info(ctas_ctes_bancarias);")
    cols_ctas = [col[1] for col in cursor.fetchall()]
    if "empresa_id" not in cols_ctas:
        cursor.execute("ALTER TABLE ctas_ctes_bancarias ADD COLUMN empresa_id INTEGER;")
        # Asignación inicial por titular (sin inventar vínculos ambiguos)
        cursor.execute("""
            UPDATE ctas_ctes_bancarias
            SET empresa_id = (
                SELECT e.id FROM empresas e
                WHERE LOWER(REPLACE(ctas_ctes_bancarias.titular, '.', ''))
                      LIKE '%' || LOWER(REPLACE(REPLACE(e.razon_social, ' S.A.', ''), '.', '')) || '%'
                ORDER BY LENGTH(e.razon_social) DESC
                LIMIT 1
            )
            WHERE empresa_id IS NULL;
        """)
        cursor.execute("""
            UPDATE ctas_ctes_bancarias
            SET empresa_id = 1
            WHERE empresa_id IS NULL
              AND LOWER(titular) LIKE '%silo%chico%';
        """)

    _clasificar_cuentas_bancarias(cursor)

    # Columnas de calificaciones (legado Access)
    cursor.execute("PRAGMA table_info(ctas_ctes_bancarias);")
    cols_ctas = [col[1] for col in cursor.fetchall()]
    for col_name, col_sql in [
        ("fecha_acuerdo", "TEXT"),
        ("fw_usd", "REAL DEFAULT 0"),
        ("varios", "TEXT"),
        ("moneda", "TEXT DEFAULT 'ARS'"),
    ]:
        if col_name not in cols_ctas:
            cursor.execute(f"ALTER TABLE ctas_ctes_bancarias ADD COLUMN {col_name} {col_sql};")

    # 3. Movimientos Cta Cte Bancos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movimientos_cta_cte_bancos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cuenta_id INTEGER, fecha_cobro TEXT, fecha_debito TEXT, proveedor TEXT,
            nro_cheque TEXT, haber REAL DEFAULT 0, debe REAL DEFAULT 0, imp_chq REAL DEFAULT 0,
            saldo REAL DEFAULT 0, cta_cte_nro TEXT DEFAULT '1652/8', conciliado INTEGER DEFAULT 0
        );
    """)
    cursor.execute("PRAGMA table_info(movimientos_cta_cte_bancos);")
    cols_mov = [col[1] for col in cursor.fetchall()]
    for col_name, col_sql in [
        ("asiento_id", "INTEGER"),
        ("tipo_operacion", "TEXT"),
        ("id_access", "INTEGER"),
        ("es_prestamo", "INTEGER DEFAULT 0"),
        ("nro_credito", "TEXT"),
        ("nro_cuota", "INTEGER"),
        ("generado_por_credito", "INTEGER DEFAULT 0"),
    ]:
        if col_name not in cols_mov:
            cursor.execute(f"ALTER TABLE movimientos_cta_cte_bancos ADD COLUMN {col_name} {col_sql};")
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mov_bancos_id_access "
        "ON movimientos_cta_cte_bancos(id_access) WHERE id_access IS NOT NULL;"
    )

    # Contabilidad: plan de cuentas + asientos vinculados a la gestión
    init_contabilidad(cursor)

    # 4. Cartera de Cheques
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cartera_cheques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT, nro_cheque TEXT, banco TEXT, cuit_emisor TEXT, librador TEXT,
            fecha_emision TEXT, fecha_pago TEXT, monto REAL, moneda TEXT DEFAULT 'ARS',
            estado TEXT DEFAULT 'En cartera', cuenta_id INTEGER
        );
    """)
    cursor.execute("PRAGMA table_info(cartera_cheques);")
    cols_chq = {col[1] for col in cursor.fetchall()}
    for col, ddl in [
        ("empresa_id", "INTEGER DEFAULT 1"),
        ("cliente_cuit", "TEXT"),
        ("cliente_nombre", "TEXT"),
        ("titular", "TEXT"),
        ("dador", "TEXT"),
        ("es_tercero", "INTEGER DEFAULT 0"),
        ("fecha_recepcion", "TEXT"),
        ("cc_id", "INTEGER"),
        ("movimiento_banco_id", "INTEGER"),
        ("proveedor_cuit", "TEXT"),
        ("op_id", "INTEGER"),
        ("observaciones", "TEXT"),
        ("caja_cc_id", "INTEGER"),
    ]:
        if col not in cols_chq:
            cursor.execute(f"ALTER TABLE cartera_cheques ADD COLUMN {col} {ddl};")

    # Caja chica (pagos menores / cobros en efectivo)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS caja_movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            fecha TEXT NOT NULL,
            tipo TEXT NOT NULL,
            concepto TEXT,
            monto REAL NOT NULL,
            forma TEXT DEFAULT 'Efectivo',
            referencia TEXT,
            cheque_id INTEGER,
            proveedor_cuit TEXT,
            cc_id INTEGER,
            cc_proveedor_id INTEGER,
            usuario_registro TEXT,
            created_at TEXT
        );
    """)

    # Bienes de uso / patrimonio (EECC + carpetas bancarias)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bienes_patrimoniales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            id_access INTEGER,
            tipo_rodado TEXT,
            nro_factura TEXT,
            marca TEXT,
            detalle TEXT,
            fecha_compra TEXT,
            modelo TEXT,
            anio TEXT,
            marca_chasis TEXT,
            nro_chasis TEXT,
            marca_motor TEXT,
            nro_motor TEXT,
            nro_patente TEXT,
            importe_neto REAL DEFAULT 0,
            iva REAL DEFAULT 0,
            otros_impuestos REAL DEFAULT 0,
            total REAL DEFAULT 0,
            porcentaje_iva REAL DEFAULT 0,
            total_usd REAL DEFAULT 0,
            fecha_baja TEXT,
            valor_residual REAL DEFAULT 0,
            comprador TEXT,
            UNIQUE(empresa_id, id_access)
        );
    """)
    cursor.execute("PRAGMA table_info(bienes_patrimoniales);")
    cols_bp = {col[1] for col in cursor.fetchall()}
    for col, ddl in [
        ("categoria", "TEXT"),
        ("valor_historico", "REAL"),
        ("valor_comercial", "REAL DEFAULT 0"),
        ("valor_comercial_usd", "REAL DEFAULT 0"),
        ("tipo_cambio_comercial", "REAL DEFAULT 0"),
        ("fecha_valor_comercial", "TEXT"),
        ("moneda", "TEXT DEFAULT 'ARS'"),
        ("vida_util_anos", "INTEGER"),
        ("tasa_anual_pct", "REAL"),
        ("metodo_amortizacion", "TEXT DEFAULT 'Lineal'"),
        ("amortizacion_acumulada", "REAL DEFAULT 0"),
        ("fecha_inicio_amort", "TEXT"),
        ("ubicacion", "TEXT"),
        ("observaciones", "TEXT"),
        ("activo", "INTEGER DEFAULT 1"),
        ("superficie_ha", "REAL"),
        ("nomenclatura", "TEXT"),
        ("partida", "TEXT"),
        ("usuario_registro", "TEXT"),
    ]:
        if col not in cols_bp:
            cursor.execute(f"ALTER TABLE bienes_patrimoniales ADD COLUMN {col} {ddl};")
    # Completar categoría / valor histórico / vida útil sobre datos importados
    cursor.execute("""
        UPDATE bienes_patrimoniales
        SET valor_historico = CASE
            WHEN COALESCE(valor_historico, 0) > 0.01 THEN valor_historico
            WHEN COALESCE(importe_neto, 0) > 0.01 THEN importe_neto
            WHEN COALESCE(total, 0) > 0.01 THEN total
            ELSE COALESCE(valor_historico, 0)
        END
        WHERE COALESCE(valor_historico, 0) < 0.01;
    """)
    cursor.execute("""
        UPDATE bienes_patrimoniales
        SET categoria = CASE
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%COSECH%' THEN 'Cosechadora'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%SEMBRAD%' THEN 'Sembradora'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%TOLVA%'
              OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%ACOPLAD%' THEN 'Tolva / Acoplado'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%EXTRACTOR%' THEN 'Extractora'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%PICK%'
              OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%CAMIONETA%'
              OR UPPER(TRIM(COALESCE(tipo_rodado,''))) IN ('SW4','HILUX') THEN 'Camioneta'
            WHEN UPPER(REPLACE(TRIM(COALESCE(tipo_rodado,'')),'Ó','O')) LIKE '%CAMION%' THEN 'Camión'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%TRACTOR%' THEN 'Tractor'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%CABEZAL%' THEN 'Cabezal'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%FERTILIZ%' THEN 'Fertilizadora'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%DESMALEZ%' THEN 'Desmalezadora'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%PULVERIZ%' THEN 'Pulverizador'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%RETRO%' THEN 'Retroexcavadora'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%NIVELAD%' THEN 'Niveladora'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%GUINCHE%'
              OR UPPER(REPLACE(TRIM(COALESCE(tipo_rodado,'')),'Ú','U')) LIKE '%GRUA%' THEN 'Guinche / Grúa'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%CASILLA%'
              OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%INSTAL%' THEN 'Instalación'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%INMUEBLE%'
              OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%PROPIEDAD%'
              OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%CAMPO%'
              OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%LOTE%'
              OR UPPER(TRIM(COALESCE(categoria,''))) = 'PROPIEDAD' THEN 'Propiedad'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%MUEBLE%' THEN 'Muebles'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) IN ('AUTO','SEDAN','RODADO')
              OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%AUTO%' THEN 'Rodado'
            WHEN UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%MAQUINARIA%'
              OR TRIM(COALESCE(tipo_rodado,'')) = '' THEN
                CASE
                    WHEN UPPER(TRIM(COALESCE(categoria,''))) IN (
                        'TRACTOR','COSECHADORA','SEMBRADORA','CAMIONETA','CAMIÓN','CAMION',
                        'TOLVA / ACOPLADO','PULVERIZADOR','PROPIEDAD','INSTALACIÓN','INSTALACION',
                        'RODADO','MUEBLES','EXTRACTORA','CABEZAL','FERTILIZADORA','DESMALEZADORA'
                    ) THEN categoria
                    ELSE COALESCE(NULLIF(TRIM(categoria),''), 'Maquinaria')
                END
            ELSE COALESCE(NULLIF(TRIM(categoria),''), 'Maquinaria')
        END
        WHERE COALESCE(categoria,'') IN ('', 'Maquinaria', 'Rodado', 'Instalacion', 'Propiedad', 'Muebles', 'Otro')
           OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%TRACTOR%'
           OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%COSECH%'
           OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%PICK%'
           OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%ACOPLAD%'
           OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%TOLVA%'
           OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%SEMBRAD%'
           OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%CAMION%'
           OR UPPER(TRIM(COALESCE(tipo_rodado,''))) LIKE '%PULVERIZ%';
    """)
    cursor.execute("""
        UPDATE bienes_patrimoniales
        SET tasa_anual_pct = CASE
                WHEN categoria IN ('Rodado','Camión','Camioneta') THEN 20
                WHEN categoria = 'Propiedad' THEN 2
                WHEN categoria IN ('Instalacion','Instalación') THEN 5
                WHEN categoria = 'Muebles' THEN 10
                ELSE 10
            END,
            vida_util_anos = CASE
                WHEN categoria IN ('Rodado','Camión','Camioneta') THEN 5
                WHEN categoria = 'Propiedad' THEN 50
                WHEN categoria IN ('Instalacion','Instalación') THEN 20
                WHEN categoria = 'Muebles' THEN 10
                ELSE 10
            END
        WHERE COALESCE(tasa_anual_pct, 0) < 0.01 OR COALESCE(vida_util_anos, 0) < 1;
    """)
    cursor.execute("""
        UPDATE bienes_patrimoniales
        SET activo = 0
        WHERE TRIM(COALESCE(fecha_baja,'')) != '';
    """)
    cursor.execute("""
        UPDATE bienes_patrimoniales
        SET activo = COALESCE(activo, 1)
        WHERE TRIM(COALESCE(fecha_baja,'')) = '' AND activo IS NULL;
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bienes_amortizaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            bien_id INTEGER NOT NULL,
            ejercicio TEXT NOT NULL,
            fecha_cierre TEXT,
            monto REAL DEFAULT 0,
            asiento_id INTEGER,
            usuario_registro TEXT,
            created_at TEXT,
            UNIQUE(empresa_id, bien_id, ejercicio)
        );
    """)

    # Normalizar estados legacy (vacíos → En cartera; no tocar Emitido/Depositado/Entregado)
    cursor.execute("""
        UPDATE cartera_cheques SET estado = 'En cartera'
        WHERE LOWER(TRIM(COALESCE(estado,''))) IN ('en cartera', 'en carteras', '')
          AND UPPER(TRIM(COALESCE(tipo,''))) != 'EMITIDO';
    """)
    # Cheques emitidos mal importados como "En cartera": si hay débito bancario con mismo nº+monto → Emitido
    cursor.execute("""
        UPDATE cartera_cheques
        SET tipo = 'Emitido',
            estado = 'Emitido',
            cuenta_id = (
                SELECT m.cuenta_id FROM movimientos_cta_cte_bancos m
                WHERE TRIM(COALESCE(m.nro_cheque,'')) = TRIM(COALESCE(cartera_cheques.nro_cheque,''))
                  AND ABS(COALESCE(m.haber,0) - COALESCE(cartera_cheques.monto,0)) < 0.5
                  AND COALESCE(m.haber,0) > 0
                ORDER BY m.id DESC LIMIT 1
            ),
            movimiento_banco_id = (
                SELECT m.id FROM movimientos_cta_cte_bancos m
                WHERE TRIM(COALESCE(m.nro_cheque,'')) = TRIM(COALESCE(cartera_cheques.nro_cheque,''))
                  AND ABS(COALESCE(m.haber,0) - COALESCE(cartera_cheques.monto,0)) < 0.5
                  AND COALESCE(m.haber,0) > 0
                ORDER BY m.id DESC LIMIT 1
            )
        WHERE LOWER(TRIM(COALESCE(estado,''))) LIKE '%cartera%'
          AND EXISTS (
            SELECT 1 FROM movimientos_cta_cte_bancos m
            WHERE TRIM(COALESCE(m.nro_cheque,'')) = TRIM(COALESCE(cartera_cheques.nro_cheque,''))
              AND ABS(COALESCE(m.haber,0) - COALESCE(cartera_cheques.monto,0)) < 0.5
              AND COALESCE(m.haber,0) > 0
          );
    """)

    # 5. Créditos y Préstamos (cabecera Access + moneda ARS/USD)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS creditos_prestamos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            banco TEXT, nro_credito TEXT, descripcion TEXT, monto_total REAL, moneda TEXT DEFAULT 'ARS',
            total_cuotas INTEGER, cuotas_pagadas INTEGER DEFAULT 0, monto_cuota REAL, fecha_vencimiento_proxima TEXT
        );
    """)
    cursor.execute("PRAGMA table_info(creditos_prestamos);")
    cols_cred = [col[1] for col in cursor.fetchall()]
    for col, ddl in [
        ("empresa_id", "INTEGER DEFAULT 1"),
        ("entidad_financiera", "TEXT"),
        ("origen_detalle", "TEXT"),
        ("tna", "REAL DEFAULT 0"),
        ("tea", "REAL DEFAULT 0"),
        ("amortizacion", "TEXT"),
        ("plazo", "INTEGER DEFAULT 0"),
        ("fecha_operacion", "TEXT"),
        ("cuenta_id", "INTEGER"),
        ("estado", "TEXT DEFAULT 'Activo'"),
        ("observaciones", "TEXT"),
    ]:
        if col not in cols_cred:
            cursor.execute(f"ALTER TABLE creditos_prestamos ADD COLUMN {col} {ddl};")
    # Copiar banco → entidad_financiera si quedó vacío
    cursor.execute("""
        UPDATE creditos_prestamos
        SET entidad_financiera = COALESCE(NULLIF(entidad_financiera, ''), banco)
        WHERE entidad_financiera IS NULL OR entidad_financiera = '';
    """)
    # Access: TNA/TEA ×100 (2900 → 29.00). Normalizar filas legacy en disco.
    cursor.execute("""
        UPDATE creditos_prestamos
        SET tna = ROUND(tna / 100.0, 4)
        WHERE ABS(COALESCE(tna, 0)) >= 200;
    """)
    cursor.execute("""
        UPDATE creditos_prestamos
        SET tea = ROUND(tea / 100.0, 4)
        WHERE ABS(COALESCE(tea, 0)) >= 200;
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS creditos_cuotas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            credito_id INTEGER NOT NULL,
            nro_cuota INTEGER NOT NULL,
            cuenta_id INTEGER,
            fecha_pago TEXT,
            capital REAL DEFAULT 0,
            intereses REAL DEFAULT 0,
            impuestos REAL DEFAULT 0,
            cargos REAL DEFAULT 0,
            total_pagar REAL DEFAULT 0,
            capital_usd REAL DEFAULT 0,
            intereses_usd REAL DEFAULT 0,
            cargos_usd REAL DEFAULT 0,
            tipo_cambio REAL DEFAULT 0,
            capital_ars REAL DEFAULT 0,
            intereses_ars REAL DEFAULT 0,
            cargos_ars REAL DEFAULT 0,
            total_ars REAL DEFAULT 0,
            nota TEXT,
            estado TEXT DEFAULT 'Pendiente',
            movimiento_banco_id INTEGER,
            id_access INTEGER,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (credito_id) REFERENCES creditos_prestamos(id)
        );
    """)
    cursor.execute("PRAGMA table_info(creditos_cuotas);")
    cols_cuotas = [col[1] for col in cursor.fetchall()]
    for col, ddl in [
        ("id_access", "INTEGER"),
        ("movimiento_banco_id", "INTEGER"),
    ]:
        if col not in cols_cuotas:
            cursor.execute(f"ALTER TABLE creditos_cuotas ADD COLUMN {col} {ddl};")
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_creditos_cuotas_id_access "
        "ON creditos_cuotas(id_access) WHERE id_access IS NOT NULL;"
    )

    # 6. Padrón ARBA
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS padron_arba (
            cuit TEXT PRIMARY KEY, razon_social TEXT, alicuota_percepcion REAL DEFAULT 1.75, alicuota_retencion REAL DEFAULT 3.00
        );
    """)

    # 7. Retenciones SICORE e IIBB
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS retenciones_sicore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT, razon_social TEXT, cuit TEXT, base_imponible REAL,
            regimen TEXT, importe_retenido REAL, nro_comprobante TEXT, tipo_retencion TEXT DEFAULT 'SICORE'
        );
    """)

    cursor.execute("PRAGMA table_info(retenciones_sicore);")
    cols = [col[1] for col in cursor.fetchall()]
    if 'tipo_retencion' not in cols:
        cursor.execute("ALTER TABLE retenciones_sicore ADD COLUMN tipo_retencion TEXT DEFAULT 'SICORE';")

    # Importación de retenciones históricas
    cursor.execute("SELECT COUNT(*) FROM retenciones_sicore;")
    if cursor.fetchone()[0] < 1000:
        ret_path = os.path.join(BASE_DIR, EXCEL_RETENCIONES)
        if os.path.exists(ret_path):
            try:
                xls_ret = pd.ExcelFile(ret_path)
                df_ret = pd.read_excel(xls_ret, sheet_name=0)
                for idx, row in df_ret.iterrows():
                    fec = safe_date(row.get('Fecha Operacion', ''))
                    prov = str(row.get('Nombre Proveedor', '')).strip()
                    cuit = str(row.get('Cuit Sujeto a Retencion', '')).strip()
                    base = float(row.get('BAse Imponible', 0) or 0)
                    imp = float(row.get('Importe Retenido', 0) or 0)
                    reg_desc = str(row.get('REgimen Retencion', '06'))
                    reg = obtener_codigo_regimen(reg_desc)
                    nro_id = int(row.get('id', 0) or 0)
                    nro_comp = f"2026-HIST-{nro_id:04d}" if nro_id > 0 else f"2026-HIST-{idx+1:04d}"
                    
                    cursor.execute("""
                        INSERT INTO retenciones_sicore (fecha, razon_social, cuit, base_imponible, regimen, importe_retenido, nro_comprobante, tipo_retencion)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'SICORE');
                    """, (fec, prov, cuit, base, reg, imp, nro_comp))
                print("✅ Se cargaron exitosamente las retenciones históricas.")
            except Exception as e:
                print(f"Error importando retenciones.xlsx: {e}")

 # 8. Órdenes de Pago
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ordenes_pago (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nro_orden TEXT, cuit TEXT, fecha TEXT, total_pagado REAL,
            retencion_iibb REAL DEFAULT 0, retencion_sicore REAL DEFAULT 0,
            forma_pago TEXT, observaciones TEXT, usuario_registro TEXT,
            empresa_id INTEGER DEFAULT 1
        );
    """)

    # 9. Cuentas Corrientes Proveedores
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cuentas_corrientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            entidad_id TEXT, tipo_comprobante TEXT, numero_comprobante TEXT, 
            forma_pago TEXT, nro_cheque TEXT, fecha TEXT, vencimiento TEXT, 
            neto REAL, iva REAL, debe REAL DEFAULT 0, haber REAL DEFAULT 0, 
            total REAL, estado TEXT DEFAULT 'Pendiente', usuario_registro TEXT,
            empresa_id INTEGER DEFAULT 1
        );
    """)
    cursor.execute("PRAGMA table_info(cuentas_corrientes);")
    cols_cc = {col[1] for col in cursor.fetchall()}
    if "observaciones" not in cols_cc:
        cursor.execute("ALTER TABLE cuentas_corrientes ADD COLUMN observaciones TEXT;")
    if "asiento_id" not in cols_cc:
        cursor.execute("ALTER TABLE cuentas_corrientes ADD COLUMN asiento_id INTEGER;")

    # 10. Agro / Márgenes brutos — campos, lotes, campañas, arrendamientos
    from agro_campania import init_agro_schema
    init_agro_schema(cursor)
    from agro_almacen import init_almacen_schema
    init_almacen_schema(cursor)
    init_audit_schema(cursor)
    from ganaderia import init_ganaderia_schema
    init_ganaderia_schema(cursor)
    from actividades_imputacion import init_actividades_schema
    init_actividades_schema(cursor)
    from flujo_proyeccion_import import init_flujo_proyeccion_schema
    init_flujo_proyeccion_schema(cursor)

    conn.commit()
    conn.close()

def _col_excel(row_or_cols, *candidates):
    """Busca una columna tolerando mayúsculas y caracteres raros (Nº, etc.)."""
    if hasattr(row_or_cols, "index"):
        cols = list(row_or_cols.index)
    else:
        cols = list(row_or_cols)

    def norm(s):
        return "".join(ch for ch in str(s).lower() if ch.isalnum())

    mapa = {norm(c): c for c in cols}
    for cand in candidates:
        key = norm(cand)
        if key in mapa:
            return mapa[key]
    return None


def importar_movimientos_bancarios_historicos(forzar: bool = False):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM movimientos_cta_cte_bancos;")
    cant = cursor.fetchone()[0]

    if cant > 0 and not forzar:
        conn.close()
        return {"status": "skip", "message": f"Ya hay {cant} movimientos cargados."}

    candidatos = [
        os.path.join(BASE_DIR, EXCEL_BANCOS),
        os.path.join(BASE_DIR, "tablas", "movimientos bancarios.xlsx"),
        os.path.join(BASE_DIR, "tablas", "bancos y mov.xlsx"),
        os.path.join(BASE_DIR, "movimientos bancarios.xlsx"),
    ]
    excel_path = next((p for p in candidatos if os.path.exists(p)), None)
    if not excel_path:
        conn.close()
        print("AVISO: No se encontro Excel de movimientos bancarios en tablas/.")
        return {"status": "error", "message": "Excel de movimientos no encontrado."}

    try:
        print(f"Importando movimientos bancarios desde: {excel_path}")
        df_bancos = pd.read_excel(excel_path, sheet_name=0)
        col_cta = _col_excel(df_bancos.columns, "Cta Cte", "CtaCte", "cta_cte")
        if not col_cta:
            raise ValueError("No se encontró la columna 'Cta Cte' en el Excel.")

        df_validos = df_bancos[df_bancos[col_cta].notnull()].copy()
        col_fecha = _col_excel(df_bancos.columns, "FechaCobro", "Fecha Cobro", "Fecha")
        col_fecha_alt = _col_excel(df_bancos.columns, "Fecha")
        col_debito = _col_excel(df_bancos.columns, "Debitados", "Fecha Debito", "FechaDébito")
        col_prov = _col_excel(df_bancos.columns, "Proveedor", "Detalle")
        col_chq = _col_excel(df_bancos.columns, "Nº Cheque", "N° Cheque", "No Cheque", "Nro Cheque", "Cheque")
        col_haber = _col_excel(df_bancos.columns, "HABER", "Haber")
        col_debe = _col_excel(df_bancos.columns, "DEBE", "Debe")
        col_imp = _col_excel(df_bancos.columns, "IMPALCHQ", "Imp Cheque", "Imp. Cheque")
        col_id_access = _col_excel(df_bancos.columns, "Id", "ID", "id")
        col_es_prest = _col_excel(df_bancos.columns, "Credito Bancario", "Crédito Bancario")
        col_nro_cred = _col_excel(df_bancos.columns, "Nro de Credito", "Nro de Crédito", "Nº Credito")
        col_nro_cuota = _col_excel(df_bancos.columns, "Nº Cuota", "Nro Cuota", "N° Cuota")

        # Mapa nro cta -> id cuenta
        cursor.execute("SELECT id, nro_cta_cte FROM ctas_ctes_bancarias;")
        mapa_cta = {
            str(r["nro_cta_cte"]).strip(): int(r["id"])
            for r in cursor.fetchall()
            if r["nro_cta_cte"]
        }

        if forzar and cant > 0:
            cursor.execute("DELETE FROM movimientos_cta_cte_bancos;")

        registros = []
        for _, row in df_validos.iterrows():
            cta_nro = str(row[col_cta]).strip()
            if not cta_nro or cta_nro.lower() == "nan":
                continue

            fec_cobro = ""
            if col_fecha:
                fec_cobro = safe_date(row.get(col_fecha))
            if not fec_cobro and col_fecha_alt:
                fec_cobro = safe_date(row.get(col_fecha_alt))

            fec_debito = safe_date(row.get(col_debito)) if col_debito else ""
            conciliado = 1 if fec_debito else 0

            prov = "Movimiento Bancario"
            if col_prov and pd.notnull(row.get(col_prov)):
                prov = str(row.get(col_prov)).strip() or prov

            nro_chq = ""
            if col_chq and pd.notnull(row.get(col_chq)):
                chq_raw = row.get(col_chq)
                if str(chq_raw) not in ("0.0", "0", "nan", "None"):
                    nro_chq = str(chq_raw).replace(".0", "").strip()

            haber = float(row.get(col_haber, 0) or 0) if col_haber else 0.0
            debe = float(row.get(col_debe, 0) or 0) if col_debe else 0.0
            imp_chq = float(row.get(col_imp, 0) or 0) if col_imp else 0.0
            cuenta_id = mapa_cta.get(cta_nro, 1)

            id_access = None
            if col_id_access and pd.notnull(row.get(col_id_access)):
                try:
                    id_access = int(float(row.get(col_id_access)))
                except (TypeError, ValueError):
                    id_access = None

            es_prestamo = 0
            if col_es_prest and pd.notnull(row.get(col_es_prest)):
                val = row.get(col_es_prest)
                es_prestamo = 1 if (val is True or str(val).strip().lower() in ("1", "true", "si", "sí", "yes")) else 0

            nro_credito = ""
            if col_nro_cred and pd.notnull(row.get(col_nro_cred)):
                nro_credito = str(row.get(col_nro_cred)).strip()
                if nro_credito.lower() in ("nan", "none"):
                    nro_credito = ""

            nro_cuota = None
            if col_nro_cuota and pd.notnull(row.get(col_nro_cuota)):
                try:
                    nro_cuota = int(float(row.get(col_nro_cuota)))
                except (TypeError, ValueError):
                    nro_cuota = None

            tipo_op = "Cuota crédito" if es_prestamo else None
            if tipo_op is None:
                if haber > 0 and debe <= 0:
                    tipo_op = "Debito"
                elif debe > 0:
                    tipo_op = "Deposito"
                else:
                    tipo_op = "Debito"

            registros.append((
                cuenta_id, fec_cobro, fec_debito, prov, nro_chq,
                haber, debe, imp_chq, cta_nro, conciliado,
                id_access, es_prestamo, nro_credito or None, nro_cuota, tipo_op,
            ))

        cursor.executemany("""
            INSERT INTO movimientos_cta_cte_bancos
            (cuenta_id, fecha_cobro, fecha_debito, proveedor, nro_cheque, haber, debe, imp_chq, cta_cte_nro, conciliado,
             id_access, es_prestamo, nro_credito, nro_cuota, tipo_operacion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, registros)
        conn.commit()
        print(f"OK: Se importaron {len(registros)} movimientos bancarios.")
        conn.close()
        return {"status": "success", "imported": len(registros)}
    except Exception as e:
        conn.close()
        print(f"ERROR importando movimientos bancarios: {e}")
        return {"status": "error", "message": str(e)}


def backfill_id_access_y_prestamos_desde_excel() -> dict:
    """Marca movimientos existentes con Id Access + flag préstamo sin duplicar filas."""
    candidatos = [
        os.path.join(BASE_DIR, EXCEL_BANCOS),
        os.path.join(BASE_DIR, "tablas", "movimientos bancarios.xlsx"),
        os.path.join(BASE_DIR, "tablas", "bancos y mov.xlsx"),
        os.path.join(BASE_DIR, "movimientos bancarios.xlsx"),
    ]
    excel_path = next((p for p in candidatos if os.path.exists(p)), None)
    if not excel_path:
        return {"status": "skip", "message": "Excel no encontrado."}

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS n FROM movimientos_cta_cte_bancos WHERE id_access IS NOT NULL;")
    ya = int(cursor.fetchone()["n"] or 0)
    cursor.execute("SELECT COUNT(*) AS n FROM movimientos_cta_cte_bancos;")
    total = int(cursor.fetchone()["n"] or 0)
    if total == 0:
        conn.close()
        return {"status": "skip", "message": "Sin movimientos en banco."}

    try:
        df = pd.read_excel(excel_path, sheet_name=0)
        col_cta = _col_excel(df.columns, "Cta Cte", "CtaCte", "cta_cte")
        col_id = _col_excel(df.columns, "Id", "ID", "id")
        col_fecha = _col_excel(df.columns, "FechaCobro", "Fecha Cobro", "Fecha")
        col_prov = _col_excel(df.columns, "Proveedor", "Detalle")
        col_haber = _col_excel(df.columns, "HABER", "Haber")
        col_debe = _col_excel(df.columns, "DEBE", "Debe")
        col_es_prest = _col_excel(df.columns, "Credito Bancario", "Crédito Bancario")
        col_nro_cred = _col_excel(df.columns, "Nro de Credito", "Nro de Crédito", "Nº Credito")
        col_nro_cuota = _col_excel(df.columns, "Nº Cuota", "Nro Cuota", "N° Cuota")
        if not col_cta or not col_id:
            conn.close()
            return {"status": "error", "message": "Faltan columnas Id / Cta Cte en Excel."}

        actualizados = 0
        prestamos = 0
        for _, row in df.iterrows():
            if pd.isnull(row.get(col_cta)) or pd.isnull(row.get(col_id)):
                continue
            try:
                id_access = int(float(row.get(col_id)))
            except (TypeError, ValueError):
                continue
            cta_nro = str(row.get(col_cta)).strip()
            if not cta_nro or cta_nro.lower() == "nan":
                continue
            fec = safe_date(row.get(col_fecha)) if col_fecha else ""
            haber = float(row.get(col_haber, 0) or 0) if col_haber else 0.0
            debe = float(row.get(col_debe, 0) or 0) if col_debe else 0.0
            prov = ""
            if col_prov and pd.notnull(row.get(col_prov)):
                prov = str(row.get(col_prov)).strip()

            es_prestamo = 0
            if col_es_prest and pd.notnull(row.get(col_es_prest)):
                val = row.get(col_es_prest)
                es_prestamo = 1 if (val is True or str(val).strip().lower() in ("1", "true", "si", "sí", "yes")) else 0

            nro_credito = None
            if col_nro_cred and pd.notnull(row.get(col_nro_cred)):
                nro_credito = str(row.get(col_nro_cred)).strip()
                if nro_credito.lower() in ("nan", "none", ""):
                    nro_credito = None

            nro_cuota = None
            if col_nro_cuota and pd.notnull(row.get(col_nro_cuota)):
                try:
                    nro_cuota = int(float(row.get(col_nro_cuota)))
                except (TypeError, ValueError):
                    nro_cuota = None

            cursor.execute(
                "SELECT id FROM movimientos_cta_cte_bancos WHERE id_access = ? LIMIT 1;",
                (id_access,),
            )
            if cursor.fetchone():
                if es_prestamo:
                    cursor.execute(
                        """
                        UPDATE movimientos_cta_cte_bancos
                        SET es_prestamo = 1,
                            nro_credito = COALESCE(?, nro_credito),
                            nro_cuota = COALESCE(?, nro_cuota),
                            tipo_operacion = CASE
                                WHEN COALESCE(generado_por_credito, 0) = 1 OR es_prestamo = 1 OR ? = 1
                                THEN 'Cuota crédito' ELSE tipo_operacion END
                        WHERE id_access = ?;
                        """,
                        (nro_credito, nro_cuota, es_prestamo, id_access),
                    )
                    prestamos += 1
                continue

            # Match por cta + fecha + montos (tolerancia 1 centavo)
            params = [cta_nro, round(haber, 2), round(debe, 2)]
            q = """
                SELECT id FROM movimientos_cta_cte_bancos
                WHERE TRIM(cta_cte_nro) = ?
                  AND ROUND(COALESCE(haber,0), 2) = ?
                  AND ROUND(COALESCE(debe,0), 2) = ?
                  AND id_access IS NULL
            """
            if fec:
                q += " AND TRIM(COALESCE(fecha_cobro,'')) = ?"
                params.append(fec)
            if prov:
                q += " AND TRIM(COALESCE(proveedor,'')) = ?"
                params.append(prov)
            q += " LIMIT 1;"
            cursor.execute(q, params)
            hit = cursor.fetchone()
            if not hit and fec:
                # reintento sin proveedor
                cursor.execute(
                    """
                    SELECT id FROM movimientos_cta_cte_bancos
                    WHERE TRIM(cta_cte_nro) = ?
                      AND ROUND(COALESCE(haber,0), 2) = ?
                      AND ROUND(COALESCE(debe,0), 2) = ?
                      AND TRIM(COALESCE(fecha_cobro,'')) = ?
                      AND id_access IS NULL
                    LIMIT 1;
                    """,
                    (cta_nro, round(haber, 2), round(debe, 2), fec),
                )
                hit = cursor.fetchone()
            if not hit:
                continue

            tipo = "Cuota crédito" if es_prestamo else None
            if tipo:
                cursor.execute(
                    """
                    UPDATE movimientos_cta_cte_bancos
                    SET id_access = ?, es_prestamo = ?, nro_credito = ?, nro_cuota = ?,
                        tipo_operacion = COALESCE(?, tipo_operacion)
                    WHERE id = ?;
                    """,
                    (id_access, es_prestamo, nro_credito, nro_cuota, tipo, hit["id"]),
                )
            else:
                cursor.execute(
                    """
                    UPDATE movimientos_cta_cte_bancos
                    SET id_access = ?, es_prestamo = ?, nro_credito = ?, nro_cuota = ?
                    WHERE id = ?;
                    """,
                    (id_access, es_prestamo, nro_credito, nro_cuota, hit["id"]),
                )
            actualizados += 1
            if es_prestamo:
                prestamos += 1

        conn.commit()
        cursor.execute("SELECT COUNT(*) AS n FROM movimientos_cta_cte_bancos WHERE id_access IS NOT NULL;")
        con_id = int(cursor.fetchone()["n"] or 0)
        cursor.execute("SELECT COUNT(*) AS n FROM movimientos_cta_cte_bancos WHERE COALESCE(es_prestamo,0)=1;")
        n_prest = int(cursor.fetchone()["n"] or 0)
        conn.close()
        return {
            "status": "success",
            "matched": actualizados,
            "prestamos_marcados": prestamos,
            "con_id_access": con_id,
            "prestamos_total": n_prest,
            "ya_tenian_id": ya,
        }
    except Exception as e:
        conn.close()
        return {"status": "error", "message": str(e)}


def _excel_prestamos_path() -> Optional[str]:
    candidatos = [
        os.path.join(BASE_DIR, EXCEL_BANCOS),
        os.path.join(BASE_DIR, "tablas", "movimientos bancarios.xlsx"),
        os.path.join(BASE_DIR, "tablas", "bancos y mov.xlsx"),
        os.path.join(BASE_DIR, "movimientos bancarios.xlsx"),
    ]
    return next((p for p in candidatos if os.path.exists(p)), None)


def _fila_excel_es_usd(row, col_moneda, col_tc) -> bool:
    """USD si Moneda=Dolares o hay TC de conversión a pesos."""
    if col_tc and pd.notnull(row.get(col_tc)):
        try:
            if float(row.get(col_tc) or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    if col_moneda and pd.notnull(row.get(col_moneda)):
        mon = str(row.get(col_moneda)).strip().lower()
        if any(x in mon for x in ("dolar", "dólar", "usd", "u$s")):
            return True
    return False


def _cargar_mapa_excel_prestamos() -> dict:
    """Mapa id_access -> datos de cuota/crédito desde Excel (hoja Prestamos + Credito Bancario)."""
    excel_path = _excel_prestamos_path()
    if not excel_path:
        return {}
    frames = []
    try:
        xls = pd.ExcelFile(excel_path)
        df0 = pd.read_excel(xls, sheet_name=0)
        col_flag = _col_excel(df0.columns, "Credito Bancario", "Crédito Bancario")
        if col_flag:
            frames.append(df0[df0[col_flag] == True].copy())
        if "Prestamos" in xls.sheet_names:
            frames.append(pd.read_excel(xls, sheet_name="Prestamos"))
    except Exception:
        return {}
    if not frames:
        return {}
    df = pd.concat(frames, ignore_index=True)
    col_id = _col_excel(df.columns, "Id", "ID", "id")
    if not col_id:
        return {}
    col_nro = _col_excel(df.columns, "Nro de Credito", "Nro de Crédito", "Nº Credito")
    col_cuota = _col_excel(df.columns, "Nº Cuota", "Nro Cuota", "N° Cuota")
    col_tna = _col_excel(df.columns, "TNA")
    col_tea = _col_excel(df.columns, "TEA")
    col_amort = _col_excel(df.columns, "Sistema de amortizacion", "Sistema de amortización", "Amortizacion")
    col_moneda = _col_excel(df.columns, "Moneda")
    col_tc = _col_excel(df.columns, "TC", "Tipo de Cambio", "Tipo Cambio")
    col_cap_usd = _col_excel(df.columns, "Capital U$S", "Capital USD")
    col_int_usd = _col_excel(df.columns, "Intereses U$S", "Intereses USD")
    col_cargos = _col_excel(df.columns, "Cargos", "Otros Gastos", "Otros")
    col_int = _col_excel(df.columns, "Intereses")
    col_imp = _col_excel(df.columns, "Impuestos")
    col_plazo = _col_excel(df.columns, "Plazo")
    col_detalle = _col_excel(df.columns, "Detalle del Bien", "Detalle", "Origen")
    col_prov = _col_excel(df.columns, "Proveedor")
    col_haber = _col_excel(df.columns, "HABER", "Haber")
    col_fecha = _col_excel(df.columns, "FechaCobro", "Fecha Cobro", "Fecha")
    col_fecha_op = _col_excel(df.columns, "Fecha")
    col_monto_orig = _col_excel(df.columns, "Importe Monto origen U$S", "Importe Monto origen $")

    mapa = {}
    for _, row in df.iterrows():
        if pd.isnull(row.get(col_id)):
            continue
        try:
            id_access = int(float(row.get(col_id)))
        except (TypeError, ValueError):
            continue
        es_usd = _fila_excel_es_usd(row, col_moneda, col_tc)
        tc = 0.0
        if col_tc and pd.notnull(row.get(col_tc)):
            try:
                tc = float(row.get(col_tc) or 0)
            except (TypeError, ValueError):
                tc = 0.0
        tna = float(row.get(col_tna) or 0) if col_tna else 0.0
        tea = float(row.get(col_tea) or 0) if col_tea else 0.0
        # Access a veces ×100; Excel Prestamos ya trae % real (3.5 / 115.62)
        tna = _normalizar_tasa_pct(tna)
        tea = _normalizar_tasa_pct(tea)
        nro = ""
        if col_nro and pd.notnull(row.get(col_nro)):
            nro = str(row.get(col_nro)).strip()
            if nro.lower() in ("nan", "none"):
                nro = ""
        nro_cuota = 1
        if col_cuota and pd.notnull(row.get(col_cuota)):
            try:
                nro_cuota = int(float(row.get(col_cuota)))
            except (TypeError, ValueError):
                nro_cuota = 1
        amort = ""
        if col_amort and pd.notnull(row.get(col_amort)):
            amort = str(row.get(col_amort)).strip()
        plazo = ""
        if col_plazo and pd.notnull(row.get(col_plazo)):
            plazo = str(row.get(col_plazo)).strip()
        detalle = ""
        if col_detalle and pd.notnull(row.get(col_detalle)):
            detalle = str(row.get(col_detalle)).strip()
        entidad = ""
        if col_prov and pd.notnull(row.get(col_prov)):
            entidad = str(row.get(col_prov)).strip()
        haber = float(row.get(col_haber) or 0) if col_haber else 0.0
        cap_usd = float(row.get(col_cap_usd) or 0) if col_cap_usd else 0.0
        int_usd = float(row.get(col_int_usd) or 0) if col_int_usd else 0.0
        intereses = float(row.get(col_int) or 0) if col_int else 0.0
        impuestos = float(row.get(col_imp) or 0) if col_imp else 0.0
        cargos = float(row.get(col_cargos) or 0) if col_cargos else 0.0
        fecha_pago = safe_date(row.get(col_fecha)) if col_fecha else ""
        fecha_op = safe_date(row.get(col_fecha_op)) if col_fecha_op else ""
        monto_origen = 0.0
        if col_monto_orig and pd.notnull(row.get(col_monto_orig)):
            try:
                monto_origen = float(row.get(col_monto_orig) or 0)
            except (TypeError, ValueError):
                monto_origen = 0.0

        # Preferir hoja Prestamos (datos más ricos) si el Id se repite
        prev = mapa.get(id_access)
        info = {
            "id_access": id_access,
            "nro_credito": nro,
            "nro_cuota": nro_cuota,
            "tna": tna,
            "tea": tea,
            "amortizacion": amort,
            "plazo": plazo,
            "detalle": detalle,
            "entidad": entidad,
            "moneda": "USD" if es_usd else "ARS",
            "tipo_cambio": tc,
            "capital_usd": cap_usd,
            "intereses_usd": int_usd,
            "intereses": intereses,
            "impuestos": impuestos,
            "cargos": cargos,
            "haber": haber,
            "fecha_pago": fecha_pago,
            "fecha_operacion": fecha_op,
            "monto_origen": monto_origen,
        }
        if prev is None or (es_usd and prev.get("moneda") != "USD") or (tna and not prev.get("tna")):
            mapa[id_access] = info
        elif prev is not None:
            # merge: completar vacíos
            for k, v in info.items():
                if v in (None, "", 0, 0.0) and prev.get(k) not in (None, "", 0, 0.0):
                    continue
                if prev.get(k) in (None, "", 0, 0.0) and v not in (None, "", 0, 0.0):
                    prev[k] = v
            mapa[id_access] = prev
    return mapa


def sincronizar_creditos_desde_movimientos_prestamo() -> dict:
    """Arma/actualiza créditos y cuotas desde movimientos préstamo + Excel (TNA/TEA/USD/TC)."""
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    excel_map = _cargar_mapa_excel_prestamos()

    cursor.execute(
        """
        SELECT * FROM movimientos_cta_cte_bancos
        WHERE COALESCE(es_prestamo, 0) = 1
          AND TRIM(COALESCE(nro_credito, '')) != ''
        ORDER BY nro_credito, COALESCE(nro_cuota, 0), fecha_cobro, id;
        """
    )
    movs = [dict(r) for r in cursor.fetchall()]
    if not movs:
        conn.close()
        return {"status": "skip", "message": "No hay movimientos de préstamo etiquetados."}

    from collections import defaultdict
    por_credito = defaultdict(list)
    for m in movs:
        por_credito[str(m["nro_credito"]).strip()].append(m)

    creados = 0
    actualizados = 0
    cuotas_ok = 0
    cuotas_upd = 0
    usd_count = 0

    for nro, lista in por_credito.items():
        # Enriquecer cabecera con primera fila Excel disponible del grupo
        metas = []
        for m in lista:
            xa = excel_map.get(m.get("id_access"))
            if xa:
                metas.append(xa)
        meta0 = metas[0] if metas else {}
        es_usd = any(x.get("moneda") == "USD" for x in metas) or any(
            float(excel_map.get(m.get("id_access"), {}).get("tipo_cambio") or 0) > 0 for m in lista
        )
        moneda = "USD" if es_usd else "ARS"
        if es_usd:
            usd_count += 1

        tna = next((float(x.get("tna") or 0) for x in metas if float(x.get("tna") or 0)), 0.0)
        tea = next((float(x.get("tea") or 0) for x in metas if float(x.get("tea") or 0)), 0.0)
        amort = next((x.get("amortizacion") or "" for x in metas if x.get("amortizacion")), "")
        detalle = next((x.get("detalle") or "" for x in metas if x.get("detalle")), "")
        entidad = next((x.get("entidad") or "" for x in metas if x.get("entidad")), "")
        if not entidad:
            entidad = (lista[0].get("proveedor") or "").strip() or "Entidad financiera"
        fecha_op = next((x.get("fecha_operacion") or "" for x in metas if x.get("fecha_operacion")), "") or (
            lista[0].get("fecha_cobro") or ""
        )

        if moneda == "USD":
            monto_total = sum(float(x.get("capital_usd") or 0) for x in metas) or sum(
                float(x.get("monto_origen") or 0) for x in metas
            )
            if monto_total < 0.01:
                # fallback: haber / tc promedio
                tots = []
                for x in metas:
                    tc = float(x.get("tipo_cambio") or 0)
                    hab = float(x.get("haber") or 0)
                    if tc > 0 and hab > 0:
                        tots.append(hab / tc)
                monto_total = sum(tots) if tots else sum(float(m.get("haber") or 0) for m in lista)
        else:
            monto_total = sum(float(m.get("haber") or 0) for m in lista)

        plazo_txt = next((x.get("plazo") or "" for x in metas if x.get("plazo")), "")
        try:
            plazo_n = int("".join(ch for ch in plazo_txt if ch.isdigit()) or len(lista))
        except ValueError:
            plazo_n = len(lista)

        cuenta_id = lista[0].get("cuenta_id")
        cursor.execute(
            "SELECT id FROM creditos_prestamos WHERE TRIM(nro_credito)=? AND COALESCE(empresa_id,1)=? LIMIT 1;",
            (nro, empresa_id),
        )
        existente = cursor.fetchone()
        if existente:
            credito_id = int(existente["id"])
            cursor.execute(
                """
                UPDATE creditos_prestamos SET
                    banco=?, entidad_financiera=?, origen_detalle=?, descripcion=?,
                    monto_total=?, moneda=?, tna=?, tea=?, amortizacion=?,
                    plazo=?, fecha_operacion=COALESCE(NULLIF(fecha_operacion,''), ?),
                    cuenta_id=COALESCE(cuenta_id, ?), estado='Activo'
                WHERE id=?;
                """,
                (
                    entidad, entidad, detalle or "Importado cta cte", detalle or "Importado cta cte",
                    monto_total, moneda, tna, tea, amort,
                    plazo_n, fecha_op, cuenta_id, credito_id,
                ),
            )
            actualizados += 1
        else:
            cursor.execute(
                """
                INSERT INTO creditos_prestamos (
                    banco, entidad_financiera, nro_credito, origen_detalle, descripcion,
                    monto_total, moneda, tna, tea, amortizacion, plazo, fecha_operacion,
                    cuenta_id, total_cuotas, cuotas_pagadas, estado, empresa_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'Activo', ?);
                """,
                (
                    entidad, entidad, nro, detalle or "Importado cta cte", detalle or "Importado cta cte",
                    monto_total, moneda, tna, tea, amort, plazo_n, fecha_op,
                    cuenta_id, len(lista), empresa_id,
                ),
            )
            credito_id = cursor.lastrowid
            creados += 1

        for m in lista:
            id_access = m.get("id_access")
            xa = excel_map.get(id_access) or {}
            nro_cuota = int(xa.get("nro_cuota") or m.get("nro_cuota") or 0) or 1
            mov_id = int(m["id"])
            debitado = bool((m.get("fecha_debito") or "").strip())
            estado = "Pagada" if debitado else "Pendiente"
            tc = float(xa.get("tipo_cambio") or 0)
            cap_usd = float(xa.get("capital_usd") or 0)
            int_usd = float(xa.get("intereses_usd") or 0)
            intereses = float(xa.get("intereses") or 0)
            impuestos = float(xa.get("impuestos") or 0)
            cargos = float(xa.get("cargos") or 0)
            haber = float(m.get("haber") or 0)
            fecha_pago = (xa.get("fecha_pago") or m.get("fecha_cobro") or "")
            if moneda == "USD":
                capital_ars = round(cap_usd * tc, 2) if tc > 0 and cap_usd else haber
                intereses_ars = round(int_usd * tc, 2) if tc > 0 and int_usd else intereses
                cargos_ars = round(float(xa.get("cargos") or 0) * tc, 2) if tc > 0 else cargos
                total_ars = haber if haber > 0 else round((cap_usd + int_usd) * tc, 2)
                capital = 0
                total_pagar = total_ars
            else:
                capital_ars = 0
                intereses_ars = 0
                cargos_ars = 0
                total_ars = haber
                # En ARS, intereses/impuestos/cargos del Excel; capital ≈ haber - esos
                capital = max(haber - intereses - impuestos - cargos, 0)
                total_pagar = haber

            cursor.execute(
                "SELECT id FROM creditos_cuotas WHERE movimiento_banco_id = ? OR (id_access IS NOT NULL AND id_access = ?) LIMIT 1;",
                (mov_id, id_access),
            )
            cuota_row = cursor.fetchone()
            if cuota_row:
                cursor.execute(
                    """
                    UPDATE creditos_cuotas SET
                        credito_id=?, nro_cuota=?, cuenta_id=?, fecha_pago=?,
                        capital=?, intereses=?, impuestos=?, cargos=?, total_pagar=?,
                        capital_usd=?, intereses_usd=?, cargos_usd=?, tipo_cambio=?,
                        capital_ars=?, intereses_ars=?, cargos_ars=?, total_ars=?,
                        estado=?, movimiento_banco_id=?, id_access=COALESCE(?, id_access)
                    WHERE id=?;
                    """,
                    (
                        credito_id, nro_cuota, m.get("cuenta_id"), fecha_pago,
                        capital, intereses, impuestos, cargos, total_pagar,
                        cap_usd, int_usd, float(xa.get("cargos") or 0) if moneda == "USD" else 0,
                        tc, capital_ars, intereses_ars, cargos_ars, total_ars,
                        estado, mov_id, id_access, cuota_row["id"],
                    ),
                )
                cuotas_upd += 1
            else:
                cursor.execute(
                    """
                    INSERT INTO creditos_cuotas (
                        credito_id, nro_cuota, cuenta_id, fecha_pago,
                        capital, intereses, impuestos, cargos, total_pagar,
                        capital_usd, intereses_usd, cargos_usd, tipo_cambio,
                        capital_ars, intereses_ars, cargos_ars, total_ars,
                        nota, estado, movimiento_banco_id, id_access, empresa_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        credito_id, nro_cuota, m.get("cuenta_id"), fecha_pago,
                        capital, intereses, impuestos, cargos, total_pagar,
                        cap_usd, int_usd, float(xa.get("cargos") or 0) if moneda == "USD" else 0,
                        tc, capital_ars, intereses_ars, cargos_ars, total_ars,
                        f"Access Id {id_access}" if id_access else "",
                        estado, mov_id, id_access, empresa_id,
                    ),
                )
                cuotas_ok += 1

        _refrescar_estados_cuotas_por_debito(cursor, credito_id)
        _sincronizar_resumen_credito(cursor, credito_id)

    conn.commit()
    conn.close()
    return {
        "status": "success",
        "creditos_creados": creados,
        "creditos_actualizados": actualizados,
        "cuotas_vinculadas": cuotas_ok,
        "cuotas_actualizadas": cuotas_upd,
        "creditos_detectados": len(por_credito),
        "creditos_usd": usd_count,
        "excel_filas": len(excel_map),
    }


def _refrescar_estados_cuotas_por_debito(cursor, credito_id: int):
    """Si el movimiento tiene fecha_débito → cuota Pagada; si no → Pendiente (flujo proyectado)."""
    cursor.execute(
        """
        UPDATE creditos_cuotas
        SET estado = CASE
            WHEN EXISTS (
                SELECT 1 FROM movimientos_cta_cte_bancos m
                WHERE m.id = creditos_cuotas.movimiento_banco_id
                  AND TRIM(COALESCE(m.fecha_debito, '')) != ''
            ) THEN 'Pagada'
            ELSE 'Pendiente'
        END
        WHERE credito_id = ?;
        """,
        (credito_id,),
    )
    # Crédito histórico si no queda ninguna pendiente
    cursor.execute(
        """
        SELECT COUNT(*) AS pend
        FROM creditos_cuotas
        WHERE credito_id = ? AND UPPER(COALESCE(estado,'')) != 'PAGADA';
        """,
        (credito_id,),
    )
    pend = int(cursor.fetchone()["pend"] or 0)
    cursor.execute(
        "UPDATE creditos_prestamos SET estado = ? WHERE id = ?;",
        ("Historico" if pend == 0 else "Activo", credito_id),
    )

init_db()
importar_movimientos_bancarios_historicos()

# --- MODELOS PYDANTIC ---
class EmpresaActivaModel(BaseModel):
    empresa_activa_id: int

class EmpresaItemModel(BaseModel):
    razon_social: str
    cuit: str
    tenant_id: str
    localidad: Optional[str] = ""

class FacturaRenglonModel(BaseModel):
    descripcion: str = ""
    destino_tipo: str = "Gasto General / Estructura"
    actividad_id: Optional[int] = None
    cuenta_imputacion_id: Optional[int] = None
    actividad_nombre: Optional[str] = ""
    cuenta_nombre: Optional[str] = ""
    neto: float = 0.0
    alicuota_iva: float = 0.21
    item_almacen_id: Optional[int] = None
    item_nombre: Optional[str] = ""
    unidad: Optional[str] = ""
    cantidad: float = 0.0
    precio_unitario: float = 0.0
    precio_unitario_usd: float = 0.0
    tipo_cambio: float = 0.0
    campania_codigo: Optional[str] = ""
    detalle_tipo: Optional[str] = ""  # insumo | labor | gasto


class FacturaModel(BaseModel):
    cuit: str
    tipo_comprobante: str
    numero_comprobante: str
    fecha: str
    vencimiento: str
    neto: float = 0.0
    iva: float = 0.0
    total: float
    usuario_registro: str = "Administrador"
    percepcion_iibb: float = 0.0
    renglones: Optional[List[FacturaRenglonModel]] = None

class OrdenPagoModel(BaseModel):
    cuit: str
    fecha: str
    total_pagado: float
    retencion_iibb: float = 0.0
    retencion_sicore: float = 0.0
    regimen_sicore: Optional[str] = "078"
    forma_pago: str = "Transferencia"
    nro_cheque: Optional[str] = ""
    fecha_vto_cheque: Optional[str] = ""
    cheques_cartera: Optional[List[int]] = None
    solo_retenciones: bool = False
    base_imponible: float = 0.0
    observaciones: Optional[str] = ""
    usuario_registro: str = "Administrador"
    # True = solo aplica retenciones en CC (sin transferencia/cheque/efectivo).
    # Las facturas quedan Pendiente hasta cargar el pago real después.
    alicuota_iibb: Optional[float] = None  # decimal (0.0175) o % (1.75)

class AjustarCuentaModel(BaseModel):
    es_cuenta_ajuste: int

class AjusteSaldoCcModel(BaseModel):
    """Crédito o débito solo de cuenta corriente: sin asiento y sin retención."""
    cuit: str
    sentido: str  # credito | debito
    importe: float
    fecha: str
    concepto: str
    usuario_registro: str = "Administrador"

class CentroCostoModel(BaseModel):
    centro_costo: str = "1"  # '1' oficial | 'SP' sin partida

class EntidadModel(BaseModel):
    cuit: str
    razon_social: str
    nombre_fantasia: Optional[str] = ""
    domicilio: Optional[str] = ""
    localidad: Optional[str] = ""
    provincia: Optional[str] = ""
    es_proveedor: int = 1
    es_cliente: int = 0
    centro_costo: str = "1"
    regimen_sicore: Optional[str] = ""
    es_propietario_inmueble: int = 0
    es_cuenta_ajuste: int = 0


class ConfiguracionModel(BaseModel):
    razon_social: str
    cuit: str
    condicion_iva: str
    localidad: str
    contacto_email: str
    cit_arba: Optional[str] = ""
    agente_retencion_iibb: int = 1
    agente_retencion_ganancias: int = 1
    agente_percepcion_iibb: int = 1

class CuentaBancariaModel(BaseModel):
    nro_cta_cte: str
    banco: str
    titular: str
    cbu: str = ""
    moneda: str = "ARS"
    fecha_acuerdo: Optional[str] = ""
    acuerdo_cta_cte: float = 0.0
    visa_business: float = 0.0
    tarjeta_rural: float = 0.0
    vta_cpd: float = 0.0
    leasing_sgr: float = 0.0
    fw_usd: float = 0.0
    fw_pesos: float = 0.0
    sola_firma: float = 0.0
    credito_aval_sgr: float = 0.0
    varios: Optional[str] = ""
    es_cuenta_bancaria: int = 1

class CuentaBancariaTipoModel(BaseModel):
    es_cuenta_bancaria: int

class MovimientoCtaCteBancoModel(BaseModel):
    cuenta_id: Optional[int] = 1
    fecha_cobro: str
    fecha_debito: Optional[str] = ""
    proveedor: str
    nro_cheque: Optional[str] = ""
    fecha_vencimiento: Optional[str] = ""
    tipo_operacion: str
    monto: float
    imp_chq: Optional[float] = 0.0
    aplica_imp_cheque: bool = False
    cta_cte_nro: Optional[str] = "1652/8"
    cuenta_destino_id: Optional[int] = None

class ConciliacionManualModel(BaseModel):
    fecha_debito: str
    ids: Optional[List[int]] = None

class ConciliacionLoteModel(BaseModel):
    ids: List[int]
    fecha_debito: str

class ChequeModel(BaseModel):
    tipo: str = "Tercero"  # Tercero | Propio | Emitido
    nro_cheque: str
    banco: str = ""
    cuit_emisor: Optional[str] = ""
    librador: Optional[str] = ""
    titular: Optional[str] = ""
    dador: Optional[str] = ""
    cliente_cuit: Optional[str] = ""
    cliente_nombre: Optional[str] = ""
    fecha_emision: Optional[str] = ""
    fecha_pago: str = ""
    fecha_recepcion: Optional[str] = ""
    monto: float
    moneda: str = "ARS"
    estado: str = "En cartera"
    cuenta_id: Optional[int] = None
    es_tercero: int = 1
    observaciones: Optional[str] = ""
    impactar_cc_cliente: bool = True


class ChequeDepositarModel(BaseModel):
    cuenta_id: int
    fecha: Optional[str] = ""
    observaciones: Optional[str] = ""


class ChequeAplicarPagoModel(BaseModel):
    proveedor_cuit: str
    fecha: Optional[str] = ""
    observaciones: Optional[str] = ""


class CajaMovimientoModel(BaseModel):
    fecha: str
    monto: float
    concepto: str = ""
    forma: str = "Efectivo"
    referencia: Optional[str] = ""
    proveedor_cuit: Optional[str] = ""
    impactar_cc_proveedor: bool = False
    usuario_registro: str = "Administrador"


class CajaCobrarChequeModel(BaseModel):
    fecha: Optional[str] = ""
    observaciones: Optional[str] = ""
    usuario_registro: str = "Administrador"


class BienPatrimonialModel(BaseModel):
    categoria: str = "Maquinaria"  # Tractor | Cosechadora | Camioneta | Tolva / Acoplado | ...
    tipo_rodado: Optional[str] = ""
    marca: Optional[str] = ""
    modelo: Optional[str] = ""
    detalle: Optional[str] = ""
    anio: Optional[str] = ""
    nro_patente: Optional[str] = ""
    nro_chasis: Optional[str] = ""
    nro_motor: Optional[str] = ""
    nro_factura: Optional[str] = ""
    fecha_compra: Optional[str] = ""
    fecha_inicio_amort: Optional[str] = ""
    valor_historico: float = 0.0
    importe_neto: Optional[float] = None
    iva: Optional[float] = 0.0
    total: Optional[float] = None
    valor_residual: float = 0.0
    valor_comercial: float = 0.0
    valor_comercial_usd: float = 0.0
    tipo_cambio_comercial: float = 0.0
    fecha_valor_comercial: Optional[str] = ""
    moneda: str = "ARS"
    vida_util_anos: Optional[int] = None
    tasa_anual_pct: Optional[float] = None
    metodo_amortizacion: str = "Lineal"
    amortizacion_acumulada: float = 0.0
    ubicacion: Optional[str] = ""
    superficie_ha: Optional[float] = None
    nomenclatura: Optional[str] = ""
    partida: Optional[str] = ""
    observaciones: Optional[str] = ""
    activo: int = 1
    fecha_baja: Optional[str] = ""
    usuario_registro: str = "Administrador"


class AmortizarBienesModel(BaseModel):
    ejercicio: Optional[str] = ""  # ej. 2025/2026
    fecha_cierre: Optional[str] = ""  # default 30/06 del ejercicio
    aplicar: bool = False  # False = solo simulación
    generar_asiento: bool = False
    usuario_registro: str = "Administrador"


class CreditoModel(BaseModel):
    entidad_financiera: str = ""
    banco: Optional[str] = ""  # alias legado
    nro_credito: str
    origen_detalle: Optional[str] = ""
    descripcion: Optional[str] = ""
    monto_total: float
    moneda: str = "ARS"  # ARS | USD
    tna: float = 0.0
    tea: float = 0.0
    amortizacion: Optional[str] = ""
    plazo: int = 0
    fecha_operacion: Optional[str] = ""
    fecha_vencimiento_proxima: Optional[str] = ""
    cuenta_id: Optional[int] = None
    total_cuotas: int = 0
    cuotas_pagadas: int = 0
    monto_cuota: float = 0.0
    observaciones: Optional[str] = ""
    estado: str = "Activo"

class CreditoCuotaModel(BaseModel):
    nro_cuota: int
    cuenta_id: Optional[int] = None
    fecha_pago: str
    capital: float = 0.0
    intereses: float = 0.0
    impuestos: float = 0.0
    cargos: float = 0.0
    total_pagar: float = 0.0
    capital_usd: float = 0.0
    intereses_usd: float = 0.0
    cargos_usd: float = 0.0
    tipo_cambio: float = 0.0
    capital_ars: float = 0.0
    intereses_ars: float = 0.0
    cargos_ars: float = 0.0
    total_ars: float = 0.0
    nota: Optional[str] = ""
    estado: str = "Pendiente"
    proyectar_en_banco: bool = True
    movimiento_banco_id: Optional[int] = None
    id_access: Optional[int] = None

class CreditoCuotasLoteModel(BaseModel):
    cuotas: List[CreditoCuotaModel]
    reemplazar: bool = True


class EscalaAparceriaModel(BaseModel):
    rendimiento_tn_ha: float
    porcentaje: float
    orden: int = 0


class LoteAgroModel(BaseModel):
    codigo: Optional[str] = ""
    nombre: str
    superficie_base: float = 0.0
    lat: Optional[float] = None
    lng: Optional[float] = None
    geojson: Optional[str] = ""
    orden: int = 0


class ContratoArrendamientoModel(BaseModel):
    modalidad: str = "kilos_fijos"  # kilos_fijos | aparceria | monto_fijo
    grano: str = "Soja"
    kilos_por_ha: float = 0.0
    porcentaje_base: float = 0.0
    monto_fijo: float = 0.0
    vigencia_desde: Optional[str] = ""
    vigencia_hasta: Optional[str] = ""
    observaciones: Optional[str] = ""
    escalas: Optional[List[EscalaAparceriaModel]] = None
    campania_id: Optional[int] = None


class CampoAgroModel(BaseModel):
    tipo: str = "propio"  # propio | arrendado
    nombre: str
    superficie_total: float = 0.0
    arrendador_cuit: Optional[str] = ""
    arrendador_razon: Optional[str] = ""
    arrendador_domicilio: Optional[str] = ""
    arrendador_telefono: Optional[str] = ""
    arrendador_email: Optional[str] = ""
    arrendador_localidad: Optional[str] = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    direccion_ref: Optional[str] = ""
    notas: Optional[str] = ""
    lotes: Optional[List[LoteAgroModel]] = None
    contrato: Optional[ContratoArrendamientoModel] = None
    sync_proveedor: bool = True


class CampaniaAgroModel(BaseModel):
    codigo: str
    nombre: Optional[str] = ""
    activa: int = 1
    fecha_inicio: Optional[str] = ""
    fecha_fin: Optional[str] = ""
    notas: Optional[str] = ""


class PlanLoteItemModel(BaseModel):
    lote_id: int
    cultivo_antecesor: Optional[str] = ""
    cultivo_planificado: Optional[str] = ""
    superficie: Optional[float] = None
    notas: Optional[str] = ""
    modificado_manual: int = 1


class PlanLoteLoteModel(BaseModel):
    items: List[PlanLoteItemModel]


# --- FUNCIONES AUXILIARES DE CORRELATIVIDAD ---

def obtener_siguiente_certificado_db(tipo: str, base_defecto: int) -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT nro_comprobante FROM retenciones_sicore WHERE UPPER(tipo_retencion) = ? OR (tipo_retencion IS NULL AND ? = 'SICORE');", (tipo.upper(), tipo.upper()))
    rows = cursor.fetchall()
    conn.close()

    max_secuencia = base_defecto
    for r in rows:
        nro = r['nro_comprobante']
        if nro and '-' in str(nro):
            partes = str(nro).split('-')
            if len(partes) >= 2:
                try:
                    num = int(partes[-1])
                    if num > max_secuencia:
                        max_secuencia = num
                except ValueError:
                    pass
    return max_secuencia

# --- ENDPOINTS API ---

@app.get("/api/configuracion")
def obtener_configuracion():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM configuracion_empresa WHERE id = 1;")
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {}

@app.post("/api/configuracion")
def guardar_configuracion(data: ConfiguracionModel):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO configuracion_empresa (
            id, razon_social, cuit, condicion_iva, localidad, contacto_email, cit_arba,
            agente_retencion_iibb, agente_retencion_ganancias, agente_percepcion_iibb
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            razon_social=excluded.razon_social, cuit=excluded.cuit, condicion_iva=excluded.condicion_iva,
            localidad=excluded.localidad, contacto_email=excluded.contacto_email, cit_arba=excluded.cit_arba,
            agente_retencion_iibb=excluded.agente_retencion_iibb,
            agente_retencion_ganancias=excluded.agente_retencion_ganancias,
            agente_percepcion_iibb=excluded.agente_percepcion_iibb;
    """, (
        data.razon_social, data.cuit, data.condicion_iva, data.localidad, data.contacto_email, data.cit_arba,
        1 if data.agente_retencion_iibb else 0,
        1 if data.agente_retencion_ganancias else 0,
        1 if data.agente_percepcion_iibb else 0,
    ))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Configuración guardada correctamente."}

@app.get("/api/entidades")
def obtener_entidades(
    rol: Optional[str] = None,
    incluir_ajuste: bool = False,
    solo_ajuste: bool = False,
):
    """
    rol=cliente → solo es_cliente=1 (pueden ser también proveedores).
    rol=proveedor → solo es_proveedor=1.
    Por defecto oculta cuentas de ajuste / banco (es_cuenta_ajuste=1).
    incluir_ajuste=true → incluye todas.
    solo_ajuste=true → solo ctas de ajuste.
    """
    conn = get_db()
    cursor = conn.cursor()
    q = "SELECT * FROM entidades WHERE 1=1"
    params: list = []
    rol_n = (rol or "").strip().lower()
    if rol_n in ("cliente", "clientes"):
        q += " AND COALESCE(es_cliente, 0) = 1"
    elif rol_n in ("proveedor", "proveedores"):
        q += " AND COALESCE(es_proveedor, 0) = 1"
    if solo_ajuste:
        q += " AND COALESCE(es_cuenta_ajuste, 0) = 1"
    elif not incluir_ajuste:
        q += " AND COALESCE(es_cuenta_ajuste, 0) = 0"
    q += " ORDER BY COALESCE(NULLIF(TRIM(nombre_fantasia),''), razon_social) COLLATE NOCASE ASC;"
    cursor.execute(q, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def _normalizar_centro_costo(valor: Optional[str], razon: str = "", fantasia: str = "") -> str:
    v = (valor or "").strip().upper()
    texto = f"{razon} {fantasia}".upper()
    if v in ("SP", "S/P", "SIN_PARTIDA", "SIN PARTIDA"):
        return "SP"
    if "(S/P)" in texto or " S/P" in texto or texto.endswith("S/P"):
        return "SP"
    return "1"

@app.post("/api/entidades/sincronizar_access")
def api_sincronizar_padron_access():
    """Actualiza el padrón desde tablas/proveedores.xlsx (Access),
    marcando locadores/arrendadores y S/P (CUIT placeholder / centro SP)."""
    try:
        from sincronizar_padron_access import sincronizar_padron_access
        result = sincronizar_padron_access()
    except Exception as exc:
        raise HTTPException(500, f"No se pudo sincronizar el padrón: {exc}")
    if result.get("status") != "ok":
        raise HTTPException(400, result.get("message") or "Error al sincronizar")
    return result


@app.post("/api/entidades")
def guardar_entidad(data: EntidadModel):
    conn = get_db()
    cursor = conn.cursor()
    cuit_clean = "".join(filter(str.isdigit, str(data.cuit)))
    if not cuit_clean or not (data.razon_social or "").strip():
        conn.close()
        raise HTTPException(status_code=400, detail="CUIT y razón social son obligatorios.")
    nombre = (data.nombre_fantasia or data.razon_social or "").strip()
    razon = (data.razon_social or "").strip()
    centro = _normalizar_centro_costo(data.centro_costo, razon, nombre)
    es_prop = 1 if data.es_propietario_inmueble else 0
    es_ajuste = 1 if data.es_cuenta_ajuste else 0
    # Propietario de inmueble → SICORE 032 (RG 830) de antemano
    reg = (data.regimen_sicore or "").strip()
    if es_prop and not reg:
        reg = "032"
    if reg and len(reg) <= 3 and reg.isdigit():
        reg = reg.zfill(3)

    cursor.execute("PRAGMA table_info(entidades);")
    cols = {r[1] for r in cursor.fetchall()}
    if "regimen_sicore" not in cols:
        cursor.execute("ALTER TABLE entidades ADD COLUMN regimen_sicore TEXT DEFAULT '';")
    if "es_propietario_inmueble" not in cols:
        cursor.execute("ALTER TABLE entidades ADD COLUMN es_propietario_inmueble INTEGER DEFAULT 0;")
    if "es_cuenta_ajuste" not in cols:
        cursor.execute("ALTER TABLE entidades ADD COLUMN es_cuenta_ajuste INTEGER DEFAULT 0;")

    cursor.execute("SELECT cuit FROM entidades WHERE REPLACE(cuit, '-', '') = ?;", (cuit_clean,))
    existe = cursor.fetchone()
    if existe:
        cursor.execute("""
            UPDATE entidades
            SET razon_social = ?, nombre_fantasia = ?, domicilio = ?, localidad = ?,
                provincia = ?, es_proveedor = ?, es_cliente = ?, centro_costo = ?,
                regimen_sicore = ?, es_propietario_inmueble = ?, es_cuenta_ajuste = ?
            WHERE REPLACE(cuit, '-', '') = ?;
        """, (razon, nombre, data.domicilio or "", data.localidad or "",
              data.provincia or "", data.es_proveedor, data.es_cliente, centro,
              reg, es_prop, es_ajuste, cuit_clean))
    else:
        cursor.execute("""
            INSERT INTO entidades (
                cuit, razon_social, nombre_fantasia, domicilio, localidad, provincia,
                es_proveedor, es_cliente, centro_costo, regimen_sicore,
                es_propietario_inmueble, es_cuenta_ajuste
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (cuit_clean, razon, nombre, data.domicilio or "", data.localidad or "",
              data.provincia or "", data.es_proveedor, data.es_cliente, centro, reg,
              es_prop, es_ajuste))
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "message": "Entidad guardada.",
        "centro_costo": centro,
        "regimen_sicore": reg,
        "es_propietario_inmueble": es_prop,
        "es_cuenta_ajuste": es_ajuste,
    }

@app.delete("/api/entidades/{cuit}")
def eliminar_entidad(cuit: str):
    conn = get_db()
    cursor = conn.cursor()
    cuit_clean = "".join(filter(str.isdigit, str(cuit)))
    cursor.execute("DELETE FROM entidades WHERE REPLACE(cuit, '-', '') = ?;", (cuit_clean,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Entidad eliminada."}

@app.put("/api/entidades/{cuit}/ajuste")
def marcar_cuenta_ajuste(cuit: str, data: AjustarCuentaModel):
    conn = get_db()
    cursor = conn.cursor()
    cuit_clean = "".join(filter(str.isdigit, str(cuit)))
    cursor.execute("UPDATE entidades SET es_cuenta_ajuste = ? WHERE REPLACE(cuit, '-', '') = ?;", (data.es_cuenta_ajuste, cuit_clean))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Estado de cuenta de ajuste actualizado."}

@app.put("/api/entidades/{cuit}/centro_costo")
def marcar_centro_costo(cuit: str, data: CentroCostoModel):
    conn = get_db()
    cursor = conn.cursor()
    cuit_clean = "".join(filter(str.isdigit, str(cuit)))
    cursor.execute("SELECT razon_social, nombre_fantasia FROM entidades WHERE REPLACE(cuit, '-', '') = ?;", (cuit_clean,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Entidad no encontrada.")
    centro = _normalizar_centro_costo(data.centro_costo, row["razon_social"] or "", row["nombre_fantasia"] or "")
    cursor.execute(
        "UPDATE entidades SET centro_costo = ? WHERE REPLACE(cuit, '-', '') = ?;",
        (centro, cuit_clean),
    )
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "centro_costo": centro,
        "message": "Centro de costos S/P (sin asiento oficial)." if centro == "SP" else "Centro de costos 1 (contabilidad oficial).",
    }

@app.get("/api/bancos/cuentas")
@app.get("/api/ctas_ctes_bancarias")
def listar_cuentas_bancarias(tipo: str = "bancarias"):
    """
    tipo:
      - bancarias: solo cuentas de banco reales (default)
      - pago: clientes / cuentas de pago del legado Access
      - todas: sin filtrar por tipo
    """
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    tipo_norm = (tipo or "bancarias").strip().lower()
    query = """
        SELECT * FROM ctas_ctes_bancarias
        WHERE baja = 0 AND empresa_id = ?
    """
    params = [empresa_id]
    if tipo_norm in ("bancarias", "banco", "bank"):
        query += " AND COALESCE(es_cuenta_bancaria, 1) = 1"
    elif tipo_norm in ("pago", "pagos", "cliente", "clientes"):
        query += " AND COALESCE(es_cuenta_bancaria, 1) = 0"
    query += " ORDER BY banco ASC;"
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

@app.post("/api/bancos/cuentas")
def crear_cuenta_bancaria(data: CuentaBancariaModel):
    empresa_id = get_empresa_activa_id()
    es_banco = int(data.es_cuenta_bancaria)
    if data.banco and data.nro_cta_cte and data.banco.strip().upper() == data.nro_cta_cte.strip().upper():
        es_banco = 0
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ctas_ctes_bancarias (
            nro_cta_cte, banco, titular, cbu, moneda, fecha_acuerdo,
            acuerdo_cta_cte, visa_business, tarjeta_rural, vta_cpd, leasing_sgr,
            fw_usd, fw_pesos, sola_firma, credito_aval_sgr, varios,
            empresa_id, es_cuenta_bancaria
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (
        data.nro_cta_cte, data.banco, data.titular, data.cbu or "", data.moneda,
        data.fecha_acuerdo or "", data.acuerdo_cta_cte, data.visa_business,
        data.tarjeta_rural, data.vta_cpd, data.leasing_sgr, data.fw_usd,
        data.fw_pesos, data.sola_firma, data.credito_aval_sgr, data.varios or "",
        empresa_id, es_banco,
    ))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Cuenta bancaria registrada con éxito."}

@app.put("/api/bancos/cuentas/{cuenta_id}")
def actualizar_cuenta_bancaria(cuenta_id: int, data: CuentaBancariaModel):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE ctas_ctes_bancarias SET
            nro_cta_cte=?, banco=?, titular=?, cbu=?, moneda=?, fecha_acuerdo=?,
            acuerdo_cta_cte=?, visa_business=?, tarjeta_rural=?, vta_cpd=?, leasing_sgr=?,
            fw_usd=?, fw_pesos=?, sola_firma=?, credito_aval_sgr=?, varios=?,
            es_cuenta_bancaria=?
        WHERE id = ?;
    """, (
        data.nro_cta_cte, data.banco, data.titular, data.cbu or "", data.moneda,
        data.fecha_acuerdo or "", data.acuerdo_cta_cte, data.visa_business,
        data.tarjeta_rural, data.vta_cpd, data.leasing_sgr, data.fw_usd,
        data.fw_pesos, data.sola_firma, data.credito_aval_sgr, data.varios or "",
        int(data.es_cuenta_bancaria), cuenta_id,
    ))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Calificación / cuenta actualizada."}

@app.patch("/api/bancos/cuentas/{cuenta_id}/tipo")
def marcar_tipo_cuenta(cuenta_id: int, data: CuentaBancariaTipoModel):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM ctas_ctes_bancarias WHERE id = ?;", (cuenta_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    flag = 1 if int(data.es_cuenta_bancaria) else 0
    cursor.execute(
        "UPDATE ctas_ctes_bancarias SET es_cuenta_bancaria = ? WHERE id = ?;",
        (flag, cuenta_id),
    )
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "message": "Tipo de cuenta actualizado.",
        "es_cuenta_bancaria": flag,
    }

@app.get("/api/bancos/movimientos_cta_cte")
def listar_movimientos_cta_cte(
    cuenta_id: Optional[int] = None,
    cta_cte_nro: Optional[str] = None,
    estado: Optional[str] = "sin_conciliar",
    q: Optional[str] = None,
):
    conn = get_db()
    cursor = conn.cursor()

    cta_nro = (cta_cte_nro or "").strip()
    if not cta_nro and cuenta_id:
        cursor.execute("SELECT nro_cta_cte FROM ctas_ctes_bancarias WHERE id = ?;", (cuenta_id,))
        row_cta = cursor.fetchone()
        if row_cta and row_cta["nro_cta_cte"]:
            cta_nro = str(row_cta["nro_cta_cte"]).strip()

    query = "SELECT * FROM movimientos_cta_cte_bancos WHERE 1=1"
    params = []

    if cta_nro:
        query += " AND TRIM(cta_cte_nro) = ?"
        params.append(cta_nro)
    elif cuenta_id:
        query += " AND cuenta_id = ?"
        params.append(cuenta_id)

    estado_norm = (estado or "sin_conciliar").strip().lower()
    # Lógica Access: conciliado = tiene fecha de débito efectivo en el resumen bancario
    if estado_norm in ("sin_conciliar", "pendientes", "pendiente"):
        query += " AND (fecha_debito IS NULL OR TRIM(COALESCE(fecha_debito, '')) = '')"
    elif estado_norm in ("conciliados", "conciliado"):
        query += " AND fecha_debito IS NOT NULL AND TRIM(fecha_debito) != ''"
    # estado == todos / historico: sin filtro extra

    qq = (q or "").strip()
    if qq:
        like = f"%{qq}%"
        query += """
          AND (
            COALESCE(proveedor,'') LIKE ?
            OR TRIM(COALESCE(nro_cheque,'')) LIKE ?
            OR COALESCE(tipo_operacion,'') LIKE ?
            OR CAST(COALESCE(haber,0) AS TEXT) LIKE ?
            OR CAST(COALESCE(debe,0) AS TEXT) LIKE ?
          )
        """
        params.extend([like, like, like, like, like])

    query += " ORDER BY fecha_cobro ASC, id ASC;"

    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]

    saldo_acumulado = 0.0
    procesados = []
    for r in rows:
        haber = float(r.get("haber", 0) or 0)
        debe = float(r.get("debe", 0) or 0)
        imp_chq = float(r.get("imp_chq", 0) or 0)
        saldo_acumulado += debe - haber - imp_chq
        r_copy = dict(r)
        r_copy["saldo"] = round(saldo_acumulado, 2)
        procesados.append(r_copy)

    conn.close()
    return procesados


def _posicion_fci(cursor, fecha: Optional[str] = None, cuenta_id: Optional[int] = None) -> Dict[str, Any]:
    """
    FCI activos = suscripciones (haber) − rescates (debe) hasta la fecha.
    Siguen siendo disponibilidad de la empresa (como un plazo fijo).
    """
    params: List[Any] = []
    where_fecha = ""
    if fecha:
        where_fecha = " AND fecha_cobro <= ?"
        params.append(fecha[:10])
    where_cta = ""
    if cuenta_id:
        where_cta = " AND cuenta_id = ?"
        params.append(cuenta_id)

    cursor.execute(
        f"""
        SELECT
            COALESCE(SUM(CASE
                WHEN LOWER(TRIM(COALESCE(tipo_operacion,''))) IN ('fci', 'suscripcion fci', 'suscripción fci')
                THEN COALESCE(haber, 0) ELSE 0 END), 0) AS suscripto,
            COALESCE(SUM(CASE
                WHEN LOWER(TRIM(COALESCE(tipo_operacion,''))) IN ('rescate fci', 'rescate_fci', 'rescatefci')
                THEN COALESCE(debe, 0) ELSE 0 END), 0) AS rescatado
        FROM movimientos_cta_cte_bancos
        WHERE 1=1 {where_fecha} {where_cta};
        """,
        params,
    )
    row = cursor.fetchone()
    sus = float(row["suscripto"] or 0)
    res = float(row["rescatado"] or 0)
    activo = round(max(0.0, sus - res), 2)
    return {
        "fecha": (fecha or datetime.now().strftime("%Y-%m-%d"))[:10],
        "suscripto": round(sus, 2),
        "rescatado": round(res, 2),
        "activo": activo,
    }


@app.get("/api/bancos/fci/posicion")
def api_fci_posicion(fecha: Optional[str] = None, cuenta_id: Optional[int] = None):
    """Disponibilidad FCI a una fecha (restan a las deudas en el financiero)."""
    conn = get_db()
    cursor = conn.cursor()
    data = _posicion_fci(cursor, fecha=fecha, cuenta_id=cuenta_id)
    # Detalle por cuenta
    cursor.execute(
        """
        SELECT cuenta_id, TRIM(cta_cte_nro) AS cta,
               COALESCE(SUM(CASE
                   WHEN LOWER(TRIM(COALESCE(tipo_operacion,''))) IN ('fci', 'suscripcion fci', 'suscripción fci')
                   THEN COALESCE(haber, 0) ELSE 0 END), 0) AS suscripto,
               COALESCE(SUM(CASE
                   WHEN LOWER(TRIM(COALESCE(tipo_operacion,''))) IN ('rescate fci', 'rescate_fci', 'rescatefci')
                   THEN COALESCE(debe, 0) ELSE 0 END), 0) AS rescatado
        FROM movimientos_cta_cte_bancos
        WHERE (? IS NULL OR fecha_cobro <= ?)
        GROUP BY cuenta_id, TRIM(cta_cte_nro)
        HAVING (suscripto - rescatado) > 0.01;
        """,
        (fecha[:10] if fecha else None, fecha[:10] if fecha else "9999-12-31"),
    )
    por_cuenta = []
    for r in cursor.fetchall():
        d = dict(r)
        d["activo"] = round(float(d["suscripto"] or 0) - float(d["rescatado"] or 0), 2)
        por_cuenta.append(d)
    conn.close()
    data["por_cuenta"] = por_cuenta
    return data


@app.get("/api/bancos/movimientos_cta_cte/{id_mov}/detalle")
def detalle_movimiento_banco(id_mov: int):
    """Detalle del movimiento: enlace a cta cte proveedor y/o datos del cheque."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM movimientos_cta_cte_bancos WHERE id = ?;", (id_mov,))
    mov = cursor.fetchone()
    if not mov:
        conn.close()
        raise HTTPException(status_code=404, detail="Movimiento no encontrado.")
    m = dict(mov)

    proveedor_txt = (m.get("proveedor") or "").strip()
    entidad = None
    if proveedor_txt:
        # Match exacto o contenido (nombre fantasia / razón social)
        cursor.execute(
            """
            SELECT cuit, razon_social, nombre_fantasia, centro_costo
            FROM entidades
            WHERE COALESCE(es_cuenta_ajuste, 0) = 0
              AND (
                    UPPER(TRIM(COALESCE(razon_social,''))) = UPPER(?)
                 OR UPPER(TRIM(COALESCE(nombre_fantasia,''))) = UPPER(?)
                 OR UPPER(COALESCE(razon_social,'')) LIKE '%' || UPPER(?) || '%'
                 OR UPPER(COALESCE(nombre_fantasia,'')) LIKE '%' || UPPER(?) || '%'
              )
            ORDER BY
                CASE
                    WHEN UPPER(TRIM(COALESCE(razon_social,''))) = UPPER(?) THEN 0
                    WHEN UPPER(TRIM(COALESCE(nombre_fantasia,''))) = UPPER(?) THEN 1
                    ELSE 2
                END
            LIMIT 1;
            """,
            (proveedor_txt, proveedor_txt, proveedor_txt, proveedor_txt, proveedor_txt, proveedor_txt),
        )
        row_e = cursor.fetchone()
        if row_e:
            entidad = dict(row_e)

    cheque = None
    nro = (m.get("nro_cheque") or "").strip()
    if nro and nro not in ("—", "-", "S/N"):
        cursor.execute(
            """
            SELECT * FROM cartera_cheques
            WHERE TRIM(COALESCE(nro_cheque,'')) = ?
            ORDER BY id DESC LIMIT 1;
            """,
            (nro,),
        )
        row_c = cursor.fetchone()
        if row_c:
            cheque = dict(row_c)
        else:
            # Fallback: datos del propio movimiento bancario
            cheque = {
                "nro_cheque": nro,
                "tipo": m.get("tipo_operacion") or "Cheque",
                "banco": "",
                "fecha_emision": m.get("fecha_cobro") or "",
                "fecha_pago": m.get("fecha_debito") or "",
                "monto": float(m.get("haber") or m.get("debe") or 0),
                "estado": "Desde movimiento bancario",
                "librador": proveedor_txt,
                "sintetico": True,
            }
        # Buscar también en cta cte proveedor por nro cheque
        if entidad:
            cuit_clean = re.sub(r"\D", "", entidad.get("cuit") or "")
            cursor.execute(
                """
                SELECT id, tipo_comprobante, numero_comprobante, forma_pago, nro_cheque,
                       fecha, vencimiento, total, debe, haber, estado
                FROM cuentas_corrientes
                WHERE REPLACE(entidad_id, '-', '') = ?
                  AND TRIM(COALESCE(nro_cheque,'')) = ?
                ORDER BY fecha DESC LIMIT 5;
                """,
                (cuit_clean, nro),
            )
            cc_chqs = [dict(r) for r in cursor.fetchall()]
        else:
            cursor.execute(
                """
                SELECT id, entidad_id, tipo_comprobante, numero_comprobante, forma_pago, nro_cheque,
                       fecha, vencimiento, total, debe, haber, estado
                FROM cuentas_corrientes
                WHERE TRIM(COALESCE(nro_cheque,'')) = ?
                ORDER BY fecha DESC LIMIT 5;
                """,
                (nro,),
            )
            cc_chqs = [dict(r) for r in cursor.fetchall()]
    else:
        cc_chqs = []

    cuit_link = re.sub(r"\D", "", (entidad or {}).get("cuit") or "")
    conn.close()
    return {
        "movimiento": m,
        "entidad": entidad,
        "cheque": cheque,
        "movimientos_cta_cte_cheque": cc_chqs,
        "links": {
            "cta_cte_proveedor": f"detallegestioncc.html?cuit={cuit_link}" if cuit_link else None,
            "orden_pago": f"ordenes_pago.html?cuit={cuit_link}" if cuit_link else None,
        },
    }


@app.post("/api/bancos/importar_movimientos")
def api_importar_movimientos(forzar: bool = False):
    """Importa movimientos desde tablas/movimientos bancarios.xlsx"""
    return importar_movimientos_bancarios_historicos(forzar=forzar)


@app.post("/api/access/sincronizar_actualizacion")
def api_sincronizar_access_actualizacion():
    """
    Importa tablas/tablas actualizacion.xlsx (export Access reciente):
    facturas, pagos, movimientos bancarios y cheques — upsert por Id Access.
    """
    from importar_actualizacion_access import importar_actualizacion

    empresa_id = get_empresa_activa_id()
    result = importar_actualizacion(empresa_id=empresa_id)
    if result.get("status") != "ok":
        raise HTTPException(400, result.get("message") or "Error al sincronizar Access")
    return result


@app.post("/api/bancos/prestamos/vincular_existentes")
def api_vincular_prestamos_existentes():
    """Etiqueta Id Access + arma créditos/cuotas desde movimientos ya cargados (sin duplicar)."""
    bf = backfill_id_access_y_prestamos_desde_excel()
    sync = sincronizar_creditos_desde_movimientos_prestamo()
    return {"backfill": bf, "sync": sync}

# Impuesto a los débitos y créditos bancarios (Ley 25.413):
# Cada movimiento lleva SOLO su tramo de 6‰ (0,6%).
# El 1,2% se conforma a lo largo del ciclo (crédito + débito), nunca en un solo asiento.
ALICUOTA_IMP_DEBITOS_CREDITOS = 6 / 1000  # 6‰ por lado
# Alias legacy (no usar para estimar un solo movimiento)
ALICUOTA_IMP_CHEQUE_DEBITO = ALICUOTA_IMP_DEBITOS_CREDITOS
ALICUOTA_IMP_CHEQUE_CREDITO = ALICUOTA_IMP_DEBITOS_CREDITOS
ALICUOTA_IMP_CHEQUE_TOTAL = ALICUOTA_IMP_DEBITOS_CREDITOS * 2  # solo referencia documental


def _resolver_imp_cheque(data: MovimientoCtaCteBancoModel) -> float:
    """
    Impuesto a los débitos y créditos bancarios (6‰ por movimiento):

    - Débito / cheque emitido / e-cheq: 6‰ sobre el monto (lado débito).
    - Depósito / acreditación: 6‰ sobre el monto (lado crédito), si aplica.
    - El 1,2% NO se carga en un solo movimiento: se forma con un crédito + un débito.

    Exentos (salvo que el usuario fuerce importe / aplica_imp_cheque):
    - Transferencia entre cuentas misma titularidad
    - Suscripción FCI / Rescate FCI
    - Depósitos sin marcar «aplica» (misma titularidad otro banco, ciertos préstamos
      agro, rescate FCI ya tipificado, ALYC/caución 24hs Mercado de Valores, etc.)
    """
    tipo = (data.tipo_operacion or "").strip().lower()
    informado = float(data.imp_chq or 0)
    estimado_lado = round(float(data.monto) * ALICUOTA_IMP_DEBITOS_CREDITOS, 2)

    # Exentos por tipo (0 salvo importe forzado con aplica)
    if tipo in (
        "transferencia", "transferencias",
        "fci", "suscripcion fci", "suscripción fci",
        "rescate fci", "rescate_fci", "rescatefci",
    ):
        return informado if data.aplica_imp_cheque else 0.0

    # Lado DÉBITO: cheque emitido, débito genérico
    if tipo in ("cheque", "debito", "débito"):
        if informado > 0:
            return informado
        if data.aplica_imp_cheque:
            return estimado_lado
        return 0.0

    # Lado CRÉDITO: depósito / acreditación
    if tipo in ("deposito", "depósito", "acreditacion", "acreditación", "credito", "crédito"):
        if informado > 0 and data.aplica_imp_cheque:
            return informado
        if data.aplica_imp_cheque:
            return estimado_lado if informado <= 0 else informado
        return 0.0

    return informado if data.aplica_imp_cheque else 0.0


def _normalizar_titular(titular: str) -> str:
    t = (titular or "").lower()
    t = re.sub(r"\.", "", t)
    t = re.sub(r"\bs\.?a\.?\b", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


@app.get("/api/bancos/cuentas/{cuenta_id}/misma_titularidad")
def cuentas_misma_titularidad(cuenta_id: int):
    """Cuentas bancarias del mismo titular, excluyendo la cuenta origen (para transferencias)."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, banco, nro_cta_cte, titular, es_cuenta_bancaria, empresa_id
        FROM ctas_ctes_bancarias WHERE id = ? AND COALESCE(baja, 0) = 0;
        """,
        (cuenta_id,),
    )
    origen = cursor.fetchone()
    if not origen:
        conn.close()
        raise HTTPException(status_code=404, detail="Cuenta origen no encontrada.")

    titular_norm = _normalizar_titular(origen["titular"])
    empresa_id = origen["empresa_id"] if origen["empresa_id"] is not None else get_empresa_activa_id()
    cursor.execute(
        """
        SELECT id, banco, nro_cta_cte, titular, es_cuenta_bancaria
        FROM ctas_ctes_bancarias
        WHERE COALESCE(baja, 0) = 0
          AND id != ?
          AND COALESCE(empresa_id, ?) = ?
          AND COALESCE(es_cuenta_bancaria, 1) = 1
        ORDER BY banco ASC, nro_cta_cte ASC;
        """,
        (cuenta_id, empresa_id, empresa_id),
    )
    destinos = []
    for r in cursor.fetchall():
        if _normalizar_titular(r["titular"]) == titular_norm:
            destinos.append(dict(r))
    conn.close()
    return {
        "origen": {
            "id": int(origen["id"]),
            "banco": origen["banco"],
            "nro_cta_cte": origen["nro_cta_cte"],
            "titular": origen["titular"],
        },
        "destinos": destinos,
    }


@app.post("/api/bancos/movimientos_cta_cte")
def guardar_movimiento_cta_cte(data: MovimientoCtaCteBancoModel):
    conn = get_db()
    cursor = conn.cursor()
    tipo = (data.tipo_operacion or "").strip().lower()
    fec_debito = (data.fecha_debito or "").strip()
    # Access: conciliado solo si hay fecha de débito efectivo
    conciliado = 1 if fec_debito else 0
    imp_chq = _resolver_imp_cheque(data)
    empresa_id = get_empresa_activa_id()

    # Transferencia entre cuentas misma titularidad: sale (haber) + entra (debe), sin imp. cheque
    if tipo in ("transferencia", "transferencias"):
        if not data.cuenta_destino_id:
            conn.close()
            raise HTTPException(status_code=400, detail="Debe indicar la cuenta destino de la transferencia.")
        if int(data.cuenta_destino_id) == int(data.cuenta_id or 0):
            conn.close()
            raise HTTPException(status_code=400, detail="La cuenta destino no puede ser la misma que la origen.")

        cursor.execute(
            "SELECT * FROM ctas_ctes_bancarias WHERE id IN (?, ?);",
            (data.cuenta_id, data.cuenta_destino_id),
        )
        mapas = {int(r["id"]): dict(r) for r in cursor.fetchall()}
        origen = mapas.get(int(data.cuenta_id or 0))
        destino = mapas.get(int(data.cuenta_destino_id))
        if not origen or not destino:
            conn.close()
            raise HTTPException(status_code=404, detail="Cuenta origen o destino no encontrada.")
        if _normalizar_titular(origen["titular"]) != _normalizar_titular(destino["titular"]):
            conn.close()
            raise HTTPException(status_code=400, detail="Las cuentas no son de la misma titularidad.")

        detalle_salida = f"Transferencia a {destino['banco']} {destino['nro_cta_cte']}"
        detalle_entrada = f"Transferencia desde {origen['banco']} {origen['nro_cta_cte']}"
        if data.proveedor and data.proveedor.strip():
            detalle_salida = f"{detalle_salida} — {data.proveedor.strip()}"
            detalle_entrada = f"{detalle_entrada} — {data.proveedor.strip()}"

        try:
            asiento_id = asiento_para_movimiento_banco(
                cursor,
                fecha=data.fecha_cobro,
                tipo_operacion="Transferencia",
                proveedor=data.proveedor or "",
                monto=data.monto,
                imp_chq=0.0,
                cuenta_origen=origen,
                cuenta_destino=destino,
                empresa_id=empresa_id,
                origen_id=None,
            )
            cursor.execute("""
                INSERT INTO movimientos_cta_cte_bancos
                (cuenta_id, fecha_cobro, fecha_debito, proveedor, nro_cheque, haber, debe, imp_chq, cta_cte_nro, conciliado, asiento_id, tipo_operacion)
                VALUES (?, ?, ?, ?, '', ?, 0, 0, ?, ?, ?, 'Transferencia');
            """, (origen["id"], data.fecha_cobro, fec_debito, detalle_salida, data.monto, origen["nro_cta_cte"], conciliado, asiento_id))
            id_salida = cursor.lastrowid
            cursor.execute("""
                INSERT INTO movimientos_cta_cte_bancos
                (cuenta_id, fecha_cobro, fecha_debito, proveedor, nro_cheque, haber, debe, imp_chq, cta_cte_nro, conciliado, asiento_id, tipo_operacion)
                VALUES (?, ?, ?, ?, '', 0, ?, 0, ?, ?, ?, 'Transferencia');
            """, (destino["id"], data.fecha_cobro, fec_debito, detalle_entrada, data.monto, destino["nro_cta_cte"], conciliado, asiento_id))
            cursor.execute(
                "UPDATE asientos_contables SET origen_id = ? WHERE id = ?;",
                (id_salida, asiento_id),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail=f"No se pudo registrar la transferencia/asiento: {e}")
        conn.close()
        return {
            "status": "success",
            "message": "Transferencia registrada en ambas cuentas (sin impuesto) y asiento contable generado.",
            "imp_chq_aplicado": 0.0,
            "asiento_id": asiento_id,
        }

    haber = data.monto if tipo in ("debito", "débito", "cheque", "fci", "suscripcion fci", "suscripción fci") else 0.0
    debe = data.monto if tipo in ("deposito", "depósito", "rescate fci", "rescate_fci", "rescatefci") else 0.0

    if haber < 0.01 and debe < 0.01:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Tipo de operación no reconocido: {data.tipo_operacion}")

    cursor.execute("SELECT * FROM ctas_ctes_bancarias WHERE id = ?;", (data.cuenta_id,))
    cuenta = cursor.fetchone()
    if not cuenta:
        conn.close()
        raise HTTPException(status_code=404, detail="Cuenta bancaria no encontrada.")
    cuenta = dict(cuenta)

    try:
        cursor.execute("""
            INSERT INTO movimientos_cta_cte_bancos
            (cuenta_id, fecha_cobro, fecha_debito, proveedor, nro_cheque, haber, debe, imp_chq, cta_cte_nro, conciliado, tipo_operacion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (data.cuenta_id, data.fecha_cobro, fec_debito, data.proveedor, data.nro_cheque, haber, debe, imp_chq, data.cta_cte_nro, conciliado, data.tipo_operacion))
        mov_id = cursor.lastrowid

        # Cheque emitido: también en cartera (fecha emisión = fecha cobro) para consulta detalle
        if tipo == "cheque" and (data.nro_cheque or "").strip():
            nro = (data.nro_cheque or "").strip()
            vto_chq = (data.fecha_vencimiento or fec_debito or data.fecha_cobro or "").strip()[:10]
            cursor.execute(
                """
                SELECT id FROM cartera_cheques
                WHERE TRIM(COALESCE(nro_cheque,'')) = ? AND COALESCE(cuenta_id, 0) = ?
                LIMIT 1;
                """,
                (nro, data.cuenta_id),
            )
            ya = cursor.fetchone()
            if ya:
                cursor.execute(
                    """
                    UPDATE cartera_cheques
                    SET fecha_emision = COALESCE(NULLIF(fecha_emision,''), ?),
                        fecha_pago = COALESCE(NULLIF(fecha_pago,''), ?),
                        monto = ?, banco = COALESCE(NULLIF(banco,''), ?),
                        librador = COALESCE(NULLIF(librador,''), ?),
                        estado = CASE WHEN estado IS NULL OR estado = '' THEN 'Emitido' ELSE estado END
                    WHERE id = ?;
                    """,
                    (
                        data.fecha_cobro, vto_chq, data.monto,
                        cuenta.get("banco") or "", data.proveedor or "", ya["id"],
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO cartera_cheques
                    (tipo, nro_cheque, banco, cuit_emisor, librador, fecha_emision, fecha_pago, monto, moneda, estado, cuenta_id, empresa_id)
                    VALUES ('Emitido', ?, ?, '', ?, ?, ?, ?, 'ARS', 'Emitido', ?, ?);
                    """,
                    (
                        nro, cuenta.get("banco") or "", data.proveedor or "",
                        data.fecha_cobro, vto_chq, data.monto, data.cuenta_id, empresa_id,
                    ),
                )

        asiento_id = asiento_para_movimiento_banco(
            cursor,
            fecha=data.fecha_cobro,
            tipo_operacion=data.tipo_operacion,
            proveedor=data.proveedor,
            monto=data.monto,
            imp_chq=imp_chq,
            cuenta_origen=cuenta,
            empresa_id=empresa_id,
            origen_id=mov_id,
            nro_cheque=data.nro_cheque or "",
        )
        if asiento_id is not None:
            cursor.execute(
                "UPDATE movimientos_cta_cte_bancos SET asiento_id = ? WHERE id = ?;",
                (asiento_id, mov_id),
            )

        cc_proveedor = False
        if tipo in ("cheque", "debito", "débito") and (data.proveedor or "").strip():
            cursor.execute(
                """
                SELECT cuit FROM entidades
                WHERE COALESCE(es_proveedor, 0) = 1
                  AND (
                    UPPER(TRIM(COALESCE(razon_social,''))) = UPPER(TRIM(?))
                    OR UPPER(TRIM(COALESCE(nombre_fantasia,''))) = UPPER(TRIM(?))
                  )
                LIMIT 1;
                """,
                (data.proveedor.strip(), data.proveedor.strip()),
            )
            entp = cursor.fetchone()
            if entp:
                cuit_p = entp["cuit"]
                vto_cc = ((data.fecha_vencimiento or data.fecha_cobro or "")[:10])
                forma_cc = "Cheque Propio" if tipo == "cheque" else "Débito bancario"
                cursor.execute(
                    """
                    INSERT INTO cuentas_corrientes (
                        entidad_id, tipo_comprobante, numero_comprobante, forma_pago, nro_cheque,
                        fecha, vencimiento, neto, iva, debe, haber, total, estado, usuario_registro, empresa_id
                    ) VALUES (?, 'Pago banco', ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, 'Pagado', 'Banco', ?);
                    """,
                    (
                        cuit_p, data.nro_cheque or str(mov_id), forma_cc, data.nro_cheque or "",
                        data.fecha_cobro, vto_cc, data.monto, data.monto, empresa_id,
                    ),
                )
                restante = float(data.monto or 0)
                placeholders = ",".join("?" for _ in _TIPOS_PAGO)
                cuit_clean = "".join(filter(str.isdigit, str(cuit_p)))
                cursor.execute(
                    f"""
                    SELECT id, COALESCE(NULLIF(total, 0), debe, 0) AS monto
                    FROM cuentas_corrientes
                    WHERE REPLACE(entidad_id, '-', '') = ?
                      AND COALESCE(empresa_id, 1) = ?
                      AND COALESCE(debe, 0) > 0.01
                      AND COALESCE(tipo_comprobante, '') NOT IN ({placeholders})
                      AND COALESCE(estado, 'Pendiente') IN ('Pendiente', 'pendiente', '')
                    ORDER BY fecha ASC, id ASC;
                    """,
                    (cuit_clean, empresa_id, *_TIPOS_PAGO),
                )
                for row in cursor.fetchall():
                    if restante <= 0.01:
                        break
                    cursor.execute(
                        "UPDATE cuentas_corrientes SET estado = 'Pagado' WHERE id = ?;",
                        (row["id"],),
                    )
                    restante -= float(row["monto"] or 0)
                cc_proveedor = True
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail=f"No se pudo guardar movimiento/asiento: {e}")

    conn.close()
    msg = (
        "Movimiento guardado (Centro S/P: sin asiento en contabilidad oficial)."
        if asiento_id is None
        else "Movimiento guardado y asiento contable generado."
    )
    if cc_proveedor:
        msg += " El pago bajó la cuenta corriente del proveedor."
    return {
        "status": "success",
        "message": msg,
        "imp_chq_aplicado": imp_chq,
        "asiento_id": asiento_id,
        "movimiento_id": mov_id,
    }

@app.delete("/api/bancos/movimientos_cta_cte/{id_mov}")
def eliminar_movimiento_cta_cte_banco(id_mov: int):
    conn = get_db()
    cursor = conn.cursor()
    resultado = eliminar_cascada_movimiento_banco(cursor, id_mov)
    if not resultado.get("ok"):
        conn.close()
        raise HTTPException(status_code=404, detail=resultado.get("message", "No encontrado"))
    conn.commit()
    conn.close()
    return {"status": "success", **resultado}

@app.get("/api/asientos")
def api_listar_asientos(limit: int = 500, ejercicio: Optional[str] = None):
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    rows = listar_asientos_plano(cursor, empresa_id=empresa_id, limit=limit, ejercicio=ejercicio)
    conn.close()
    return rows

@app.get("/api/asientos/ejercicios")
def api_ejercicios_contables():
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    init_contabilidad(cursor)
    conn.commit()
    lista = ejercicios_disponibles(cursor, empresa_id=empresa_id)
    # Incluir ejercicio actual aunque aún no haya asientos
    hoy = datetime.now().strftime("%Y-%m-%d")
    actual = ejercicio_desde_fecha(hoy)
    if actual and actual not in lista:
        lista = [actual] + lista
    conn.close()
    return {
        "cierre_eecc": "30/06",
        "ejercicio_actual": actual,
        "ejercicios": lista,
    }

@app.post("/api/asientos/recalcular_bancos")
def api_recalcular_asientos_bancos(
    solo_sin_asiento: bool = True,
    limit: Optional[int] = None,
    ejercicio: Optional[str] = None,
):
    """Genera asientos para movimientos bancarios históricos (cierre EECC 30/06)."""
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    try:
        resultado = recalcular_asientos_movimientos_bancarios(
            conn,
            empresa_id=empresa_id,
            solo_sin_asiento=solo_sin_asiento,
            limit=limit,
            ejercicio=ejercicio,
        )
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))
    conn.close()
    return resultado

@app.get("/api/plan_cuentas")
def api_plan_cuentas():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, codigo_cuenta, nombre_cuenta, tipo_cuenta FROM plan_de_cuentas WHERE COALESCE(activa,1)=1 ORDER BY codigo_cuenta;"
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ——— Bienes de uso / Patrimonio (EECC + carpetas bancarias) ———

_TASAS_BIEN_DEFAULT = {
    "Rodado": (20.0, 5),
    "Camión": (20.0, 5),
    "Camioneta": (20.0, 5),
    "Maquinaria": (10.0, 10),
    "Tractor": (10.0, 10),
    "Cosechadora": (10.0, 10),
    "Sembradora": (10.0, 10),
    "Tolva / Acoplado": (10.0, 10),
    "Extractora": (10.0, 10),
    "Cabezal": (10.0, 10),
    "Fertilizadora": (10.0, 10),
    "Desmalezadora": (10.0, 10),
    "Pulverizador": (10.0, 10),
    "Retroexcavadora": (10.0, 10),
    "Niveladora": (10.0, 10),
    "Guinche / Grúa": (10.0, 10),
    "Propiedad": (2.0, 50),
    "Instalacion": (5.0, 20),
    "Instalación": (5.0, 20),
    "Muebles": (10.0, 10),
    "Otro": (10.0, 10),
}

# Categorías estilo Access (detalle operativo)
_CATEGORIAS_BIEN_ACCESS = [
    "Tractor", "Cosechadora", "Sembradora", "Tolva / Acoplado", "Pulverizador",
    "Fertilizadora", "Desmalezadora", "Extractora", "Cabezal", "Niveladora",
    "Retroexcavadora", "Guinche / Grúa", "Maquinaria",
    "Camioneta", "Camión", "Rodado",
    "Propiedad", "Instalación", "Muebles", "Otro",
]


def _norm_txt_bien(s: str) -> str:
    t = (s or "").strip().upper()
    for a, b in (
        ("Á", "A"), ("É", "E"), ("Í", "I"), ("Ó", "O"), ("Ú", "U"),
        ("Ñ", "N"), ("Ü", "U"),
    ):
        t = t.replace(a, b)
    return t


def _categoria_bien_desde_tipo(tipo_rodado: str = "", categoria: str = "") -> str:
    """
    Categoría operativa como en Access: Tractor, Tolva, Camioneta, Cosechadora, etc.
    Prioriza tipo_rodado (campo Access); si no alcanza, usa categoria actual.
    """
    t = _norm_txt_bien(tipo_rodado)
    c = _norm_txt_bien(categoria)
    blob = f"{t} {c}".strip()

    reglas = [
        (("COSECH",), "Cosechadora"),
        (("SEMBRAD",), "Sembradora"),
        (("TOLVA", "ACOPLAD"), "Tolva / Acoplado"),
        (("EXTRACTOR",), "Extractora"),  # antes de Tractor (EXTRACTORA contiene TRACTOR)
        (("PICK", "CAMIONETA", "SW4", "HILUX"), "Camioneta"),
        (("CAMION",), "Camión"),
        (("TRACTOR",), "Tractor"),
        (("CABEZAL",), "Cabezal"),
        (("FERTILIZ",), "Fertilizadora"),
        (("DESMALEZ",), "Desmalezadora"),
        (("PULVERIZ",), "Pulverizador"),
        (("RETRO",), "Retroexcavadora"),
        (("NIVELAD",), "Niveladora"),
        (("GUINCHE", "GRUA"), "Guinche / Grúa"),
        (("CASILLA", "INSTAL"), "Instalación"),
        (("PROPIEDAD", "INMUEBLE", "CAMPO", "LOTE"), "Propiedad"),
        (("MUEBLE",), "Muebles"),
        (("AUTO", "SEDAN", "RODADO"), "Rodado"),
        (("MAQUINARIA",), "Maquinaria"),
    ]
    for keys, label in reglas:
        if any(k in blob for k in keys):
            return label
    # Si ya es una categoría Access conocida, respetarla
    for known in _CATEGORIAS_BIEN_ACCESS:
        if _norm_txt_bien(known) == c:
            return known
    if c:
        # título amigable
        return (categoria or tipo_rodado or "Otro").strip().title() or "Otro"
    return "Maquinaria"


def _defaults_amort_categoria(categoria: str):
    cat = (categoria or "Maquinaria").strip()
    if cat in _TASAS_BIEN_DEFAULT:
        return _TASAS_BIEN_DEFAULT[cat]
    # familia por palabras
    n = _norm_txt_bien(cat)
    if any(k in n for k in ("CAMION", "CAMIONETA", "PICK", "RODADO", "AUTO", "SEDAN")):
        return _TASAS_BIEN_DEFAULT["Rodado"]
    if any(k in n for k in ("PROPIEDAD", "INMUEBLE", "CAMPO")):
        return _TASAS_BIEN_DEFAULT["Propiedad"]
    if "INSTAL" in n or "CASILLA" in n:
        return _TASAS_BIEN_DEFAULT["Instalación"]
    if "MUEBLE" in n:
        return _TASAS_BIEN_DEFAULT["Muebles"]
    return _TASAS_BIEN_DEFAULT["Maquinaria"]


def _valor_historico_bien(row: dict) -> float:
    for k in ("valor_historico", "importe_neto", "total"):
        try:
            v = float(row.get(k) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0.01:
            return round(v, 2)
    return 0.0


def _enriquecer_bien(row: dict, fecha_corte: Optional[str] = None) -> dict:
    d = dict(row)
    cat = _categoria_bien_desde_tipo(d.get("tipo_rodado") or "", d.get("categoria") or "")
    d["categoria"] = cat
    tasa_def, vida_def = _defaults_amort_categoria(cat)
    hist = _valor_historico_bien(d)
    d["valor_historico"] = hist
    residual = round(float(d.get("valor_residual") or 0), 2)
    tasa = float(d.get("tasa_anual_pct") or 0) or tasa_def
    vida = int(d.get("vida_util_anos") or 0) or vida_def
    if tasa <= 0 and vida > 0:
        tasa = round(100.0 / vida, 4)
    if vida <= 0 and tasa > 0:
        vida = max(1, int(round(100.0 / tasa)))
    d["tasa_anual_pct"] = tasa
    d["vida_util_anos"] = vida
    acum = round(float(d.get("amortizacion_acumulada") or 0), 2)
    base = max(0.0, hist - residual)
    cuota = round(base * (tasa / 100.0), 2) if tasa > 0 else (round(base / vida, 2) if vida else 0.0)
    d["cuota_anual"] = cuota
    # Amortización teórica al corte (no altera acum guardada)
    inicio = (d.get("fecha_inicio_amort") or d.get("fecha_compra") or "")[:10]
    corte = (fecha_corte or datetime.now().strftime("%Y-%m-%d"))[:10]
    anios = 0.0
    if inicio and corte >= inicio:
        try:
            d0 = datetime.strptime(inicio[:10], "%Y-%m-%d")
            d1 = datetime.strptime(corte[:10], "%Y-%m-%d")
            anios = max(0.0, (d1 - d0).days / 365.25)
        except ValueError:
            anios = 0.0
    teorica = round(min(base, cuota * anios), 2) if cuota > 0 else 0.0
    d["amortizacion_teorica"] = teorica
    d["amortizacion_acumulada"] = acum
    vnc = round(max(0.0, hist - acum), 2)
    d["valor_neto_contable"] = vnc
    comercial_usd = round(float(d.get("valor_comercial_usd") or 0), 2)
    tc = round(float(d.get("tipo_cambio_comercial") or 0), 4)
    comercial_ars = round(float(d.get("valor_comercial") or 0), 2)
    if comercial_usd > 0.01 and tc > 0:
        comercial_ars = round(comercial_usd * tc, 2)
    elif comercial_ars > 0.01 and comercial_usd < 0.01:
        # legado: valor_comercial estaba en pesos
        comercial_usd = 0.0
    d["valor_comercial_usd"] = comercial_usd
    d["tipo_cambio_comercial"] = tc
    d["valor_comercial"] = comercial_ars  # equiv. pesos
    d["valor_comercial_ars"] = comercial_ars
    d["activo"] = int(d.get("activo") if d.get("activo") is not None else 1)
    if (d.get("fecha_baja") or "").strip():
        d["activo"] = 0
    return d


def _payload_bien_from_model(data: BienPatrimonialModel) -> dict:
    tipo = (data.tipo_rodado or "").strip()
    cat = _categoria_bien_desde_tipo(tipo, (data.categoria or "").strip())
    if not tipo:
        tipo = cat
    tasa_def, vida_def = _defaults_amort_categoria(cat)
    hist = float(data.valor_historico or 0)
    if hist < 0.01:
        hist = float(data.importe_neto or data.total or 0)
    neto = float(data.importe_neto if data.importe_neto is not None else hist)
    total = float(data.total if data.total is not None else (neto + float(data.iva or 0)))
    tasa = float(data.tasa_anual_pct) if data.tasa_anual_pct not in (None, 0) else tasa_def
    vida = int(data.vida_util_anos) if data.vida_util_anos else vida_def
    return {
        "categoria": cat,
        "tipo_rodado": tipo,
        "marca": (data.marca or "").strip(),
        "modelo": (data.modelo or "").strip(),
        "detalle": (data.detalle or "").strip(),
        "anio": str(data.anio or "").replace(".0", "").strip(),
        "nro_patente": (data.nro_patente or "").strip(),
        "nro_chasis": (data.nro_chasis or "").strip(),
        "nro_motor": (data.nro_motor or "").strip(),
        "nro_factura": (data.nro_factura or "").strip(),
        "fecha_compra": (data.fecha_compra or "")[:10],
        "fecha_inicio_amort": (data.fecha_inicio_amort or data.fecha_compra or "")[:10],
        "valor_historico": round(hist, 2),
        "importe_neto": round(neto, 2),
        "iva": round(float(data.iva or 0), 2),
        "total": round(total, 2),
        "valor_residual": round(float(data.valor_residual or 0), 2),
        "valor_comercial_usd": round(float(data.valor_comercial_usd or 0), 2),
        "tipo_cambio_comercial": round(float(data.tipo_cambio_comercial or 0), 4),
        "valor_comercial": round(
            float(data.valor_comercial_usd or 0) * float(data.tipo_cambio_comercial or 0)
            if float(data.valor_comercial_usd or 0) > 0 and float(data.tipo_cambio_comercial or 0) > 0
            else float(data.valor_comercial or 0),
            2,
        ),
        "fecha_valor_comercial": (data.fecha_valor_comercial or "")[:10],
        "moneda": (data.moneda or "ARS").strip() or "ARS",
        "vida_util_anos": vida,
        "tasa_anual_pct": tasa,
        "metodo_amortizacion": (data.metodo_amortizacion or "Lineal").strip() or "Lineal",
        "amortizacion_acumulada": round(float(data.amortizacion_acumulada or 0), 2),
        "ubicacion": (data.ubicacion or "").strip(),
        "superficie_ha": data.superficie_ha,
        "nomenclatura": (data.nomenclatura or "").strip(),
        "partida": (data.partida or "").strip(),
        "observaciones": (data.observaciones or "").strip(),
        "activo": 1 if int(data.activo or 0) else 0,
        "fecha_baja": (data.fecha_baja or "")[:10] if not int(data.activo or 0) else "",
        "usuario_registro": (data.usuario_registro or "Administrador").strip(),
    }


@app.get("/api/patrimonio/bienes")
def api_patrimonio_listar(
    categoria: str = "",
    q: str = "",
    solo_activos: bool = True,
    fecha_corte: str = "",
):
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    sql = "SELECT * FROM bienes_patrimoniales WHERE COALESCE(empresa_id,1)=?"
    params: list = [empresa_id]
    q_txt = (q or "").strip()
    # Listado operativo: sin bajas. Búsqueda histórica (con texto) o desmarcar "solo activos" las incluye.
    if solo_activos and not q_txt:
        sql += " AND COALESCE(activo,1)=1 AND TRIM(COALESCE(fecha_baja,''))=''"
    if (categoria or "").strip():
        sql += " AND UPPER(TRIM(COALESCE(categoria,''))) = UPPER(TRIM(?))"
        params.append(categoria.strip())
    if q_txt:
        like = f"%{q_txt}%"
        sql += """ AND (
            COALESCE(marca,'') LIKE ? OR COALESCE(modelo,'') LIKE ?
            OR COALESCE(detalle,'') LIKE ? OR COALESCE(nro_patente,'') LIKE ?
            OR COALESCE(tipo_rodado,'') LIKE ? OR COALESCE(ubicacion,'') LIKE ?
            OR COALESCE(nomenclatura,'') LIKE ? OR COALESCE(partida,'') LIKE ?
            OR COALESCE(categoria,'') LIKE ?
        )"""
        params.extend([like] * 9)
    sql += " ORDER BY COALESCE(categoria,''), COALESCE(fecha_compra,''), id;"
    cursor.execute(sql, params)
    corte = (fecha_corte or "")[:10] or None
    rows = [_enriquecer_bien(dict(r), corte) for r in cursor.fetchall()]
    tot_hist = round(sum(r["valor_historico"] for r in rows), 2)
    tot_com_usd = round(sum(float(r.get("valor_comercial_usd") or 0) for r in rows), 2)
    tot_com = round(sum(float(r.get("valor_comercial_ars") or r.get("valor_comercial") or 0) for r in rows), 2)
    tot_vnc = round(sum(r["valor_neto_contable"] for r in rows), 2)
    tot_acum = round(sum(float(r.get("amortizacion_acumulada") or 0) for r in rows), 2)
    cursor.execute(
        """
        SELECT DISTINCT TRIM(categoria) AS c
        FROM bienes_patrimoniales
        WHERE COALESCE(empresa_id,1)=? AND TRIM(COALESCE(categoria,'')) != ''
        ORDER BY c COLLATE NOCASE;
        """,
        (empresa_id,),
    )
    cats_db = [r["c"] for r in cursor.fetchall() if r["c"]]
    cats = sorted(set(_CATEGORIAS_BIEN_ACCESS + cats_db), key=lambda x: x.lower())
    conn.close()
    return {
        "bienes": rows,
        "categorias": cats,
        "resumen": {
            "cantidad": len(rows),
            "total_historico": tot_hist,
            "total_comercial_usd": tot_com_usd,
            "total_comercial": tot_com,
            "total_neto_contable": tot_vnc,
            "total_amort_acumulada": tot_acum,
        },
    }


@app.get("/api/patrimonio/bienes/{bien_id}")
def api_patrimonio_get(bien_id: int):
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM bienes_patrimoniales WHERE id=? AND COALESCE(empresa_id,1)=?;",
        (bien_id, empresa_id),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Bien no encontrado")
    return _enriquecer_bien(dict(row))


@app.post("/api/patrimonio/bienes")
def api_patrimonio_crear(data: BienPatrimonialModel):
    empresa_id = get_empresa_activa_id()
    p = _payload_bien_from_model(data)
    if p["valor_historico"] < 0.01 and p["valor_comercial"] < 0.01 and p["valor_comercial_usd"] < 0.01:
        raise HTTPException(400, "Indicá al menos valor histórico (EECC) o valor comercial (USD).")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO bienes_patrimoniales (
            empresa_id, categoria, tipo_rodado, marca, modelo, detalle, anio,
            nro_patente, nro_chasis, nro_motor, nro_factura, fecha_compra, fecha_inicio_amort,
            valor_historico, importe_neto, iva, total, valor_residual,
            valor_comercial_usd, tipo_cambio_comercial, valor_comercial, fecha_valor_comercial, moneda,
            vida_util_anos, tasa_anual_pct, metodo_amortizacion, amortizacion_acumulada,
            ubicacion, superficie_ha, nomenclatura, partida, observaciones,
            activo, fecha_baja, usuario_registro
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
        """,
        (
            empresa_id, p["categoria"], p["tipo_rodado"], p["marca"], p["modelo"], p["detalle"], p["anio"],
            p["nro_patente"], p["nro_chasis"], p["nro_motor"], p["nro_factura"], p["fecha_compra"], p["fecha_inicio_amort"],
            p["valor_historico"], p["importe_neto"], p["iva"], p["total"], p["valor_residual"],
            p["valor_comercial_usd"], p["tipo_cambio_comercial"], p["valor_comercial"], p["fecha_valor_comercial"], p["moneda"],
            p["vida_util_anos"], p["tasa_anual_pct"], p["metodo_amortizacion"], p["amortizacion_acumulada"],
            p["ubicacion"], p["superficie_ha"], p["nomenclatura"], p["partida"], p["observaciones"],
            p["activo"], p["fecha_baja"], p["usuario_registro"],
        ),
    )
    bien_id = cursor.lastrowid
    conn.commit()
    cursor.execute("SELECT * FROM bienes_patrimoniales WHERE id=?;", (bien_id,))
    out = _enriquecer_bien(dict(cursor.fetchone()))
    conn.close()
    return {"status": "success", "id": bien_id, "bien": out}


@app.put("/api/patrimonio/bienes/{bien_id}")
def api_patrimonio_actualizar(bien_id: int, data: BienPatrimonialModel):
    empresa_id = get_empresa_activa_id()
    p = _payload_bien_from_model(data)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM bienes_patrimoniales WHERE id=? AND COALESCE(empresa_id,1)=?;",
        (bien_id, empresa_id),
    )
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(404, "Bien no encontrado")
    cursor.execute(
        """
        UPDATE bienes_patrimoniales SET
            categoria=?, tipo_rodado=?, marca=?, modelo=?, detalle=?, anio=?,
            nro_patente=?, nro_chasis=?, nro_motor=?, nro_factura=?, fecha_compra=?, fecha_inicio_amort=?,
            valor_historico=?, importe_neto=?, iva=?, total=?, valor_residual=?,
            valor_comercial_usd=?, tipo_cambio_comercial=?, valor_comercial=?, fecha_valor_comercial=?, moneda=?,
            vida_util_anos=?, tasa_anual_pct=?, metodo_amortizacion=?, amortizacion_acumulada=?,
            ubicacion=?, superficie_ha=?, nomenclatura=?, partida=?, observaciones=?,
            activo=?, fecha_baja=?, usuario_registro=?
        WHERE id=?;
        """,
        (
            p["categoria"], p["tipo_rodado"], p["marca"], p["modelo"], p["detalle"], p["anio"],
            p["nro_patente"], p["nro_chasis"], p["nro_motor"], p["nro_factura"], p["fecha_compra"], p["fecha_inicio_amort"],
            p["valor_historico"], p["importe_neto"], p["iva"], p["total"], p["valor_residual"],
            p["valor_comercial_usd"], p["tipo_cambio_comercial"], p["valor_comercial"], p["fecha_valor_comercial"], p["moneda"],
            p["vida_util_anos"], p["tasa_anual_pct"], p["metodo_amortizacion"], p["amortizacion_acumulada"],
            p["ubicacion"], p["superficie_ha"], p["nomenclatura"], p["partida"], p["observaciones"],
            p["activo"], p["fecha_baja"], p["usuario_registro"],
            bien_id,
        ),
    )
    conn.commit()
    cursor.execute("SELECT * FROM bienes_patrimoniales WHERE id=?;", (bien_id,))
    out = _enriquecer_bien(dict(cursor.fetchone()))
    conn.close()
    return {"status": "success", "bien": out}


@app.delete("/api/patrimonio/bienes/{bien_id}")
def api_patrimonio_baja(bien_id: int, hard: bool = False):
    """Baja lógica (fecha_baja) o hard delete."""
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM bienes_patrimoniales WHERE id=? AND COALESCE(empresa_id,1)=?;",
        (bien_id, empresa_id),
    )
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(404, "Bien no encontrado")
    if hard:
        cursor.execute("DELETE FROM bienes_amortizaciones WHERE bien_id=? AND COALESCE(empresa_id,1)=?;", (bien_id, empresa_id))
        cursor.execute("DELETE FROM bienes_patrimoniales WHERE id=?;", (bien_id,))
        msg = "Bien eliminado."
    else:
        hoy = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            "UPDATE bienes_patrimoniales SET activo=0, fecha_baja=? WHERE id=?;",
            (hoy, bien_id),
        )
        msg = "Bien dado de baja."
    conn.commit()
    conn.close()
    return {"status": "success", "message": msg}


@app.get("/api/patrimonio/resumen_bancario")
def api_patrimonio_resumen_bancario():
    """Totales por categoría para carpetas bancarias (valor comercial + VNC)."""
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM bienes_patrimoniales
        WHERE COALESCE(empresa_id,1)=? AND COALESCE(activo,1)=1 AND TRIM(COALESCE(fecha_baja,''))='';
        """,
        (empresa_id,),
    )
    rows = [_enriquecer_bien(dict(r)) for r in cursor.fetchall()]
    por_cat: dict = {}
    for r in rows:
        cat = r["categoria"]
        bucket = por_cat.setdefault(cat, {
            "categoria": cat, "cantidad": 0,
            "valor_historico": 0.0, "valor_comercial_usd": 0.0,
            "valor_comercial": 0.0, "valor_neto_contable": 0.0,
        })
        bucket["cantidad"] += 1
        bucket["valor_historico"] = round(bucket["valor_historico"] + r["valor_historico"], 2)
        bucket["valor_comercial_usd"] = round(bucket["valor_comercial_usd"] + float(r.get("valor_comercial_usd") or 0), 2)
        bucket["valor_comercial"] = round(bucket["valor_comercial"] + float(r.get("valor_comercial_ars") or r.get("valor_comercial") or 0), 2)
        bucket["valor_neto_contable"] = round(bucket["valor_neto_contable"] + r["valor_neto_contable"], 2)
    conn.close()
    categorias = sorted(por_cat.values(), key=lambda x: x["categoria"])
    return {
        "categorias": categorias,
        "total_historico": round(sum(c["valor_historico"] for c in categorias), 2),
        "total_comercial_usd": round(sum(c["valor_comercial_usd"] for c in categorias), 2),
        "total_comercial": round(sum(c["valor_comercial"] for c in categorias), 2),
        "total_neto_contable": round(sum(c["valor_neto_contable"] for c in categorias), 2),
        "cantidad": len(rows),
    }


@app.post("/api/patrimonio/amortizar")
def api_patrimonio_amortizar(data: AmortizarBienesModel):
    """Simula o aplica amortización lineal del ejercicio (cierre 30/06)."""
    from motor_contable import crear_asiento, _asegurar_cuenta, init_contabilidad

    empresa_id = get_empresa_activa_id()
    ej = (data.ejercicio or "").strip()
    if not ej:
        ej = ejercicio_desde_fecha(datetime.now().strftime("%Y-%m-%d"))
    # ejercicio "2025/2026" → cierre 2026-06-30
    cierre = (data.fecha_cierre or "").strip()[:10]
    if not cierre:
        try:
            anio_fin = int(ej.split("/")[-1])
            cierre = f"{anio_fin}-06-30"
        except Exception:
            cierre = datetime.now().strftime("%Y-%m-%d")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM bienes_patrimoniales
        WHERE COALESCE(empresa_id,1)=? AND COALESCE(activo,1)=1 AND TRIM(COALESCE(fecha_baja,''))='';
        """,
        (empresa_id,),
    )
    bienes = [dict(r) for r in cursor.fetchall()]
    detalle = []
    total_amort = 0.0
    for b in bienes:
        enr = _enriquecer_bien(b, cierre)
        hist = enr["valor_historico"]
        residual = round(float(b.get("valor_residual") or 0), 2)
        base = max(0.0, hist - residual)
        acum = round(float(b.get("amortizacion_acumulada") or 0), 2)
        pendiente = max(0.0, base - acum)
        if pendiente < 0.01:
            continue
        # ¿Ya amortizado este ejercicio?
        cursor.execute(
            """
            SELECT monto FROM bienes_amortizaciones
            WHERE COALESCE(empresa_id,1)=? AND bien_id=? AND ejercicio=?;
            """,
            (empresa_id, b["id"], ej),
        )
        ya = cursor.fetchone()
        if ya and data.aplicar:
            continue
        cuota = enr["cuota_anual"]
        # Prorrateo primer año si compra dentro del ejercicio
        inicio = (b.get("fecha_inicio_amort") or b.get("fecha_compra") or "")[:10]
        factor = 1.0
        if inicio and inicio > f"{int(cierre[:4]) - 1}-07-01":
            try:
                d0 = datetime.strptime(inicio, "%Y-%m-%d")
                d1 = datetime.strptime(cierre, "%Y-%m-%d")
                if d0 > d1:
                    continue
                dias = (d1 - d0).days + 1
                factor = min(1.0, max(0.0, dias / 365.25))
            except ValueError:
                factor = 1.0
        monto = round(min(pendiente, cuota * factor), 2)
        if monto < 0.01:
            continue
        total_amort += monto
        detalle.append({
            "bien_id": b["id"],
            "descripcion": f"{enr['categoria']} · {b.get('marca') or ''} {b.get('modelo') or b.get('detalle') or ''}".strip(),
            "categoria": enr["categoria"],
            "valor_historico": hist,
            "amort_acumulada_antes": acum,
            "amort_ejercicio": monto,
            "amort_acumulada_despues": round(acum + monto, 2),
            "valor_neto_despues": round(max(0.0, hist - acum - monto), 2),
            "ya_registrada": bool(ya),
            "monto_previo_ejercicio": float(ya["monto"]) if ya else 0.0,
        })

    asiento_id = None
    if data.aplicar and detalle:
        init_contabilidad(cursor)
        # Cuentas según plan (gasto amort / depreciación acum. bienes de uso)
        cta_gasto = _asegurar_cuenta(cursor, "5.1.90", "Amortización Bienes de Uso", "Resultado")
        cta_acum = _asegurar_cuenta(cursor, "1.2.90", "Amortización Acumulada Bienes de Uso", "Activo")
        for d in detalle:
            if d.get("ya_registrada"):
                continue
            cursor.execute(
                """
                UPDATE bienes_patrimoniales
                SET amortizacion_acumulada = COALESCE(amortizacion_acumulada,0) + ?
                WHERE id=?;
                """,
                (d["amort_ejercicio"], d["bien_id"]),
            )
            cursor.execute(
                """
                INSERT INTO bienes_amortizaciones (
                    empresa_id, bien_id, ejercicio, fecha_cierre, monto, usuario_registro, created_at
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(empresa_id, bien_id, ejercicio) DO UPDATE SET
                    monto=excluded.monto, fecha_cierre=excluded.fecha_cierre;
                """,
                (
                    empresa_id, d["bien_id"], ej, cierre, d["amort_ejercicio"],
                    data.usuario_registro or "Administrador",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        if data.generar_asiento and total_amort > 0.01:
            asiento_id = crear_asiento(
                cursor,
                fecha=cierre,
                concepto=f"Amortización bienes de uso ejercicio {ej}",
                lineas=[
                    {"cuenta_id": cta_gasto, "debe": total_amort, "haber": 0},
                    {"cuenta_id": cta_acum, "debe": 0, "haber": total_amort},
                ],
                empresa_id=empresa_id,
                origen_modulo="patrimonio_amort",
                referencia=f"AMORT-{ej}",
            )
            cursor.execute(
                """
                UPDATE bienes_amortizaciones SET asiento_id=?
                WHERE COALESCE(empresa_id,1)=? AND ejercicio=? AND asiento_id IS NULL;
                """,
                (asiento_id, empresa_id, ej),
            )
        conn.commit()

    conn.close()
    return {
        "status": "success",
        "simulacion": not data.aplicar,
        "ejercicio": ej,
        "fecha_cierre": cierre,
        "total_amortizacion": round(total_amort, 2),
        "cantidad": len(detalle),
        "asiento_id": asiento_id,
        "detalle": detalle,
    }


@app.patch("/api/bancos/movimientos_cta_cte/{id_mov}/conciliar")
def conciliar_movimiento_manual(id_mov: int, data: ConciliacionManualModel):
    fecha = (data.fecha_debito or "").strip()
    if not fecha:
        raise HTTPException(status_code=400, detail="Debe indicar fecha de débito / conciliación.")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM movimientos_cta_cte_bancos WHERE id = ?;", (id_mov,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Movimiento no encontrado.")
    cursor.execute(
        """
        UPDATE movimientos_cta_cte_bancos
        SET fecha_debito = ?, conciliado = 1
        WHERE id = ?;
        """,
        (fecha, id_mov),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Movimiento conciliado.", "id": id_mov, "fecha_debito": fecha}

@app.patch("/api/bancos/movimientos_cta_cte/{id_mov}/desconciliar")
def desconciliar_movimiento(id_mov: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM movimientos_cta_cte_bancos WHERE id = ?;", (id_mov,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Movimiento no encontrado.")
    cursor.execute(
        """
        UPDATE movimientos_cta_cte_bancos
        SET fecha_debito = '', conciliado = 0
        WHERE id = ?;
        """,
        (id_mov,),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Movimiento desconciliado.", "id": id_mov}

@app.post("/api/bancos/movimientos_cta_cte/conciliar_lote")
def conciliar_movimientos_lote(data: ConciliacionLoteModel):
    fecha = (data.fecha_debito or "").strip()
    if not fecha:
        raise HTTPException(status_code=400, detail="Debe indicar fecha de débito.")
    if not data.ids:
        raise HTTPException(status_code=400, detail="No hay movimientos seleccionados.")
    conn = get_db()
    cursor = conn.cursor()
    actualizados = 0
    for mid in data.ids:
        cursor.execute(
            """
            UPDATE movimientos_cta_cte_bancos
            SET fecha_debito = ?, conciliado = 1
            WHERE id = ?;
            """,
            (fecha, int(mid)),
        )
        actualizados += cursor.rowcount
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Se conciliaron {actualizados} movimientos.", "actualizados": actualizados}

@app.post("/api/bancos/conciliar/automatica")
async def conciliar_automatica(
    file: UploadFile = File(...),
    cuenta_id: int = Form(...),
    tolerancia_dias: int = Form(3),
    cta_cte_nro: Optional[str] = Form(None),
):
    """Cruza extracto Excel/CSV con movimientos pendientes de la cuenta."""
    contenido = await file.read()
    nombre = (file.filename or "").lower()
    try:
        if nombre.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contenido))
        else:
            df = pd.read_excel(io.BytesIO(contenido))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"No se pudo leer el extracto: {e}")

    if df.empty:
        raise HTTPException(status_code=400, detail="El extracto no tiene filas.")

    col_fecha = _col_excel(df.columns, "Fecha", "Fecha movimiento", "FechaMov", "Date")
    col_importe = _col_excel(df.columns, "Importe", "Monto", "Amount", "HABER", "DEBE", "Importe $")
    col_haber = _col_excel(df.columns, "HABER", "Haber", "Débito", "Debito")
    col_debe = _col_excel(df.columns, "DEBE", "Debe", "Crédito", "Credito")
    if not col_fecha:
        raise HTTPException(status_code=400, detail="El extracto debe tener columna de Fecha.")

    conn = get_db()
    cursor = conn.cursor()
    cta_nro = (cta_cte_nro or "").strip()
    if not cta_nro:
        cursor.execute("SELECT nro_cta_cte FROM ctas_ctes_bancarias WHERE id = ?;", (cuenta_id,))
        row = cursor.fetchone()
        cta_nro = str(row["nro_cta_cte"]).strip() if row and row["nro_cta_cte"] else ""

    cursor.execute(
        """
        SELECT id, fecha_cobro, haber, debe, proveedor, nro_cheque
        FROM movimientos_cta_cte_bancos
        WHERE TRIM(cta_cte_nro) = ?
          AND (fecha_debito IS NULL OR TRIM(COALESCE(fecha_debito, '')) = '')
        ORDER BY fecha_cobro ASC, id ASC;
        """,
        (cta_nro,),
    )
    pendientes = [dict(r) for r in cursor.fetchall()]
    usados = set()
    conciliados = []
    sin_match = []

    for _, erow in df.iterrows():
        fec = safe_date(erow.get(col_fecha))
        if not fec:
            continue
        importe = 0.0
        if col_importe and pd.notnull(erow.get(col_importe)):
            try:
                importe = abs(float(erow.get(col_importe) or 0))
            except (TypeError, ValueError):
                importe = 0.0
        if importe == 0 and col_haber and pd.notnull(erow.get(col_haber)):
            try:
                importe = abs(float(erow.get(col_haber) or 0))
            except (TypeError, ValueError):
                pass
        if importe == 0 and col_debe and pd.notnull(erow.get(col_debe)):
            try:
                importe = abs(float(erow.get(col_debe) or 0))
            except (TypeError, ValueError):
                pass
        if importe <= 0:
            sin_match.append({"fecha": fec, "importe": importe, "motivo": "sin importe"})
            continue

        try:
            fec_dt = datetime.strptime(fec, "%Y-%m-%d").date()
        except ValueError:
            continue

        mejor = None
        mejor_diff_dias = 999
        for mov in pendientes:
            if mov["id"] in usados:
                continue
            mov_monto = abs(float(mov.get("haber") or 0) or float(mov.get("debe") or 0))
            if abs(mov_monto - importe) > 0.05:
                continue
            try:
                mov_dt = datetime.strptime(str(mov.get("fecha_cobro") or "")[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            diff = abs((mov_dt - fec_dt).days)
            if diff <= tolerancia_dias and diff < mejor_diff_dias:
                mejor = mov
                mejor_diff_dias = diff

        if mejor:
            usados.add(mejor["id"])
            cursor.execute(
                """
                UPDATE movimientos_cta_cte_bancos
                SET fecha_debito = ?, conciliado = 1
                WHERE id = ?;
                """,
                (fec, mejor["id"]),
            )
            conciliados.append({
                "movimiento_id": mejor["id"],
                "proveedor": mejor.get("proveedor"),
                "importe": importe,
                "fecha_extracto": fec,
                "fecha_cobro": mejor.get("fecha_cobro"),
            })
        else:
            sin_match.append({"fecha": fec, "importe": importe, "motivo": "sin match"})

    conn.commit()
    conn.close()
    return {
        "status": "success",
        "cuenta": cta_nro,
        "conciliados": len(conciliados),
        "sin_match": len(sin_match),
        "detalle_conciliados": conciliados[:200],
        "detalle_sin_match": sin_match[:100],
    }


@app.get("/api/bancos/cheques")
def listar_cheques(
    tipo: Optional[str] = None,
    estado: Optional[str] = "disponibles",
    q: Optional[str] = None,
    empresa_id: Optional[int] = None,
):
    """
    estado=disponibles → En cartera (listos para depositar o pagar).
    estado=todos → todos.
    estado=<texto> → filtro exacto.
    q → busca por nº cheque, banco, cliente, titular, librador, dador, CUIT, observaciones.
    """
    conn = get_db()
    cursor = conn.cursor()
    eid = empresa_id or get_empresa_activa_id()
    query = "SELECT * FROM cartera_cheques WHERE COALESCE(empresa_id, 1) = ?"
    params: list = [eid]
    if tipo:
        query += " AND UPPER(TRIM(tipo)) = UPPER(TRIM(?))"
        params.append(tipo)
    est = (estado or "disponibles").strip().lower()
    if est in ("disponibles", "disponible", "en_cartera", "cartera"):
        # Solo cheques recibidos aún no depositados / entregados. Nunca emitidos propios.
        query += """
          AND LOWER(TRIM(COALESCE(estado,''))) IN ('en cartera', 'en carteras')
          AND UPPER(TRIM(COALESCE(tipo,''))) NOT IN ('EMITIDO')
        """
    elif est in ("emitido", "emitidos"):
        query += " AND UPPER(TRIM(COALESCE(tipo,''))) = 'EMITIDO'"
    elif est not in ("todos", "all", ""):
        query += " AND LOWER(TRIM(COALESCE(estado,''))) = ?"
        params.append(est)
    qq = (q or "").strip()
    if qq:
        like = f"%{qq}%"
        query += """
          AND (
            TRIM(COALESCE(nro_cheque,'')) LIKE ?
            OR COALESCE(banco,'') LIKE ?
            OR COALESCE(cliente_nombre,'') LIKE ?
            OR COALESCE(dador,'') LIKE ?
            OR COALESCE(titular,'') LIKE ?
            OR COALESCE(librador,'') LIKE ?
            OR COALESCE(cliente_cuit,'') LIKE ?
            OR COALESCE(cuit_emisor,'') LIKE ?
            OR COALESCE(proveedor_cuit,'') LIKE ?
            OR COALESCE(observaciones,'') LIKE ?
          )
        """
        params.extend([like] * 10)
    query += " ORDER BY COALESCE(fecha_pago, fecha_recepcion, '') ASC, id ASC;"
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


@app.post("/api/bancos/cheques")
def registrar_cheque(data: ChequeModel):
    """
    Alta de cheque/eCheq recibido de un cliente.
    Impacta haber en la cta cte del cliente y queda 'En cartera'.
    """
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    nro = (data.nro_cheque or "").strip()
    if not nro:
        conn.close()
        raise HTTPException(400, "Nº de cheque obligatorio")
    if not data.monto or float(data.monto) <= 0:
        conn.close()
        raise HTTPException(400, "Importe inválido")

    cliente_cuit = re.sub(r"\D", "", data.cliente_cuit or "")
    cliente_nombre = (data.cliente_nombre or data.dador or "").strip()
    titular = (data.titular or data.librador or "").strip()
    librador = (data.librador or titular or cliente_nombre).strip()
    cuit_emisor = re.sub(r"\D", "", data.cuit_emisor or "")
    fecha_rec = (data.fecha_recepcion or data.fecha_emision or data.fecha_pago or "").strip()
    fecha_em = (data.fecha_emision or fecha_rec).strip()
    fecha_vto = (data.fecha_pago or fecha_rec).strip()
    es_tercero = int(data.es_tercero if data.es_tercero is not None else 1)
    tipo = (data.tipo or ("Tercero" if es_tercero else "Propio")).strip()
    if tipo.lower() in ("tercero", "terceros"):
        tipo = "Tercero"
        es_tercero = 1
    elif tipo.lower() in ("propio", "propios"):
        tipo = "Propio"
        es_tercero = 0

    cc_id = None
    if data.impactar_cc_cliente and cliente_cuit:
        cursor.execute(
            """
            INSERT INTO cuentas_corrientes (
                entidad_id, tipo_comprobante, numero_comprobante, forma_pago, nro_cheque,
                fecha, vencimiento, neto, iva, debe, haber, total, estado, usuario_registro, empresa_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 'Aplicado', ?, ?);
            """,
            (
                cliente_cuit,
                "Cheque Terceros" if es_tercero else "Cheque / E-Cheq",
                nro,
                "Cheque",
                nro,
                fecha_rec,
                fecha_vto,
                float(data.monto),
                float(data.monto),
                float(data.monto),
                (data.observaciones or cliente_nombre or "")[:80],
                empresa_id,
            ),
        )
        cc_id = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO cartera_cheques (
            tipo, nro_cheque, banco, cuit_emisor, librador, fecha_emision, fecha_pago,
            monto, moneda, estado, cuenta_id, empresa_id, cliente_cuit, cliente_nombre,
            titular, dador, es_tercero, fecha_recepcion, cc_id, observaciones
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'En cartera', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            tipo,
            nro,
            (data.banco or "").strip(),
            cuit_emisor,
            librador,
            fecha_em,
            fecha_vto,
            float(data.monto),
            data.moneda or "ARS",
            empresa_id,
            cliente_cuit,
            cliente_nombre,
            titular,
            (data.dador or cliente_nombre).strip(),
            es_tercero,
            fecha_rec,
            cc_id,
            (data.observaciones or "").strip(),
        ),
    )
    chq_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "id": chq_id,
        "cc_id": cc_id,
        "message": "Cheque en cartera" + (" e impactado en CC del cliente." if cc_id else "."),
    }


@app.put("/api/bancos/cheques/{cheque_id}")
def actualizar_cheque(cheque_id: int, data: ChequeModel):
    """Corrige datos de un cheque (útil si se cargó mal). Sincroniza CC vinculada si existe."""
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM cartera_cheques WHERE id = ? AND COALESCE(empresa_id,1) = ?;",
        (cheque_id, empresa_id),
    )
    ch = cursor.fetchone()
    if not ch:
        conn.close()
        raise HTTPException(404, "Cheque no encontrado")

    nro = (data.nro_cheque or "").strip()
    if not nro:
        conn.close()
        raise HTTPException(400, "Nº de cheque obligatorio")
    if not data.monto or float(data.monto) <= 0:
        conn.close()
        raise HTTPException(400, "Importe inválido")

    cliente_cuit = re.sub(r"\D", "", data.cliente_cuit or "")
    cliente_nombre = (data.cliente_nombre or data.dador or "").strip()
    titular = (data.titular or data.librador or "").strip()
    librador = (data.librador or titular or cliente_nombre).strip()
    cuit_emisor = re.sub(r"\D", "", data.cuit_emisor or "")
    fecha_rec = (data.fecha_recepcion or data.fecha_emision or data.fecha_pago or "").strip()
    fecha_em = (data.fecha_emision or fecha_rec).strip()
    fecha_vto = (data.fecha_pago or fecha_rec).strip()
    es_tercero = int(data.es_tercero if data.es_tercero is not None else 1)
    tipo = (data.tipo or ("Tercero" if es_tercero else "Propio")).strip()
    if tipo.lower() in ("tercero", "terceros"):
        tipo = "Tercero"
        es_tercero = 1
    elif tipo.lower() in ("propio", "propios"):
        tipo = "Propio"
        es_tercero = 0
    # No forzar Emitido a Propio/Tercero si ya era emitido y no lo cambiaron
    if str(ch["tipo"] or "").upper() == "EMITIDO" and tipo.lower() not in ("tercero", "terceros", "propio", "propios"):
        tipo = "Emitido"
        es_tercero = 0

    monto = float(data.monto)
    cursor.execute(
        """
        UPDATE cartera_cheques SET
            tipo=?, nro_cheque=?, banco=?, cuit_emisor=?, librador=?,
            fecha_emision=?, fecha_pago=?, monto=?,
            cliente_cuit=?, cliente_nombre=?, titular=?, dador=?,
            es_tercero=?, fecha_recepcion=?, observaciones=?
        WHERE id=?;
        """,
        (
            tipo,
            nro,
            (data.banco or "").strip(),
            cuit_emisor,
            librador,
            fecha_em,
            fecha_vto,
            monto,
            cliente_cuit,
            cliente_nombre,
            titular,
            (data.dador or cliente_nombre).strip(),
            es_tercero,
            fecha_rec,
            (data.observaciones or "").strip(),
            cheque_id,
        ),
    )

    # Sincronizar movimiento CC de ingreso (si existe)
    cc_id = ch["cc_id"] if "cc_id" in ch.keys() else None
    if cc_id:
        cursor.execute(
            """
            UPDATE cuentas_corrientes SET
                entidad_id=COALESCE(NULLIF(?, ''), entidad_id),
                numero_comprobante=?, nro_cheque=?,
                fecha=COALESCE(NULLIF(?, ''), fecha),
                vencimiento=COALESCE(NULLIF(?, ''), vencimiento),
                neto=?, haber=?, total=?,
                tipo_comprobante=?
            WHERE id=?;
            """,
            (
                cliente_cuit,
                nro,
                nro,
                fecha_rec,
                fecha_vto,
                monto,
                monto,
                monto,
                "Cheque Terceros" if es_tercero else "Cheque / E-Cheq",
                cc_id,
            ),
        )

    conn.commit()
    conn.close()
    return {"status": "success", "id": cheque_id, "message": "Cheque actualizado."}


@app.post("/api/bancos/cheques/{cheque_id}/depositar")
def depositar_cheque(cheque_id: int, data: ChequeDepositarModel):
    """Deposita el cheque en una cta cte bancaria → sale de disponibles."""
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM cartera_cheques WHERE id = ? AND COALESCE(empresa_id,1) = ?;",
        (cheque_id, empresa_id),
    )
    ch = cursor.fetchone()
    if not ch:
        conn.close()
        raise HTTPException(404, "Cheque no encontrado")
    est = (ch["estado"] or "").strip().lower()
    if est not in ("en cartera", "en carteras"):
        conn.close()
        raise HTTPException(400, f"El cheque no está disponible (estado: {ch['estado']})")

    cursor.execute(
        "SELECT id, nro_cta_cte, banco FROM ctas_ctes_bancarias WHERE id = ?;",
        (data.cuenta_id,),
    )
    cta = cursor.fetchone()
    if not cta:
        conn.close()
        raise HTTPException(400, "Cuenta bancaria inválida")

    fecha = (data.fecha or ch["fecha_recepcion"] or ch["fecha_pago"] or "").strip()
    if not fecha:
        from datetime import date as _date
        fecha = _date.today().isoformat()

    ref = (ch["cliente_nombre"] or ch["dador"] or ch["librador"] or "Depósito cheque").strip()
    obs = (data.observaciones or "").strip()
    proveedor = f"Dep. Ch. {ch['nro_cheque']} — {ref}"[:120]
    cursor.execute(
        """
        INSERT INTO movimientos_cta_cte_bancos
        (cuenta_id, fecha_cobro, fecha_debito, proveedor, nro_cheque, haber, debe, imp_chq,
         cta_cte_nro, conciliado, tipo_operacion)
        VALUES (?, ?, '', ?, ?, 0, ?, 0, ?, 0, 'Deposito');
        """,
        (
            data.cuenta_id,
            fecha,
            proveedor,
            ch["nro_cheque"],
            float(ch["monto"] or 0),
            cta["nro_cta_cte"] or "",
        ),
    )
    mov_id = cursor.lastrowid
    cursor.execute(
        """
        UPDATE cartera_cheques
        SET estado = 'Depositado', cuenta_id = ?, movimiento_banco_id = ?,
            observaciones = TRIM(COALESCE(observaciones,'') || ?)
        WHERE id = ?;
        """,
        (data.cuenta_id, mov_id, (" | " + obs) if obs else "", cheque_id),
    )
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "movimiento_id": mov_id,
        "message": f"Cheque depositado en {cta['banco'] or ''} {cta['nro_cta_cte'] or ''}".strip(),
    }


@app.post("/api/bancos/cheques/{cheque_id}/aplicar_pago")
def aplicar_cheque_pago(cheque_id: int, data: ChequeAplicarPagoModel):
    """Entrega el cheque como pago a un proveedor → sale de disponibles."""
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM cartera_cheques WHERE id = ? AND COALESCE(empresa_id,1) = ?;",
        (cheque_id, empresa_id),
    )
    ch = cursor.fetchone()
    if not ch:
        conn.close()
        raise HTTPException(404, "Cheque no encontrado")
    est = (ch["estado"] or "").strip().lower()
    if est not in ("en cartera", "en carteras"):
        conn.close()
        raise HTTPException(400, f"El cheque no está disponible (estado: {ch['estado']})")

    prov_cuit = re.sub(r"\D", "", data.proveedor_cuit or "")
    if len(prov_cuit) < 10:
        conn.close()
        raise HTTPException(400, "CUIT de proveedor inválido")

    cursor.execute(
        """
        SELECT razon_social, nombre_fantasia FROM entidades
        WHERE REPLACE(cuit,'-','') = ? LIMIT 1;
        """,
        (prov_cuit,),
    )
    ent = cursor.fetchone()
    prov_nombre = ""
    if ent:
        prov_nombre = (ent["nombre_fantasia"] or ent["razon_social"] or "").strip()

    fecha = (data.fecha or ch["fecha_recepcion"] or ch["fecha_pago"] or "").strip()
    if not fecha:
        from datetime import date as _date
        fecha = _date.today().isoformat()

    monto = float(ch["monto"] or 0)
    nro = ch["nro_cheque"] or ""
    cursor.execute(
        """
        INSERT INTO cuentas_corrientes (
            entidad_id, tipo_comprobante, numero_comprobante, forma_pago, nro_cheque,
            fecha, vencimiento, neto, iva, debe, haber, total, estado, usuario_registro, empresa_id
        ) VALUES (?, 'Pago con Cheque', ?, 'Cheque cartera', ?, ?, ?, ?, 0, 0, ?, ?, 'Aplicado', ?, ?);
        """,
        (
            prov_cuit,
            nro,
            nro,
            fecha,
            fecha,
            monto,
            monto,
            monto,
            (data.observaciones or f"Ch. {nro} de {ch['librador'] or ch['cliente_nombre'] or ''}")[:80],
            empresa_id,
        ),
    )
    cc_pago_id = cursor.lastrowid
    cursor.execute(
        """
        UPDATE cartera_cheques
        SET estado = 'Entregado proveedor', proveedor_cuit = ?, op_id = ?,
            observaciones = TRIM(COALESCE(observaciones,'') || ?)
        WHERE id = ?;
        """,
        (
            prov_cuit,
            cc_pago_id,
            (" | Pago a " + (prov_nombre or prov_cuit))[:80],
            cheque_id,
        ),
    )
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "cc_id": cc_pago_id,
        "message": f"Cheque aplicado a pago de {prov_nombre or prov_cuit}",
    }


@app.delete("/api/bancos/cheques/{cheque_id}")
def eliminar_cheque_cartera(cheque_id: int):
    """
    Elimina un cheque de cartera.
    Si impactó CC del cliente / depósito bancario / pago a proveedor, revierte esos vínculos.
    """
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM cartera_cheques WHERE id = ? AND COALESCE(empresa_id,1) = ?;",
        (cheque_id, empresa_id),
    )
    ch = cursor.fetchone()
    if not ch:
        conn.close()
        raise HTTPException(404, "Cheque no encontrado")

    revertidos = []
    cc_id = ch["cc_id"] if "cc_id" in ch.keys() else None
    mov_id = ch["movimiento_banco_id"] if "movimiento_banco_id" in ch.keys() else None
    op_id = ch["op_id"] if "op_id" in ch.keys() else None

    if cc_id:
        cursor.execute("DELETE FROM cuentas_corrientes WHERE id = ?;", (cc_id,))
        if cursor.rowcount:
            revertidos.append(f"CC ingreso #{cc_id}")
    if mov_id:
        cursor.execute("DELETE FROM movimientos_cta_cte_bancos WHERE id = ?;", (mov_id,))
        if cursor.rowcount:
            revertidos.append(f"mov. banco #{mov_id}")
    if op_id:
        cursor.execute("DELETE FROM cuentas_corrientes WHERE id = ?;", (op_id,))
        if cursor.rowcount:
            revertidos.append(f"CC pago #{op_id}")

    caja_cc = ch["caja_cc_id"] if "caja_cc_id" in ch.keys() else None
    if caja_cc:
        cursor.execute("DELETE FROM cuentas_corrientes WHERE id = ?;", (caja_cc,))
        if cursor.rowcount:
            revertidos.append(f"CC caja #{caja_cc}")
        cursor.execute(
            "DELETE FROM caja_movimientos WHERE cheque_id = ? AND COALESCE(empresa_id,1) = ?;",
            (cheque_id, empresa_id),
        )
        if cursor.rowcount:
            revertidos.append("mov. caja")

    cursor.execute("DELETE FROM cartera_cheques WHERE id = ?;", (cheque_id,))
    conn.commit()
    conn.close()
    extra = (" · Revertido: " + ", ".join(revertidos)) if revertidos else ""
    return {
        "status": "success",
        "message": f"Cheque {ch['nro_cheque'] or cheque_id} eliminado.{extra}",
    }


def _ensure_entidad_caja(cursor, empresa_id: int = 1) -> str:
    """Devuelve el CUIT de la entidad CAJA (la crea si no existe)."""
    cursor.execute(
        """
        SELECT cuit FROM entidades
        WHERE UPPER(TRIM(COALESCE(razon_social,''))) IN ('CAJA', 'CAJA EFECTIVO', 'EFECTIVO')
           OR UPPER(TRIM(COALESCE(nombre_fantasia,''))) IN ('CAJA', 'CAJA EFECTIVO', 'EFECTIVO')
        ORDER BY CASE WHEN UPPER(TRIM(COALESCE(nombre_fantasia,''))) = 'CAJA' THEN 0 ELSE 1 END
        LIMIT 1;
        """
    )
    row = cursor.fetchone()
    if row and row["cuit"]:
        return str(row["cuit"])
    # CUIT sintético local (no AFIP): 20 + 9 dígitos fijos de caja
    cuit = "20999999001"
    cursor.execute(
        """
        INSERT INTO entidades (
            cuit, razon_social, nombre_fantasia, es_cliente, es_proveedor,
            es_cuenta_bancaria, es_cuenta_ajuste
        ) VALUES (?, 'CAJA', 'CAJA', 0, 0, 1, 0);
        """,
        (cuit,),
    )
    return cuit


def _saldo_caja_movimientos(cursor, empresa_id: int, hasta: Optional[str] = None) -> float:
    hasta = (hasta or "9999-12-31")[:10]
    cursor.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN LOWER(tipo)='ingreso' THEN monto
                                 WHEN LOWER(tipo)='egreso' THEN -monto
                                 ELSE 0 END), 0) AS saldo
        FROM caja_movimientos
        WHERE COALESCE(empresa_id, 1) = ?
          AND COALESCE(fecha, '') <= ?;
        """,
        (empresa_id, hasta),
    )
    row = cursor.fetchone()
    return round(float(row["saldo"] or 0), 2) if row else 0.0


def _insert_caja_mov(
    cursor,
    *,
    empresa_id: int,
    fecha: str,
    tipo: str,
    monto: float,
    concepto: str,
    forma: str = "Efectivo",
    referencia: str = "",
    cheque_id: Optional[int] = None,
    proveedor_cuit: Optional[str] = None,
    usuario: str = "Administrador",
    mirror_cc: bool = True,
) -> dict:
    """Inserta movimiento de caja y espejo en cta cte de entidad CAJA."""
    from datetime import datetime as _dt

    tipo = (tipo or "").strip().lower()
    if tipo not in ("ingreso", "egreso"):
        raise HTTPException(400, "Tipo de caja inválido (ingreso|egreso).")
    monto = round(float(monto or 0), 2)
    if monto <= 0:
        raise HTTPException(400, "El monto debe ser mayor a cero.")
    fecha = (fecha or "")[:10]
    if not fecha:
        fecha = _dt.now().strftime("%Y-%m-%d")

    cuit_caja = _ensure_entidad_caja(cursor, empresa_id)
    concepto = (concepto or ("Ingreso a caja" if tipo == "ingreso" else "Pago en efectivo")).strip()
    forma = (forma or "Efectivo").strip()
    referencia = (referencia or "").strip()
    usuario = (usuario or "Administrador").strip()

    cc_id = None
    if mirror_cc:
        debe = monto if tipo == "ingreso" else 0.0
        haber = monto if tipo == "egreso" else 0.0
        tipo_comp = "Ingreso Caja" if tipo == "ingreso" else "Egreso Caja"
        cursor.execute(
            """
            INSERT INTO cuentas_corrientes (
                entidad_id, tipo_comprobante, numero_comprobante, forma_pago, nro_cheque,
                fecha, vencimiento, neto, iva, debe, haber, total, estado, usuario_registro, empresa_id,
                observaciones
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                cuit_caja,
                tipo_comp,
                referencia or tipo_comp,
                forma,
                "",
                fecha,
                fecha,
                monto,
                debe,
                haber,
                monto,
                "Registrado",
                usuario,
                empresa_id,
                concepto,
            ),
        )
        cc_id = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO caja_movimientos (
            empresa_id, fecha, tipo, concepto, monto, forma, referencia,
            cheque_id, proveedor_cuit, cc_id, usuario_registro, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            empresa_id,
            fecha,
            tipo,
            concepto,
            monto,
            forma,
            referencia,
            cheque_id,
            (proveedor_cuit or "").strip() or None,
            cc_id,
            usuario,
            _dt.now().isoformat(timespec="seconds"),
        ),
    )
    mov_id = cursor.lastrowid
    return {"id": mov_id, "cc_id": cc_id, "cuit_caja": cuit_caja, "monto": monto, "fecha": fecha, "tipo": tipo}


@app.get("/api/caja/saldo")
def api_caja_saldo():
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    _ensure_entidad_caja(cursor, empresa_id)
    cursor.execute("SELECT COUNT(*) FROM caja_movimientos WHERE COALESCE(empresa_id,1)=?;", (empresa_id,))
    n = int(cursor.fetchone()[0] or 0)
    saldo = _saldo_caja_movimientos(cursor, empresa_id) if n else _saldo_caja_efectivo(cursor, empresa_id, "9999-12-31")
    # Si aún no hay movimientos del módulo, devolver también el legado como sugerencia
    legado = 0.0
    if n == 0:
        legado = round(float(saldo or 0), 2)
        saldo = 0.0
    conn.commit()
    conn.close()
    return {
        "saldo": round(float(saldo or 0), 2),
        "movimientos": n,
        "saldo_legado_sugerido": legado,
        "usa_modulo_caja": n > 0,
    }


@app.get("/api/caja/movimientos")
def api_caja_movimientos(desde: str = "", hasta: str = "", limit: int = 200):
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    q = """
        SELECT * FROM caja_movimientos
        WHERE COALESCE(empresa_id, 1) = ?
    """
    params: list = [empresa_id]
    if (desde or "").strip():
        q += " AND COALESCE(fecha,'') >= ?"
        params.append(desde[:10])
    if (hasta or "").strip():
        q += " AND COALESCE(fecha,'') <= ?"
        params.append(hasta[:10])
    q += " ORDER BY fecha DESC, id DESC LIMIT ?;"
    params.append(max(1, min(int(limit or 200), 1000)))
    cursor.execute(q, params)
    rows = [dict(r) for r in cursor.fetchall()]
    saldo = _saldo_caja_movimientos(cursor, empresa_id)
    conn.close()
    return {"saldo": saldo, "movimientos": rows}


@app.post("/api/caja/ingreso")
def api_caja_ingreso(data: CajaMovimientoModel):
    """Ingreso de efectivo a caja (cobro, extracción de banco, saldo inicial, etc.)."""
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    mov = _insert_caja_mov(
        cursor,
        empresa_id=empresa_id,
        fecha=data.fecha,
        tipo="ingreso",
        monto=data.monto,
        concepto=data.concepto or "Ingreso a caja",
        forma=data.forma or "Efectivo",
        referencia=data.referencia or "",
        usuario=data.usuario_registro or "Administrador",
    )
    conn.commit()
    saldo = _saldo_caja_movimientos(cursor, empresa_id)
    conn.close()
    return {"status": "success", "message": "Ingreso registrado en caja.", "movimiento": mov, "saldo": saldo}


@app.post("/api/caja/egreso")
def api_caja_egreso(data: CajaMovimientoModel):
    """Pago menor en efectivo desde caja. Opcionalmente impacta CC del proveedor."""
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    saldo_act = _saldo_caja_movimientos(cursor, empresa_id)
    if float(data.monto or 0) > saldo_act + 0.01:
        conn.close()
        raise HTTPException(
            400,
            f"Saldo insuficiente en caja ($ {saldo_act:,.2f}). Registrá un ingreso o saldo inicial antes.",
        )

    prov_cuit = "".join(ch for ch in str(data.proveedor_cuit or "") if ch.isdigit())
    mov = _insert_caja_mov(
        cursor,
        empresa_id=empresa_id,
        fecha=data.fecha,
        tipo="egreso",
        monto=data.monto,
        concepto=data.concepto or "Pago en efectivo",
        forma=data.forma or "Efectivo",
        referencia=data.referencia or "",
        proveedor_cuit=prov_cuit or None,
        usuario=data.usuario_registro or "Administrador",
    )

    cc_prov_id = None
    if data.impactar_cc_proveedor and prov_cuit:
        cursor.execute(
            """
            SELECT cuit, COALESCE(nombre_fantasia, razon_social) AS nombre,
                   COALESCE(es_proveedor, 0) AS es_proveedor
            FROM entidades WHERE REPLACE(cuit,'-','') = ? LIMIT 1;
            """,
            (prov_cuit,),
        )
        ent = cursor.fetchone()
        if not ent:
            conn.close()
            raise HTTPException(404, "Proveedor no encontrado en el padrón.")
        if int(ent["es_proveedor"] or 0) != 1:
            conn.close()
            raise HTTPException(400, "La entidad no está marcada como Proveedor.")
        monto = round(float(data.monto or 0), 2)
        cursor.execute(
            """
            INSERT INTO cuentas_corrientes (
                entidad_id, tipo_comprobante, numero_comprobante, forma_pago, nro_cheque,
                fecha, vencimiento, neto, iva, debe, haber, total, estado, usuario_registro, empresa_id,
                observaciones
            ) VALUES (?, 'Pago', ?, 'Efectivo / Caja', '', ?, ?, ?, 0, 0, ?, ?, 'Pagado', ?, ?, ?);
            """,
            (
                ent["cuit"],
                data.referencia or f"CAJA-{mov['id']}",
                (data.fecha or "")[:10],
                (data.fecha or "")[:10],
                monto,
                monto,
                monto,
                data.usuario_registro or "Administrador",
                empresa_id,
                data.concepto or f"Pago en efectivo desde caja — {ent['nombre']}",
            ),
        )
        cc_prov_id = cursor.lastrowid
        cursor.execute(
            "UPDATE caja_movimientos SET cc_proveedor_id = ? WHERE id = ?;",
            (cc_prov_id, mov["id"]),
        )

    conn.commit()
    saldo = _saldo_caja_movimientos(cursor, empresa_id)
    conn.close()
    return {
        "status": "success",
        "message": "Egreso registrado en caja.",
        "movimiento": mov,
        "cc_proveedor_id": cc_prov_id,
        "saldo": saldo,
    }


@app.post("/api/caja/cobrar_cheque/{cheque_id}")
def api_caja_cobrar_cheque(cheque_id: int, data: CajaCobrarChequeModel):
    """Cobra un cheque de cartera en caja: el efectivo entra a caja y el cheque sale de disponibles."""
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM cartera_cheques WHERE id = ? AND COALESCE(empresa_id,1) = ?;",
        (cheque_id, empresa_id),
    )
    ch = cursor.fetchone()
    if not ch:
        conn.close()
        raise HTTPException(404, "Cheque no encontrado")
    est = (ch["estado"] or "").strip().lower()
    if est not in ("en cartera", "en carteras"):
        conn.close()
        raise HTTPException(400, f"El cheque no está disponible (estado: {ch['estado']})")

    monto = round(float(ch["monto"] or 0), 2)
    if monto <= 0:
        conn.close()
        raise HTTPException(400, "Monto de cheque inválido")

    from datetime import date as _date
    fecha = (data.fecha or ch["fecha_recepcion"] or ch["fecha_pago"] or "").strip()[:10]
    if not fecha:
        fecha = _date.today().isoformat()

    nro = (ch["nro_cheque"] or "").strip()
    ref = (ch["cliente_nombre"] or ch["dador"] or ch["librador"] or "").strip()
    concepto = f"Cobro en caja ch. {nro}" + (f" — {ref}" if ref else "")
    obs = (data.observaciones or "").strip()
    if obs:
        concepto = f"{concepto} ({obs})"

    mov = _insert_caja_mov(
        cursor,
        empresa_id=empresa_id,
        fecha=fecha,
        tipo="ingreso",
        monto=monto,
        concepto=concepto[:200],
        forma="Cheque cobrado",
        referencia=nro,
        cheque_id=cheque_id,
        usuario=data.usuario_registro or "Administrador",
    )
    cursor.execute(
        """
        UPDATE cartera_cheques
        SET estado = 'Cobrado en caja', caja_cc_id = ?,
            observaciones = TRIM(COALESCE(observaciones,'') || ?)
        WHERE id = ?;
        """,
        (mov.get("cc_id"), " | Cobrado en caja", cheque_id),
    )
    conn.commit()
    saldo = _saldo_caja_movimientos(cursor, empresa_id)
    conn.close()
    return {
        "status": "success",
        "message": f"Cheque {nro} cobrado en caja ($ {monto:,.2f}).",
        "movimiento": mov,
        "saldo": saldo,
    }


@app.delete("/api/caja/movimientos/{mov_id}")
def api_caja_eliminar_mov(mov_id: int):
    """Anula un movimiento de caja (y su espejo en CC / vínculo de cheque si aplica)."""
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM caja_movimientos WHERE id = ? AND COALESCE(empresa_id,1) = ?;",
        (mov_id, empresa_id),
    )
    mov = cursor.fetchone()
    if not mov:
        conn.close()
        raise HTTPException(404, "Movimiento de caja no encontrado")
    if mov["cc_id"]:
        cursor.execute("DELETE FROM cuentas_corrientes WHERE id = ?;", (mov["cc_id"],))
    if mov["cc_proveedor_id"]:
        cursor.execute("DELETE FROM cuentas_corrientes WHERE id = ?;", (mov["cc_proveedor_id"],))
    if mov["cheque_id"]:
        cursor.execute(
            """
            UPDATE cartera_cheques
            SET estado = 'En cartera', caja_cc_id = NULL
            WHERE id = ? AND LOWER(TRIM(COALESCE(estado,''))) LIKE '%caja%';
            """,
            (mov["cheque_id"],),
        )
    cursor.execute("DELETE FROM caja_movimientos WHERE id = ?;", (mov_id,))
    conn.commit()
    saldo = _saldo_caja_movimientos(cursor, empresa_id)
    conn.close()
    return {"status": "success", "message": "Movimiento anulado.", "saldo": saldo}


def _normalizar_tasa_pct(valor) -> float:
    """Access guardaba TNA/TEA ×100: 2900 → 29.00%, 375 → 3.75%.
    Si ya viene como % real (< 100), se deja igual. Altas nuevas usan % real."""
    try:
        v = float(valor or 0)
    except (TypeError, ValueError):
        return 0.0
    if abs(v) >= 200:
        return round(v / 100.0, 4)
    return round(v, 4)

def _aplicar_tasas_credito(row: dict) -> dict:
    if not row:
        return row
    row["tna"] = _normalizar_tasa_pct(row.get("tna"))
    row["tea"] = _normalizar_tasa_pct(row.get("tea"))
    return row

@app.get("/api/bancos/creditos")
def listar_creditos(moneda: Optional[str] = None, estado: Optional[str] = "activos"):
    """Lista créditos. Por defecto solo Activos (cuotas pendientes = flujo proyectado)."""
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()

    q = """
        SELECT c.*,
               (SELECT COUNT(*) FROM creditos_cuotas q WHERE q.credito_id = c.id) AS cant_cuotas,
               (SELECT COUNT(*) FROM creditos_cuotas q WHERE q.credito_id = c.id AND UPPER(COALESCE(q.estado,'')) = 'PAGADA') AS cant_pagadas,
               (SELECT COUNT(*) FROM creditos_cuotas q WHERE q.credito_id = c.id AND UPPER(COALESCE(q.estado,'')) != 'PAGADA') AS cant_pendientes
        FROM creditos_prestamos c
        WHERE COALESCE(c.empresa_id, 1) = ?
    """
    params: list = [empresa_id]
    est = (estado or "activos").strip().lower()
    if est in ("activos", "activo", "pendientes"):
        q += """
            AND EXISTS (
                SELECT 1 FROM creditos_cuotas q
                WHERE q.credito_id = c.id AND UPPER(COALESCE(q.estado,'')) != 'PAGADA'
            )
        """
    elif est in ("historicos", "historico", "histórico", "históricos"):
        q += """
            AND NOT EXISTS (
                SELECT 1 FROM creditos_cuotas q
                WHERE q.credito_id = c.id AND UPPER(COALESCE(q.estado,'')) != 'PAGADA'
            )
        """
    if moneda:
        q += " AND UPPER(COALESCE(c.moneda,'ARS')) = ?"
        params.append(moneda.strip().upper())
    q += " ORDER BY COALESCE(c.fecha_vencimiento_proxima, c.fecha_operacion) ASC, c.id DESC;"
    cursor.execute(q, params)
    rows = [_aplicar_tasas_credito(dict(r)) for r in cursor.fetchall()]
    conn.close()
    return rows

@app.get("/api/bancos/creditos/{credito_id}")
def obtener_credito(credito_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM creditos_prestamos WHERE id = ?;", (credito_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Crédito no encontrado.")
    credito = _aplicar_tasas_credito(dict(row))
    cursor.execute(
        "SELECT * FROM creditos_cuotas WHERE credito_id = ? ORDER BY nro_cuota ASC, id ASC;",
        (credito_id,),
    )
    credito["cuotas"] = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return credito

def _sincronizar_resumen_credito(cursor, credito_id: int):
    cursor.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN UPPER(COALESCE(estado,'')) = 'PAGADA' THEN 1 ELSE 0 END) AS pagadas,
               MIN(CASE WHEN UPPER(COALESCE(estado,'Pendiente')) IN ('PENDIENTE','PROYECTADA','') THEN fecha_pago END) AS prox
        FROM creditos_cuotas WHERE credito_id = ?;
        """,
        (credito_id,),
    )
    r = cursor.fetchone()
    total = int(r["total"] or 0)
    pagadas = int(r["pagadas"] or 0)
    prox = r["prox"]
    cursor.execute(
        """
        UPDATE creditos_prestamos
        SET total_cuotas = ?, cuotas_pagadas = ?, fecha_vencimiento_proxima = ?
        WHERE id = ?;
        """,
        (total, pagadas, prox, credito_id),
    )

def _monto_cuota_ars(cuota: CreditoCuotaModel, moneda: str) -> float:
    mon = (moneda or "ARS").upper()
    if mon in ("USD", "U$S", "DOLARES", "DÓLARES"):
        if cuota.total_ars and cuota.total_ars > 0:
            return float(cuota.total_ars)
        tc = float(cuota.tipo_cambio or 0)
        base_usd = float(cuota.capital_usd or 0) + float(cuota.intereses_usd or 0) + float(cuota.cargos_usd or 0)
        if tc > 0 and base_usd > 0:
            return round(base_usd * tc, 2)
        return float(cuota.total_pagar or 0)
    total = float(cuota.total_pagar or 0)
    if total > 0:
        return total
    return round(
        float(cuota.capital or 0) + float(cuota.intereses or 0) + float(cuota.impuestos or 0) + float(cuota.cargos or 0),
        2,
    )

def _marcar_movimiento_como_prestamo(cursor, mov_id: int, credito: dict, cuota: CreditoCuotaModel):
    nro = credito.get("nro_credito") or ""
    cursor.execute(
        """
        UPDATE movimientos_cta_cte_bancos
        SET es_prestamo = 1,
            nro_credito = COALESCE(NULLIF(?, ''), nro_credito),
            nro_cuota = COALESCE(?, nro_cuota),
            id_access = COALESCE(?, id_access),
            tipo_operacion = 'Cuota crédito'
        WHERE id = ?;
        """,
        (nro, cuota.nro_cuota, cuota.id_access, mov_id),
    )

def _buscar_movimiento_existente_cuota(cursor, credito: dict, cuota: CreditoCuotaModel, cuenta_id: int, monto: float):
    """Prioridad: id_access → movimiento_banco_id → match cta/fecha/monto/nro crédito."""
    if cuota.id_access:
        cursor.execute(
            "SELECT id FROM movimientos_cta_cte_bancos WHERE id_access = ? LIMIT 1;",
            (cuota.id_access,),
        )
        row = cursor.fetchone()
        if row:
            return int(row["id"])
    if cuota.movimiento_banco_id:
        cursor.execute(
            "SELECT id FROM movimientos_cta_cte_bancos WHERE id = ? LIMIT 1;",
            (cuota.movimiento_banco_id,),
        )
        row = cursor.fetchone()
        if row:
            return int(row["id"])

    nro = (credito.get("nro_credito") or "").strip()
    fecha = (cuota.fecha_pago or "").strip()
    if cuenta_id and fecha and monto >= 0.01:
        cursor.execute(
            """
            SELECT id FROM movimientos_cta_cte_bancos
            WHERE cuenta_id = ?
              AND TRIM(COALESCE(fecha_cobro,'')) = ?
              AND ROUND(COALESCE(haber,0), 2) = ROUND(?, 2)
              AND (
                    COALESCE(es_prestamo,0) = 1
                 OR TRIM(COALESCE(nro_credito,'')) = ?
                 OR TRIM(COALESCE(tipo_operacion,'')) = 'Cuota crédito'
              )
            ORDER BY CASE WHEN id_access IS NOT NULL THEN 0 ELSE 1 END, id
            LIMIT 1;
            """,
            (cuenta_id, fecha, monto, nro),
        )
        row = cursor.fetchone()
        if row:
            return int(row["id"])
        # match amplio: misma cta/fecha/monto sin exigir flag (legado Access)
        cursor.execute(
            """
            SELECT id FROM movimientos_cta_cte_bancos
            WHERE cuenta_id = ?
              AND TRIM(COALESCE(fecha_cobro,'')) = ?
              AND ROUND(COALESCE(haber,0), 2) = ROUND(?, 2)
            ORDER BY CASE WHEN COALESCE(es_prestamo,0)=1 THEN 0 ELSE 1 END, id
            LIMIT 1;
            """,
            (cuenta_id, fecha, monto),
        )
        row = cursor.fetchone()
        if row:
            return int(row["id"])
    return None

def _proyectar_cuota_en_banco(cursor, credito: dict, cuota_id: int, cuota: CreditoCuotaModel, empresa_id: int) -> Optional[int]:
    """Vincula cuota a movimiento existente (Id Access) o crea uno NUEVO solo si no existe."""
    cuenta_id = cuota.cuenta_id or credito.get("cuenta_id")
    monto = _monto_cuota_ars(cuota, credito.get("moneda") or "ARS")

    mov_id = _buscar_movimiento_existente_cuota(cursor, credito, cuota, cuenta_id, monto if monto else 0.0)
    if mov_id:
        _marcar_movimiento_como_prestamo(cursor, mov_id, credito, cuota)
        cursor.execute(
            "UPDATE creditos_cuotas SET movimiento_banco_id = ?, id_access = COALESCE(?, id_access) WHERE id = ?;",
            (mov_id, cuota.id_access, cuota_id),
        )
        return mov_id

    if not cuota.proyectar_en_banco:
        return None
    if not cuenta_id or monto < 0.01:
        return None

    cursor.execute("SELECT nro_cta_cte, banco FROM ctas_ctes_bancarias WHERE id = ?;", (cuenta_id,))
    cta = cursor.fetchone()
    if not cta:
        return None
    nro = credito.get("nro_credito") or ""
    detalle = f"Cuota {cuota.nro_cuota} crédito {nro} — {credito.get('entidad_financiera') or credito.get('banco') or ''}"
    mon = (credito.get("moneda") or "ARS").upper()
    if mon in ("USD", "U$S", "DOLARES", "DÓLARES"):
        detalle += f" (USD TC {cuota.tipo_cambio or '-'})"
    cursor.execute(
        """
        INSERT INTO movimientos_cta_cte_bancos
        (cuenta_id, fecha_cobro, fecha_debito, proveedor, nro_cheque, haber, debe, imp_chq, cta_cte_nro, conciliado,
         tipo_operacion, es_prestamo, nro_credito, nro_cuota, id_access, generado_por_credito)
        VALUES (?, ?, '', ?, '', ?, 0, 0, ?, 0, 'Cuota crédito', 1, ?, ?, ?, 1);
        """,
        (
            cuenta_id, cuota.fecha_pago, detalle.strip(), monto, cta["nro_cta_cte"],
            nro, cuota.nro_cuota, cuota.id_access,
        ),
    )
    mov_id = cursor.lastrowid
    cursor.execute(
        "UPDATE creditos_cuotas SET movimiento_banco_id = ?, id_access = COALESCE(?, id_access) WHERE id = ?;",
        (mov_id, cuota.id_access, cuota_id),
    )
    return mov_id

def _liberar_o_borrar_movimiento_cuota(cursor, mid: Optional[int]):
    """Solo borra proyecciones generadas por el módulo. Nunca toca movimientos históricos Access."""
    if not mid:
        return
    cursor.execute(
        "SELECT id, COALESCE(generado_por_credito,0) AS gen, id_access FROM movimientos_cta_cte_bancos WHERE id = ?;",
        (int(mid),),
    )
    row = cursor.fetchone()
    if not row:
        return
    if int(row["gen"] or 0) == 1 and row["id_access"] is None:
        try:
            eliminar_cascada_movimiento_banco(cursor, int(mid))
        except Exception:
            cursor.execute("DELETE FROM movimientos_cta_cte_bancos WHERE id = ?;", (mid,))
    else:
        # histórico: solo desmarca vínculo lógico de cuota (conserva movimiento)
        cursor.execute(
            """
            UPDATE movimientos_cta_cte_bancos
            SET es_prestamo = COALESCE(es_prestamo, 1)
            WHERE id = ?;
            """,
            (mid,),
        )

@app.post("/api/bancos/creditos")
def registrar_credito(data: CreditoModel):
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    entidad = (data.entidad_financiera or data.banco or "").strip()
    if not entidad or not data.nro_credito.strip():
        conn.close()
        raise HTTPException(status_code=400, detail="Entidad financiera y Nº de crédito son obligatorios.")
    moneda = (data.moneda or "ARS").strip().upper()
    if moneda in ("DOLARES", "DÓLARES", "U$S", "USD"):
        moneda = "USD"
    else:
        moneda = "ARS"
    origen = (data.origen_detalle or data.descripcion or "").strip()
    tna = _normalizar_tasa_pct(data.tna)
    tea = _normalizar_tasa_pct(data.tea)
    cursor.execute(
        """
        INSERT INTO creditos_prestamos (
            banco, entidad_financiera, nro_credito, origen_detalle, descripcion,
            monto_total, moneda, tna, tea, amortizacion, plazo, fecha_operacion,
            fecha_vencimiento_proxima, cuenta_id, total_cuotas, cuotas_pagadas,
            monto_cuota, observaciones, estado, empresa_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            entidad, entidad, data.nro_credito.strip(), origen, origen,
            data.monto_total, moneda, tna, tea, data.amortizacion or "",
            data.plazo or data.total_cuotas or 0, data.fecha_operacion or "",
            data.fecha_vencimiento_proxima or "", data.cuenta_id,
            data.total_cuotas or data.plazo or 0, data.cuotas_pagadas,
            data.monto_cuota, data.observaciones or "", data.estado or "Activo", empresa_id,
        ),
    )
    nuevo_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {"status": "success", "id": nuevo_id, "message": "Crédito registrado."}

@app.put("/api/bancos/creditos/{credito_id}")
def actualizar_credito(credito_id: int, data: CreditoModel):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM creditos_prestamos WHERE id = ?;", (credito_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Crédito no encontrado.")
    entidad = (data.entidad_financiera or data.banco or "").strip()
    moneda = (data.moneda or "ARS").strip().upper()
    moneda = "USD" if moneda in ("DOLARES", "DÓLARES", "U$S", "USD") else "ARS"
    origen = (data.origen_detalle or data.descripcion or "").strip()
    tna = _normalizar_tasa_pct(data.tna)
    tea = _normalizar_tasa_pct(data.tea)
    cursor.execute(
        """
        UPDATE creditos_prestamos SET
            banco=?, entidad_financiera=?, nro_credito=?, origen_detalle=?, descripcion=?,
            monto_total=?, moneda=?, tna=?, tea=?, amortizacion=?, plazo=?, fecha_operacion=?,
            fecha_vencimiento_proxima=?, cuenta_id=?, total_cuotas=?, cuotas_pagadas=?,
            monto_cuota=?, observaciones=?, estado=?
        WHERE id=?;
        """,
        (
            entidad, entidad, data.nro_credito.strip(), origen, origen,
            data.monto_total, moneda, tna, tea, data.amortizacion or "",
            data.plazo or data.total_cuotas or 0, data.fecha_operacion or "",
            data.fecha_vencimiento_proxima or "", data.cuenta_id,
            data.total_cuotas or data.plazo or 0, data.cuotas_pagadas,
            data.monto_cuota, data.observaciones or "", data.estado or "Activo", credito_id,
        ),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Crédito actualizado."}

@app.delete("/api/bancos/creditos/{credito_id}")
def eliminar_credito(credito_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT movimiento_banco_id FROM creditos_cuotas WHERE credito_id = ?;", (credito_id,))
    movs = [r["movimiento_banco_id"] for r in cursor.fetchall() if r["movimiento_banco_id"]]
    for mid in movs:
        _liberar_o_borrar_movimiento_cuota(cursor, mid)
    cursor.execute("DELETE FROM creditos_cuotas WHERE credito_id = ?;", (credito_id,))
    cursor.execute("DELETE FROM creditos_prestamos WHERE id = ?;", (credito_id,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Crédito y cuotas eliminados (movimientos históricos conservados)."}

@app.get("/api/bancos/creditos/{credito_id}/cuotas")
def listar_cuotas_credito(credito_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM creditos_cuotas WHERE credito_id = ? ORDER BY nro_cuota ASC, id ASC;",
        (credito_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

@app.post("/api/bancos/creditos/{credito_id}/cuotas")
def guardar_cuotas_credito(credito_id: int, data: CreditoCuotasLoteModel):
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM creditos_prestamos WHERE id = ?;", (credito_id,))
    cred = cursor.fetchone()
    if not cred:
        conn.close()
        raise HTTPException(status_code=404, detail="Crédito no encontrado.")
    credito = dict(cred)

    if data.reemplazar:
        cursor.execute("SELECT movimiento_banco_id FROM creditos_cuotas WHERE credito_id = ?;", (credito_id,))
        for r in cursor.fetchall():
            _liberar_o_borrar_movimiento_cuota(cursor, r["movimiento_banco_id"])
        cursor.execute("DELETE FROM creditos_cuotas WHERE credito_id = ?;", (credito_id,))

    ids = []
    vinculados = 0
    creados = 0
    for cuota in data.cuotas:
        mon = (credito.get("moneda") or "ARS").upper()
        total_ars = _monto_cuota_ars(cuota, mon)
        capital_ars = float(cuota.capital_ars or 0)
        intereses_ars = float(cuota.intereses_ars or 0)
        cargos_ars = float(cuota.cargos_ars or 0)
        if mon == "USD" and cuota.tipo_cambio and cuota.tipo_cambio > 0:
            if capital_ars < 0.01:
                capital_ars = round(float(cuota.capital_usd or 0) * float(cuota.tipo_cambio), 2)
            if intereses_ars < 0.01:
                intereses_ars = round(float(cuota.intereses_usd or 0) * float(cuota.tipo_cambio), 2)
            if cargos_ars < 0.01:
                cargos_ars = round(float(cuota.cargos_usd or 0) * float(cuota.tipo_cambio), 2)
        total_pagar = float(cuota.total_pagar or 0)
        if mon == "ARS" and total_pagar < 0.01:
            total_pagar = round(
                float(cuota.capital or 0) + float(cuota.intereses or 0) + float(cuota.impuestos or 0) + float(cuota.cargos or 0),
                2,
            )
        if mon == "USD":
            total_pagar = total_ars

        cursor.execute(
            """
            INSERT INTO creditos_cuotas (
                credito_id, nro_cuota, cuenta_id, fecha_pago,
                capital, intereses, impuestos, cargos, total_pagar,
                capital_usd, intereses_usd, cargos_usd, tipo_cambio,
                capital_ars, intereses_ars, cargos_ars, total_ars,
                nota, estado, movimiento_banco_id, id_access, empresa_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                credito_id, cuota.nro_cuota, cuota.cuenta_id or credito.get("cuenta_id"),
                cuota.fecha_pago,
                cuota.capital, cuota.intereses, cuota.impuestos, cuota.cargos, total_pagar,
                cuota.capital_usd, cuota.intereses_usd, cuota.cargos_usd, cuota.tipo_cambio,
                capital_ars, intereses_ars, cargos_ars, total_ars,
                cuota.nota or "", cuota.estado or "Pendiente",
                cuota.movimiento_banco_id, cuota.id_access, empresa_id,
            ),
        )
        cid = cursor.lastrowid
        ids.append(cid)
        antes = cuota.movimiento_banco_id or cuota.id_access
        mov = _proyectar_cuota_en_banco(cursor, credito, cid, cuota, empresa_id)
        if mov:
            if antes:
                vinculados += 1
            else:
                # si el mov ya existía (match), cuenta como vínculo; si generado_por_credito=1, creado
                cursor.execute(
                    "SELECT COALESCE(generado_por_credito,0) AS gen FROM movimientos_cta_cte_bancos WHERE id=?;",
                    (mov,),
                )
                g = cursor.fetchone()
                if g and int(g["gen"] or 0) == 1:
                    creados += 1
                else:
                    vinculados += 1

    _sincronizar_resumen_credito(cursor, credito_id)
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "cuotas_ids": ids,
        "vinculados_existentes": vinculados,
        "proyecciones_nuevas": creados,
        "message": (
            f"{len(ids)} cuota(s): {vinculados} vinculadas a movimientos existentes "
            f"(sin duplicar), {creados} proyecciones nuevas."
        ),
    }

@app.get("/api/arba/padron/estado")
def api_arba_padron_estado():
    asegurar_carpeta_padrones()
    return estado_padron_arba()


@app.get("/api/arba/padron/detectar")
def api_arba_padron_detectar():
    asegurar_carpeta_padrones()
    return detectar_archivos_padron()


@app.get("/api/arba/padron/job")
def api_arba_padron_job():
    """Estado del job de importación en segundo plano (reloj / progreso)."""
    asegurar_carpeta_padrones()
    return get_import_job_status()


@app.post("/api/arba/padron/importar")
def api_arba_padron_importar(auto: bool = True):
    """Inicia importación en segundo plano de PadronRGS Ret/Per detectados."""
    asegurar_carpeta_padrones()
    job = start_import_job(auto_detect=auto)
    if job.get("status") == "error" and not job.get("accepted"):
        raise HTTPException(status_code=400, detail=job.get("message") or "Error al importar padrón")
    return job


@app.post("/api/arba/padron/upload")
async def api_arba_padron_upload(
    retenciones: Optional[UploadFile] = File(None),
    percepciones: Optional[UploadFile] = File(None),
):
    """Sube Ret y/o Perc (TXT o ZIP) e inicia importación en segundo plano."""
    asegurar_carpeta_padrones()
    ruta_ret = None
    ruta_per = None
    for upload, kind in ((retenciones, "ret"), (percepciones, "per")):
        if not upload or not upload.filename:
            continue
        fname = os.path.basename(upload.filename)
        dest = os.path.join(PADRONES_DIR, fname)
        content = await upload.read()
        with open(dest, "wb") as f:
            f.write(content)
        low = fname.lower()
        if kind == "ret" or "ret" in low:
            ruta_ret = dest
        if kind == "per" or "per" in low or "perc" in low:
            ruta_per = dest
    det = detectar_archivos_padron()
    ruta_ret = ruta_ret or det.get("retenciones")
    ruta_per = ruta_per or det.get("percepciones")
    job = start_import_job(ruta_ret=ruta_ret, ruta_per=ruta_per, auto_detect=False)
    if job.get("status") == "error" and not job.get("accepted"):
        raise HTTPException(status_code=400, detail=job.get("message") or "Error al importar padrón")
    return job


@app.get("/api/arba/consultar/{cuit}")
def consultar_arba(cuit: str):
    """Consulta alícuotas Ret/Perc del padrón ARBA del mes cargado."""
    data = consultar_cuit_padron(cuit)
    # Compat con OP / front existente
    return {
        "cuit": data.get("cuit") or cuit,
        "razon_social": data.get("razon_social") or "",
        "alicuota_percepcion": float(data.get("alicuota_percepcion") or 0),
        "alicuota_retencion": float(data.get("alicuota_retencion") or 0),
        "ret_grupo": data.get("ret_grupo") or "00",
        "perc_grupo": data.get("perc_grupo") or "00",
        "estado": data.get("estado") or "",
        "encontrado": bool(data.get("encontrado")),
        "periodo": data.get("periodo") or {},
    }

@app.get("/api/retenciones/filtrar")
@app.get("/api/retenciones")
@app.get("/api/sicore/consultar")
@app.get("/api/sicore")
def filtrar_retenciones(
    desde: Optional[str] = None, 
    hasta: Optional[str] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None
):
    conn = get_db()
    cursor = conn.cursor()
    
    f_desde = desde or fecha_desde or ""
    f_hasta = hasta or fecha_hasta or ""
    
    # Consulta relacional estricta cruzando con las tablas maestras normativas
    query = """
        SELECT 
            r.id, 
            r.fecha, 
            r.cuit, 
            COALESCE(e.razon_social, r.razon_social) as razon_social,
            r.base_imponible, 
            r.importe_retenido,
            r.regimen, 
            r.nro_comprobante,
            r.tipo_retencion,
            COALESCE(e.domicilio, 'S/D') as domicilio,
            COALESCE(e.localidad, 'Chivilcoy') as localidad,
            COALESCE(e.provincia_codigo, '01') as provincia_codigo,
            COALESCE(e.tipo_documento, '80') as tipo_documento,
            COALESCE(e.condicion_iva_codigo, '01') as condicion_iva_codigo
        FROM retenciones_sicore r
        LEFT JOIN entidades e ON REPLACE(e.cuit, '-', '') = REPLACE(r.cuit, '-', '')
        WHERE UPPER(COALESCE(r.tipo_retencion, 'SICORE')) = 'SICORE'
    """
    params = []
    
    if f_desde and f_hasta:
        query += " AND r.fecha BETWEEN ? AND ?"
        params.extend([f_desde, f_hasta])
        
    query += " ORDER BY r.fecha ASC, r.id ASC;"
    
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

@app.delete("/api/retenciones/{id_retencion}")
def eliminar_retencion_directa(id_retencion: int):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT nro_comprobante FROM retenciones_sicore WHERE id = ?;", (id_retencion,))
    row = cursor.fetchone()
    
    if row:
        nro_comp = row['nro_comprobante']
        cursor.execute("DELETE FROM retenciones_sicore WHERE id = ?;", (id_retencion,))
        if nro_comp:
            cursor.execute("DELETE FROM cuentas_corrientes WHERE numero_comprobante = ?;", (nro_comp,))
            
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Retención eliminada correctamente."}
    
    conn.close()
    raise HTTPException(status_code=404, detail="Retención no encontrada")

# Pagos / retenciones: nunca son "cargo a pagar"
_TIPOS_PAGO = (
    "Pago", "Orden de Pago",
    "Retencion Ganancias", "Ret IIBB", "Retención SICORE",
    "Retención IIBB", "Percep IIBB", "RET GANANCIAS", "RET IVA COMPRAS",
)
# Alias histórico (cargos ambiguos del legado Access se tratan aparte en saldos)
_TIPOS_NO_FACTURA = _TIPOS_PAGO + (
    "Transferencia", "Movimiento", "Cheque", "Caja", "PAGO Terceros",
)

def _explicar_origen_movimiento(cursor, mov: dict) -> dict:
    """Arma de qué operación salió un renglón de cuenta corriente, sin inventar vínculos."""
    tipo = (mov.get("tipo_comprobante") or "").strip()
    nro = (mov.get("numero_comprobante") or "").strip()
    tipo_up = tipo.upper()
    if tipo_up.startswith("AJUSTE"):
        titulo = "Ajuste manual de saldo"
        resumen = "Crédito o débito cargado solo en la cuenta corriente. No tiene asiento contable ni retención."
    elif "RETEN" in tipo_up or tipo_up.startswith("RET ") or "PERCEP" in tipo_up:
        titulo = "Retención o percepción"
        resumen = "Importe retenido o percibido, aplicado al saldo del proveedor."
    elif tipo_up in ("ORDEN DE PAGO", "PAGO", "PAGO TERCEROS", "TRANSFERENCIA", "CAJA", "CHEQUE") or "PAGO" in tipo_up or "CHEQUE" in tipo_up:
        titulo = "Pago"
        resumen = "Salida de dinero o valor que cancela saldo (transferencia, caja, cheque u orden de pago)."
    elif "FACTURA" in tipo_up or "NOTA DE DÉBITO" in tipo_up or "NOTA DE DEBITO" in tipo_up or tipo_up == "MOVIMIENTO":
        titulo = "Cargo del proveedor"
        resumen = "Factura, nota de débito o movimiento importado que aumenta lo que hay que pagar."
    else:
        titulo = tipo or "Movimiento"
        resumen = "Movimiento de cuenta corriente."

    vinculos = []
    if nro:
        try:
            cursor.execute(
                """
                SELECT nro_orden, fecha, total_pagado, retencion_iibb, retencion_sicore,
                       forma_pago, observaciones
                FROM ordenes_pago
                WHERE nro_orden = ?;
                """,
                (nro,),
            )
            op = cursor.fetchone()
            if op:
                vinculos.append({
                    "clase": "Orden de pago",
                    "detalle": (
                        f"{op['nro_orden']} del {op['fecha'] or '—'} · "
                        f"{op['forma_pago'] or 'sin forma de pago'} · "
                        f"pagado {float(op['total_pagado'] or 0):,.2f}"
                    ),
                    "extra": op["observaciones"] or "",
                })
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute(
                """
                SELECT fecha, tipo_retencion, regimen, importe_retenido, nro_comprobante, razon_social
                FROM retenciones_sicore
                WHERE nro_comprobante = ?;
                """,
                (nro,),
            )
            for ret in cursor.fetchall():
                vinculos.append({
                    "clase": f"Certificado {ret['tipo_retencion'] or 'retención'}",
                    "detalle": (
                        f"{ret['nro_comprobante']} · régimen {ret['regimen'] or '—'} · "
                        f"importe {float(ret['importe_retenido'] or 0):,.2f}"
                    ),
                    "extra": ret["razon_social"] or "",
                })
        except sqlite3.OperationalError:
            pass
    try:
        cursor.execute(
            """
            SELECT descripcion, destino_tipo, actividad_nombre, cuenta_nombre,
                   neto, cantidad, unidad, item_nombre, campania_codigo, detalle_tipo
            FROM factura_imputaciones
            WHERE cc_id = ?;
            """,
            (mov.get("id"),),
        )
        for imp in cursor.fetchall():
            partes = [imp["descripcion"] or imp["item_nombre"] or "Renglón"]
            if imp["actividad_nombre"]:
                partes.append(imp["actividad_nombre"])
            if imp["cuenta_nombre"]:
                partes.append(imp["cuenta_nombre"])
            if imp["campania_codigo"]:
                partes.append(f"campaña {imp['campania_codigo']}")
            if float(imp["cantidad"] or 0):
                partes.append(f"{imp['cantidad']} {imp['unidad'] or ''}".strip())
            vinculos.append({
                "clase": "Imputación de factura",
                "detalle": " · ".join(partes),
                "extra": f"Neto {float(imp['neto'] or 0):,.2f}",
            })
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute(
            """
            SELECT nro_cheque, banco, librador, cliente_nombre, monto, estado, observaciones
            FROM cartera_cheques
            WHERE cc_id = ? OR op_id = ?;
            """,
            (mov.get("id"), mov.get("id")),
        )
        for ch in cursor.fetchall():
            vinculos.append({
                "clase": "Cheque",
                "detalle": (
                    f"Nº {ch['nro_cheque'] or '—'} · {ch['banco'] or ''} · "
                    f"{ch['librador'] or ch['cliente_nombre'] or ''} · "
                    f"estado {ch['estado'] or '—'}"
                ).strip(),
                "extra": ch["observaciones"] or "",
            })
    except sqlite3.OperationalError:
        pass

    return {
        "titulo": titulo,
        "resumen": resumen,
        "vinculos": vinculos,
        "movimiento": {
            "id": mov.get("id"),
            "fecha": mov.get("fecha"),
            "tipo_comprobante": tipo,
            "numero_comprobante": nro,
            "forma_pago": mov.get("forma_pago") or "",
            "nro_cheque": mov.get("nro_cheque") or "",
            "neto": float(mov.get("neto") or 0),
            "iva": float(mov.get("iva") or 0),
            "debe": float(mov.get("debe") or 0),
            "haber": float(mov.get("haber") or 0),
            "total": float(mov.get("total") or 0),
            "estado": mov.get("estado") or "",
            "usuario_registro": mov.get("usuario_registro") or "",
            "observaciones": mov.get("observaciones") or "",
            "id_access": mov.get("id_access"),
        },
    }


@app.get("/api/cc/movimiento/{id_mov}")
def origen_movimiento_cc(id_mov: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cuentas_corrientes WHERE id = ?;", (id_mov,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Movimiento no encontrado")
    data = _explicar_origen_movimiento(cursor, dict(row))
    conn.close()
    return data


@app.get("/api/retenciones/certificado")
def api_certificado_retencion(nro: Optional[str] = None, cc_id: Optional[int] = None):
    """Datos para reimprimir un certificado SICORE / IIBB (PDF vía impresión del navegador)."""
    conn = get_db()
    cursor = conn.cursor()
    nro_comp = (nro or "").strip()
    if not nro_comp and cc_id:
        cursor.execute(
            "SELECT numero_comprobante, tipo_comprobante FROM cuentas_corrientes WHERE id = ?;",
            (int(cc_id),),
        )
        mov = cursor.fetchone()
        if not mov:
            conn.close()
            raise HTTPException(status_code=404, detail="Movimiento no encontrado")
        nro_comp = (mov["numero_comprobante"] or "").strip()
    if not nro_comp:
        conn.close()
        raise HTTPException(status_code=400, detail="Indique nro de certificado o cc_id")

    cursor.execute(
        """
        SELECT fecha, razon_social, cuit, base_imponible, regimen, importe_retenido,
               nro_comprobante, tipo_retencion
        FROM retenciones_sicore
        WHERE nro_comprobante = ?
        LIMIT 1;
        """,
        (nro_comp,),
    )
    ret = cursor.fetchone()
    if not ret:
        conn.close()
        raise HTTPException(status_code=404, detail="Certificado no encontrado")

    cursor.execute("SELECT * FROM configuracion_empresa WHERE id = 1;")
    cfg = cursor.fetchone()
    cuit_suj = "".join(ch for ch in str(ret["cuit"] or "") if ch.isdigit())
    cursor.execute(
        """
        SELECT razon_social, nombre_fantasia, cuit, domicilio, localidad, provincia
        FROM entidades WHERE REPLACE(cuit, '-', '') = ? LIMIT 1;
        """,
        (cuit_suj,),
    )
    ent = cursor.fetchone()
    conn.close()

    agente = {
        "razon_social": (cfg["razon_social"] if cfg else None) or "SILO CHICO S.A.",
        "cuit": (cfg["cuit"] if cfg else None) or "30717802868",
        "domicilio": (cfg["domicilio"] if cfg else None) or "",
        "localidad": (cfg["localidad"] if cfg else None) or "",
    }
    sujeto_nombre = (
        (ent["nombre_fantasia"] if ent and ent["nombre_fantasia"] else None)
        or (ent["razon_social"] if ent else None)
        or ret["razon_social"]
        or ""
    )
    base = float(ret["base_imponible"] or 0)
    importe = float(ret["importe_retenido"] or 0)
    regimen = (ret["regimen"] or "").strip()
    # Si el régimen no trae %, inferirla de base/importe (certificados viejos solo decían "ARBA")
    alicuota_pct = None
    import re as _re
    m_pct = _re.search(r"([\d.,]+)\s*%", regimen)
    if m_pct:
        try:
            alicuota_pct = float(m_pct.group(1).replace(",", "."))
        except ValueError:
            alicuota_pct = None
    if alicuota_pct is None and base > 0.01 and importe > 0:
        alicuota_pct = round(importe / base * 100, 4)
    if alicuota_pct is not None and "%" not in regimen:
        regimen = f"{regimen} {alicuota_pct:.2f}%".strip() if regimen else f"{alicuota_pct:.2f}%"

    return {
        "nro_comprobante": ret["nro_comprobante"],
        "tipo_retencion": (ret["tipo_retencion"] or "SICORE").upper(),
        "fecha": ret["fecha"],
        "regimen": regimen,
        "alicuota_pct": alicuota_pct,
        "base_imponible": base,
        "importe_retenido": importe,
        "agente": agente,
        "sujeto": {
            "razon_social": sujeto_nombre,
            "cuit": (ent["cuit"] if ent else None) or ret["cuit"],
            "domicilio": (ent["domicilio"] if ent else None) or "",
            "localidad": (ent["localidad"] if ent else None) or "",
        },
    }


@app.post("/api/cuentas_corrientes/ajuste")
def registrar_ajuste_saldo_cc(data: AjusteSaldoCcModel):
    """Corrige el saldo del proveedor. No genera asiento ni retención."""
    sentido = (data.sentido or "").strip().lower()
    if sentido not in ("credito", "debito"):
        raise HTTPException(status_code=400, detail="El sentido debe ser credito o debito.")
    importe = round(float(data.importe or 0), 2)
    if importe <= 0:
        raise HTTPException(status_code=400, detail="El importe tiene que ser mayor a cero.")
    concepto = (data.concepto or "").strip()
    if not concepto:
        raise HTTPException(status_code=400, detail="Indicá el motivo del ajuste.")
    cuit_clean = "".join(filter(str.isdigit, str(data.cuit)))
    if not cuit_clean:
        raise HTTPException(status_code=400, detail="CUIT inválido.")

    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(cuentas_corrientes);")
    if "observaciones" not in {c[1] for c in cursor.fetchall()}:
        cursor.execute("ALTER TABLE cuentas_corrientes ADD COLUMN observaciones TEXT;")

    debe = importe if sentido == "debito" else 0.0
    haber = importe if sentido == "credito" else 0.0
    tipo = "Ajuste débito" if sentido == "debito" else "Ajuste crédito"
    cursor.execute(
        """
        INSERT INTO cuentas_corrientes (
            entidad_id, tipo_comprobante, numero_comprobante, forma_pago, nro_cheque,
            fecha, vencimiento, neto, iva, debe, haber, total, estado,
            usuario_registro, empresa_id, observaciones
        ) VALUES (?, ?, '', 'Ajuste de saldo', '', ?, ?, 0, 0, ?, ?, ?, 'Ajuste', ?, ?, ?);
        """,
        (
            cuit_clean, tipo, data.fecha, data.fecha,
            debe, haber, importe,
            data.usuario_registro or "Administrador",
            empresa_id, concepto,
        ),
    )
    nuevo_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "id": nuevo_id,
        "message": "Ajuste registrado solo en la cuenta corriente (sin asiento ni retención).",
    }


@app.get("/api/cuentas_corrientes/{cuit}")
def obtener_detalle_cuenta_corriente(cuit: str):
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cuit_clean = "".join(filter(str.isdigit, str(cuit)))
    
    cursor.execute("SELECT * FROM entidades WHERE REPLACE(cuit, '-', '') = ?;", (cuit_clean,))
    entidad_row = cursor.fetchone()
    entidad_dict = dict(entidad_row) if entidad_row else {"cuit": cuit, "razon_social": cuit, "es_cuenta_ajuste": 0}

    cursor.execute("""
        SELECT * FROM cuentas_corrientes 
        WHERE REPLACE(entidad_id, '-', '') = ?
          AND COALESCE(empresa_id, 1) = ?
        ORDER BY fecha ASC, id ASC;
    """, (cuit_clean, empresa_id))
    movs = [dict(r) for r in cursor.fetchall()]

    saldo_acumulado = 0.0
    movs_procesados = []
    for m in movs:
        debe = float(m.get('debe', 0) or 0)
        haber = float(m.get('haber', 0) or 0)
        saldo_acumulado += (debe - haber)
        m_copy = dict(m)
        m_copy['saldo_renglon'] = round(saldo_acumulado, 2)
        movs_procesados.append(m_copy)

    conn.close()
    return {
        "entidad": entidad_dict,
        "saldo_actual": round(saldo_acumulado, 2),
        "movimientos": movs_procesados
    }

@app.delete("/api/cuentas_corrientes/{id_mov}")
def eliminar_movimiento_cuenta_corriente(id_mov: int):
    """Borra un solo renglón de CC.

    OP y retenciones son movimientos independientes: borrar la transferencia/OP
    no elimina las retenciones del mismo día (ni viceversa, salvo el certificado
    vinculado al propio renglón de retención).
    """
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM cuentas_corrientes WHERE id = ?;", (id_mov,))
    mov = cursor.fetchone()

    if mov:
        tipo_comp = (mov["tipo_comprobante"] or "").strip()
        nro_comp = mov["numero_comprobante"]
        asiento_id = None
        try:
            asiento_id = mov["asiento_id"]
        except (KeyError, IndexError, TypeError):
            asiento_id = None

        # Imputaciones de factura/NC
        try:
            cursor.execute("DELETE FROM factura_imputaciones WHERE cc_id = ?;", (id_mov,))
        except Exception:
            pass

        cursor.execute("DELETE FROM cuentas_corrientes WHERE id = ?;", (id_mov,))

        if asiento_id:
            try:
                from motor_contable import eliminar_asiento
                eliminar_asiento(cursor, int(asiento_id))
            except Exception as exc:
                print(f"AVISO al borrar asiento CC: {exc}")

        # Solo si se borra el renglón de retención, limpiar su certificado
        tipo_up = tipo_comp.upper()
        es_retencion = (
            tipo_comp in ("Retención SICORE", "Retención IIBB", "Retencion Ganancias", "Retencion IIBB")
            or "RETEN" in tipo_up
        )
        if es_retencion and nro_comp:
            cursor.execute("DELETE FROM retenciones_sicore WHERE nro_comprobante = ?;", (nro_comp,))

        conn.commit()
        mensaje = "Movimiento eliminado. Las demás líneas de la cuenta quedan intactas."
    else:
        conn.rollback()
        mensaje = "Movimiento no encontrado."

    conn.close()
    return {"status": "success", "message": mensaje}

@app.get("/api/granos")
def obtener_granos():
    """Catálogo de granos (preferir import Access; fallback hardcode)."""
    try:
        conn = get_db()
        cur = conn.cursor()
        eid = get_empresa_activa_id()
        cur.execute(
            "SELECT id, nombre FROM catalogo_granos WHERE empresa_id=? ORDER BY nombre COLLATE NOCASE;",
            (eid,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        if rows:
            return rows
    except Exception:
        pass
    return [
        {"id": 1, "nombre": "Soja"},
        {"id": 2, "nombre": "Maíz"},
        {"id": 3, "nombre": "Trigo"},
        {"id": 4, "nombre": "Girasol"},
        {"id": 5, "nombre": "Cebada"},
    ]

@app.post("/api/facturas")
def guardar_factura(data: FacturaModel):
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    from actividades_imputacion import init_actividades_schema
    init_actividades_schema(cursor)

    tipo_up = (data.tipo_comprobante or "").upper()
    es_nc = (
        "NOTA DE CRÉDITO" in tipo_up
        or "NOTA DE CREDITO" in tipo_up
        or tipo_up.startswith("NC ")
    )
    # Factura / ND → DEBE (cargo). NC → HABER (baja saldo a pagar).
    debe = 0.0 if es_nc else float(data.total or 0)
    haber = float(data.total or 0) if es_nc else 0.0

    cursor.execute("""
        INSERT INTO cuentas_corrientes (
            entidad_id, tipo_comprobante, numero_comprobante, fecha, vencimiento,
            neto, iva, debe, haber, total, estado, usuario_registro, empresa_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pendiente', ?, ?);
    """, (
        data.cuit, data.tipo_comprobante, data.numero_comprobante, data.fecha,
        data.vencimiento, data.neto, data.iva, debe, haber, data.total,
        data.usuario_registro, empresa_id,
    ))
    cc_id = cursor.lastrowid

    for r in (data.renglones or []):
        iva_r = round(float(r.neto or 0) * float(r.alicuota_iva or 0), 2)
        cursor.execute(
            """
            INSERT INTO factura_imputaciones (
                empresa_id, cc_id, descripcion, destino_tipo,
                actividad_id, cuenta_imputacion_id, actividad_nombre, cuenta_nombre,
                neto, alicuota_iva, iva,
                item_almacen_id, item_nombre, unidad, cantidad,
                precio_unitario, precio_unitario_usd, tipo_cambio,
                campania_codigo, detalle_tipo
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
            """,
            (
                empresa_id, cc_id, r.descripcion, r.destino_tipo,
                r.actividad_id, r.cuenta_imputacion_id,
                r.actividad_nombre or "", r.cuenta_nombre or "",
                float(r.neto or 0), float(r.alicuota_iva or 0), iva_r,
                r.item_almacen_id, r.item_nombre or "", r.unidad or "",
                float(r.cantidad or 0), float(r.precio_unitario or 0),
                float(r.precio_unitario_usd or 0), float(r.tipo_cambio or 0),
                r.campania_codigo or "", r.detalle_tipo or "",
            ),
        )
        # Insumo: factura → ingreso almacén; NC → egreso/devolución
        dest = (r.destino_tipo or "").lower()
        if "insumo" in dest and float(r.cantidad or 0) > 0 and float(r.precio_unitario or 0) >= 0:
            try:
                if es_nc:
                    from agro_almacen import egresar_almacen_nc
                    egresar_almacen_nc(
                        cursor,
                        empresa_id=empresa_id,
                        item_id=r.item_almacen_id,
                        nombre=(r.item_nombre or r.descripcion or "Insumo"),
                        fecha=data.fecha,
                        cantidad=float(r.cantidad),
                        precio_unitario_neto=float(r.precio_unitario or 0),
                        proveedor_cuit=data.cuit,
                        nro_comprobante=data.numero_comprobante,
                        observaciones=f"NC {data.tipo_comprobante} {data.numero_comprobante}",
                    )
                else:
                    from agro_almacen import ingresar_almacen
                    ingresar_almacen(
                        cursor,
                        empresa_id=empresa_id,
                        item_id=r.item_almacen_id,
                        tipo="producto",
                        codigo="",
                        nombre=(r.item_nombre or r.descripcion or "Insumo"),
                        categoria=r.cuenta_nombre or "",
                        unidad=r.unidad or "Kg",
                        fecha=data.fecha,
                        cantidad=float(r.cantidad),
                        precio_unitario_neto=float(r.precio_unitario or 0),
                        proveedor_cuit=data.cuit,
                        proveedor_nombre="",
                        nro_comprobante=data.numero_comprobante,
                        observaciones=f"Factura {data.tipo_comprobante} {data.numero_comprobante}",
                    )
            except Exception as exc:
                print(f"AVISO almacén desde factura/NC: {exc}")

    # Asiento contable: factura/ND normal o INVERSO para NC
    asiento_id = None
    try:
        cursor.execute("PRAGMA table_info(cuentas_corrientes);")
        cols_cc = {c[1] for c in cursor.fetchall()}
        if "asiento_id" not in cols_cc:
            cursor.execute("ALTER TABLE cuentas_corrientes ADD COLUMN asiento_id INTEGER;")
        # Discriminar IVA 21 / 10.5 desde renglones si hay
        iva21 = 0.0
        iva105 = 0.0
        for r in (data.renglones or []):
            al = float(r.alicuota_iva or 0)
            neto_r = float(r.neto or 0)
            if abs(al - 0.105) < 0.001:
                iva105 += round(neto_r * al, 2)
            elif al > 0:
                iva21 += round(neto_r * al, 2)
        if iva21 <= 0 and iva105 <= 0:
            iva21 = float(data.iva or 0)

        asiento_id = asiento_para_comprobante_compra(
            cursor,
            fecha=data.fecha,
            tipo_comprobante=data.tipo_comprobante,
            numero_comprobante=data.numero_comprobante,
            cuit_proveedor=data.cuit,
            neto=float(data.neto or 0),
            iva=float(data.iva or 0),
            iva_21=iva21,
            iva_105=iva105,
            percepcion_iibb=float(data.percepcion_iibb or 0),
            total=float(data.total or 0),
            es_nota_credito=es_nc,
            empresa_id=empresa_id,
            origen_id=cc_id,
        )
        if asiento_id:
            cursor.execute(
                "UPDATE cuentas_corrientes SET asiento_id = ? WHERE id = ?;",
                (asiento_id, cc_id),
            )
    except Exception as exc:
        print(f"AVISO asiento factura/NC: {exc}")

    conn.commit()
    conn.close()
    if es_nc:
        msg = "Nota de Crédito registrada: crédito en cta cte (HABER), asiento inverso"
        if asiento_id:
            msg += f" #{asiento_id}"
        msg += " y egreso de almacén si hubo insumos."
    else:
        msg = "Factura registrada."
        if asiento_id:
            msg += f" Asiento #{asiento_id}."
    return {"status": "success", "message": msg, "cc_id": cc_id, "asiento_id": asiento_id, "es_nota_credito": es_nc}

@app.get("/api/saldos_proveedores")
def obtener_saldos_proveedores():
    """Proveedores a pagar (solo es_proveedor=1):
    - Saldo neto > 0, o
    - Cargos Pendientes (Debe) sin cubrir — aunque el neto quede a favor
      (p. ej. quedó un Movimiento/Factura pendiente después de una OP).
    Convención: Factura/ND/cargo → Debe; Pago/OP/NC → Haber.
    """
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    # Excluir solo pagos/retenciones puros; Movimiento/Cheque pueden ser cargos pendientes
    placeholders = ",".join("?" for _ in _TIPOS_PAGO)
    cursor.execute(f"""
        SELECT e.cuit,
               COALESCE(e.nombre_fantasia, e.razon_social) AS proveedor,
               COALESCE(e.centro_costo, '1') AS centro_costo,
               ROUND(COALESCE(SUM(cc.debe - cc.haber), 0.0), 2) AS saldo_neto,
               ROUND(COALESCE(SUM(
                   CASE
                       WHEN COALESCE(cc.debe, 0) > 0.01
                            AND COALESCE(cc.tipo_comprobante, '') NOT IN ({placeholders})
                            AND COALESCE(cc.estado, 'Pendiente') IN ('Pendiente', 'pendiente', '')
                       THEN COALESCE(NULLIF(cc.total, 0), cc.debe, 0)
                       ELSE 0
                   END
               ), 0.0), 2) AS total_pendiente,
               SUM(
                   CASE
                       WHEN COALESCE(cc.debe, 0) > 0.01
                            AND COALESCE(cc.tipo_comprobante, '') NOT IN ({placeholders})
                            AND COALESCE(cc.estado, 'Pendiente') IN ('Pendiente', 'pendiente', '')
                       THEN 1 ELSE 0
                   END
               ) AS cant_facturas,
               SUM(
                   CASE
                       WHEN COALESCE(cc.haber, 0) > 0.01
                            OR COALESCE(cc.tipo_comprobante, '') IN ('Pago', 'Orden de Pago')
                       THEN 1 ELSE 0
                   END
               ) AS cant_pagos,
               MAX(
                   CASE
                       WHEN COALESCE(cc.debe, 0) > 0.01
                            AND COALESCE(cc.tipo_comprobante, '') NOT IN ({placeholders})
                            AND COALESCE(cc.estado, 'Pendiente') IN ('Pendiente', 'pendiente', '')
                       THEN cc.fecha
                   END
               ) AS ultima_pendiente,
               MAX(
                   CASE
                       WHEN COALESCE(cc.haber, 0) > 0.01
                            OR COALESCE(cc.tipo_comprobante, '') IN ('Pago', 'Orden de Pago')
                       THEN cc.fecha
                   END
               ) AS ultima_pago,
               COALESCE(MAX(cc.fecha), '-') AS ultima_factura
        FROM entidades e
        INNER JOIN cuentas_corrientes cc
            ON REPLACE(cc.entidad_id, '-', '') = REPLACE(e.cuit, '-', '')
           AND COALESCE(cc.empresa_id, 1) = ?
        WHERE COALESCE(e.es_cuenta_ajuste, 0) = 0
          AND COALESCE(e.es_cuenta_bancaria, 0) = 0
          AND COALESCE(e.es_proveedor, 0) = 1
        GROUP BY e.cuit, proveedor, centro_costo
        HAVING ROUND(saldo_neto, 2) >= 0.01
        ORDER BY proveedor ASC;
    """, (*_TIPOS_PAGO, *_TIPOS_PAGO, *_TIPOS_PAGO, empresa_id))
    rows = []
    for r in cursor.fetchall():
        d = dict(r)
        neto = float(d.get("saldo_neto") or 0)
        pend = float(d.get("total_pendiente") or 0)
        # Si el neto es a pagar, usar neto; si solo quedan pendientes sueltos, mostrar esos
        if neto >= 0.01:
            d["saldo_a_pagar"] = round(neto, 2)
        else:
            d["saldo_a_pagar"] = round(pend, 2)
        if d["saldo_a_pagar"] < 0.01:
            continue
        d["ultima_factura"] = d.get("ultima_pendiente") or d.get("ultima_factura") or "-"
        rows.append(d)
    conn.close()
    return rows


@app.get("/api/saldos_clientes")
def obtener_saldos_clientes():
    """Clientes a cobrar (solo es_cliente=1).
    Convención: venta/cargo → Debe; cobro/recibo → Haber.
    Saldo a cobrar = Debe − Haber > 0 (el cliente nos debe).
    """
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT e.cuit,
               COALESCE(e.nombre_fantasia, e.razon_social) AS cliente,
               COALESCE(e.centro_costo, '1') AS centro_costo,
               ROUND(COALESCE(SUM(cc.debe - cc.haber), 0.0), 2) AS saldo_neto,
               COUNT(cc.id) AS cant_movimientos,
               COALESCE(MAX(cc.fecha), '-') AS ultima_operacion
        FROM entidades e
        INNER JOIN cuentas_corrientes cc
            ON REPLACE(cc.entidad_id, '-', '') = REPLACE(e.cuit, '-', '')
           AND COALESCE(cc.empresa_id, 1) = ?
        WHERE COALESCE(e.es_cuenta_ajuste, 0) = 0
          AND COALESCE(e.es_cuenta_bancaria, 0) = 0
          AND COALESCE(e.es_cliente, 0) = 1
        GROUP BY e.cuit, cliente, centro_costo
        HAVING ROUND(saldo_neto, 2) >= 0.01
        ORDER BY cliente COLLATE NOCASE ASC;
        """,
        (empresa_id,),
    )
    rows = []
    for r in cursor.fetchall():
        d = dict(r)
        saldo = round(float(d.get("saldo_neto") or 0), 2)
        if saldo < 0.01:
            continue
        d["saldo_a_cobrar"] = saldo
        d["ultima_factura"] = d.get("ultima_operacion") or "-"
        rows.append(d)
    conn.close()
    return rows


class CobroClienteModel(BaseModel):
    cuit: str
    fecha: str
    monto: float
    forma_pago: str = "Transferencia"
    observaciones: Optional[str] = ""
    usuario_registro: str = "Administrador"
    nro_recibo: Optional[str] = ""


@app.post("/api/cobros")
def registrar_cobro_cliente(data: CobroClienteModel):
    """Registra un cobro a cliente (Recibo): HABER en cta cte. No usa órdenes de pago."""
    empresa_id = get_empresa_activa_id()
    cuit_clean = "".join(ch for ch in str(data.cuit or "") if ch.isdigit())
    monto = round(float(data.monto or 0), 2)
    if not cuit_clean or monto <= 0:
        raise HTTPException(400, "CUIT y monto son obligatorios.")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT cuit, COALESCE(nombre_fantasia, razon_social) AS nombre,
               COALESCE(es_cliente, 0) AS es_cliente
        FROM entidades
        WHERE REPLACE(cuit, '-', '') = ?
        LIMIT 1;
        """,
        (cuit_clean,),
    )
    ent = cursor.fetchone()
    if not ent:
        conn.close()
        raise HTTPException(404, "Cliente no encontrado en el padrón.")
    if int(ent["es_cliente"] or 0) != 1:
        conn.close()
        raise HTTPException(
            400,
            "La entidad no está marcada como Cliente. Marcála en Proveedores/Clientes antes de cobrar.",
        )

    nro = (data.nro_recibo or "").strip()
    if not nro:
        cursor.execute(
            """
            SELECT COUNT(*) FROM cuentas_corrientes
            WHERE COALESCE(empresa_id, 1) = ?
              AND UPPER(COALESCE(tipo_comprobante, '')) LIKE '%RECIBO%';
            """,
            (empresa_id,),
        )
        n = int(cursor.fetchone()[0] or 0) + 1
        nro = f"RC-{n:08d}"

    fecha = (data.fecha or "")[:10]
    obs = (data.observaciones or "").strip()
    cursor.execute(
        """
        INSERT INTO cuentas_corrientes (
            entidad_id, tipo_comprobante, numero_comprobante, forma_pago, nro_cheque,
            fecha, vencimiento, neto, iva, debe, haber, total, estado, usuario_registro, empresa_id,
            observaciones
        ) VALUES (?, 'Recibo', ?, ?, '', ?, ?, ?, 0, 0, ?, ?, 'Cobrado', ?, ?, ?);
        """,
        (
            ent["cuit"], nro, data.forma_pago or "Transferencia",
            fecha, fecha, monto, monto, monto,
            data.usuario_registro or "Administrador", empresa_id,
            obs or f"Cobro cliente {ent['nombre']}",
        ),
    )
    cc_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "message": f"Cobro registrado (Recibo {nro}).",
        "cc_id": cc_id,
        "nro_recibo": nro,
        "saldo_impactado_haber": monto,
    }


class FacturaVentaModel(BaseModel):
    cuit: str
    tipo_comprobante: str = "Factura A"
    numero_comprobante: str
    fecha: str
    vencimiento: Optional[str] = None
    neto: float
    iva: float = 0.0
    alicuota_percepcion: float = 0.0  # ej. 1.75 = 1.75%
    percepcion_iibb: float = 0.0
    total: float = 0.0
    usuario_registro: str = "Administrador"
    observaciones: Optional[str] = ""


@app.post("/api/facturas_venta")
def emitir_factura_venta(data: FacturaVentaModel):
    """Factura de venta a cliente + percepción IIBB (si la empresa es agente)."""
    empresa_id = get_empresa_activa_id()
    cuit_clean = "".join(ch for ch in str(data.cuit or "") if ch.isdigit())
    neto = round(float(data.neto or 0), 2)
    iva = round(float(data.iva or 0), 2)
    perc = round(float(data.percepcion_iibb or 0), 2)
    alic = float(data.alicuota_percepcion or 0)
    if alic > 1:  # vino como porcentaje 1.75
        alic = alic / 100.0
    total = round(float(data.total or 0), 2)
    if total <= 0:
        total = round(neto + iva + perc, 2)
    if not cuit_clean or neto <= 0 or total <= 0:
        raise HTTPException(400, "CUIT, neto y total son obligatorios.")
    nro = (data.numero_comprobante or "").strip()
    if not nro:
        raise HTTPException(400, "Indicá el número de comprobante.")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT cuit, razon_social, nombre_fantasia, domicilio, localidad,
               COALESCE(es_cliente, 0) AS es_cliente, COALESCE(centro_costo, '1') AS centro_costo
        FROM entidades WHERE REPLACE(cuit, '-', '') = ? LIMIT 1;
        """,
        (cuit_clean,),
    )
    ent = cursor.fetchone()
    if not ent:
        conn.close()
        raise HTTPException(404, "Cliente no encontrado. Dale de alta en el padrón como Cliente.")
    if int(ent["es_cliente"] or 0) != 1:
        conn.close()
        raise HTTPException(400, "La entidad no está marcada como Cliente.")

    cursor.execute("SELECT * FROM configuracion_empresa WHERE id = 1;")
    cfg = cursor.fetchone()
    es_agente_perc = True
    if cfg is not None:
        es_agente_perc = bool(int(cfg["agente_percepcion_iibb"] or 0))
    if not es_agente_perc:
        perc = 0.0
        total = round(neto + iva, 2)

    # Cliente S/P: no percepción
    cc = str(ent["centro_costo"] or "1").upper()
    nom = f"{ent['razon_social'] or ''} {ent['nombre_fantasia'] or ''}".upper()
    if cc in ("SP", "S/P") or "(S/P)" in nom or nom.endswith(" S/P"):
        perc = 0.0
        total = round(neto + iva, 2)

    fecha = (data.fecha or "")[:10]
    venc = (data.vencimiento or fecha)[:10]
    tipo = (data.tipo_comprobante or "Factura A").strip()

    cursor.execute(
        """
        INSERT INTO cuentas_corrientes (
            entidad_id, tipo_comprobante, numero_comprobante, fecha, vencimiento,
            neto, iva, debe, haber, total, estado, usuario_registro, empresa_id, observaciones
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'Pendiente', ?, ?, ?);
        """,
        (
            ent["cuit"], tipo, nro, fecha, venc,
            neto, iva, total, total,
            data.usuario_registro or "Administrador", empresa_id,
            (data.observaciones or "").strip() or f"Venta {tipo} {nro}",
        ),
    )
    cc_id = cursor.lastrowid

    asiento_id = None
    try:
        asiento_id = asiento_para_factura_venta(
            cursor,
            fecha=fecha,
            tipo_comprobante=tipo,
            numero_comprobante=nro,
            cuit_cliente=ent["cuit"],
            neto=neto,
            iva=iva,
            percepcion_iibb=perc,
            total=total,
            empresa_id=empresa_id,
            origen_id=cc_id,
        )
        if asiento_id:
            try:
                cursor.execute("UPDATE cuentas_corrientes SET asiento_id = ? WHERE id = ?;", (asiento_id, cc_id))
            except Exception:
                pass
    except Exception as exc:
        print(f"AVISO asiento venta: {exc}")

    cert_perc = None
    if perc > 0.01:
        anio = fecha[:4] if len(fecha) >= 4 else "2026"
        sec = obtener_siguiente_certificado_db("PERC_IIBB", 1000) + 1
        cert_perc = f"{anio}-P{sec}"
        razon = ent["razon_social"] or ent["nombre_fantasia"] or "Cliente"
        cursor.execute(
            """
            INSERT INTO retenciones_sicore
                (fecha, razon_social, cuit, base_imponible, regimen, importe_retenido, nro_comprobante, tipo_retencion)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'PERCEPCION_IIBB');
            """,
            (fecha, razon, ent["cuit"], neto, f"ARBA-PERC {(alic * 100):.2f}%", perc, cert_perc),
        )
        # Renglón informativo en CC (opcional — la perc ya está en el total de la factura).
        # No duplicamos haber/debe: solo el certificado.

    conn.commit()
    conn.close()
    return {
        "status": "success",
        "message": "Factura de venta registrada.",
        "cc_id": cc_id,
        "asiento_id": asiento_id,
        "total": total,
        "percepcion_iibb": perc,
        "alicuota_percepcion": alic,
        "cert_percepcion": cert_perc,
        "cliente": {
            "razon_social": ent["nombre_fantasia"] or ent["razon_social"],
            "cuit": ent["cuit"],
            "domicilio": ent["domicilio"] or "",
            "localidad": ent["localidad"] or "",
        },
    }


@app.get("/api/proveedores/consulta")
def consultar_padron_proveedores(q: str = ""):
    """Busca en todo el padrón de proveedores (aunque no tengan saldo a pagar)
    para poder revisar la cuenta corriente histórica.
    """
    empresa_id = get_empresa_activa_id()
    texto = (q or "").strip()
    if len(texto) < 2:
        return []
    conn = get_db()
    cursor = conn.cursor()
    like = f"%{texto}%"
    digitos = "".join(ch for ch in texto if ch.isdigit())
    cursor.execute(
        """
        SELECT e.cuit,
               COALESCE(e.nombre_fantasia, e.razon_social) AS proveedor,
               COALESCE(e.centro_costo, '1') AS centro_costo,
               ROUND(COALESCE(SUM(cc.debe - cc.haber), 0.0), 2) AS saldo_neto,
               COALESCE(MAX(cc.fecha), '-') AS ultima_factura,
               COUNT(cc.id) AS cant_movimientos
        FROM entidades e
        LEFT JOIN cuentas_corrientes cc
            ON REPLACE(cc.entidad_id, '-', '') = REPLACE(e.cuit, '-', '')
           AND COALESCE(cc.empresa_id, 1) = ?
        WHERE COALESCE(e.es_cuenta_ajuste, 0) = 0
          AND COALESCE(e.es_cuenta_bancaria, 0) = 0
          AND COALESCE(e.es_proveedor, 0) = 1
          AND (
                COALESCE(e.razon_social, '') LIKE ?
                OR COALESCE(e.nombre_fantasia, '') LIKE ?
                OR REPLACE(COALESCE(e.cuit, ''), '-', '') LIKE ?
          )
        GROUP BY e.cuit, proveedor, centro_costo
        ORDER BY proveedor ASC
        LIMIT 80;
        """,
        (empresa_id, like, like, f"%{digitos}%" if digitos else like),
    )
    rows = []
    for r in cursor.fetchall():
        d = dict(r)
        saldo = float(d.get("saldo_neto") or 0)
        d["saldo_a_pagar"] = round(saldo, 2)
        d["cant_facturas"] = int(d.get("cant_movimientos") or 0)
        d["desde_padron"] = True
        rows.append(d)
    conn.close()
    return rows


@app.get("/api/clientes/consulta")
def consultar_padron_clientes(q: str = ""):
    """Busca clientes (es_cliente=1) en el padrón, con o sin saldo."""
    empresa_id = get_empresa_activa_id()
    texto = (q or "").strip()
    if len(texto) < 2:
        return []
    conn = get_db()
    cursor = conn.cursor()
    like = f"%{texto}%"
    digitos = "".join(ch for ch in texto if ch.isdigit())
    cursor.execute(
        """
        SELECT e.cuit,
               COALESCE(e.nombre_fantasia, e.razon_social) AS cliente,
               COALESCE(e.centro_costo, '1') AS centro_costo,
               ROUND(COALESCE(SUM(cc.debe - cc.haber), 0.0), 2) AS saldo_neto,
               COALESCE(MAX(cc.fecha), '-') AS ultima_operacion,
               COUNT(cc.id) AS cant_movimientos
        FROM entidades e
        LEFT JOIN cuentas_corrientes cc
            ON REPLACE(cc.entidad_id, '-', '') = REPLACE(e.cuit, '-', '')
           AND COALESCE(cc.empresa_id, 1) = ?
        WHERE COALESCE(e.es_cuenta_ajuste, 0) = 0
          AND COALESCE(e.es_cuenta_bancaria, 0) = 0
          AND COALESCE(e.es_cliente, 0) = 1
          AND (
                COALESCE(e.razon_social, '') LIKE ?
                OR COALESCE(e.nombre_fantasia, '') LIKE ?
                OR REPLACE(COALESCE(e.cuit, ''), '-', '') LIKE ?
          )
        GROUP BY e.cuit, cliente, centro_costo
        ORDER BY cliente COLLATE NOCASE
        LIMIT 40;
        """,
        (empresa_id, like, like, f"%{digitos}%" if digitos else like),
    )
    rows = []
    for r in cursor.fetchall():
        d = dict(r)
        saldo = round(float(d.get("saldo_neto") or 0), 2)
        d["saldo_a_cobrar"] = saldo if saldo > 0 else 0.0
        d["desde_padron"] = True
        rows.append(d)
    conn.close()
    return rows


@app.get("/api/comprobantes_pendientes/{cuit}")
def obtener_comprobantes_pendientes(cuit: str):
    """Facturas / ND / cargos pendientes de un proveedor para armar la OP.

    Solo incluye estado Pendiente (o vacío). Nunca reinyecta facturas ya Pagadas:
    eso inflaba el neto de la OP con histórico cancelado.
    """
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cuit_clean = "".join(filter(str.isdigit, str(cuit)))
    placeholders = ",".join("?" for _ in _TIPOS_PAGO)
    cursor.execute(f"""
        SELECT id,
               fecha,
               tipo_comprobante,
               numero_comprobante AS nro_comprobante,
               COALESCE(neto, 0) AS neto,
               COALESCE(iva, 0) AS iva,
               COALESCE(NULLIF(total, 0), debe, 0) AS total,
               estado
        FROM cuentas_corrientes
        WHERE REPLACE(entidad_id, '-', '') = ?
          AND COALESCE(empresa_id, 1) = ?
          AND COALESCE(debe, 0) > 0.01
          AND COALESCE(tipo_comprobante, '') NOT IN ({placeholders})
          AND UPPER(TRIM(COALESCE(estado, 'Pendiente'))) IN ('PENDIENTE', '')
        ORDER BY fecha ASC, id ASC;
    """, (cuit_clean, empresa_id, *_TIPOS_PAGO))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def _saldo_cuenta_bancaria(cursor, cuenta_id: int, cta_nro: str, hasta: str) -> float:
    """Saldo a la vista = Σ(debe − haber − imp_chq) hasta la fecha."""
    cta = (cta_nro or "").strip()
    hasta = (hasta or "9999-12-31")[:10]
    if cta:
        cursor.execute(
            """
            SELECT COALESCE(SUM(
                COALESCE(debe,0) - COALESCE(haber,0) - COALESCE(imp_chq,0)
            ), 0) AS saldo
            FROM movimientos_cta_cte_bancos
            WHERE TRIM(COALESCE(cta_cte_nro,'')) = ?
              AND COALESCE(fecha_cobro,'') <= ?;
            """,
            (cta, hasta),
        )
    else:
        cursor.execute(
            """
            SELECT COALESCE(SUM(
                COALESCE(debe,0) - COALESCE(haber,0) - COALESCE(imp_chq,0)
            ), 0) AS saldo
            FROM movimientos_cta_cte_bancos
            WHERE cuenta_id = ?
              AND COALESCE(fecha_cobro,'') <= ?;
            """,
            (cuenta_id, hasta),
        )
    row = cursor.fetchone()
    return float(row["saldo"] or 0) if row else 0.0


def _es_cuenta_ars(nro: str, banco: str = "") -> bool:
    s = f"{nro or ''} {banco or ''}".upper()
    if any(x in s for x in ("USD", "U$S", "US$", "DOLAR", "DÓLAR")):
        return False
    return True


def _saldo_caja_efectivo(cursor, empresa_id: int, hasta: str) -> float:
    """Saldo de caja chica.
    Preferencia: tabla caja_movimientos (módulo Tesorería → Caja).
    Si aún no hay movimientos, cae al saldo histórico de la entidad CAJA en cta cte.
    """
    hasta = (hasta or "9999-12-31")[:10]
    cursor.execute(
        """
        SELECT COUNT(*) AS n,
               COALESCE(SUM(CASE WHEN LOWER(tipo)='ingreso' THEN monto
                                 WHEN LOWER(tipo)='egreso' THEN -monto
                                 ELSE 0 END), 0) AS saldo
        FROM caja_movimientos
        WHERE COALESCE(empresa_id, 1) = ?
          AND COALESCE(fecha, '') <= ?;
        """,
        (empresa_id, hasta),
    )
    row = cursor.fetchone()
    if row and int(row["n"] or 0) > 0:
        return float(row["saldo"] or 0)

    cursor.execute(
        """
        SELECT COALESCE(SUM(cc.debe - cc.haber), 0) AS saldo
        FROM cuentas_corrientes cc
        JOIN entidades e ON REPLACE(e.cuit,'-','') = REPLACE(cc.entidad_id,'-','')
        WHERE COALESCE(cc.empresa_id, 1) = ?
          AND (
                UPPER(TRIM(COALESCE(e.razon_social,''))) IN ('CAJA', 'CAJA EFECTIVO', 'EFECTIVO')
             OR UPPER(TRIM(COALESCE(e.nombre_fantasia,''))) IN ('CAJA', 'CAJA EFECTIVO', 'EFECTIVO')
          )
          AND COALESCE(cc.fecha, '') <= ?;
        """,
        (empresa_id, hasta),
    )
    row = cursor.fetchone()
    return float(row["saldo"] or 0) if row else 0.0


def _posicion_plazos_fijos(cursor, hasta: str) -> float:
    """Plazos fijos activos ≈ colocaciones − rescates hasta la fecha."""
    cursor.execute(
        """
        SELECT
          COALESCE(SUM(CASE
            WHEN lower(COALESCE(proveedor,'')) LIKE '%plazo fijo%'
              OR lower(COALESCE(proveedor,'')) LIKE '%plazo%fijo%'
              OR lower(COALESCE(tipo_operacion,'')) LIKE '%plazo fijo%'
            THEN COALESCE(haber, 0) ELSE 0 END), 0) AS colocaciones,
          COALESCE(SUM(CASE
            WHEN (lower(COALESCE(proveedor,'')) LIKE '%rescate%plazo%'
              OR lower(COALESCE(proveedor,'')) LIKE '%plazo%rescate%'
              OR lower(COALESCE(proveedor,'')) LIKE '%vto plazo%')
            THEN COALESCE(debe, 0) ELSE 0 END), 0) AS rescates
        FROM movimientos_cta_cte_bancos
        WHERE COALESCE(fecha_cobro,'') <= ?;
        """,
        ((hasta or "9999-12-31")[:10],),
    )
    row = cursor.fetchone()
    if not row:
        return 0.0
    return max(0.0, float(row["colocaciones"] or 0) - float(row["rescates"] or 0))


def _lineas_disponibilidades_financiero(cursor, empresa_id: int, fecha: str) -> List[dict]:
    """
    Disponibilidades a fecha actual (desde): saldos a la vista (ARS), caja, FCI y plazos fijos.
    Se muestran como montos negativos (restan a las deudas del período).
    """
    fecha = (fecha or datetime.now().strftime("%Y-%m-%d"))[:10]
    out: List[dict] = []

    def _add_disp(detalle, monto, cta="", varios=""):
        monto = round(float(monto or 0), 2)
        if abs(monto) < 0.01:
            return
        sub = -abs(monto)
        out.append({
            "fecha": fecha,
            "detalle": detalle,
            "forma_pago": "Disponibilidad",
            "cta_cte": cta,
            "nro_cuota": "",
            "tipo_cambio": 0,
            "plazo": "",
            "capital": 0,
            "intereses": 0,
            "impuestos": 0,
            "cargos": 0,
            "subtotal": sub,
            "monto_ars": sub,
            "monto_usd": 0,
            "varios": varios or "Saldo a la vista / liquidez",
            "origen": "Disponibilidad",
            "fuente": "disponibilidad",
            "moneda": "ARS",
            "es_disponibilidad": True,
        })

    cursor.execute(
        """
        SELECT id, banco, nro_cta_cte
        FROM ctas_ctes_bancarias
        WHERE COALESCE(empresa_id, 1) = ?
          AND COALESCE(baja, 0) = 0
          AND COALESCE(es_cuenta_bancaria, 1) = 1
        ORDER BY banco, nro_cta_cte;
        """,
        (empresa_id,),
    )
    for r in cursor.fetchall():
        d = dict(r)
        nro = (d.get("nro_cta_cte") or "").strip()
        banco = (d.get("banco") or "Banco").strip()
        if not _es_cuenta_ars(nro, banco):
            continue
        saldo = _saldo_cuenta_bancaria(cursor, int(d["id"]), nro, fecha)
        if saldo > 0.01:
            _add_disp(f"Saldo {banco}", saldo, nro, "Cta cte a la vista")

    caja = _saldo_caja_efectivo(cursor, empresa_id, fecha)
    if caja > 0.01:
        _add_disp("CAJA - Efectivo", caja, "", "Efectivo en caja")

    fci = _posicion_fci(cursor, fecha=fecha)
    fci_activo = float(fci.get("activo") or 0)
    if fci_activo > 0.01:
        _add_disp("FCI (suscripciones activas)", fci_activo, "", "Inversión FCI · disponibilidad")

    pf = _posicion_plazos_fijos(cursor, fecha)
    if pf > 0.01:
        _add_disp("Plazos fijos", pf, "", "Colocaciones a plazo")

    cursor.execute(
        """
        SELECT COALESCE(SUM(monto), 0) AS total, COUNT(*) AS n
        FROM cartera_cheques
        WHERE COALESCE(empresa_id, 1) = ?
          AND LOWER(TRIM(COALESCE(estado,''))) IN ('en cartera', 'en carteras')
          AND UPPER(TRIM(COALESCE(tipo,''))) NOT IN ('EMITIDO');
        """,
        (empresa_id,),
    )
    row_ch = cursor.fetchone()
    cheques = float(row_ch["total"] or 0) if row_ch else 0.0
    if cheques > 0.01:
        n = int(row_ch["n"] or 0) if row_ch else 0
        _add_disp("Cheques en cartera", cheques, "", f"{n} cheque(s) aún disponibles")

    return out



def _fecha_iso_ff(fecha: Optional[str]) -> str:
    """Normaliza fecha a YYYY-MM-DD (acepta ISO o DD/MM/AAAA)."""
    return _fecha_iso_contable((fecha or "").strip())


def _lineas_facturas_cc_financiero(cursor, empresa_id: int, desde: str, hasta: str) -> List[dict]:
    """
    Facturas/cargos pendientes en cta cte (Campo+), por vencimiento.
    No filtra fechas en SQL: muchas filas legacy/nuevas vienen en DD/MM/AAAA
    y fallan un BETWEEN contra ISO.
    """
    placeholders = ",".join("?" for _ in _TIPOS_NO_FACTURA)
    cursor.execute(
        f"""
        SELECT cc.vencimiento, cc.fecha, cc.tipo_comprobante, cc.numero_comprobante,
               COALESCE(NULLIF(cc.total,0), cc.debe, 0) AS monto,
               cc.entidad_id,
               COALESCE(e.nombre_fantasia, e.razon_social, cc.entidad_id) AS proveedor
        FROM cuentas_corrientes cc
        LEFT JOIN entidades e
          ON REPLACE(e.cuit,'-','') = REPLACE(cc.entidad_id,'-','')
        WHERE COALESCE(cc.empresa_id, 1) = ?
          AND COALESCE(cc.debe, 0) > 0.01
          AND COALESCE(cc.tipo_comprobante, '') NOT IN ({placeholders})
          AND COALESCE(cc.estado, 'Pendiente') IN ('Pendiente', 'pendiente', '')
          AND COALESCE(e.es_cuenta_bancaria, 0) = 0
          AND COALESCE(e.es_cuenta_ajuste, 0) = 0
        ORDER BY cc.id;
        """,
        (empresa_id, *_TIPOS_NO_FACTURA),
    )
    out: List[dict] = []
    for r in cursor.fetchall():
        d = dict(r)
        fecha = _fecha_iso_ff(d.get("vencimiento") or d.get("fecha") or "")
        if not fecha or fecha < desde or fecha > hasta:
            continue
        monto = round(float(d.get("monto") or 0), 2)
        if monto < 0.01:
            continue
        tipo = d.get("tipo_comprobante") or "Comprobante"
        nro = d.get("numero_comprobante") or ""
        prov = (d.get("proveedor") or d.get("entidad_id") or "").strip()
        out.append({
            "fecha": fecha,
            "detalle": prov,
            "forma_pago": "Cta Cte",
            "cta_cte": "",
            "nro_cuota": "",
            "tipo_cambio": 0,
            "plazo": "30 días",
            "capital": monto,
            "intereses": 0,
            "impuestos": 0,
            "cargos": 0,
            "subtotal": monto,
            "monto_ars": monto,
            "monto_usd": 0,
            "varios": f"{tipo} {nro}".strip(),
            "origen": "Proveedor a pagar",
            "fuente": "factura",
            "moneda": "ARS",
            "es_disponibilidad": False,
        })
    return out


def _merge_facturas_cc(lineas: List[dict], facturas: List[dict]) -> List[dict]:
    """Agrega facturas CC evitando duplicar mismo proveedor+fecha+importe."""
    existing = {
        (
            (L.get("detalle") or "").strip().lower(),
            (L.get("fecha") or "")[:10],
            round(float(L.get("subtotal") or L.get("monto_ars") or 0), 2),
        )
        for L in lineas
        if not L.get("es_disponibilidad") and not L.get("es_cierre_mes")
    }
    for L in facturas:
        key = (
            (L.get("detalle") or "").strip().lower(),
            (L.get("fecha") or "")[:10],
            round(float(L.get("subtotal") or 0), 2),
        )
        if key in existing:
            continue
        lineas.append(L)
        existing.add(key)
    return lineas


def _lineas_cheques_emitidos_financiero(
    cursor, empresa_id: int, desde: str, hasta: str, tc: float = 0.0
) -> List[dict]:
    """
    Cheques emitidos pendientes: el financiero usa fecha_pago (= vencimiento / pago del cheque),
    no la fecha de emisión.
    """
    cursor.execute(
        """
        SELECT nro_cheque, banco, fecha_pago, fecha_emision, monto, moneda,
               COALESCE(librador, titular, '') AS quien, observaciones
        FROM cartera_cheques
        WHERE COALESCE(empresa_id, 1) = ?
          AND (
                UPPER(COALESCE(tipo,'')) LIKE '%EMIT%'
             OR UPPER(COALESCE(estado,'')) LIKE '%EMIT%'
          )
          AND UPPER(COALESCE(estado,'')) NOT IN (
                'DEBITADO', 'ANULADO', 'COBRADO', 'ENTREGADO PROVEEDOR', 'ENTREGADO'
          )
        ORDER BY id;
        """,
        (empresa_id,),
    )
    out: List[dict] = []
    for r in cursor.fetchall():
        d = dict(r)
        # Prioridad: vencimiento/pago del cheque (NO emisión)
        fecha = _fecha_iso_ff(d.get("fecha_pago") or "") or _fecha_iso_ff(d.get("fecha_emision") or "")
        if not fecha or fecha < desde or fecha > hasta:
            continue
        mon = float(d.get("monto") or 0)
        if abs(mon) < 0.01:
            continue
        moneda = (d.get("moneda") or "ARS").upper()
        det = f"{d.get('quien') or d.get('banco') or 'Cheque emitido'}".strip()
        cta = str(d.get("nro_cheque") or "").strip()
        if moneda == "USD":
            ars = mon * tc if tc > 0 else 0.0
            usd = mon
        else:
            ars = mon
            usd = 0.0
        if abs(ars) < 0.01 and abs(usd) < 0.01:
            continue
        out.append({
            "fecha": fecha,
            "detalle": det,
            "forma_pago": "Cheques Empresa",
            "cta_cte": cta,
            "nro_cuota": "",
            "tipo_cambio": tc if moneda == "USD" else 0,
            "plazo": "",
            "capital": round(ars, 2),
            "intereses": 0,
            "impuestos": 0,
            "cargos": 0,
            "subtotal": round(ars, 2),
            "monto_ars": round(ars, 2),
            "monto_usd": round(usd, 2),
            "varios": f"Ch. {cta} · vto/pago {fecha}",
            "origen": "Cheques Empresa",
            "fuente": "cheque_emitido",
            "moneda": moneda,
            "es_disponibilidad": False,
        })
    return out


def _merge_cheques_ff(lineas: List[dict], cheques: List[dict]) -> List[dict]:
    """Evita duplicar cheque por nro + fecha de pago + importe."""
    existing = {
        (
            (L.get("cta_cte") or "").strip(),
            (L.get("fecha") or "")[:10],
            round(float(L.get("subtotal") or L.get("monto_ars") or 0), 2),
        )
        for L in lineas
        if (L.get("fuente") == "cheque_emitido" or "cheque" in (L.get("forma_pago") or "").lower())
        and not L.get("es_disponibilidad")
    }
    # también por detalle+fecha+monto por si Access no trae nro
    existing_det = {
        (
            (L.get("detalle") or "").strip().lower(),
            (L.get("fecha") or "")[:10],
            round(float(L.get("subtotal") or L.get("monto_ars") or 0), 2),
        )
        for L in lineas
        if not L.get("es_disponibilidad") and not L.get("es_cierre_mes")
    }
    for L in cheques:
        key_nro = (
            (L.get("cta_cte") or "").strip(),
            (L.get("fecha") or "")[:10],
            round(float(L.get("subtotal") or 0), 2),
        )
        key_det = (
            (L.get("detalle") or "").strip().lower(),
            (L.get("fecha") or "")[:10],
            round(float(L.get("subtotal") or 0), 2),
        )
        if (key_nro[0] and key_nro in existing) or key_det in existing_det:
            continue
        lineas.append(L)
        if key_nro[0]:
            existing.add(key_nro)
        existing_det.add(key_det)
    return lineas


IDC_DEB_CRED_PCT = 0.012
_MESES_ES_FF = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _cierre_mes_rows(mes_key: str, pagar_mes: float, acum: float) -> List[dict]:
    """Filas de cierre: total mes + 1.2% IDC (el IDC suma al acumulado)."""
    pagar_mes = round(float(pagar_mes or 0), 2)
    if pagar_mes < 0.005 or not mes_key or len(mes_key) < 7:
        return []
    y, m = int(mes_key[:4]), int(mes_key[5:7])
    label = f"{_MESES_ES_FF[m - 1]}/{y}"
    # último día del mes (orden visual al final del bloque)
    if m == 12:
        fecha_cierre = f"{y}-12-31"
    else:
        from calendar import monthrange
        fecha_cierre = f"{y}-{m:02d}-{monthrange(y, m)[1]:02d}"
    idc = round(pagar_mes * IDC_DEB_CRED_PCT, 2)
    rows = [
        {
            "fecha": fecha_cierre,
            "detalle": f"Total a Pagar mes {label}",
            "forma_pago": "",
            "cta_cte": "",
            "nro_cuota": "",
            "tipo_cambio": 0,
            "plazo": "",
            "capital": 0,
            "intereses": 0,
            "impuestos": 0,
            "cargos": 0,
            "subtotal": pagar_mes,
            "monto_ars": 0.0,  # no recontar en totales
            "monto_usd": 0,
            "varios": "Cierre mensual",
            "origen": "Cierre",
            "fuente": "cierre_mes",
            "moneda": "ARS",
            "es_disponibilidad": False,
            "es_cierre_mes": True,
            "es_idc": False,
            "totales": round(acum, 2),
            "acumulado": round(acum, 2),
            "mes_label": label,
        },
        {
            "fecha": fecha_cierre,
            "detalle": f"Imp. Déb/Créd 1,2% · {label}",
            "forma_pago": "",
            "cta_cte": "",
            "nro_cuota": "",
            "tipo_cambio": 0,
            "plazo": "",
            "capital": 0,
            "intereses": 0,
            "impuestos": idc,
            "cargos": 0,
            "subtotal": idc,
            "monto_ars": idc,
            "monto_usd": 0,
            "varios": "Impuesto a los débitos y créditos",
            "origen": "Cierre",
            "fuente": "imp_deb_cred",
            "moneda": "ARS",
            "es_disponibilidad": False,
            "es_cierre_mes": True,
            "es_idc": True,
            "totales": round(acum + idc, 2),
            "acumulado": round(acum + idc, 2),
            "mes_label": label,
        },
    ]
    return rows


def _fmt_cuota_api(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    try:
        f = float(s.replace(",", "."))
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
    except Exception:
        pass
    if s.endswith(".0"):
        return s[:-2]
    return s


@app.get("/api/financiero/flujo_proyectado")
def api_flujo_proyectado(
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    tc: Optional[float] = None,
    pizarra_soja: Optional[float] = None,
    pizarra_carne: Optional[float] = None,
    incluir_disponibilidades: bool = True,
    fuente: Optional[str] = None,
):
    """
    Consulta de Estado Financiero (estilo Access).
    Fuente preferida: flujo_proyeccion_access (movimientos Proyeccion=Si del Access:
    Cheques Empresa, Deb Bancario, Pago Extras, Sueldos, créditos, etc.).
    Fallback: cheques emitidos / créditos / facturas / alquileres si no hay proyección importada.
    """
    from datetime import date as _date, timedelta
    from flujo_proyeccion_import import init_flujo_proyeccion_schema

    empresa_id = get_empresa_activa_id()
    hoy = _date.today().isoformat()
    desde = (desde or hoy)[:10]
    if not hasta:
        d0 = _date.fromisoformat(desde)
        hasta = (d0 + timedelta(days=365)).isoformat()
    hasta = hasta[:10]
    tc = float(tc or 0) or 0.0
    pizarra_soja = float(pizarra_soja or 0) or 0.0
    pizarra_carne = float(pizarra_carne or 0) or 0.0
    modo = (fuente or "access").strip().lower()

    conn = get_db()
    cur = conn.cursor()
    init_flujo_proyeccion_schema(cur)

    cur.execute(
        "SELECT COUNT(*) AS n FROM flujo_proyeccion_access WHERE COALESCE(empresa_id,1)=?",
        (empresa_id,),
    )
    n_access = int(cur.fetchone()["n"] or 0)

    lineas: List[dict] = []
    origen_datos = "access"

    if n_access > 0 and modo != "live":
        sql = """
            SELECT id, detalle, forma_pago, cta_cte, fecha_debito, nro_cuota,
                   tipo_cambio, plazo, capital, intereses, impuestos, cargos,
                   subtotal, moneda, capital_usd, nro_cheque, nro_credito, varios,
                   es_disponibilidad, fuente
            FROM flujo_proyeccion_access
            WHERE COALESCE(empresa_id, 1) = ?
              AND fecha_debito BETWEEN ? AND ?
        """
        params: list = [empresa_id, desde, hasta]
        if not incluir_disponibilidades:
            sql += " AND COALESCE(es_disponibilidad, 0) = 0 AND subtotal >= 0"
        sql += " ORDER BY fecha_debito ASC, id ASC"
        cur.execute(sql, params)
        for r in cur.fetchall():
            d = dict(r)
            sub = round(float(d.get("subtotal") or 0), 2)
            if abs(sub) < 0.01:
                continue
            es_disp = int(d.get("es_disponibilidad") or 0) == 1 or sub < 0
            forma = (d.get("forma_pago") or "").strip()
            detalle = (d.get("detalle") or "").strip()
            dl = detalle.lower()
            # Saldos live reemplazan el snapshot Access (banco y cheques en cartera).
            # Un cheque ya entregado a proveedor no vuelve a sumar.
            if incluir_disponibilidades and es_disp:
                if dl.startswith("saldo ") or "cheques en cartera" in dl:
                    continue
            fuente_key = "otro"
            fl = forma.lower()
            if "cheques empresa" in fl:
                fuente_key = "cheque_emitido"
            elif "deb" in fl:
                fuente_key = "debito"
            elif "pago extras" in dl:
                fuente_key = "extras"
            elif any(k in dl for k in ("sueldo", "sindicato", "aguinaldo")):
                fuente_key = "sueldos"
            elif "cheques en cartera" in dl or es_disp:
                fuente_key = "disponibilidad"
            elif any(k in fl for k in ("americano", "bullet", "mensual", "semestral", "anual")):
                fuente_key = "credito"
            lineas.append({
                "fecha": (d.get("fecha_debito") or desde)[:10],
                "detalle": detalle,
                "forma_pago": forma,
                "cta_cte": (d.get("cta_cte") or d.get("nro_cheque") or "").strip(),
                "nro_cuota": _fmt_cuota_api(d.get("nro_cuota")),
                "tipo_cambio": float(d.get("tipo_cambio") or 0),
                "plazo": (d.get("plazo") or "").strip(),
                "capital": round(float(d.get("capital") or 0), 2),
                "intereses": round(float(d.get("intereses") or 0), 2),
                "impuestos": round(float(d.get("impuestos") or 0), 2),
                "cargos": round(float(d.get("cargos") or 0), 2),
                "subtotal": sub,
                "monto_ars": sub,
                "monto_usd": round(float(d.get("capital_usd") or 0), 2),
                "varios": (d.get("varios") or "").strip(),
                "origen": forma or ("Disponibilidad" if es_disp else "Proyección Access"),
                "fuente": fuente_key,
                "moneda": (d.get("moneda") or "ARS"),
                "es_disponibilidad": es_disp,
            })
        if incluir_disponibilidades:
            lineas = _lineas_disponibilidades_financiero(cur, empresa_id, desde) + lineas
    else:
        origen_datos = "live"

        def _add(fecha, detalle, origen, monto_ars, monto_usd=0.0, fuente_k="", moneda="ARS",
                 forma_pago="", cta_cte="", nro_cuota="", plazo="", capital=0.0,
                 intereses=0.0, impuestos=0.0, cargos=0.0, varios="", es_disp=False):
            monto_ars = round(float(monto_ars or 0), 2)
            monto_usd = round(float(monto_usd or 0), 2)
            if abs(monto_ars) < 0.01 and abs(monto_usd) < 0.01:
                return
            lineas.append({
                "fecha": (fecha or desde)[:10],
                "detalle": detalle or "",
                "forma_pago": forma_pago or origen,
                "cta_cte": cta_cte,
                "nro_cuota": nro_cuota,
                "tipo_cambio": tc if moneda == "USD" else 0,
                "plazo": plazo,
                "capital": round(float(capital or 0), 2),
                "intereses": round(float(intereses or 0), 2),
                "impuestos": round(float(impuestos or 0), 2),
                "cargos": round(float(cargos or 0), 2),
                "subtotal": monto_ars,
                "monto_ars": monto_ars,
                "monto_usd": monto_usd,
                "varios": varios,
                "origen": origen or "",
                "fuente": fuente_k,
                "moneda": moneda,
                "es_disponibilidad": es_disp,
            })

        # Cheques emitidos se unifican más abajo (_lineas_cheques_emitidos_financiero)
        # usando fecha_pago = vencimiento (no emisión).

        cur.execute(
            """
            SELECT q.fecha_pago, q.nro_cuota, q.total_ars, q.total_pagar,
                   q.capital_usd, q.intereses_usd, q.cargos_usd, q.tipo_cambio,
                   c.nro_credito, c.entidad_financiera, c.banco, c.moneda
            FROM creditos_cuotas q
            JOIN creditos_prestamos c ON c.id = q.credito_id
            WHERE COALESCE(q.empresa_id, c.empresa_id, 1) = ?
              AND UPPER(COALESCE(q.estado,'')) = 'PENDIENTE'
              AND COALESCE(q.fecha_pago,'') BETWEEN ? AND ?
            ORDER BY q.fecha_pago, q.nro_cuota;
            """,
            (empresa_id, desde, hasta),
        )
        for r in cur.fetchall():
            d = dict(r)
            fecha = d.get("fecha_pago") or desde
            mon_c = (d.get("moneda") or "ARS").upper()
            banco = d.get("entidad_financiera") or d.get("banco") or ""
            det = banco or f"Crédito {d.get('nro_credito') or ''}"
            if mon_c == "USD":
                usd = float(d.get("capital_usd") or 0) + float(d.get("intereses_usd") or 0)
                ars = float(d.get("total_ars") or 0)
                if ars < 0.01 and tc > 0 and usd > 0:
                    ars = usd * tc
                _add(fecha, det, "Crédito bancario", ars, usd, "credito", "USD",
                     forma_pago="Americano", nro_cuota=str(d.get("nro_cuota") or ""),
                     capital=ars)
            else:
                ars = float(d.get("total_ars") or d.get("total_pagar") or 0)
                _add(fecha, det, "Crédito bancario", ars, 0, "credito", "ARS",
                     forma_pago="Crédito", nro_cuota=str(d.get("nro_cuota") or ""),
                     capital=ars)

        # Facturas CC se unifican más abajo (_lineas_facturas_cc_financiero)
        # para soportar vencimientos DD/MM/AAAA (ej. carga +30 días).

        # Alquileres (mismo criterio anterior)
        cur.execute(
            """
            SELECT codigo FROM campanias_agro
            WHERE empresa_id = ?
            ORDER BY
              CASE WHEN COALESCE(activa,0)=1 THEN 0 ELSE 1 END,
              codigo DESC
            LIMIT 3;
            """,
            (empresa_id,),
        )
        camps = [r["codigo"] for r in cur.fetchall()]
        if camps:
            ph = ",".join("?" for _ in camps)
            cur.execute(
                f"""
                SELECT a.locador, a.campania_codigo, COALESCE(NULLIF(UPPER(a.grano),''),'SOJA') AS grano,
                       SUM(CASE WHEN COALESCE(a.haber,0) > 0 THEN a.haber ELSE 0 END) AS tn_haber,
                       SUM(CASE WHEN COALESCE(a.debe,0) > 0 THEN a.debe ELSE 0 END) AS tn_debe,
                       MAX(c.fecha_finalizacion) AS fecha_fin,
                       MAX(c.modalidad) AS modalidad
                FROM alquileres_cta_cte a
                LEFT JOIN contratos_alquileres c
                  ON c.empresa_id = a.empresa_id
                 AND UPPER(TRIM(c.locador)) = UPPER(TRIM(a.locador))
                 AND c.campania_codigo = a.campania_codigo
                WHERE a.empresa_id = ?
                  AND a.campania_codigo IN ({ph})
                GROUP BY a.locador, a.campania_codigo, COALESCE(NULLIF(UPPER(a.grano),''),'SOJA')
                HAVING (SUM(CASE WHEN COALESCE(a.haber,0) > 0 THEN a.haber ELSE 0 END)
                      - SUM(CASE WHEN COALESCE(a.debe,0) > 0 THEN a.debe ELSE 0 END)) > 0.01;
                """,
                (empresa_id, *camps),
            )
            for r in cur.fetchall():
                d = dict(r)
                tn_pend = float(d.get("tn_haber") or 0) - float(d.get("tn_debe") or 0)
                if tn_pend <= 0.01:
                    continue
                grano = (d.get("grano") or "SOJA").upper()
                if any(k in grano for k in ("NOV", "VACA", "TERN", "HACIEN", "CARNE", "KG VIVO")):
                    px = pizarra_carne if pizarra_carne > 0 else pizarra_soja
                    rubro = "Alquiler ganadero"
                else:
                    px = pizarra_soja
                    rubro = "Alquiler agrícola"
                if px <= 0:
                    continue
                monto = tn_pend * px
                fecha = (d.get("fecha_fin") or hasta or desde)[:10]
                if fecha < desde:
                    continue
                if fecha > hasta:
                    fecha = hasta
                tn_fmt = f"{tn_pend:,.3f}".replace(",", "X").replace(".", ",").replace("X", ".")
                px_fmt = f"{px:,.0f}".replace(",", ".")
                det = f"{d.get('locador')} · {tn_fmt} tn {grano}"
                _add(fecha, det, rubro, monto, 0, "alquiler", "ARS",
                     varios=f"{rubro} {d.get('campania_codigo')} × $ {px_fmt}", capital=monto)

        if incluir_disponibilidades:
            lineas = _lineas_disponibilidades_financiero(cur, empresa_id, desde) + lineas

    # Cuotas de contratos creados en CAmpo+ (cronograma → financiero)
    try:
        cur.execute(
            """
            SELECT q.fecha_vencimiento, q.nro_cuota, q.kilos, q.valuacion_ars, q.detalle, q.estado,
                   c.nombre_campo, c.propietario, c.locador, c.grano, c.hectareas, c.ubicacion
            FROM contratos_alquileres_cuotas q
            JOIN contratos_alquileres c ON c.id = q.contrato_id
            WHERE COALESCE(q.empresa_id, c.empresa_id, 1) = ?
              AND COALESCE(q.fecha_vencimiento,'') BETWEEN ? AND ?
              AND UPPER(COALESCE(q.estado,'')) NOT IN ('PAGADA', 'CANCELADA', 'ANULADA')
            ORDER BY q.fecha_vencimiento, q.nro_cuota;
            """,
            (empresa_id, desde, hasta),
        )
        for r in cur.fetchall():
            d = dict(r)
            val = round(float(d.get("valuacion_ars") or 0), 2)
            if val < 0.01 and pizarra_soja > 0:
                kg = float(d.get("kilos") or 0)
                val = round((kg / 1000.0) * pizarra_soja, 2)
            if val < 0.01:
                continue
            campo = (d.get("nombre_campo") or "").strip()
            prop = (d.get("propietario") or d.get("locador") or "").strip()
            grano = (d.get("grano") or "").strip()
            det = (d.get("detalle") or "").strip() or f"{campo} · {prop} · {grano}"
            lineas.append({
                "fecha": (d.get("fecha_vencimiento") or desde)[:10],
                "detalle": det,
                "forma_pago": f"Alquiler cuota {d.get('nro_cuota') or ''}".strip(),
                "cta_cte": "",
                "nro_cuota": str(d.get("nro_cuota") or ""),
                "tipo_cambio": 0,
                "plazo": "",
                "capital": val,
                "intereses": 0,
                "impuestos": 0,
                "cargos": 0,
                "subtotal": val,
                "monto_ars": val,
                "monto_usd": 0,
                "varios": f"{float(d.get('kilos') or 0):,.0f} kg {grano}".replace(",", "."),
                "origen": "Alquiler / aparcería",
                "fuente": "alquiler",
                "moneda": "ARS",
                "es_disponibilidad": False,
            })
    except Exception:
        pass

    # Facturas CC Campo+ (Access + live): vto automático 30 días, fechas DD/MM, etc.
    fact_cc = _lineas_facturas_cc_financiero(cur, empresa_id, desde, hasta)
    lineas = _merge_facturas_cc(lineas, fact_cc)
    # Cheques emitidos Campo+: financiero = fecha de vencimiento/pago (no emisión)
    chq_ff = _lineas_cheques_emitidos_financiero(cur, empresa_id, desde, hasta, tc)
    lineas = _merge_cheques_ff(lineas, chq_ff)

    conn.close()

    for L in lineas:
        L["fecha"] = _fecha_iso_ff(L.get("fecha") or "") or desde

    filtradas = [L for L in lineas if desde <= (L["fecha"] or "")[:10] <= hasta]
    filtradas.sort(key=lambda x: (
        x["fecha"],
        0 if x.get("es_disponibilidad") else 1,
        0 if x.get("es_cierre_mes") else 1,
        x.get("detalle") or "",
    ))

    total_ars = 0.0
    total_pagar = 0.0
    total_disp = 0.0
    total_usd = 0.0
    total_idc = 0.0
    por_fuente: dict = {}
    out = []
    mes_key = None
    pagar_mes = 0.0
    acum_mes = 0.0
    cierres_mensuales: List[dict] = []

    def _flush_cierre():
        nonlocal pagar_mes, acum_mes, total_idc, total_ars, total_pagar, mes_key
        rows = _cierre_mes_rows(mes_key or "", pagar_mes, pagar_mes)
        if not rows:
            pagar_mes = 0.0
            acum_mes = 0.0
            return
        # El total del mes es solo ese período, no el acumulado desde el inicio del rango.
        out.append(rows[0])
        idc_row = rows[1]
        idc = float(idc_row["monto_ars"])
        total_ars += idc
        total_pagar += idc
        total_idc += idc
        idc_row["totales"] = round(pagar_mes + idc, 2)
        idc_row["acumulado"] = round(pagar_mes + idc, 2)
        out.append(idc_row)
        por_fuente["imp_deb_cred"] = por_fuente.get("imp_deb_cred", 0.0) + idc
        cierres_mensuales.append({
            "mes": mes_key,
            "mes_label": idc_row.get("mes_label"),
            "total_a_pagar": round(pagar_mes, 2),
            "imp_deb_cred": idc,
            "total_con_imp": round(pagar_mes + idc, 2),
        })
        pagar_mes = 0.0
        acum_mes = 0.0

    for L in filtradas:
        if not L.get("es_disponibilidad"):
            fk = (L.get("fecha") or "")[:7]
            if mes_key is not None and fk and fk != mes_key:
                _flush_cierre()
            if fk:
                mes_key = fk
            if float(L.get("monto_ars") or 0) > 0:
                pagar_mes += float(L["monto_ars"])
                acum_mes += float(L["monto_ars"])

        total_ars += L["monto_ars"]
        total_usd += L.get("monto_usd") or 0
        if L.get("es_disponibilidad") or L["monto_ars"] < 0:
            total_disp += L["monto_ars"]
        else:
            total_pagar += L["monto_ars"]
        L2 = dict(L)
        L2["totales"] = round(acum_mes, 2)
        L2["acumulado"] = round(acum_mes, 2)
        out.append(L2)
        por_fuente[L["fuente"]] = por_fuente.get(L["fuente"], 0.0) + L["monto_ars"]

    _flush_cierre()

    kilos = 0.0
    if pizarra_soja > 0:
        base_kg = total_pagar if total_pagar > 0 else total_ars
        if pizarra_soja >= 10000:
            kilos = round(base_kg / pizarra_soja * 1000, 1)
        else:
            kilos = round(base_kg / pizarra_soja, 1)

    mes_label = ""
    try:
        d0 = _date.fromisoformat(desde)
        mes_label = f"{_MESES_ES_FF[d0.month - 1]}/{d0.year}"
    except Exception:
        mes_label = desde

    nota = (
        "Solo pendientes (sin fecha debitado/conciliados). "
        "Fecha = vto/pago. Disponibilidades a la fecha Desde: saldos a la vista, caja, FCI y plazos fijos. "
        "Incluye facturas CC Campo+. Cierre mensual + 1,2% imp. débitos/créditos."
        if origen_datos == "access"
        else "Proyección live: cheques/créditos/facturas + disponibilidades. Cierre mensual + 1,2% IDC."
    )

    return {
        "desde": desde,
        "hasta": hasta,
        "tc": tc,
        "pizarra_soja": pizarra_soja,
        "pizarra_carne": pizarra_carne,
        "origen": origen_datos,
        "incluir_disponibilidades": incluir_disponibilidades,
        "total_ars": round(total_ars, 2),
        "total_a_pagar": round(total_pagar, 2),
        "total_disponibilidades": round(total_disp, 2),
        "total_imp_deb_cred": round(total_idc, 2),
        "total_usd": round(total_usd, 2),
        "equivalente_kg": kilos,
        "por_fuente": {k: round(v, 2) for k, v in por_fuente.items()},
        "cantidad": len(out),
        "mes_label": mes_label,
        "cierres_mensuales": cierres_mensuales,
        "lineas": out,
        "nota": nota,
        "filas_access": n_access,
        "facturas_cc_agregadas": len(fact_cc),
    }


@app.post("/api/financiero/reimportar_proyeccion")
def api_reimportar_proyeccion_access():
    """Reimporta proyección Access (Proyeccion=Si) desde movimientos bancarios.xlsx."""
    from flujo_proyeccion_import import importar_flujo_proyeccion_access

    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cur = conn.cursor()
    try:
        result = importar_flujo_proyeccion_access(cur, empresa_id)
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))
    conn.close()
    return result


@app.get("/api/dashboard/kpis")
def dashboard_kpis():
    """KPIs del Index filtrados por la empresa activa."""
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COALESCE(SUM(s.saldo), 0.0) AS total
        FROM (
            SELECT COALESCE(SUM(cc.debe - cc.haber), 0.0) AS saldo
            FROM cuentas_corrientes cc
            WHERE COALESCE(cc.empresa_id, 1) = ?
              AND EXISTS (
                  SELECT 1 FROM entidades e
                  WHERE REPLACE(e.cuit, '-', '') = REPLACE(cc.entidad_id, '-', '')
                    AND COALESCE(e.es_cuenta_ajuste, 0) = 0
                    AND COALESCE(e.es_cuenta_bancaria, 0) = 0
              )
            GROUP BY REPLACE(cc.entidad_id, '-', '')
            HAVING ROUND(saldo, 2) >= 0.01
        ) s;
    """, (empresa_id,))
    compromisos = float(cursor.fetchone()["total"] or 0)

    cursor.execute("""
        SELECT COALESCE(SUM(acuerdo_cta_cte), 0.0) AS acuerdos,
               COALESCE(SUM(fw_usd), 0.0) AS deuda_usd
        FROM ctas_ctes_bancarias
        WHERE baja = 0 AND empresa_id = ?
          AND COALESCE(es_cuenta_bancaria, 1) = 1;
    """, (empresa_id,))
    bancos = cursor.fetchone()
    acuerdos = float(bancos["acuerdos"] or 0)
    deuda_usd = float(bancos["deuda_usd"] or 0)

    fci = _posicion_fci(cursor, fecha=datetime.now().strftime("%Y-%m-%d"))
    fci_activo = float(fci.get("activo") or 0)
    compromisos_neto = round(max(0.0, compromisos - fci_activo), 2)

    alquileres_kg = 0.0
    campania_activa = ""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='campanias_agro';"
    )
    if cursor.fetchone():
        cursor.execute(
            "SELECT codigo FROM campanias_agro WHERE activa = 1 ORDER BY codigo DESC LIMIT 1;"
        )
        row_camp = cursor.fetchone()
        campania_activa = (row_camp["codigo"] if row_camp else "") or ""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='contratos_alquileres';"
    )
    if cursor.fetchone():
        cursor.execute(
            """
            SELECT COALESCE(SUM(
                CASE
                    WHEN COALESCE(kilos_totales, 0) > 0 THEN kilos_totales
                    ELSE COALESCE(tn_totales, 0) * 1000
                END
            ), 0.0) AS kg
            FROM contratos_alquileres
            WHERE COALESCE(empresa_id, 1) = ?
              AND (? = '' OR campania_codigo = ?)
              AND UPPER(TRIM(COALESCE(grano, ''))) LIKE 'SOJA%';
            """,
            (empresa_id, campania_activa, campania_activa),
        )
        alquileres_kg = float(cursor.fetchone()["kg"] or 0)

    conn.close()
    return {
        "empresa_id": empresa_id,
        "compromisos_ars": compromisos,
        "fci_activos_ars": fci_activo,
        "compromisos_neto_fci_ars": compromisos_neto,
        "acuerdos_bancarios_ars": acuerdos,
        "deuda_usd": deuda_usd,
        "alquileres_kg": alquileres_kg,
        "campania_activa": campania_activa,
    }

@app.get("/api/ordenes_pago")
def listar_ordenes_pago():
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT op.*, COALESCE(e.nombre_fantasia, e.razon_social) as proveedor
        FROM ordenes_pago op
        LEFT JOIN entidades e ON REPLACE(e.cuit, '-', '') = REPLACE(op.cuit, '-', '')
        WHERE op.empresa_id = ?
        ORDER BY op.fecha DESC, op.id DESC;
    """, (empresa_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows
# --- GESTIÓN DE EMPRESAS (fuente: tabla empresas) ---

@app.get("/api/empresas")
def listar_empresas(request: Request):
    from plataforma import _token_from_headers

    if not _token_from_headers({k.lower(): v for k, v in request.headers.items()}):
        raise HTTPException(status_code=401, detail="Tenés que iniciar sesión")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM empresas ORDER BY razon_social COLLATE NOCASE ASC;")
    rows = [_row_to_empresa(r) for r in cursor.fetchall()]
    conn.close()
    return rows

@app.get("/api/empresas/{empresa_id}")
def obtener_empresa(empresa_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM empresas WHERE id = ?;", (empresa_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")
    return _row_to_empresa(row)

@app.post("/api/empresas")
def crear_empresa(data: EmpresaItemModel, request: Request):
    from plataforma import _token_from_headers, exigir_cupo_empresa

    if not _token_from_headers({k.lower(): v for k, v in request.headers.items()}):
        raise HTTPException(status_code=401, detail="Tenés que iniciar sesión")
    exigir_cupo_empresa(DB_PATH)
    conn = get_db()
    cursor = conn.cursor()
    tenant = (data.tenant_id or "").strip().lower()
    cuit = re.sub(r"[^0-9]", "", data.cuit or "")
    if not data.razon_social.strip() or not cuit or not tenant:
        conn.close()
        raise HTTPException(status_code=400, detail="Razón social, CUIT y tenant_id son obligatorios.")
    try:
        cursor.execute(
            """
            INSERT INTO empresas (razon_social, cuit, tenant_id, localidad)
            VALUES (?, ?, ?, ?);
            """,
            (data.razon_social.strip(), cuit, tenant, (data.localidad or "").strip()),
        )
        nuevo_id = cursor.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="El tenant_id ya existe.")
    conn.close()
    return {"status": "success", "id": nuevo_id, "message": "Empresa registrada correctamente."}

@app.put("/api/empresas/{empresa_id}")
def actualizar_empresa(empresa_id: int, data: EmpresaItemModel):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM empresas WHERE id = ?;", (empresa_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")
    tenant = (data.tenant_id or "").strip().lower()
    cuit = re.sub(r"[^0-9]", "", data.cuit or "")
    if not data.razon_social.strip() or not cuit or not tenant:
        conn.close()
        raise HTTPException(status_code=400, detail="Razón social, CUIT y tenant_id son obligatorios.")
    try:
        cursor.execute(
            """
            UPDATE empresas
            SET razon_social = ?, cuit = ?, tenant_id = ?, localidad = ?
            WHERE id = ?;
            """,
            (data.razon_social.strip(), cuit, tenant, (data.localidad or "").strip(), empresa_id),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="El tenant_id ya existe en otra empresa.")
    conn.close()
    return {"status": "success", "message": "Empresa actualizada correctamente."}

@app.delete("/api/empresas/{empresa_id}")
def eliminar_empresa(empresa_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM empresas;")
    total = cursor.fetchone()[0]
    if total <= 1:
        conn.close()
        raise HTTPException(status_code=400, detail="No se puede eliminar la única empresa del sistema.")
    cursor.execute("SELECT id FROM empresas WHERE id = ?;", (empresa_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")
    cursor.execute("SELECT empresa_activa_id FROM configuracion_empresa WHERE id = 1;")
    cfg = cursor.fetchone()
    activa_id = int(cfg["empresa_activa_id"]) if cfg and cfg["empresa_activa_id"] is not None else None
    cursor.execute("DELETE FROM empresas WHERE id = ?;", (empresa_id,))
    if activa_id == empresa_id:
        cursor.execute("SELECT id FROM empresas ORDER BY id ASC LIMIT 1;")
        primera = cursor.fetchone()
        if primera:
            cursor.execute(
                "UPDATE configuracion_empresa SET empresa_activa_id = ? WHERE id = 1;",
                (int(primera["id"]),),
            )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Empresa eliminada correctamente."}

@app.get("/api/empresa_activa")
def obtener_empresa_activa():
    emp_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, razon_social, cuit, tenant_id, localidad FROM empresas WHERE id = ?;",
        (emp_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="No hay empresas registradas.")
    return _row_to_empresa(row)

@app.post("/api/empresa_activa")
def actualizar_empresa_activa(data: EmpresaActivaModel):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM empresas WHERE id = ?;", (data.empresa_activa_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="La empresa indicada no existe.")
    cursor.execute(
        "UPDATE configuracion_empresa SET empresa_activa_id = ? WHERE id = 1;",
        (data.empresa_activa_id,),
    )
    conn.commit()
    cursor.execute(
        "SELECT id, razon_social, cuit, tenant_id, localidad FROM empresas WHERE id = ?;",
        (data.empresa_activa_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return {
        "status": "success",
        "message": "Empresa activa actualizada correctamente.",
        "empresa": _row_to_empresa(row),
    }

@app.post("/api/ordenes_pago")
def emitir_orden_pago(data: OrdenPagoModel):
    empresa_id = get_empresa_activa_id()
    conn = get_db()
    cursor = conn.cursor()
    cuit_clean = "".join(filter(str.isdigit, str(data.cuit)))

    # Proveedor S/P: sin retenciones de ningún tipo
    cursor.execute(
        "SELECT razon_social, nombre_fantasia, centro_costo FROM entidades WHERE REPLACE(cuit, '-', '') = ?;",
        (cuit_clean,),
    )
    ent = cursor.fetchone()
    es_sp = False
    if ent:
        cc = (ent["centro_costo"] or "1").upper()
        nom = f"{ent['razon_social'] or ''} {ent['nombre_fantasia'] or ''}".upper()
        es_sp = cc == "SP" or "(S/P)" in nom or " S/P" in nom
    ret_iibb = 0.0 if es_sp else float(data.retencion_iibb or 0)
    ret_sicore = 0.0 if es_sp else float(data.retencion_sicore or 0)
    solo_ret = bool(getattr(data, "solo_retenciones", False)) and not es_sp

    # Perfil empresa: si no es agente, no registra esa retención
    cursor.execute("SELECT * FROM configuracion_empresa WHERE id = 1;")
    cfg = cursor.fetchone()
    if cfg and not es_sp:
        if not int(cfg["agente_retencion_iibb"] or 0):
            ret_iibb = 0.0
        if not int(cfg["agente_retencion_ganancias"] or 0):
            ret_sicore = 0.0

    if solo_ret and (ret_sicore + ret_iibb) < 0.01:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="OP solo retenciones: no hay importe de retención a aplicar.",
        )

    # Si es solo retenciones, el "total_pagado" de la OP es únicamente lo retenido
    total_op = float(data.total_pagado or 0)
    if solo_ret:
        total_op = ret_sicore + ret_iibb

    base_imp = float(data.base_imponible or 0)
    if base_imp < 0.01:
        base_imp = total_op if total_op > 0.01 else (ret_sicore + ret_iibb)

    cursor.execute("SELECT COUNT(*) FROM ordenes_pago WHERE COALESCE(empresa_id, 1) = ?;", (empresa_id,))
    count = cursor.fetchone()[0] + 1
    anio_op = data.fecha[:4] if (data.fecha and len(data.fecha) >= 4) else "2026"
    nro_op = f"OP-{anio_op}-{count:05d}"

    forma = (data.forma_pago or "Transferencia").strip()
    es_cheque = "cheque" in forma.lower()
    vto_cheque = (data.fecha_vto_cheque or "").strip()[:10]
    venc_pago = vto_cheque if (es_cheque and vto_cheque) else data.fecha
    obs = (data.observaciones or "").strip()
    if solo_ret:
        forma = "Solo retenciones"
        if "solo retencion" not in obs.lower():
            obs = (obs + " | OP solo retenciones (sin pago efectivo/cheque/transferencia)").strip(" |")

    cursor.execute("""
        INSERT INTO ordenes_pago (nro_orden, cuit, fecha, total_pagado, retencion_iibb, retencion_sicore, forma_pago, observaciones, usuario_registro, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (nro_op, data.cuit, data.fecha, total_op, ret_iibb, ret_sicore, forma, obs, data.usuario_registro, empresa_id))

    monto_pago_efectivo = 0.0 if solo_ret else (total_op - (ret_sicore + ret_iibb))
    if monto_pago_efectivo < 0:
        monto_pago_efectivo = 0.0 if solo_ret else total_op

    # Solo impacta HABER de pago si hubo transferencia/cheque/efectivo
    if monto_pago_efectivo > 0.01:
        cursor.execute("""
            INSERT INTO cuentas_corrientes (entidad_id, tipo_comprobante, numero_comprobante, forma_pago, nro_cheque, fecha, vencimiento, neto, iva, debe, haber, total, estado, usuario_registro, empresa_id)
            VALUES (?, 'Orden de Pago', ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, 'Pagado', ?, ?);
        """, (data.cuit, nro_op, forma, data.nro_cheque, data.fecha, venc_pago, monto_pago_efectivo, monto_pago_efectivo, data.usuario_registro, empresa_id))
        if es_cheque and (data.nro_cheque or "").strip():
            cursor.execute(
                """
                INSERT INTO cartera_cheques (
                    tipo, nro_cheque, banco, librador, fecha_emision, fecha_pago,
                    monto, moneda, estado, empresa_id, proveedor_cuit
                ) VALUES ('Emitido', ?, '', ?, ?, ?, ?, 'ARS', 'Emitido', ?, ?);
                """,
                (
                    (data.nro_cheque or "").strip(),
                    (ent["razon_social"] if ent else "") or "",
                    data.fecha,
                    venc_pago,
                    monto_pago_efectivo,
                    empresa_id,
                    cuit_clean,
                ),
            )
        for cid in (data.cheques_cartera or []):
            cursor.execute(
                """
                SELECT id, estado, nro_cheque FROM cartera_cheques
                WHERE id = ? AND COALESCE(empresa_id, 1) = ?;
                """,
                (int(cid), empresa_id),
            )
            ch = cursor.fetchone()
            if not ch:
                conn.rollback()
                conn.close()
                raise HTTPException(400, "Un cheque de cartera ya no está disponible.")
            est = (ch["estado"] or "").strip().lower()
            if est not in ("en cartera", "en carteras"):
                conn.rollback()
                conn.close()
                raise HTTPException(400, f"El cheque {ch['nro_cheque']} no está en cartera ({ch['estado']}).")
            cursor.execute(
                """
                UPDATE cartera_cheques
                SET estado = 'Entregado proveedor', proveedor_cuit = ?,
                    observaciones = TRIM(COALESCE(observaciones,'') || ?)
                WHERE id = ?;
                """,
                (cuit_clean, f" | OP {nro_op}", int(cid)),
            )
    elif solo_ret:
        # Renglón informativo de OP (haber 0) para trazabilidad / reimpresión
        cursor.execute("""
            INSERT INTO cuentas_corrientes (entidad_id, tipo_comprobante, numero_comprobante, forma_pago, nro_cheque, fecha, vencimiento, neto, iva, debe, haber, total, estado, usuario_registro, empresa_id, observaciones)
            VALUES (?, 'Orden de Pago', ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 'Retenciones', ?, ?, ?);
        """, (data.cuit, nro_op, forma, data.nro_cheque, data.fecha, data.fecha, data.usuario_registro, empresa_id,
              "Solo retenciones — pago pendiente de cargar"))

    razon = ent["razon_social"] if ent else "Proveedor Registrado"

    cert_sicore_nro = None
    cert_iibb_nro = None

    if ret_sicore > 0:
        sec_sicore = obtener_siguiente_certificado_db("SICORE", 2073) + 1
        cert_sicore_nro = f"{anio_op}-{sec_sicore}"
        _map_reg = {"06": "078", "6": "078", "16": "094", "18": "116", "21": "025"}
        reg_sic = str(getattr(data, "regimen_sicore", None) or "078").strip()
        reg_sic = _map_reg.get(reg_sic, reg_sic)
        reg_sic = "".join(ch for ch in reg_sic if ch.isdigit()).zfill(3)[:3] or "078"

        cursor.execute("""
            INSERT INTO retenciones_sicore (fecha, razon_social, cuit, base_imponible, regimen, importe_retenido, nro_comprobante, tipo_retencion)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'SICORE');
        """, (data.fecha, razon, data.cuit, base_imp, reg_sic, ret_sicore, cert_sicore_nro))

        cursor.execute("""
            INSERT INTO cuentas_corrientes (entidad_id, tipo_comprobante, numero_comprobante, forma_pago, fecha, vencimiento, neto, iva, debe, haber, total, estado, usuario_registro, empresa_id)
            VALUES (?, 'Retención SICORE', ?, 'Retención Ganancias', ?, ?, 0, 0, 0, ?, ?, 'Aplicado', ?, ?);
        """, (data.cuit, cert_sicore_nro, data.fecha, data.fecha, ret_sicore, ret_sicore, data.usuario_registro, empresa_id))

    if ret_iibb > 0:
        sec_iibb = obtener_siguiente_certificado_db("IIBB", 2961) + 1
        cert_iibb_nro = f"{anio_op}-{sec_iibb}"
        alic_raw = float(getattr(data, "alicuota_iibb", None) or 0)
        if alic_raw > 1:  # vino como 1.75
            alic_raw = alic_raw / 100.0
        if alic_raw < 0.0000001 and base_imp > 0.01:
            alic_raw = ret_iibb / base_imp
        regimen_iibb = f"ARBA {alic_raw * 100:.2f}%" if alic_raw > 0 else "ARBA"

        cursor.execute("""
            INSERT INTO retenciones_sicore (fecha, razon_social, cuit, base_imponible, regimen, importe_retenido, nro_comprobante, tipo_retencion)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'IIBB');
        """, (data.fecha, razon, data.cuit, base_imp, regimen_iibb, ret_iibb, cert_iibb_nro))

        cursor.execute("""
            INSERT INTO cuentas_corrientes (entidad_id, tipo_comprobante, numero_comprobante, forma_pago, fecha, vencimiento, neto, iva, debe, haber, total, estado, usuario_registro, empresa_id)
            VALUES (?, 'Retención IIBB', ?, 'Retención ARBA', ?, ?, 0, 0, 0, ?, ?, 'Aplicado', ?, ?);
        """, (data.cuit, cert_iibb_nro, data.fecha, data.fecha, ret_iibb, ret_iibb, data.usuario_registro, empresa_id))

    # Marcar facturas como Pagado solo cuando hay pago efectivo (no en solo-retenciones)
    restante = 0.0 if solo_ret else float(total_op or 0)
    if restante > 0.01:
        placeholders = ",".join("?" for _ in _TIPOS_PAGO)
        cursor.execute(f"""
            SELECT id, COALESCE(NULLIF(total, 0), debe, 0) AS monto
            FROM cuentas_corrientes
            WHERE REPLACE(entidad_id, '-', '') = ?
              AND COALESCE(empresa_id, 1) = ?
              AND COALESCE(debe, 0) > 0.01
              AND COALESCE(tipo_comprobante, '') NOT IN ({placeholders})
              AND COALESCE(estado, 'Pendiente') IN ('Pendiente', 'pendiente', '')
            ORDER BY fecha ASC, id ASC;
        """, (cuit_clean, empresa_id, *_TIPOS_PAGO))
        for row in cursor.fetchall():
            if restante <= 0.01:
                break
            monto = float(row["monto"] or 0)
            cursor.execute(
                "UPDATE cuentas_corrientes SET estado = 'Pagado' WHERE id = ?;",
                (row["id"],),
            )
            restante -= monto

    conn.commit()
    conn.close()

    msg = (
        "Orden de Pago S/P registrada (sin retenciones)."
        if es_sp
        else (
            "OP solo retenciones: certificados aplicados en cta cte. Las facturas siguen pendientes hasta cargar el pago."
            if solo_ret
            else "Orden de Pago registrada con éxito."
        )
    )
    return {
        "status": "success",
        "nro_orden": nro_op,
        "cert_sicore": cert_sicore_nro,
        "cert_iibb": cert_iibb_nro,
        "es_sp": es_sp,
        "solo_retenciones": solo_ret,
        "retencion_iibb": ret_iibb,
        "retencion_sicore": ret_sicore,
        "message": msg,
    }

# Backfill Id Access / prestamos (despues de definir helpers; no duplica movimientos)
try:
    _conn_bf = get_db()
    _cur_bf = _conn_bf.cursor()
    _cur_bf.execute("SELECT COUNT(*) AS n FROM movimientos_cta_cte_bancos WHERE id_access IS NOT NULL;")
    _ya_id = int(_cur_bf.fetchone()["n"] or 0)
    _cur_bf.execute("SELECT COUNT(*) AS n FROM creditos_cuotas;")
    _n_cuotas = int(_cur_bf.fetchone()["n"] or 0)
    _conn_bf.close()
    if _ya_id == 0:
        print("Backfill Id Access / prestamos desde Excel...")
        print(backfill_id_access_y_prestamos_desde_excel())
    if _n_cuotas == 0:
        print(sincronizar_creditos_desde_movimientos_prestamo())
except Exception as e:
    print(f"AVISO backfill id_access/prestamos: {e}")

from agro_api import register_agro_routes
register_agro_routes(app, get_db, get_empresa_activa_id)
register_audit_routes(app, get_db, get_empresa_activa_id)
from ganaderia_api import register_ganaderia_routes
register_ganaderia_routes(app, get_db, get_empresa_activa_id)
from actividades_api import register_actividades_routes
register_actividades_routes(app, get_db, get_empresa_activa_id)
from liquidaciones_api import register_liquidaciones_routes
register_liquidaciones_routes(app, get_db, get_empresa_activa_id)

# Audit schema antes de SaaS (usuarios_sistema)
try:
    _conn_aud = get_db()
    init_audit_schema(_conn_aud.cursor())
    _conn_aud.commit()
    _conn_aud.close()
except Exception as _e_aud:
    print(f"AVISO init audit: {_e_aud}")

from plataforma import TenantDBMiddleware, init_plataforma_schema, register_plataforma_routes
from saas_auth import init_saas_schema, register_saas_routes
try:
    _c_saas = get_db()
    init_saas_schema(_c_saas.cursor())
    _c_saas.commit()
    _c_saas.close()
except Exception as _e_saas:
    print(f"AVISO init saas: {_e_saas}")
register_saas_routes(app, get_db, get_empresa_activa_id)
try:
    _c_plat = get_db()
    init_plataforma_schema(_c_plat.cursor(), DB_PATH)
    _c_plat.commit()
    _c_plat.close()
except Exception as _e_plat:
    print(f"AVISO init plataforma: {_e_plat}")
register_plataforma_routes(app, DB_PATH)
app.add_middleware(AuditMiddleware, get_db=get_db, get_empresa_activa_id=get_empresa_activa_id)
app.add_middleware(TenantDBMiddleware, master_path=DB_PATH)

def _restablecer_clave_eze_una_vez() -> None:
    """Solo en el disco del servidor, una vez: la clave de eze vuelve a campo+."""
    data = os.environ.get("CAMPO_DATA_DIR", "").strip()
    if not data or not os.path.exists(DB_PATH):
        return
    flag = os.path.join(data, ".clave_eze_lista")
    if os.path.exists(flag):
        return
    from saas_auth import _hash_password
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            UPDATE usuarios_sistema
            SET password_hash = ?
            WHERE LOWER(TRIM(COALESCE(login, ''))) = 'eze'
               OR LOWER(TRIM(COALESCE(email, ''))) = 'ezequielcalabresi@gmail.com';
            """,
            (_hash_password("campo+"),),
        )
        conn.commit()
    except sqlite3.OperationalError:
        return
    finally:
        conn.close()
    with open(flag, "w", encoding="utf-8") as fh:
        fh.write("ok")


_restablecer_clave_eze_una_vez()


@app.get("/")
@app.get("/index.html")
@app.get("/Index.html")
def pagina_inicio():
    """En Linux la raíz y index.html abren el mismo inicio."""
    return FileResponse(os.path.join(BASE_DIR, "Index.html"))


_data_dir = os.environ.get("CAMPO_DATA_DIR", "").strip()
if _data_dir:
    _logos = os.path.join(_data_dir, "logos")
    os.makedirs(_logos, exist_ok=True)
    app.mount("/logos", StaticFiles(directory=_logos), name="logos")

app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)