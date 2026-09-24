# -*- coding: utf-8 -*-
import os, sqlite3
import pandas as pd

os.chdir(os.path.dirname(os.path.abspath(__file__)))
c = sqlite3.connect("campoplus.db")
c.row_factory = sqlite3.Row
cur = c.cursor()

cur.execute(
    """
    SELECT COUNT(*) n FROM movimientos_cta_cte_bancos
    WHERE lower(COALESCE(proveedor,'')) LIKE '%pago extras%'
       OR lower(COALESCE(proveedor,'')) LIKE '%extras %'
    """
)
print("db pago extras", cur.fetchone()["n"])

# check Proyeccion column meaning in excel for future items
df = pd.read_excel("tablas/movimientos bancarios.xlsx", "movimientos bancarios", usecols=[
    "Proveedor", "HABER", "DEBE", "FechaCobro", "Fecha", "Forma de Pago", "Cta Cte",
    "Nº Cheque", "Proyeccion", "Debitados", "Intereses", "Impuestos", "Cargos",
    "Nº Cuota", "TC", "Plazo", "Nro de Credito", "Capital U$S", "Importe Monto origen $"
])
# Future projected: FechaCobro >= 2026-09-01 and Proyeccion?
fut = df[pd.to_datetime(df["FechaCobro"], errors="coerce") >= "2026-09-01"].copy()
print("future FechaCobro rows", len(fut))
print("Forma de Pago value counts:", fut["Forma de Pago"].astype(str).value_counts().head(15).to_string())
print("Proyeccion:", fut["Proyeccion"].astype(str).value_counts().head(10).to_string())

# Cheques empresa-like: forma cheque and haber
chq = fut[fut["Forma de Pago"].astype(str).str.lower().str.contains("cheque", na=False)]
print("future cheques", len(chq))
print(chq[["Proveedor", "FechaCobro", "HABER", "Cta Cte", "Nº Cheque", "Forma de Pago"]].head(15).to_string())

extras = fut[fut["Proveedor"].astype(str).str.lower().str.contains("pago extras|extras ", na=False)]
print("future extras", len(extras))
print(extras[["Proveedor", "FechaCobro", "HABER", "Cta Cte"]].head(15).to_string())

sueldos = fut[fut["Proveedor"].astype(str).str.lower().str.contains("sueldo|sindicato", na=False)]
print("future sueldos/sindicatos", len(sueldos))
print(sueldos[["Proveedor", "FechaCobro", "HABER", "Cta Cte", "Forma de Pago"]].head(20).to_string())

# How many "Cheques Empresa" style - maybe Tipo de Comprobante or Forma
print("sample proveedores cheque", chq["Proveedor"].astype(str).head(20).tolist())
