# -*- coding: utf-8 -*-
import os, sqlite3
from pathlib import Path
import pandas as pd

os.chdir(os.path.dirname(os.path.abspath(__file__)))
c = sqlite3.connect("campoplus.db")
c.row_factory = sqlite3.Row
cur = c.cursor()

cur.execute("PRAGMA table_info(movimientos_cta_cte_bancos)")
cols = [r[1] for r in cur.fetchall()]
print("mov cols", cols)

text_cols = [x for x in cols if x.lower() in (
    "detalle", "concepto", "observaciones", "descripcion", "beneficiario",
    "razon_social", "proveedor", "varios", "tipo_mov", "categoria", "nota"
)]
print("text cols", text_cols)

expr = "||".join([f"COALESCE({x},'')" for x in (text_cols or ["detalle"])])
for kw in ("extra", "sueldo", "sindicato", "roldan", "lopez martin", "lipera"):
    cur.execute(
        f"SELECT COUNT(*) n FROM movimientos_cta_cte_bancos WHERE lower({expr}) LIKE ?",
        (f"%{kw}%",),
    )
    print(kw, cur.fetchone()["n"])

cur.execute(
    f"""
    SELECT fecha, haber, debe, {', '.join(text_cols[:6])}
    FROM movimientos_cta_cte_bancos
    WHERE lower({expr}) LIKE '%extra%' OR lower({expr}) LIKE '%sueldo%'
    ORDER BY fecha DESC LIMIT 20
    """
)
print("samples extras/sueldos:")
for r in cur.fetchall():
    print(dict(r))

# also search cuentas_corrientes
cur.execute(
    """
    SELECT COUNT(*) n FROM cuentas_corrientes
    WHERE lower(COALESCE(tipo_comprobante,'')||COALESCE(numero_comprobante,'')||COALESCE(usuario_registro,''))
          LIKE '%extra%' OR lower(COALESCE(tipo_comprobante,'')) LIKE '%sueldo%'
    """
)
print("cc extras-ish", cur.fetchone()["n"])

# list tablas xlsx for sueldos
for p in Path("tablas").glob("*.xlsx"):
    if p.name.startswith("~$"):
        continue
    try:
        xl = pd.ExcelFile(p)
    except Exception as e:
        print("skip", p.name, e)
        continue
    hits = [s for s in xl.sheet_names if any(k in s.lower() for k in ("sueld", "emple", "extra", "financ", "estado", "personal"))]
    if hits or "mov" in p.name.lower() or "banco" in p.name.lower():
        print(p.name, "->", hits or xl.sheet_names[:8])
