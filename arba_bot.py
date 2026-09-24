import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

CUIT = "30665193337"
CIT = "Esya1956"

def ejecutar_bot_arba():
    print("🤖 Iniciando navegador automatizado para ARBA...")
    
    options = webdriver.ChromeOptions()
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    try:
        print("🌐 Abriendo portal de autenticación de ARBA...")
        driver.get("https://www.arba.gov.ar/")
        
        wait = WebDriverWait(driver, 15)
        print("🔍 Buscando botón de acceso...")
        
        btn_ingresar = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), 'Ingresá')]")))
        btn_ingresar.click()
        
        print("⌨️ Inyectando CUIT y CIT...")
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

        print("🖱️ Haciendo clic en el botón de ingresar del formulario...")
        # Buscamos el botón de envío del formulario de login
        try:
            btn_submit = driver.find_element(By.XPATH, "//button[@type='submit'] | //input[@type='submit']")
            btn_submit.click()
        except:
            # Alternativa por si el botón tiene otro selector
            botones = driver.find_elements(By.TAG_NAME, "button")
            for b in botones:
                if "ingresar" in b.text.lower() or "acceder" in b.text.lower():
                    b.click()
                    break

        print("✅ ¡Login enviado con éxito! Esperando ingreso al panel principal...")
        
        # Pausa generosa para que visualices la sesión ya adentro de ARBA
        time.sleep(15)
        
    except Exception as e:
        print("❌ Error durante la automatización:", str(e))
    finally:
        print("Proceso finalizado.")
        driver.quit()

if __name__ == "__main__":
    ejecutar_bot_arba()