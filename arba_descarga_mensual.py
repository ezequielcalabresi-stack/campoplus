# -*- coding: utf-8 -*-
"""
Asistente de descarga mensual ARBA.
No guarda contraseñas en el código: usa CUIT/CIT de configuracion_empresa (campoplus.db).
Abre el portal DFE para que completes la descarga si el sitio cambia el flujo.
Después: copiá los TXT a padrones_arba/ o usá Arba.html → Importar.
"""
import os
import sqlite3
import time

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "campoplus.db")
DEST = os.path.join(BASE, "padrones_arba")


def credenciales():
    if not os.path.exists(DB):
        return "", ""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT cuit, cit_arba FROM configuracion_empresa WHERE id=1;").fetchone()
    conn.close()
    if not row:
        return "", ""
    return (str(row["cuit"] or "").replace("-", ""), str(row["cit_arba"] or ""))


def main():
    os.makedirs(DEST, exist_ok=True)
    cuit, cit = credenciales()
    print("CAmpo+ — Asistente padrón ARBA")
    print(f"Carpeta destino descargas: {DEST}")
    if not cuit or not cit or cit.startswith("CIT-ARBA-"):
        print("⚠ Configurá CUIT y CIT real en ConfiguracionEmpresa.html antes de automatizar el login.")
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
    except ImportError:
        print("Instalá selenium y webdriver-manager: pip install selenium webdriver-manager")
        print("Luego descargá manualmente Ret/Perc desde DFE ARBA y usá Arba.html para importar.")
        return

    opts = webdriver.ChromeOptions()
    opts.add_experimental_option("prefs", {
        "download.default_directory": DEST,
        "download.prompt_for_download": False,
        "directory_upgrade": True,
    })
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    try:
        driver.get("https://www.arba.gov.ar/")
        print("Navegador abierto en ARBA. Completá login DFE y descargá:")
        print("  - Padrón Retenciones RGS (PadronRGSRet*.TXT)")
        print("  - Padrón Percepciones RGS (PadronRGSPer*.TXT)")
        print(f"Guardalos en: {DEST}")
        print("Después abrí Arba.html → Importar detectados.")
        if cuit and cit and not cit.startswith("CIT-ARBA-"):
            wait = WebDriverWait(driver, 25)
            try:
                wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(translate(., 'INGRESÁINGRESA', 'ingresaingresa'), 'ingres')]"))).click()
                time.sleep(2)
                for inp in driver.find_elements(By.TAG_NAME, "input"):
                    t = (inp.get_attribute("type") or "").lower()
                    if t in ("text", "number", "tel") and not (inp.get_attribute("value") or "").strip():
                        try:
                            inp.send_keys(cuit)
                            break
                        except Exception:
                            pass
                for inp in driver.find_elements(By.TAG_NAME, "input"):
                    if (inp.get_attribute("type") or "").lower() == "password":
                        try:
                            inp.send_keys(cit)
                            break
                        except Exception:
                            pass
            except Exception as e:
                print("Login automático parcial:", e)
        time.sleep(120)
    finally:
        driver.quit()
        print("Listo. Importá los TXT desde Arba.html")


if __name__ == "__main__":
    main()
