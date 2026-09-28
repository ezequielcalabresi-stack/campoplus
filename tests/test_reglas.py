# -*- coding: utf-8 -*-
"""Reglas que no tienen que volver a romperse: retenciones, packs y ratios."""
import sqlite3
import unittest
from datetime import date

from actividad_modulos import liquidacion_habilitada, modulos_con_actividad
from financiero_impuestos import vencimiento_percepcion_mensual, vencimiento_retencion_quincenal
from modulos_guard import modulo_de_ruta
from ratios_eecc import guardar_eecc, panel_ratios


class Retenciones(unittest.TestCase):
    def test_quincena_y_percepcion(self):
        self.assertEqual(vencimiento_retencion_quincenal(date(2026, 3, 10)), date(2026, 3, 20))
        self.assertEqual(vencimiento_retencion_quincenal(date(2026, 3, 16)), date(2026, 4, 5))
        self.assertEqual(vencimiento_percepcion_mensual(date(2026, 3, 28)), date(2026, 4, 5))


class Packs(unittest.TestCase):
    def test_liquidacion_pide_pack_y_actividad(self):
        flags = {"mod_liquidaciones": 1, "mod_ganaderia": 1, "mod_tambo": 1, "mod_agro": 0}
        self.assertTrue(liquidacion_habilitada("HACIENDA", flags, {"mod_ganaderia"}))
        self.assertFalse(liquidacion_habilitada("LECHE", flags, {"mod_ganaderia"}))
        self.assertFalse(liquidacion_habilitada("GRANO", flags, {"mod_agro"}))

    def test_actividades_cargadas(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE actividades (id INTEGER PRIMARY KEY, empresa_id INTEGER, nombre TEXT, activo INTEGER)"
        )
        conn.execute("INSERT INTO actividades VALUES (1, 1, 'TAMBO', 0)")
        conn.execute("INSERT INTO actividades VALUES (2, 1, 'Ganaderia', 1)")
        mods = modulos_con_actividad(conn, 1)
        self.assertIn("mod_ganaderia", mods)
        self.assertNotIn("mod_tambo", mods)

    def test_ruta_sin_pack(self):
        self.assertEqual(modulo_de_ruta("/api/tambo/resumen"), "mod_tambo")
        self.assertEqual(modulo_de_ruta("/api/bancos/cuentas"), "mod_bancos")
        self.assertIsNone(modulo_de_ruta("/api/auth/login"))
        self.assertIsNone(modulo_de_ruta("/api/actividades"))


class Ratios(unittest.TestCase):
    def test_moneda_homogenea_desde_el_momento_0(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE plan_de_cuentas (
                id INTEGER PRIMARY KEY, codigo_cuenta TEXT, nombre_cuenta TEXT,
                tipo_cuenta TEXT, activa INTEGER DEFAULT 1
            );
            CREATE TABLE asientos_contables (
                id INTEGER PRIMARY KEY, fecha TEXT, empresa_id INTEGER,
                anulado INTEGER DEFAULT 0, centro_costo TEXT DEFAULT '1'
            );
            CREATE TABLE detalles_asiento (
                id INTEGER PRIMARY KEY, asiento_id INTEGER, cuenta_id INTEGER,
                debe REAL, haber REAL
            );
            """
        )
        guardar_eecc(conn, 1, {
            "ejercicio": "2022/2023", "fecha_cierre": "2023-06-30", "cerrado": 1,
            "activo_corriente": 100, "pasivo_corriente": 40, "patrimonio": 70,
            "ingresos": 80, "gastos": 60, "resultado": 20, "indice_cierre": 100,
        })
        guardar_eecc(conn, 1, {
            "ejercicio": "2023/2024", "fecha_cierre": "2024-06-30", "cerrado": 1,
            "activo_corriente": 120, "pasivo_corriente": 30, "patrimonio": 90,
            "ingresos": 100, "gastos": 70, "resultado": 30, "indice_cierre": 200,
        })
        panel = panel_ratios(conn, 1, hoy=date(2026, 9, 27))
        self.assertEqual(panel["momento_0"], "2022/2023")
        primero, segundo = panel["serie"]
        self.assertEqual(primero["resultado_homogeneo"], 40.0)
        self.assertEqual(segundo["resultado_homogeneo"], 30.0)
        roe = next(r["valor"] for r in primero["ratios"] if r["codigo"] == "roe")
        self.assertAlmostEqual(roe, round(20 / 70, 4))


if __name__ == "__main__":
    unittest.main()
