import sqlite3
import pandas as pd
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "campoplus.db")
EXCEL_RETENCIONES = os.path.join(BASE_DIR, "retenciones.xlsx")

def obtener_codigo_regimen(descripcion):
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

def forzar_carga_historica():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Recrear tabla de retenciones limpiamente
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS retenciones_sicore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            razon_social TEXT,
            cuit TEXT,
            base_imponible REAL,
            regimen TEXT,
            importe_retenido REAL,
            nro_comprobante TEXT,
            tipo_retencion TEXT DEFAULT 'SICORE'
        );
    """)
    conn.commit()

    if not os.path.exists(EXCEL_RETENCIONES):
        print(f"❌ Error: No se encuentra el archivo {EXCEL_RETENCIONES}")
        return

    print("⏳ Leyendo e importando retenciones.xlsx...")
    xls_ret = pd.ExcelFile(EXCEL_RETENCIONES)
    df_ret = pd.read_excel(xls_ret, sheet_name=0)

    insertados = 0
    for idx, row in df_ret.iterrows():
        fec_raw = row.get('Fecha Operacion', '')
        if pd.notnull(fec_raw) and str(fec_raw).strip() != '':
            fec_dt = pd.to_datetime(fec_raw, dayfirst=True, errors='coerce')
            fec = fec_dt.strftime('%Y-%m-%d') if pd.notnull(fec_dt) else ''
        else:
            fec = ''

        prov = str(row.get('Nombre Proveedor', '')).strip()
        cuit = str(row.get('Cuit Sujeto a Retencion', '')).strip()
        base = float(row.get('BAse Imponible', 0) or 0)
        imp = float(row.get('Importe Retenido', 0) or 0)
        reg_desc = str(row.get('REgimen Retencion', '06'))
        reg = obtener_codigo_regimen(reg_desc)

        nro_id = int(row.get('id', 0) or 0)
        nro_comp = f"2026-{nro_id:04d}" if nro_id > 0 else f"2026-HIST-{idx+1:04d}"

        # Insertar permitiendo a SQLite gestionar la Clave Primaria autoincremental
        cursor.execute("""
            INSERT INTO retenciones_sicore (fecha, razon_social, cuit, base_imponible, regimen, importe_retenido, nro_comprobante, tipo_retencion)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'SICORE');
        """, (fec, prov, cuit, base, reg, imp, nro_comp))
        insertados += 1

    conn.commit()
    conn.close()
    print(f"✅ ¡ÉXITO! Se han cargado e insertado {insertados} registros históricos en campoplus.db.")

if __name__ == "__main__":
    forzar_carga_historica()