# -*- coding: utf-8 -*-
import os, sqlite3
from pathlib import Path
import pandas as pd

os.chdir(os.path.dirname(os.path.abspath(__file__)))
c = sqlite3.connect("campoplus.db")
c.row_factory = sqlite3.Row
cur = c.cursor()

cur.execute(
    """
    SELECT fecha_debito, fecha_cobro, proveedor, haber, debe, cta_cte_nro, tipo_operacion, nro_cheque
    FROM movimientos_cta_cte_bancos
    WHERE lower(COALESCE(proveedor,'')) LIKE '%sueldo%'
       OR lower(COALESCE(proveedor,'')) LIKE '%extra%'
       OR lower(COALESCE(proveedor,'')) LIKE '%sindicato%'
       OR lower(COALESCE(proveedor,'')) LIKE '%pago extras%'
    ORDER BY COALESCE(fecha_debito, fecha_cobro) DESC
    LIMIT 30
    """
)
print("=== movs sueldo/extra ===")
for r in cur.fetchall():
    print(dict(r))

cur.execute(
    """
    SELECT proveedor, COUNT(*) n, ROUND(SUM(COALESCE(debe,0)),2) d, ROUND(SUM(COALESCE(haber,0)),2) h
    FROM movimientos_cta_cte_bancos
    WHERE lower(COALESCE(proveedor,'')) LIKE '%extra%'
       OR lower(COALESCE(proveedor,'')) LIKE '%sueldo%'
       OR lower(COALESCE(proveedor,'')) LIKE '%sindicato%'
    GROUP BY proveedor ORDER BY n DESC LIMIT 40
    """
)
print("=== group ===")
for r in cur.fetchall():
    print(dict(r))

# cheques emitidos detail
cur.execute(
    """
    SELECT nro_cheque, banco, fecha_pago, monto, estado, tipo, librador, titular, cuit_emisor
    FROM cartera_cheques
    WHERE UPPER(COALESCE(tipo,'')) LIKE '%EMIT%' OR UPPER(COALESCE(estado,'')) LIKE '%EMIT%'
    """
)
print("=== cheques emit ===")
for r in cur.fetchall():
    print(dict(r))

# look for Cheques Empresa pattern in movimientos (emitted company checks as future debits)
cur.execute(
    """
    SELECT fecha_debito, proveedor, haber, debe, cta_cte_nro, nro_cheque, tipo_operacion
    FROM movimientos_cta_cte_bancos
    WHERE COALESCE(nro_cheque,'') != ''
      AND COALESCE(fecha_debito,'') >= '2026-09-01'
      AND COALESCE(debe,0) > 0
    ORDER BY fecha_debito LIMIT 15
    """
)
print("=== future debit with cheque ===")
for r in cur.fetchall():
    print(dict(r))

# xlsx files
for p in sorted(Path("tablas").glob("*.xlsx")):
    if p.name.startswith("~$"):
        continue
    try:
        xl = pd.ExcelFile(p)
        print(p.name, xl.sheet_names[:12])
    except Exception as e:
        print(p.name, "ERR", e)
