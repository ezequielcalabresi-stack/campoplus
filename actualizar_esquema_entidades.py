import sqlite3
import os

RUTA_DB = "campoplus.db"

def actualizar_tabla_entidades():
    if not os.path.exists(RUTA_DB):
        print("⚠️ No se encontró la base de datos campoplus.db. Ejecute primero crear_base.py")
        return
    
    conn = sqlite3.connect(RUTA_DB)
    cursor = conn.cursor()

    # Habilitar claves foráneas
    cursor.execute("PRAGMA foreign_keys = ON;")

    # Verificar si la tabla entidades existe y actualizar su estructura
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS entidades_nuevo (
        cuit TEXT PRIMARY KEY,
        nombre_fantasia TEXT,
        razon_social TEXT,
        domicilio TEXT,
        localidad TEXT,
        codigo_postal TEXT,
        provincia_id TEXT,
        es_proveedor INTEGER DEFAULT 1,
        es_cliente INTEGER DEFAULT 0,
        es_arrendatario INTEGER DEFAULT 0
    );
    """)

    # Si ya existía la tabla anterior 'entidades', migramos los datos existentes
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entidades';")
        if cursor.fetchone():
            print("Migrando datos de la tabla anterior a la nueva estructura...")
            cursor.execute("""
                INSERT OR IGNORE INTO entidades_nuevo (cuit, razon_social, domicilio, localidad, codigo_postal, provincia_id, es_proveedor, es_cliente, es_arrendatario)
                SELECT cuit, razon_social, domicilio, localidad, codigo_postal, provincia_id, es_proveedor, es_cliente, es_arrendatario 
                FROM entidades;
            """)
            cursor.execute("DROP TABLE entidades;")
        
        cursor.execute("ALTER TABLE entidades_nuevo RENAME TO entidades;")
        conn.commit()
        print("¡Estructura de 'entidades' actualizada con éxito con 'nombre_fantasia' y 'razon_social'! 🎉")
    
    except Exception as e:
        print(f"Error durante la migración: {e}")
        conn.rollback()
    
    conn.close()

if __name__ == "__main__":
    actualizar_tabla_entidades()