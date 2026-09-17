import os

class IndexadorARBA:
    def __init__(self):
        self.padron_retenciones = {}
        self.padron_percepciones = {}

    def cargar_padrones(self, ruta_ret, ruta_perc):
        print("🔄 Procesando padrones oficiales de ARBA...")
        
        # 1. Cargar Retenciones ('R')
        if os.path.exists(ruta_ret):
            with open(ruta_ret, 'r', encoding='latin-1') as f:
                count = 0
                for linea in f:
                    partes = linea.strip().split(';')
                    if len(partes) >= 10 and partes[0] == 'R':
                        cuit = partes[4].strip()
                        alicuota = partes[8].strip().replace(',', '.')
                        grupo = partes[9].strip()
                        self.padron_retenciones[cuit] = {
                            "alicuota": alicuota,
                            "grupo": grupo
                        }
                        count += 1
            print(f"✅ Retenciones indexadas: {count} registros.")

        # 2. Cargar Percepciones ('P')
        if os.path.exists(ruta_perc):
            with open(ruta_perc, 'r', encoding='latin-1') as f:
                count = 0
                for linea in f:
                    partes = linea.strip().split(';')
                    if len(partes) >= 10 and partes[0] == 'P':
                        cuit = partes[4].strip()
                        alicuota = partes[8].strip().replace(',', '.')
                        grupo = partes[9].strip()
                        self.padron_percepciones[cuit] = {
                            "alicuota": alicuota,
                            "grupo": grupo
                        }
                        count += 1
            print(f"✅ Percepciones indexadas: {count} registros.")

    def consultar_cuit(self, cuit_buscado):
        cuit_limpio = cuit_buscado.strip().replace('-', '').lstrip('0')
        
        # Buscar en Retenciones
        ret = self.padron_retenciones.get(cuit_limpio, {"alicuota": "0.00", "grupo": "00"})
        # Buscar en Percepciones
        perc = self.padron_percepciones.get(cuit_limpio, {"alicuota": "0.00", "grupo": "00"})
        
        encontrado = cuit_limpio in self.padron_retenciones or cuit_limpio in self.padron_percepciones
        
        return {
            "encontrado": encontrado,
            "cuit": cuit_buscado,
            "datos": {
                "estado": "Padrón Oficial ARBA" if encontrado else "Régimen General / Sin Alícuota Diferencial",
                "ret": f"{ret['alicuota'].replace('.', ',')} %",
                "perc": f"{perc['alicuota'].replace('.', ',')} %",
                "grupo_ret": ret['grupo'],
                "grupo_perc": perc['grupo']
            }
        }