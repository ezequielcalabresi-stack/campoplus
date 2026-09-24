import requests
import json

class ARBAWebService:
    def __init__(self, cuit, cit):
        self.cuit = cuit.replace("-", "")
        self.cit = cit
        # Endpoint oficial de consulta de alícuotas / padrones de ARBA
        self.url_base = "https://dfe.arba.gov.ar/padrones/v1/alicuotas" # O endpoint oficial de la API de ARBA
        
    def consultar_alicuotas(self, cuit_a_consultar):
        """
        Consulta en tiempo real y de forma automatizada las alícuotas 
        de retención y percepción directamente al servidor oficial de ARBA.
        """
        cuit_objetivo = cuit_a_consultar.replace("-", "")
        
        # Cabeceras y autenticación con CIT
        headers = {
            "Content-Type": "application/json",
            "X-CUIT": self.cuit,
            "X-CIT": self.cit
        }
        
        payload = {
            "cuitContribuyente": cuit_objetivo
        }

        try:
            # Petición oficial al Web Service de ARBA
            response = requests.post(self.url_base, json=payload, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "exito": True,
                    "cuit": cuit_objetivo,
                    "retencion": data.get("alicuotaRetencion", "0,00 %"),
                    "percepcion": data.get("alicuotaPercepcion", "0,00 %"),
                    "estado": data.get("estadoContribuyente", "Oficial ARBA")
                }
            else:
                return {
                    "exito": False, 
                    "error": f"Error HTTP {response.status_code}: {response.text}"
                }
        except requests.exceptions.RequestException as e:
            return {
                "exito": False, 
                "error": f"Falla de conexión con el Web Service de ARBA: {str(e)}"
            }

if __name__ == "__main__":
    print("--- CAmpo+ Conector Oficial ARBA (Web Service) ---")
    mi_cuit = input("Ingrese su CUIT (contribuyente): ").strip()
    mi_cit = input("Ingrese su CIT de ARBA: ").strip()
    
    arba = ARBAWebService(mi_cuit, mi_cit)
    
    objetivo = input("Ingrese el CUIT a consultar: ").strip()
    resultado = arba.consultar_alicuotas(objetivo)
    
    print("\nResultado de la consulta oficial:")
    print(json.dumps(resultado, indent=4, ensure_ascii=False))