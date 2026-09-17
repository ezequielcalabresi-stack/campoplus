import os
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

CARPETA_DATOS = os.path.dirname(os.path.abspath(__file__))
RUTA_JSON = os.path.join(CARPETA_DATOS, "padron_unificado.json")

# Cargar el padrón en memoria RAM una sola vez al encender el servidor
print("Cargando padrón unificado en memoria central...")
try:
    with open(RUTA_JSON, 'r', encoding='utf-8') as f:
        PADRON_GLOBAL = json.load(f)
    print(f"¡Padrón central cargado con éxito! Total CUITs: {len(PADRON_GLOBAL)}")
except Exception as e:
    print(f"Error al cargar el padrón: {e}")
    PADRON_GLOBAL = {}

class CentralARBAHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        
        # Endpoint de consulta: /cuit/XXXXXXXXXXX
        if parsed_path.path.startswith('/cuit/'):
            cuit_buscado = parsed_path.path.split('/')[-1].strip()
            
            resultado = PADRON_GLOBAL.get(cuit_buscado, {
                "ret": {"alicuota": "0,00", "grupo": "00"},
                "perc": {"alicuota": "0,00", "grupo": "00"}
            })
            
            response_data = {
                "encontrado": cuit_buscado in PADRON_GLOBAL,
                "cuit": cuit_buscado,
                "retencion": resultado.get("ret"),
                "percepcion": resultado.get("perc")
            }
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            # CORS abierto para que cualquier tablet o app cliente pueda consultar sin bloqueo
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response_data, ensure_ascii=False).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Endpoint no encontrado")

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, CentralARBAHandler)
    print(f"Servidor Central ARBA activo en puerto {PORT}")
    httpd.serve_forever()