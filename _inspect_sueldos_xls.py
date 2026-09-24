# -*- coding: utf-8 -*-
import os
import pandas as pd

os.chdir(os.path.dirname(os.path.abspath(__file__)))
xl = pd.ExcelFile("tablas/tablas1xlsx.xlsx")
print("sheets", xl.sheet_names)
for s in ["empleados", "Sueldos conceptos", "conformacion sueldos"]:
    df = pd.read_excel(xl, s, nrows=5)
    print("\n===", s, "===")
    print(list(df.columns))
    print(df.head(3).to_string())

# count rows
for s in ["empleados", "Sueldos conceptos", "conformacion sueldos"]:
    df = pd.read_excel(xl, s)
    print(s, "rows", len(df))

# search Pago Extras in movimientos
dfm = pd.read_excel("tablas/movimientos bancarios.xlsx", "movimientos bancarios", nrows=3)
print("\nmov banc cols", list(dfm.columns))
dfm = pd.read_excel("tablas/movimientos bancarios.xlsx", "movimientos bancarios")
# find text cols with Extra
cols = [c for c in dfm.columns if dfm[c].dtype == object or str(dfm[c].dtype).startswith("string")]
print("obj cols", cols[:15])
mask = False
for c in cols[:8]:
    m = dfm[c].astype(str).str.lower().str.contains("pago extras|extras ", na=False)
    if m.any():
        print("hits in", c, int(m.sum()))
        print(dfm.loc[m, list(dfm.columns)[:10]].head(8).to_string())
        break
else:
    # try all
    for c in dfm.columns:
        try:
            m = dfm[c].astype(str).str.lower().str.contains("pago extras", na=False)
            if m.any():
                print("FOUND", c, int(m.sum()))
                print(dfm.loc[m].head(5).to_string())
                break
        except Exception:
            pass
