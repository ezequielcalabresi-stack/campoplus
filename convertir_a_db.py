import json
import sqlite3
import os

print("Leyendo el archivo JSON (esto puede tardar unos segundos por el tamaño)...")
with open("padron_unificado.json", "r", encoding="utf-8") as f:
    data = json.load(f)

print(f"JSON cargado. Total de registros: {len(data)}. Creando base de datos SQLite...")

if os.path.exists("padron.db"):
    os.remove("padron.db")

conn = sqlite3.connect("padron.db")
cursor = conn.cursor()

# Crear tabla optimizada
cursor.execute("""
    CREATE TABLE IF NOT EXISTS padron (
        cuit TEXT PRIMARY KEY,
        estado TEXT,
        ret_alicuota TEXT,
        ret_grupo TEXT,
        perc_alicuota TEXT,
        perc_grupo TEXT
    )
""")

registros = []
for cuit, info in data.items():
    estado = info.get("estado", "Oficial ARBA")
    ret = info.get("ret", {})
    perc = info.get("perc", {})
    
    ret_alicuota = ret.get("alicuota", "0,00") if isinstance(ret, dict) else "0,00"
    ret_grupo = ret.get("grupo", "00") if isinstance(ret, dict) else "00"
    
    perc_alicuota = perc.get("alicuota", "0,00") if isinstance(perc, dict) else "00"
    perc_grupo = perc.get("grupo", "00") if isinstance(perc, dict) else "00"
    
    registros.append((cuit, estado, ret_alicuota, ret_grupo, perc_alicuota, perc_grupo))

print("Insertando registros en la base de datos (por favor aguardá un momento)...")
cursor.executemany("""
    INSERT OR REPLACE INTO padron (cuit, estado, ret_alicuota, ret_grupo, perc_alicuota, perc_grupo)
    VALUES (?, ?, ?, ?, ?, ?)
""", registros)

conn.commit()
conn.close()
print("¡Listo! Archivo 'padron.db' creado con éxito en esta carpeta.")