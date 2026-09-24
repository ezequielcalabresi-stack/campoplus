import sqlite3
import os
import pandas as pd

def importar_desde_tu_estructura():
    # Reemplazá con el nombre exacto de tu archivo Excel
    archivo_excel = "proveedores.xlsx" # Cambiá esto por tu archivo real
    
    if not os.path.exists(archivo_excel):
        print(f"⚠️ No se encuentra el archivo {archivo_excel}. Asegurate de guardarlo en la misma carpeta.")
        return

    print("Leyendo planilla Excel...")
    df = pd.read_excel(archivo_excel)

    conn = sqlite3.connect("campoplus.db")
    cursor = conn.cursor()

    # 1. Asegurar la tabla entidades con TODAS las columnas de tu estructura
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS entidades (
        cuit TEXT PRIMARY KEY,
        nombre_fantasia TEXT,
        razon_social TEXT,
        telefono TEXT,
        domicilio TEXT,
        localidad TEXT,
        codigo_postal TEXT,
        provincia TEXT,
        provincia_afip TEXT,
        ganancias TEXT,
        banco TEXT,
        sucursal TEXT,
        tipo_cta TEXT,
        nro_cta TEXT,
        cbu TEXT,
        email TEXT,
        es_cliente INTEGER DEFAULT 0,
        es_locador INTEGER DEFAULT 0,
        es_empleado INTEGER DEFAULT 0,
        es_proveedor INTEGER DEFAULT 1,
        condicion_iva TEXT
    );
    """)

    # Por si la tabla ya existía pero le faltaban columnas, las agregamos dinámicamente de forma segura
    columnas_existentes = [col[1] for col in cursor.execute("PRAGMA table_info(entidades);").fetchall()]
    columnas_necesarias = {
        'telefono': 'TEXT',
        'domicilio': 'TEXT',
        'localidad': 'TEXT',
        'codigo_postal': 'TEXT',
        'provincia': 'TEXT',
        'provincia_afip': 'TEXT',
        'ganancias': 'TEXT',
        'banco': 'TEXT',
        'sucursal': 'TEXT',
        'tipo_cta': 'TEXT',
        'nro_cta': 'TEXT',
        'cbu': 'TEXT',
        'email': 'TEXT',
        'es_cliente': 'INTEGER DEFAULT 0',
        'es_locador': 'INTEGER DEFAULT 0',
        'es_empleado': 'INTEGER DEFAULT 0',
        'condicion_iva': 'TEXT'
    }

    for col, tipo in columnas_necesarias.items():
        if col not in columnas_existentes:
            try:
                cursor.execute(f"ALTER TABLE entidades ADD COLUMN {col} {tipo};")
            except Exception as e:
                print(f"Nota al agregar columna {col}: {e}")

    contador = 0
    for index, row in df.iterrows():
        # Limpiar CUIT (11 dígitos)
        cuit_raw = str(row.get('CUIT', ''))
        cuit = ''.join(filter(str.isdigit, cuit_raw)).zfill(11)
        if len(cuit) != 11 or cuit == '00000000000':
            continue

        nombre_fantasia = str(row.get('Nombre Proveedor', '')).strip()
        nombre_real = str(row.get('Nombre Real', '')).strip().upper()
        razon_social = nombre_real if nombre_real and nombre_real != 'NAN' else nombre_fantasia.upper()
        
        telefono = str(row.get('Telefono', '')).strip()
        domicilio = str(row.get('Direccion', '')).strip()
        localidad = str(row.get('Localidad', '')).strip()
        cp = str(row.get('Cod Postal', '')).strip()
        provincia = str(row.get('Provincia', '')).strip()
        prov_afip = str(row.get('Cod Afip Provincia', '01')).strip().zfill(2)
        ganancias = str(row.get('Ganancias', '')).strip()
        
        banco = str(row.get('BANCO', '')).strip()
        sucursal = str(row.get('SUCURSAL', '')).strip()
        tipo_cta = str(row.get('Tipo de Cta', '')).strip()
        nro_cta = str(row.get('Nº Cta', '')).strip()
        cbu = str(row.get('CBU', '')).strip()
        email = str(row.get('eMAIL', '')).strip()
        condicion_iva = str(row.get('Cod AfipCondicion', '')).strip()

        def to_bool(val):
            val_str = str(val).strip().lower()
            return 1 if val_str in ['si', 'sí', '1', 'true', 's'] else 0

        es_cliente = to_bool(row.get('CLIENTE', 'No'))
        es_locador = to_bool(row.get('Es Locador?', 'No'))
        es_empleado = to_bool(row.get('Es Empleado?', 'No'))

        try:
            cursor.execute("""
            INSERT OR REPLACE INTO entidades (
                cuit, nombre_fantasia, razon_social, telefono, domicilio, localidad, 
                codigo_postal, provincia, provincia_afip, ganancias, banco, sucursal, 
                tipo_cta, nro_cta, cbu, email, es_cliente, es_locador, es_empleado, es_proveedor, condicion_iva
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?);
            """, (
                cuit, nombre_fantasia, razon_social, telefono, domicilio, localidad,
                cp, provincia, prov_afip, ganancias, banco, sucursal, tipo_cta, nro_cta,
                cbu, email, es_cliente, es_locador, es_empleado, condicion_iva
            ))
            contador += 1
        except Exception as e:
            print(f"Error al insertar CUIT {cuit}: {e}")

    conn.commit()
    conn.close()
    print(f"\n¡Importación masiva completada con éxito! Se procesaron y guardaron {contador} entidades en campoplus.db.")

if __name__ == "__main__":
    importar_desde_tu_estructura()