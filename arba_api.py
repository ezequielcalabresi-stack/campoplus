import os
import sqlite3
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

CARPETA_DATOS = os.path.dirname(os.path.abspath(__file__))
RUTA_DB = os.path.join(CARPETA_DATOS, "padron.db")

URL_DB_NUBE = "PEGAR_AQUI_EL_ENLACE_COPIADO"

def asegurar_db():
    if not os.path.exists(RUTA_DB) or os.path.getsize(RUTA_DB) < 1000000:
        print("Descargando base de datos SQLite desde la nube...")
        try:
            # Requerido para que GitHub permita la descarga directa
            req = urllib.request.Request(
                URL_DB_NUBE, 
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req) as response, open(RUTA_DB, 'wb') as out_file:
                out_file.write(response.read())
            print("¡Base de datos descargada con éxito en la nube!")
        except Exception as e:
            print(f"Error al descargar la base de datos: {e}")

# Asegurar que la base exista antes de levantar el servidor
asegurar_db()

def obtener_conexion():
    conn = sqlite3.connect(RUTA_DB)
    conn.row_factory = sqlite3.Row
    return conn

print("Verificando base de datos SQLite...")
try:
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM padron;")
    total = cursor.fetchone()[0]
    print(f"¡Base de datos conectada con éxito! Total CUITs: {total}")
    conn.close()
except Exception as e:
    print(f"Aviso de base de datos: {e}")

class CentralARBAHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        
        if parsed_path.path == '/stats':
            total = 0
            try:
                conn = obtener_conexion()
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM padron;")
                total = cursor.fetchone()[0]
                conn.close()
            except:
                pass

            response_data = {"total": total}
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response_data, ensure_ascii=False).encode('utf-8'))

        elif parsed_path.path.startswith('/cuit/'):
            cuit_buscado = parsed_path.path.split('/')[-1].strip()
            
            resultado_db = None
            try:
                conn = obtener_conexion()
                cursor = conn.cursor()
                cursor.execute("SELECT ret_alicuota, ret_grupo, perc_alicuota, perc_grupo, estado FROM padron WHERE cuit = ?;", (cuit_buscado,))
                resultado_db = cursor.fetchone()
                conn.close()
            except Exception as e:
                print(f"Error consultando DB: {e}")

            if resultado_db:
                response_data = {
                    "encontrado": True,
                    "cuit": cuit_buscado,
                    "estado": resultado_db["estado"] or "Oficial ARBA",
                    "retencion": {"alicuota": resultado_db["ret_alicuota"] or "0,00", "grupo": resultado_db["ret_grupo"] or "00"},
                    "percepcion": {"alicuota": resultado_db["perc_alicuota"] or "0,00", "grupo": resultado_db["perc_grupo"] or "00"}
                }
            else:
                response_data = {
                    "encontrado": False,
                    "cuit": cuit_buscado,
                    "estado": "No encontrado",
                    "retencion": {"alicuota": "0,00", "grupo": "00"},
                    "percepcion": {"alicuota": "0,00", "grupo": "00"}
                }
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response_data, ensure_ascii=False).encode('utf-8'))
        else:
            self.send_response(404)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b"Endpoint no encontrado")

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, CentralARBAHandler)
    print(f"Servidor Central ARBA activo en puerto {PORT}")
    httpd.serve_forever()
