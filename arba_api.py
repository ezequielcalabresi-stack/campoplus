import os
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

CARPETA_DATOS = os.path.dirname(os.path.abspath(__file__))
RUTA_DB = os.path.join(CARPETA_DATOS, "padron.db")

def obtener_conexion():
    conn = sqlite3.connect(RUTA_DB)
    conn.row_factory = sqlite3.Row
    return conn

# Verificar estado inicial de la base de datos
print("Verificando base de datos SQLite...")
try:
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='padron';")
    tabla_existe = cursor.fetchone()[0]
    
    if tabla_existe:
        cursor.execute("SELECT COUNT(*) FROM padron;")
        total = cursor.fetchone()[0]
        print(f"¡Base de datos conectada con éxito! Total CUITs en SQLite: {total}")
    else:
        print("⚠️ La tabla 'padron' aún no fue creada. Subí tu padrón a SQLite.")
    conn.close()
except Exception as e:
    print(f"Error al conectar con la base de datos: {e}")

class CentralARBAHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        
        # Endpoint de estadísticas: /stats
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

        # Endpoint de consulta: /cuit/XXXXXXXXXXX
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