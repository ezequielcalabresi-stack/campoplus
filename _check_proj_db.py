# -*- coding: utf-8 -*-
import os, sqlite3
import pandas as pd

os.chdir(os.path.dirname(os.path.abspath(__file__)))
c = sqlite3.connect("campoplus.db")
c.row_factory = sqlite3.Row
cur = c.cursor()

# Cheques Empresa in DB by nro
cur.execute(
    """
    SELECT fecha_debito, proveedor, haber, cta_cte_nro, nro_cheque, tipo_operacion
    FROM movimientos_cta_cte_bancos
    WHERE COALESCE(nro_cheque,'') IN ('2273','2274','2108','2245')
       OR (COALESCE(fecha_debito,'') >= '2026-09-01'
           AND lower(COALESCE(tipo_operacion,'')) LIKE '%cheque%'
           AND COALESCE(haber,0) > 0)
    ORDER BY fecha_debito LIMIT 20
    """
)
print("db cheques empresa-ish:")
for r in cur.fetchall():
    print(dict(r))

cur.execute(
    """
    SELECT COUNT(*) n FROM movimientos_cta_cte_bancos
    WHERE COALESCE(fecha_debito,'') BETWEEN '2026-09-01' AND '2026-09-30'
      AND COALESCE(haber,0) > 0
    """
)
print("sep2026 haber movs", cur.fetchone()["n"])

# Does import store Forma de Pago / Proyeccion?
cur.execute("PRAGMA table_info(movimientos_cta_cte_bancos)")
print([r[1] for r in cur.fetchall()])
