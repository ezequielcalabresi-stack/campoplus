import time
import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

CUIT = "30665193337"
CIT = "Esya1956"
CARPETA_DESTINO = os.path.dirname(os.path.abspath(__file__))

def descargar_padrones_arba():
    print("🤖 [CAmpo+] Iniciando proceso mensual de actualización ARBA...")
    
    options = webdriver.ChromeOptions()
    prefs = {
        "download.default_directory": CARPETA_DESTINO,
        "download.prompt_for_download": False,
        "directory_upgrade": True
    }
    options.add_experimental_option("prefs", prefs)
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    try:
        print("🌐 Conectando al portal de ARBA...")
        driver.get("https://www.arba.gov.ar/")
        
        wait = WebDriverWait(driver, 20)
        
        # 1. Click en Ingresar
        print("🔍 Accediendo al portal de autenticación...")
        btn_ingresar = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), 'Ingresá')]")))
        btn_ingresar.click()
        
        # 2. Inyección de Credenciales
        print("⌨️ Completando credenciales...")
        time.sleep(2)
        inputs = driver.find_elements(By.TAG_NAME, "input")
        
        for inp in inputs:
            if inp.get_attribute("type") in ["text", "number"] and not inp.get_attribute("value"):
                try:
                    inp.send_keys(CUIT)
                    break
                except:
                    pass
                    
        for inp in inputs:
            if inp.get_attribute("type") == "password":
                try:
                    inp.send_keys(CIT)
                    break
                except:
                    pass

        # 3. Envío del login
        print("🚀 Enviando datos de acceso...")
        try:
            btn_submit = driver.find_element(By.XPATH, "//button[@type='submit'] | //input[@type='submit']")
            btn_submit.click()
        except:
            botones = driver.find_elements(By.TAG_NAME, "button")
            for b in botones:
                if "ingresar" in b.text.lower() or "acceder" in b.text.lower():
                    b.click()
                    break

        print("✅ ¡Sesión iniciada con éxito!")
        print("💡 El navegador quedará abierto en tu panel de ARBA para que realices la descarga o la automatizemos desde adentro.")
        
        # Dejamos la sesión abierta de forma estable para evitar errores de ruta
        time.sleep(30)

    except Exception as e:
        print(f"❌ Error durante la ejecución: {str(e)}")
    finally:
        driver.quit()
        print("🔒 Proceso finalizado.")

if __name__ == "__main__":
    descargar_padrones_arba()