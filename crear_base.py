import sqlite3
import os

RUTA_DB = "campoplus.db"

def crear_base_datos_completa():
    if os.path.exists(RUTA_DB):
        print("La base de datos ya existe. Actualizando tablas relacionales...")
    
    conn = sqlite3.connect(RUTA_DB)
    cursor = conn.cursor()

    # Habilitar claves foráneas
    cursor.execute("PRAGMA foreign_keys = ON;")

    # 1. MÓDULO FISCAL Y PARÁMETROS GENERALES
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reg_fiscales_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo_regimen TEXT, -- 'SICORE', 'RG830', 'IIBB', etc.
        codigo TEXT,
        descripcion TEXT,
        alicuota REAL,
        minimo_no_imponible REAL,
        vigencia_desde TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS impuestos_general (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre_impuesto TEXT, -- IVA, Ganancias, IIBB, Tasas Municipales
        alicuota_general REAL,
        descripcion TEXT
    );
    """)

    # 2. MÓDULO ESTRUCTURA PRODUCTIVA (AGRICOLA)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cultivos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE, -- Soja 1ª, Soja 2ª, Maíz Temprano, Maíz Tardío, Girasol, Trigo, etc.
        tipo_ciclo TEXT     -- Estivales / Invernales
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS campos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre_campo TEXT,
        titular TEXT,
        ubicacion TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS lotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campo_id INTEGER,
        nombre_lote TEXT,
        superficie_has REAL,
        geolocalizacion TEXT, -- Requerido para IP 1 y 2 de ARCA
        FOREIGN KEY (campo_id) REFERENCES campos(id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tipos_labores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre_labor TEXT -- Siembra, Pulverización, Cosecha, Fertilización, Labranza
    );
    """)

    # 3. MÓDULO GANADERÍA (PRODUCTIVO)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS categorias_ganaderas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre_categoria TEXT, -- Terneros, Novillitos, Vacas de Cría, Toros, Invernada
        sistema_produccion TEXT -- Cría, Recría, Invernada, Ciclo Completo
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS movimientos_ganado (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        categoria_id INTEGER,
        tipo_movimiento TEXT, -- Nacimiento, Compra, Venta, Mortandad, Cambio de Categoría
        cantidad INTEGER,
        peso_promedio REAL,
        observaciones TEXT,
        FOREIGN KEY (categoria_id) REFERENCES categorias_ganaderas(id)
    );
    """)

    # 4. MÓDULO UNIDADES DE NEGOCIO (MAQUINARIA Y CAMIONES)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS maquinarias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre_maquina TEXT, -- Tractores, Cosechadoras, Sembradoras
        valor_origen REAL,
        vida_util_anos INTEGER,
        fecha_adquisicion TEXT,
        amortizacion_acumulada REAL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS flota_camiones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patente TEXT UNIQUE,
        modelo TEXT,
        chofer_asignado TEXT,
        costo_km REAL
    );
    """)

    # 5. MÓDULO CONTABILIDAD Y FINANZAS
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS plan_de_cuentas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_cuenta TEXT UNIQUE,
        nombre_cuenta TEXT,
        tipo_cuenta TEXT -- Activo, Pasivo, Patrimonio Neto, Resultado
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS asientos_contables (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        concepto TEXT,
        total_debe REAL,
        total_haber REAL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS detalles_asiento (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asiento_id INTEGER,
        cuenta_id INTEGER,
        debe REAL,
        haber REAL,
        FOREIGN KEY (asiento_id) REFERENCES asientos_contables(id),
        FOREIGN KEY (cuenta_id) REFERENCES plan_de_cuentas(id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cuentas_bancarias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        banco TEXT,
        numero_cuenta TEXT,
        cbu TEXT,
        saldo_actual REAL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cheques (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo_cheque TEXT, -- Propio o Tercero
        banco TEXT,
        numero_cheque TEXT,
        importe REAL,
        fecha_emision TEXT,
        fecha_cobro TEXT,
        estado TEXT -- Carteras, Emitido, Depositado, Rechazado, Pagado
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS caja (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        concepto TEXT,
        ingreso REAL,
        egreso REAL,
        saldo REAL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS creditos_financieros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entidad TEXT,
        monto_otorgado REAL,
        tasa_interes REAL,
        vencimiento TEXT,
        estado TEXT
    );
    """)

    # 6. MÓDULO COMERCIAL, PROVEEDORES, CLIENTES Y CONTRATOS
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS proveedores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cuit TEXT UNIQUE,
        razon_social TEXT,
        condicion_iva TEXT,
        iibb_alicuota REAL,
        iibb_grupo TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cuit TEXT UNIQUE,
        razon_social TEXT,
        condicion_iva TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cuentas_corrientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entidad_tipo TEXT, -- 'PROVEEDOR' o 'CLIENTE'
        entidad_id INTEGER,
        fecha TEXT,
        tipo_comprobante TEXT, -- Factura, OP, Recibo, NC, ND
        numero_comprobante TEXT,
        debe REAL,
        haber REAL,
        saldo_acumulado REAL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stock_productos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT UNIQUE,
        nombre_producto TEXT,
        categoria TEXT, -- Semillas, Agroquímicos, Fertilizantes, Combustible
        unidad_medida TEXT, -- Litros, Kilos, Unidades
        stock_actual REAL,
        precio_reposicion REAL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ordenes_compra (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proveedor_id INTEGER,
        fecha TEXT,
        estado TEXT, -- Emitida, Recibida, Facturada
        FOREIGN KEY (proveedor_id) REFERENCES proveedores(id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS detalle_ordenes_compra (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        orden_compra_id INTEGER,
        producto_id INTEGER,
        cantidad REAL,
        precio_unitario REAL,
        lote_destino_id INTEGER,
        FOREIGN KEY (orden_compra_id) REFERENCES ordenes_compra(id),
        FOREIGN KEY (producto_id) REFERENCES stock_productos(id),
        FOREIGN KEY (lote_destino_id) REFERENCES lotes(id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contratos_alquiler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campo_id INTEGER,
        arrendatario TEXT,
        modalidad TEXT, -- Quintales de Soja/Trigo por Hectárea o Monto Fijo
        cantidad_quintales_ha REAL,
        vencimiento TEXT,
        FOREIGN KEY (campo_id) REFERENCES campos(id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contratos_granos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER,
        cultivo_id INTEGER,
        tipo_contrato TEXT, -- Forward, Disponible, Fasón
        toneladas REAL,
        precio_pactado REAL,
        fecha_entrega TEXT,
        FOREIGN KEY (cliente_id) REFERENCES clientes(id),
        FOREIGN KEY (cultivo_id) REFERENCES cultivos(id)
    );
    """)

    # 7. MÓDULO RECURSOS HUMANOS (EMPLEADOS)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS empleados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cuil TEXT UNIQUE,
        nombre_apellido TEXT,
        cargo TEXT, -- Encargado, Tractorista, Peón Rural, Administrativo
        sueldo_basico REAL,
        fecha_ingreso TEXT
    );
    """)

    # Iniciar catálogos por defecto (Cultivos agrícolas)
    cultivos_iniciales = [
        ("Soja 1ª", "Estivales"),
        ("Soja 2ª", "Estivales"),
        ("Maíz Temprano", "Estivales"),
        ("Maíz Tardío", "Estivales"),
        ("Maíz de 2ª", "Estivales"),
        ("Girasol", "Estivales"),
        ("Trigo", "Invernales"),
        ("Cebada", "Invernales")
    ]
    cursor.executemany("INSERT OR IGNORE INTO cultivos (nombre, tipo_ciclo) VALUES (?, ?);", cultivos_iniciales)

    conn.commit()
    conn.close()
    print("¡Base de datos 'campoplus.db' COMPLETA creada con todas las tablas del ERP Agropecuario!")

if __name__ == "__main__":
    crear_base_datos_completa()