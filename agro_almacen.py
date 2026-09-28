# -*- coding: utf-8 -*-
"""
CAmpo+ — Almacén agropecuario (insumos + laboreos de terceros).
Los productos ingresan a costo NETO (sin impuestos).
Los laboreos de terceros también se 'almacenan' como unidades de costo
para imputarlos a lotes vía Órdenes de Trabajo.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional


def init_almacen_schema(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS almacen_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            tipo TEXT NOT NULL DEFAULT 'producto',
            -- producto | laboreo
            codigo TEXT,
            nombre TEXT NOT NULL,
            categoria TEXT,
            -- Semillas, Agroquímicos, Fertilizantes, Combustible, Laboreo
            unidad TEXT DEFAULT 'Kg',
            -- Kg, Lt, Un, Has, Hs
            stock_cantidad REAL DEFAULT 0,
            costo_promedio_neto REAL DEFAULT 0,
            -- siempre sin impuestos
            activo INTEGER DEFAULT 1,
            notas TEXT
        );
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_almacen_item_empresa_codigo
        ON almacen_items(empresa_id, codigo) WHERE codigo IS NOT NULL AND TRIM(codigo) != '';
    """)

    # Catálogo de categorías (extensible) + flag aplica_ot
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS almacen_categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT NOT NULL UNIQUE,
            nombre TEXT NOT NULL,
            aplica_ot INTEGER NOT NULL DEFAULT 1,
            orden INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1
        );
    """)
    cats_seed = [
        ("agroquimicos", "Agroquímicos", 1, 10),
        ("fertilizantes", "Fertilizantes", 1, 20),
        ("semillas", "Semillas", 1, 30),
        ("laboreos", "Laboreos / Servicios", 1, 40),
        ("maquinarias", "Maquinarias (lubricantes, combustibles)", 0, 50),
        ("ganaderia", "Ganadería (vacunas, sanidad)", 0, 60),
        ("varios", "Varios", 0, 90),
    ]
    for codigo, nombre, aplica, orden in cats_seed:
        cursor.execute(
            """
            INSERT OR IGNORE INTO almacen_categorias (codigo, nombre, aplica_ot, orden, activo)
            VALUES (?, ?, ?, ?, 1);
            """,
            (codigo, nombre, aplica, orden),
        )

    # asegurar columna categoria_codigo en items
    cursor.execute("PRAGMA table_info(almacen_items);")
    cols = {r[1] for r in cursor.fetchall()}
    if "categoria_codigo" not in cols:
        cursor.execute("ALTER TABLE almacen_items ADD COLUMN categoria_codigo TEXT;")
    if "clasificacion_manual" not in cols:
        cursor.execute("ALTER TABLE almacen_items ADD COLUMN clasificacion_manual INTEGER DEFAULT 0;")
    _reclasificar_categorias_items(cursor)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS almacen_movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            item_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            tipo_mov TEXT NOT NULL,
            -- ingreso | egreso_ot | ajuste
            cantidad REAL NOT NULL,
            precio_unitario_neto REAL DEFAULT 0,
            -- ingreso: costo unitario sin IVA/impuestos
            importe_neto REAL DEFAULT 0,
            stock_resultante REAL DEFAULT 0,
            costo_prom_resultante REAL DEFAULT 0,
            proveedor_cuit TEXT,
            proveedor_nombre TEXT,
            nro_comprobante TEXT,
            campania_id INTEGER,
            lote_id INTEGER,
            ot_id INTEGER,
            observaciones TEXT,
            FOREIGN KEY(item_id) REFERENCES almacen_items(id)
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ordenes_trabajo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            nro_ot TEXT,
            fecha TEXT NOT NULL,
            tipo_labor TEXT,
            contratista_cuit TEXT,
            contratista_nombre TEXT,
            campania_id INTEGER,
            campo_id INTEGER,
            lote_id INTEGER,
            superficie_has REAL DEFAULT 0,
            cultivo TEXT,
            estado TEXT DEFAULT 'Emitida',
            observaciones TEXT,
            costo_insumos_neto REAL DEFAULT 0,
            costo_laboreos_neto REAL DEFAULT 0,
            costo_total_neto REAL DEFAULT 0
        );
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ot_empresa_nro
        ON ordenes_trabajo(empresa_id, nro_ot) WHERE nro_ot IS NOT NULL AND TRIM(nro_ot) != '';
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ot_consumos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ot_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            tipo_item TEXT,
            -- producto | laboreo (denormalizado)
            dosis_por_ha REAL DEFAULT 0,
            cantidad REAL NOT NULL,
            unidad TEXT,
            costo_unitario_neto REAL DEFAULT 0,
            costo_total_neto REAL DEFAULT 0,
            movimiento_id INTEGER,
            FOREIGN KEY(ot_id) REFERENCES ordenes_trabajo(id),
            FOREIGN KEY(item_id) REFERENCES almacen_items(id)
        );
    """)
    cursor.execute("PRAGMA table_info(almacen_items);")
    cols_item = {r[1] for r in cursor.fetchall()}
    if "costo_promedio_usd" not in cols_item:
        cursor.execute("ALTER TABLE almacen_items ADD COLUMN costo_promedio_usd REAL DEFAULT 0;")
    if "presentacion" not in cols_item:
        cursor.execute("ALTER TABLE almacen_items ADD COLUMN presentacion TEXT;")
    if "detalle" not in cols_item:
        cursor.execute("ALTER TABLE almacen_items ADD COLUMN detalle TEXT;")
    if "stock_manual" not in cols_item:
        cursor.execute("ALTER TABLE almacen_items ADD COLUMN stock_manual INTEGER DEFAULT 0;")
    cursor.execute("PRAGMA table_info(almacen_movimientos);")
    cols_mov = {r[1] for r in cursor.fetchall()}
    if "precio_unitario_usd" not in cols_mov:
        cursor.execute("ALTER TABLE almacen_movimientos ADD COLUMN precio_unitario_usd REAL DEFAULT 0;")
    cursor.execute("PRAGMA table_info(ordenes_trabajo);")
    cols_ot = {r[1] for r in cursor.fetchall()}
    if "fecha_aplicacion" not in cols_ot:
        cursor.execute("ALTER TABLE ordenes_trabajo ADD COLUMN fecha_aplicacion TEXT;")
    if "labor_cultural" not in cols_ot:
        cursor.execute("ALTER TABLE ordenes_trabajo ADD COLUMN labor_cultural TEXT;")
    if "confirmada" not in cols_ot:
        cursor.execute("ALTER TABLE ordenes_trabajo ADD COLUMN confirmada INTEGER DEFAULT 0;")
    if "fecha_confirmacion" not in cols_ot:
        cursor.execute("ALTER TABLE ordenes_trabajo ADD COLUMN fecha_confirmacion TEXT;")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ot_destinos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ot_id INTEGER NOT NULL,
            orden INTEGER DEFAULT 0,
            campo_id INTEGER,
            lote_id INTEGER,
            superficie_has REAL DEFAULT 0,
            cultivo TEXT
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ot_destinos_ot ON ot_destinos(ot_id);")
    cursor.execute("PRAGMA table_info(ot_consumos);")
    if "destino_id" not in {r[1] for r in cursor.fetchall()}:
        cursor.execute("ALTER TABLE ot_consumos ADD COLUMN destino_id INTEGER;")
    init_lineas_ot_schema(cursor)
    _aplicar_costos_usd_access(cursor)


def init_lineas_ot_schema(cursor) -> None:
    """margenes_access guarda las líneas de OT (históricas de Access y nuevas)."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS margenes_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            id_access INTEGER,
            campania_codigo TEXT,
            cultivo TEXT,
            nro_orden INTEGER,
            campo TEXT,
            lote TEXT,
            cantidad_has REAL DEFAULT 0,
            fecha_orden TEXT,
            laboreo TEXT,
            labor_cultural TEXT,
            contratista TEXT,
            producto TEXT,
            tipo TEXT,
            dosis_ha REAL DEFAULT 0,
            cantidad_total REAL DEFAULT 0,
            fecha_aplicacion TEXT,
            precio REAL DEFAULT 0,
            costo_ars REAL DEFAULT 0,
            tc REAL DEFAULT 0,
            costo_usd REAL DEFAULT 0,
            varios TEXT,
            fecha_compra TEXT,
            nro_remito TEXT,
            proveedor TEXT,
            cantidad_ingresada REAL DEFAULT 0,
            unidad TEXT,
            empresa_nombre TEXT,
            UNIQUE(empresa_id, id_access)
        );
        """
    )
    cols = {r[1] for r in cursor.execute("PRAGMA table_info(margenes_access)").fetchall()}
    for col, ddl in (
        ("almacen_item_id", "INTEGER"),
        ("ot_id", "INTEGER"),
        ("ot_consumo_id", "INTEGER"),
        ("moneda_costo", "TEXT"),
        ("origen_costo", "TEXT"),
        ("mov_ingreso_id", "INTEGER"),
        ("fecha_costeo", "TEXT"),
    ):
        if col not in cols:
            cursor.execute(f"ALTER TABLE margenes_access ADD COLUMN {col} {ddl};")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_margenes_nro_orden ON margenes_access(empresa_id, nro_orden);"
    )


CRITERIOS_COSTO = {
    "peps": "Primero entrado, primero salido",
    "ueps": "Último entrado, primero salido",
    "ppp": "Precio promedio ponderado",
    "ultima": "Precio de la última compra",
}


def criterio_costo_empresa(cursor) -> str:
    try:
        cursor.execute("SELECT criterio_costo FROM configuracion_empresa WHERE id=1;")
        row = cursor.fetchone()
    except sqlite3.OperationalError:
        return "peps"
    if not row:
        return "peps"
    valor = row["criterio_costo"] if isinstance(row, sqlite3.Row) else row[0]
    valor = (valor or "peps").strip().lower()
    return valor if valor in CRITERIOS_COSTO else "peps"


def valuar_salida_insumo(cursor, empresa_id: int, item_id: int, cantidad: float, metodo: Optional[str] = None) -> Dict[str, Any]:
    """Costo de una salida de insumo según el criterio de la empresa. Insumos en U$S."""
    cant = float(cantidad or 0)
    metodo = (metodo or criterio_costo_empresa(cursor) or "peps").strip().lower()
    if metodo not in CRITERIOS_COSTO:
        metodo = "peps"
    cursor.execute(
        "SELECT nombre, tipo, costo_promedio_usd, costo_promedio_neto, stock_cantidad FROM almacen_items WHERE id=? AND empresa_id=?;",
        (item_id, empresa_id),
    )
    item = cursor.fetchone()
    if not item:
        raise ValueError("Ítem no encontrado en el almacén.")
    item = dict(item)
    promedio = float(item.get("costo_promedio_usd") or 0)
    if (item.get("tipo") or "") == "laboreo" or metodo == "ppp" or promedio <= 0 and metodo == "ppp":
        unit = promedio if (item.get("tipo") or "") != "laboreo" and promedio > 0 else float(item.get("costo_promedio_neto") or 0)
        moneda = "USD" if (item.get("tipo") or "") != "laboreo" and promedio > 0 else "ARS"
        if metodo != "ppp" and (item.get("tipo") or "") != "laboreo":
            pass
        else:
            return {
                "criterio": metodo,
                "criterio_nombre": CRITERIOS_COSTO.get(metodo, metodo),
                "moneda": moneda,
                "cantidad": cant,
                "costo_unitario": round(unit, 4),
                "costo_total": round(cant * unit, 2),
                "capas": [],
            }
    cursor.execute(
        """
        SELECT fecha, ABS(cantidad) AS qty, COALESCE(precio_unitario_usd, 0) AS usd
        FROM almacen_movimientos
        WHERE empresa_id=? AND item_id=? AND tipo_mov='ingreso' AND ABS(cantidad)>0
        ORDER BY fecha, id;
        """,
        (empresa_id, item_id),
    )
    ingresos = [dict(r) for r in cursor.fetchall()]
    cursor.execute(
        """
        SELECT COALESCE(SUM(ABS(cantidad)), 0) AS n
        FROM almacen_movimientos
        WHERE empresa_id=? AND item_id=? AND tipo_mov='egreso_ot';
        """,
        (empresa_id, item_id),
    )
    consumido = float(cursor.fetchone()["n"] or 0)
    if metodo == "ultima":
        con_precio = [c for c in ingresos if float(c["usd"] or 0) > 0]
        unit = float(con_precio[-1]["usd"]) if con_precio else promedio
        return {
            "criterio": metodo,
            "criterio_nombre": CRITERIOS_COSTO[metodo],
            "moneda": "USD",
            "cantidad": cant,
            "costo_unitario": round(unit, 4),
            "costo_total": round(cant * unit, 2),
            "capas": [{"fecha": con_precio[-1]["fecha"], "cantidad": cant, "costo_unitario": round(unit, 4)}] if con_precio else [],
        }
    orden = list(ingresos)
    if metodo == "ueps":
        orden = list(reversed(ingresos))
    restante_consumido = consumido
    capas = []
    for capa in orden:
        qty = float(capa["qty"] or 0)
        if restante_consumido >= qty - 1e-9:
            restante_consumido -= qty
            continue
        if restante_consumido > 0:
            qty -= restante_consumido
            restante_consumido = 0
        usd = float(capa["usd"] or 0) or promedio
        if qty > 0 and usd > 0:
            capas.append({"fecha": capa["fecha"], "qty": qty, "usd": usd})
    tomar = cant
    usadas = []
    total = 0.0
    for capa in capas:
        if tomar <= 1e-9:
            break
        uso = min(tomar, capa["qty"])
        total += uso * capa["usd"]
        usadas.append({
            "fecha": (capa["fecha"] or "")[:10],
            "cantidad": round(uso, 4),
            "costo_unitario": round(capa["usd"], 4),
        })
        tomar -= uso
    if tomar > 1e-6 and promedio > 0:
        total += tomar * promedio
        usadas.append({"fecha": "", "cantidad": round(tomar, 4), "costo_unitario": round(promedio, 4)})
        tomar = 0
    unit = (total / cant) if cant else 0
    return {
        "criterio": metodo,
        "criterio_nombre": CRITERIOS_COSTO[metodo],
        "moneda": "USD",
        "cantidad": cant,
        "costo_unitario": round(unit, 4),
        "costo_total": round(total, 2),
        "capas": usadas,
    }


def _aplicar_costos_usd_access(cursor) -> None:
    """Completa el costo en dólares de los productos que vinieron de Access.

    El promedio en pesos sigue en costo_promedio_neto. La OT de insumos usa
    esta moneda constante y no el valor histórico en pesos.
    """
    import json
    from pathlib import Path

    path = Path(__file__).with_name("datos") / "costos_usd_almacen.json"
    if not path.exists():
        return
    try:
        mapa = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(mapa, dict) or not mapa:
        return
    for nombre, usd in mapa.items():
        try:
            valor = float(usd or 0)
        except (TypeError, ValueError):
            continue
        if valor <= 0 or not str(nombre).strip():
            continue
        cursor.execute(
            """
            UPDATE almacen_items
            SET costo_promedio_usd = ?
            WHERE UPPER(TRIM(nombre)) = UPPER(TRIM(?))
              AND COALESCE(costo_promedio_usd, 0) = 0;
            """,
            (round(valor, 4), str(nombre).strip()),
        )
    capas = Path(__file__).with_name("datos") / "capas_usd_almacen.json"
    if not capas.exists():
        return
    try:
        filas = json.loads(capas.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(filas, list):
        return
    for fila in filas:
        if not isinstance(fila, (list, tuple)) or len(fila) < 2:
            continue
        try:
            ida = int(fila[0])
            usd = float(fila[1] or 0)
        except (TypeError, ValueError):
            continue
        if ida <= 0 or usd <= 0:
            continue
        cursor.execute(
            """
            UPDATE almacen_movimientos
            SET precio_unitario_usd = ?
            WHERE id_access = ? AND COALESCE(precio_unitario_usd, 0) = 0;
            """,
            (round(usd, 4), ida),
        )


def _inferir_categoria_codigo(nombre: str = "", tipo: str = "", categoria: str = "") -> str:
    """Heurística para mapear ítems legacy a códigos de catálogo."""
    n = f"{nombre or ''} {categoria or ''}".lower()
    t = (tipo or "").strip().lower()
    cat = (categoria or "").strip().lower()

    known = {
        "agroquimicos", "fertilizantes", "semillas", "laboreos",
        "maquinarias", "ganaderia",
    }
    if cat in known:
        return cat
    # "varios" / "insumo" no son definitivos: seguir por nombre
    if cat == "varios":
        cat = ""
        n = f"{nombre or ''}".lower()
    if "fertiliz" in cat:
        return "fertilizantes"
    if "semill" in cat:
        return "semillas"
    if "laboreo" in cat or "servicio" in cat:
        return "laboreos"
    if "maquin" in cat or "lubric" in cat or "combust" in cat:
        return "maquinarias"
    if "ganader" in cat or "vacun" in cat or "sanidad" in cat:
        return "ganaderia"
    if "agroqu" in cat:
        return "agroquimicos"

    if t == "laboreo":
        return "laboreos"

    # combustibles / lubricantes (maquinaria)
    if any(k in n for k in (
        "gas oil", "gasoil", "diesel", "combustible", "lubricante",
        "engras", "nafta", "fuel oil", "aceite hidraul", "aceite hidrául",
        "aceite motor", "grasa ",
    )):
        return "maquinarias"
    # ganadería
    if any(k in n for k in (
        "vacuna", "ivermectin", "caravana", "sanidad animal", "antiparas",
        "afthosa", "aftosa", "brucel", "ganader", "tambo", "reproductor",
    )):
        return "ganaderia"
    # fertilizantes
    if any(k in n for k in (
        "urea", "fertiliz", "fosfato", " map", "map ", " dap", "dap ",
        "sulfato", "nitrato", "amon", "npk", "superfosfato", "fosforo",
        "fósforo", "potasio", "mezcla fisica", "mezcla física",
    )):
        return "fertilizantes"
    # semillas
    if any(k in n for k in ("semilla", "seed", "hibrido", "híbrido", "bolsa de")):
        return "semillas"
    # agroquímicos / coadyuvantes de pulverización
    if any(k in n for k in (
        "glifo", "herbicid", "insecticid", "fungicid", "agroquim", "power plus",
        "2,4", "2.4", "atrazina", "dicamba", "paraquat", "insect", "fungi",
        "coadyuvan", "surfactan", "desecante", "spray", "pulveriz",
        "abamectin", "lambda", "cipermetr", "imidaclop", "clethodim",
        "haloxifop", "quizalofop", "metsulfuron", "diclosulam", "fomesafen",
        "bentazon", "picloram", "fluroxipir", "clorpirif", "dimetoato",
        "aceite rizo", "aceite mineral", "aceite agric", "aceite agrí",
    )):
        return "agroquimicos"
    if any(k in n for k in ("siembra", "cosecha", "laboreo", "rastreo", "arada")):
        return "laboreos"
    # default productos de almacén agro → agroquímicos (OT sí); el usuario puede corregir
    if t == "producto" or not t:
        return "agroquimicos"
    return "varios"


def _nombre_categoria(cursor, codigo: str) -> str:
    cursor.execute(
        "SELECT nombre FROM almacen_categorias WHERE codigo=? LIMIT 1;",
        (codigo,),
    )
    row = cursor.fetchone()
    if row:
        return row["nombre"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
    return codigo or ""


def _reclasificar_categorias_items(cursor, forzar: bool = False) -> None:
    """Asigna categoria_codigo a ítems sin código válido (o a todos si forzar)."""
    cursor.execute("SELECT codigo FROM almacen_categorias WHERE COALESCE(activo,1)=1;")
    valid = {r["codigo"] for r in cursor.fetchall()}
    if not valid:
        return
    if forzar:
        cursor.execute(
            "SELECT id, nombre, tipo, categoria, categoria_codigo, clasificacion_manual FROM almacen_items;"
        )
    else:
        placeholders = ",".join("?" * len(valid))
        cursor.execute(
            f"""
            SELECT id, nombre, tipo, categoria, categoria_codigo, clasificacion_manual
            FROM almacen_items
            WHERE COALESCE(categoria_codigo,'') = ''
               OR categoria_codigo NOT IN ({placeholders})
               OR categoria_codigo = 'varios'
            """,
            tuple(valid),
        )
    rows = cursor.fetchall()
    for r in rows:
        d = dict(r)
        # no pisar categorías explícitas distintas de vacías/varios NI edición manual
        prev = (d.get("categoria_codigo") or "").strip()
        if int(d.get("clasificacion_manual") or 0) == 1 and not forzar:
            continue
        if prev and prev != "varios" and prev in valid and not forzar:
            continue
        codigo = _inferir_categoria_codigo(
            d.get("nombre") or "", d.get("tipo") or "", d.get("categoria") or ""
        )
        if codigo not in valid:
            codigo = "agroquimicos" if (d.get("tipo") or "producto") == "producto" else "varios"
        # si el ítem ya estaba en varios y la inferencia sigue en varios, ok
        nombre_cat = _nombre_categoria(cursor, codigo)
        cursor.execute(
            """
            UPDATE almacen_items
            SET categoria_codigo=?, categoria=?
            WHERE id=?;
            """,
            (codigo, nombre_cat, d["id"]),
        )


def listar_categorias_almacen(cursor, solo_aplica_ot: Optional[bool] = None) -> List[Dict[str, Any]]:
    q = "SELECT * FROM almacen_categorias WHERE COALESCE(activo,1)=1"
    params: List[Any] = []
    if solo_aplica_ot is True:
        q += " AND COALESCE(aplica_ot,0)=1"
    elif solo_aplica_ot is False:
        q += " AND COALESCE(aplica_ot,0)=0"
    q += " ORDER BY orden ASC, nombre COLLATE NOCASE ASC;"
    cursor.execute(q, params)
    return [dict(r) for r in cursor.fetchall()]


def crear_categoria_almacen(
    cursor, *, codigo: str, nombre: str, aplica_ot: bool = False, orden: int = 100
) -> Dict[str, Any]:
    codigo = (codigo or "").strip().lower().replace(" ", "_")
    nombre = (nombre or "").strip()
    if not codigo or not nombre:
        raise ValueError("Código y nombre de categoría son obligatorios.")
    cursor.execute(
        """
        INSERT INTO almacen_categorias (codigo, nombre, aplica_ot, orden, activo)
        VALUES (?, ?, ?, ?, 1);
        """,
        (codigo, nombre, 1 if aplica_ot else 0, orden or 100),
    )
    cursor.execute("SELECT * FROM almacen_categorias WHERE id=?;", (cursor.lastrowid,))
    return dict(cursor.fetchone())


def actualizar_item_categoria(
    cursor, empresa_id: int, item_id: int, categoria_codigo: str
) -> Dict[str, Any]:
    return actualizar_item_clasificacion(
        cursor, empresa_id, item_id, categoria_codigo=categoria_codigo
    )


def actualizar_item_clasificacion(
    cursor,
    empresa_id: int,
    item_id: int,
    *,
    categoria_codigo: Optional[str] = None,
    tipo: Optional[str] = None,
    unidad: Optional[str] = None,
) -> Dict[str, Any]:
    """Edición manual de tipo/categoría/unidad. Marca clasificacion_manual=1."""
    cursor.execute(
        "SELECT * FROM almacen_items WHERE id=? AND empresa_id=?;",
        (item_id, empresa_id),
    )
    item = cursor.fetchone()
    if not item:
        raise ValueError("Ítem no encontrado.")
    item = dict(item)

    nuevo_tipo = (tipo or item.get("tipo") or "producto").strip().lower()
    if nuevo_tipo not in ("producto", "laboreo"):
        nuevo_tipo = "producto"

    cat_cod = (categoria_codigo if categoria_codigo is not None else item.get("categoria_codigo") or "").strip().lower()
    if not cat_cod:
        cat_cod = _inferir_categoria_codigo(item.get("nombre") or "", nuevo_tipo, item.get("categoria") or "")
    cursor.execute(
        "SELECT * FROM almacen_categorias WHERE codigo=? AND COALESCE(activo,1)=1;",
        (cat_cod,),
    )
    cat = cursor.fetchone()
    if not cat:
        raise ValueError(f"Categoría '{cat_cod}' no encontrada.")
    cat = dict(cat)

    nueva_unidad = (unidad if unidad is not None else item.get("unidad") or "").strip()
    if not nueva_unidad:
        nueva_unidad = "Has" if nuevo_tipo == "laboreo" else (item.get("unidad") or "Kg")

    # asegurar columna clasificacion_manual
    cursor.execute("PRAGMA table_info(almacen_items);")
    cols = {r[1] for r in cursor.fetchall()}
    if "clasificacion_manual" not in cols:
        cursor.execute("ALTER TABLE almacen_items ADD COLUMN clasificacion_manual INTEGER DEFAULT 0;")

    cursor.execute(
        """
        UPDATE almacen_items
        SET tipo=?, categoria_codigo=?, categoria=?, unidad=?, clasificacion_manual=1
        WHERE id=? AND empresa_id=?;
        """,
        (nuevo_tipo, cat_cod, cat["nombre"], nueva_unidad, item_id, empresa_id),
    )
    cursor.execute(
        "SELECT * FROM almacen_items WHERE id=? AND empresa_id=?;",
        (item_id, empresa_id),
    )
    return dict(cursor.fetchone())


def guardar_ficha_item(
    cursor,
    empresa_id: int,
    item_id: int,
    *,
    nombre: str,
    presentacion: str = "",
    detalle: str = "",
    tipo: Optional[str] = None,
    categoria_codigo: Optional[str] = None,
    unidad: Optional[str] = None,
    stock_cantidad: float = 0,
    costo_usd: float = 0,
    costo_ars: float = 0,
    tipo_cambio: float = 0,
) -> Dict[str, Any]:
    """Ficha del producto: nombre, presentación, stock y costos en U$S y pesos."""
    cursor.execute(
        "SELECT * FROM almacen_items WHERE id=? AND empresa_id=?;",
        (item_id, empresa_id),
    )
    item = cursor.fetchone()
    if not item:
        raise ValueError("Ítem no encontrado.")
    item = dict(item)
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("El nombre es obligatorio.")
    usd = round(float(costo_usd or 0), 4)
    ars = round(float(costo_ars or 0), 4)
    tc = round(float(tipo_cambio or 0), 4)
    if tc > 0 and usd > 0 and ars <= 0:
        ars = round(usd * tc, 4)
    elif tc > 0 and ars > 0 and usd <= 0:
        usd = round(ars / tc, 4)
    elif usd > 0 and ars > 0 and tc <= 0:
        tc = round(ars / usd, 4)

    actualizar_item_clasificacion(
        cursor, empresa_id, item_id,
        categoria_codigo=categoria_codigo, tipo=tipo, unidad=unidad,
    )
    stock_nuevo = round(float(stock_cantidad or 0), 4)
    stock_viejo = round(float(item.get("stock_cantidad") or 0), 4)
    manual = int(item.get("stock_manual") or 0)
    if abs(stock_nuevo - stock_viejo) > 0.0001:
        manual = 1
        delta = round(stock_nuevo - stock_viejo, 4)
        cursor.execute("PRAGMA table_info(almacen_movimientos);")
        if "precio_unitario_usd" not in {r[1] for r in cursor.fetchall()}:
            cursor.execute("ALTER TABLE almacen_movimientos ADD COLUMN precio_unitario_usd REAL DEFAULT 0;")
        cursor.execute(
            """
            INSERT INTO almacen_movimientos (
                empresa_id, item_id, fecha, tipo_mov, cantidad,
                precio_unitario_neto, precio_unitario_usd, importe_neto,
                stock_resultante, costo_prom_resultante, observaciones
            ) VALUES (?, ?, ?, 'ajuste', ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                empresa_id, item_id, datetime.now().strftime("%Y-%m-%d"),
                delta, ars, usd, round(abs(delta) * ars, 2),
                stock_nuevo, usd or ars,
                "Corrección manual de stock",
            ),
        )
    cursor.execute("PRAGMA table_info(almacen_items);")
    cols = {r[1] for r in cursor.fetchall()}
    if "presentacion" not in cols:
        cursor.execute("ALTER TABLE almacen_items ADD COLUMN presentacion TEXT;")
    if "detalle" not in cols:
        cursor.execute("ALTER TABLE almacen_items ADD COLUMN detalle TEXT;")
    if "stock_manual" not in cols:
        cursor.execute("ALTER TABLE almacen_items ADD COLUMN stock_manual INTEGER DEFAULT 0;")
    cursor.execute(
        """
        UPDATE almacen_items
        SET nombre=?, presentacion=?, detalle=?,
            stock_cantidad=?, stock_manual=?,
            costo_promedio_usd=?, costo_promedio_neto=?
        WHERE id=? AND empresa_id=?;
        """,
        (nombre, (presentacion or "").strip(), (detalle or "").strip(),
         stock_nuevo, manual, usd, ars, item_id, empresa_id),
    )
    cursor.execute("SELECT * FROM almacen_items WHERE id=? AND empresa_id=?;", (item_id, empresa_id))
    row = dict(cursor.fetchone())
    row["tipo_cambio"] = tc
    return row


def listar_items_almacen(
    cursor,
    empresa_id: int,
    tipo: Optional[str] = None,
    solo_con_stock: bool = False,
    orden: str = "nombre",
    categoria_codigo: Optional[str] = None,
    solo_aplica_ot: bool = False,
) -> List[Dict[str, Any]]:
    q = """
        SELECT i.*,
               c.nombre AS categoria_nombre,
               COALESCE(c.aplica_ot, 0) AS aplica_ot
        FROM almacen_items i
        LEFT JOIN almacen_categorias c ON c.codigo = i.categoria_codigo
        WHERE i.empresa_id = ? AND COALESCE(i.activo,1)=1
    """
    params: List[Any] = [empresa_id]
    if tipo in ("producto", "laboreo"):
        q += " AND i.tipo = ?"
        params.append(tipo)
    if solo_con_stock:
        q += " AND ABS(COALESCE(i.stock_cantidad,0)) > 0.0001"
    if categoria_codigo:
        q += " AND i.categoria_codigo = ?"
        params.append(categoria_codigo.strip().lower())
    if solo_aplica_ot:
        q += " AND COALESCE(c.aplica_ot,0)=1"
    orden_n = (orden or "nombre").strip().lower()
    if orden_n in ("stock", "cantidad"):
        q += " ORDER BY ABS(COALESCE(i.stock_cantidad,0)) DESC, i.nombre COLLATE NOCASE ASC;"
    else:
        # default: alfabético
        q += " ORDER BY i.nombre COLLATE NOCASE ASC, i.tipo ASC;"
    cursor.execute(q, params)
    out = []
    for r in cursor.fetchall():
        d = dict(r)
        # preferir nombre del catálogo si existe
        if d.get("categoria_nombre"):
            d["categoria"] = d["categoria_nombre"]
        out.append(d)
    return out


def _recalcular_promedio(stock_prev: float, costo_prev: float, cant_in: float, precio_in: float) -> float:
    """Promedio ponderado móvil (solo neto)."""
    stock_prev = float(stock_prev or 0)
    costo_prev = float(costo_prev or 0)
    cant_in = float(cant_in or 0)
    precio_in = float(precio_in or 0)
    if stock_prev <= 0 or costo_prev <= 0:
        return precio_in
    nuevo_stock = stock_prev + cant_in
    if nuevo_stock <= 0:
        return precio_in
    return round(((stock_prev * costo_prev) + (cant_in * precio_in)) / nuevo_stock, 6)


def ingresar_almacen(
    cursor,
    *,
    empresa_id: int,
    item_id: Optional[int] = None,
    tipo: str = "producto",
    codigo: str = "",
    nombre: str = "",
    categoria: str = "",
    categoria_codigo: str = "",
    unidad: str = "Kg",
    fecha: str = "",
    cantidad: float = 0.0,
    precio_unitario_neto: float = 0.0,
    proveedor_cuit: str = "",
    proveedor_nombre: str = "",
    nro_comprobante: str = "",
    observaciones: str = "",
) -> Dict[str, Any]:
    """
    Ingreso a almacén a costo NETO (sin impuestos).
    tipo=laboreo: p.ej. pulverización, siembra (unidad Has/Hs).
    """
    if cantidad <= 0:
        raise ValueError("La cantidad de ingreso debe ser mayor a cero.")
    if precio_unitario_neto < 0:
        raise ValueError("El precio unitario neto no puede ser negativo.")

    fecha = (fecha or datetime.now().strftime("%Y-%m-%d"))[:10]
    tipo = (tipo or "producto").strip().lower()
    if tipo not in ("producto", "laboreo"):
        tipo = "producto"

    cat_cod = (categoria_codigo or "").strip().lower()
    if not cat_cod:
        cat_cod = _inferir_categoria_codigo(nombre, tipo, categoria)
    cat_nombre = _nombre_categoria(cursor, cat_cod) or categoria or cat_cod

    if item_id:
        cursor.execute(
            "SELECT * FROM almacen_items WHERE id=? AND empresa_id=?;",
            (item_id, empresa_id),
        )
        item = cursor.fetchone()
        if not item:
            raise ValueError("Ítem de almacén no encontrado.")
        item = dict(item)
    else:
        if not (nombre or "").strip():
            raise ValueError("Indicá el nombre del producto o laboreo.")
        # Buscar por código o crear
        if codigo:
            cursor.execute(
                "SELECT * FROM almacen_items WHERE empresa_id=? AND codigo=?;",
                (empresa_id, codigo.strip()),
            )
            item = cursor.fetchone()
        else:
            item = None
        if item:
            item = dict(item)
            item_id = item["id"]
        else:
            cursor.execute(
                """
                INSERT INTO almacen_items
                (empresa_id, tipo, codigo, nombre, categoria, categoria_codigo, unidad,
                 stock_cantidad, costo_promedio_neto, activo)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 1);
                """,
                (
                    empresa_id, tipo, (codigo or "").strip() or None,
                    nombre.strip(), cat_nombre, cat_cod,
                    unidad or ("Has" if tipo == "laboreo" else "Kg"),
                ),
            )
            item_id = cursor.lastrowid
            cursor.execute("SELECT * FROM almacen_items WHERE id=?;", (item_id,))
            item = dict(cursor.fetchone())

    stock_prev = float(item.get("stock_cantidad") or 0)
    costo_prev = float(item.get("costo_promedio_neto") or 0)
    nuevo_prom = _recalcular_promedio(stock_prev, costo_prev, cantidad, precio_unitario_neto)
    nuevo_stock = round(stock_prev + cantidad, 6)
    importe = round(cantidad * precio_unitario_neto, 2)

    cursor.execute(
        """
        UPDATE almacen_items
        SET stock_cantidad=?, costo_promedio_neto=?,
            nombre=COALESCE(NULLIF(?,''), nombre),
            categoria=COALESCE(NULLIF(?,''), categoria),
            categoria_codigo=COALESCE(NULLIF(?,''), categoria_codigo),
            unidad=COALESCE(NULLIF(?,''), unidad),
            tipo=?
        WHERE id=?;
        """,
        (
            nuevo_stock, nuevo_prom, nombre or "", cat_nombre or "",
            cat_cod or "", unidad or "", tipo, item_id,
        ),
    )
    cursor.execute(
        """
        INSERT INTO almacen_movimientos
        (empresa_id, item_id, fecha, tipo_mov, cantidad, precio_unitario_neto, importe_neto,
         stock_resultante, costo_prom_resultante, proveedor_cuit, proveedor_nombre,
         nro_comprobante, observaciones)
        VALUES (?, ?, ?, 'ingreso', ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            empresa_id, item_id, fecha, cantidad, precio_unitario_neto, importe,
            nuevo_stock, nuevo_prom, proveedor_cuit or "", proveedor_nombre or "",
            nro_comprobante or "", observaciones or "",
        ),
    )
    mov_id = cursor.lastrowid
    return {
        "status": "success",
        "item_id": item_id,
        "movimiento_id": mov_id,
        "stock_cantidad": nuevo_stock,
        "costo_promedio_neto": nuevo_prom,
        "importe_neto": importe,
        "message": "Ingreso a almacén registrado a costo neto (sin impuestos).",
    }


def egresar_almacen_nc(
    cursor,
    *,
    empresa_id: int,
    item_id: Optional[int] = None,
    nombre: str = "",
    fecha: str = "",
    cantidad: float = 0.0,
    precio_unitario_neto: float = 0.0,
    proveedor_cuit: str = "",
    nro_comprobante: str = "",
    observaciones: str = "",
) -> Dict[str, Any]:
    """
    Devolución / egreso por Nota de Crédito de compra (baja stock).
    Si no hay stock suficiente, deja stock en 0 y registra el egreso parcial.
    """
    if cantidad <= 0:
        raise ValueError("La cantidad de egreso NC debe ser mayor a cero.")
    fecha = (fecha or datetime.now().strftime("%Y-%m-%d"))[:10]

    item = None
    if item_id:
        cursor.execute(
            "SELECT * FROM almacen_items WHERE id=? AND empresa_id=?;",
            (item_id, empresa_id),
        )
        item = cursor.fetchone()
    if not item and (nombre or "").strip():
        cursor.execute(
            """
            SELECT * FROM almacen_items
            WHERE empresa_id=? AND UPPER(TRIM(nombre))=UPPER(TRIM(?))
            ORDER BY id DESC LIMIT 1;
            """,
            (empresa_id, nombre.strip()),
        )
        item = cursor.fetchone()
    if not item:
        raise ValueError("No se encontró el ítem de almacén para egresar por NC.")

    item = dict(item)
    item_id = int(item["id"])
    stock = float(item.get("stock_cantidad") or 0)
    costo_u = float(item.get("costo_promedio_neto") or 0) or float(precio_unitario_neto or 0)
    cant = min(cantidad, stock) if stock > 0 else cantidad
    nuevo_stock = round(max(0.0, stock - cant), 6)
    importe = round(cant * costo_u, 2)

    cursor.execute(
        "UPDATE almacen_items SET stock_cantidad=? WHERE id=?;",
        (nuevo_stock, item_id),
    )
    cursor.execute(
        """
        INSERT INTO almacen_movimientos
        (empresa_id, item_id, fecha, tipo_mov, cantidad, precio_unitario_neto, importe_neto,
         stock_resultante, costo_prom_resultante, proveedor_cuit, proveedor_nombre,
         nro_comprobante, observaciones)
        VALUES (?, ?, ?, 'egreso_nc', ?, ?, ?, ?, ?, ?, '', ?, ?);
        """,
        (
            empresa_id, item_id, fecha, cant, costo_u, importe,
            nuevo_stock, costo_u, proveedor_cuit or "",
            nro_comprobante or "", observaciones or "",
        ),
    )
    return {
        "status": "success",
        "item_id": item_id,
        "movimiento_id": cursor.lastrowid,
        "stock_cantidad": nuevo_stock,
        "cantidad_egresada": cant,
        "message": "Egreso por Nota de Crédito registrado.",
    }


NRO_OT_INICIAL = 3274


def _ultimo_nro_ot(cursor, empresa_id: int) -> int:
    ultimo = NRO_OT_INICIAL - 1
    cursor.execute(
        """
        SELECT MAX(CAST(nro_ot AS INTEGER)) FROM ordenes_trabajo
        WHERE empresa_id=? AND TRIM(COALESCE(nro_ot,'')) != '' AND nro_ot NOT GLOB '*[^0-9]*';
        """,
        (empresa_id,),
    )
    row = cursor.fetchone()
    if row and row[0]:
        ultimo = max(ultimo, int(row[0]))
    cursor.execute("SELECT MAX(nro_orden) FROM margenes_access WHERE empresa_id=?;", (empresa_id,))
    row = cursor.fetchone()
    if row and row[0]:
        ultimo = max(ultimo, int(row[0]))
    return ultimo


def siguiente_nro_ot(cursor, empresa_id: int, anio: Optional[int] = None) -> str:
    return str(_ultimo_nro_ot(cursor, empresa_id) + 1)


def _nro_ot_en_uso(cursor, empresa_id: int, nro: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM ordenes_trabajo WHERE empresa_id=? AND TRIM(nro_ot)=? LIMIT 1;",
        (empresa_id, nro),
    )
    if cursor.fetchone():
        return True
    if nro.isdigit():
        cursor.execute(
            "SELECT 1 FROM margenes_access WHERE empresa_id=? AND nro_orden=? LIMIT 1;",
            (empresa_id, int(nro)),
        )
        return cursor.fetchone() is not None
    return False


def emitir_ot_consumiendo_almacen(
    cursor,
    *,
    empresa_id: int,
    fecha: str,
    tipo_labor: str,
    contratista_nombre: str = "",
    contratista_cuit: str = "",
    campania_id: Optional[int] = None,
    campo_id: Optional[int] = None,
    lote_id: Optional[int] = None,
    superficie_has: float = 0.0,
    cultivo: str = "",
    observaciones: str = "",
    consumos: Optional[List[Dict[str, Any]]] = None,
    nro_ot: Optional[str] = None,
    fecha_aplicacion: str = "",
    labor_cultural: str = "",
    destinos: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Emite la OT: registra las líneas y la salida física del almacén, sin costo.
    El costo se asigna después con costear_linea_ot. No se bloquea por falta de stock.
    cada consumo: {item_id, cantidad?, dosis_por_ha?}
    destinos: [{campo_id, lote_id, superficie_has, cultivo}]; la cantidad de cada
    consumo se reparte entre los destinos en proporción a sus hectáreas.
    """
    consumos = consumos or []
    fecha = (fecha or datetime.now().strftime("%Y-%m-%d"))[:10]
    fecha_aplic = (fecha_aplicacion or fecha)[:10]
    nro = str(nro_ot or "").strip() or siguiente_nro_ot(cursor, empresa_id)
    if _nro_ot_en_uso(cursor, empresa_id, nro):
        raise ValueError(f"La OT Nº {nro} ya existe.")
    if not any(int(c.get("item_id") or 0) for c in consumos):
        raise ValueError("Agregá al menos un producto o labor.")
    dests = _normalizar_destinos_ot(cursor, destinos, campo_id, lote_id, superficie_has, cultivo)
    total_has = round(sum(d["superficie_has"] for d in dests), 4)

    cursor.execute(
        """
        INSERT INTO ordenes_trabajo
        (empresa_id, nro_ot, fecha, tipo_labor, contratista_cuit, contratista_nombre,
         campania_id, campo_id, lote_id, superficie_has, cultivo, estado, observaciones,
         fecha_aplicacion, labor_cultural, confirmada)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pendiente de costeo', ?, ?, ?, 0);
        """,
        (
            empresa_id, nro, fecha, tipo_labor or "",
            contratista_cuit or "", contratista_nombre or "",
            campania_id, dests[0]["campo_id"], dests[0]["lote_id"], total_has,
            dests[0]["cultivo"] or cultivo or "",
            observaciones or "", fecha_aplic, labor_cultural or "",
        ),
    )
    ot_id = cursor.lastrowid
    lineas, sin_stock = _crear_lineas_ot(
        cursor, empresa_id, ot_id, nro,
        fecha=fecha, fecha_aplic=fecha_aplic, tipo_labor=tipo_labor,
        labor_cultural=labor_cultural, contratista_nombre=contratista_nombre,
        campania_id=campania_id, destinos=dests, consumos=consumos,
    )
    msg = f"OT {nro} emitida con {lineas} línea(s)"
    if len(dests) > 1:
        msg += f" en {len(dests)} lotes"
    msg += ". Queda pendiente de costeo."
    if sin_stock:
        msg += " Atención: algunos productos quedaron con stock negativo en el almacén."
    return {
        "status": "success",
        "ot_id": ot_id,
        "nro_ot": nro,
        "lineas": lineas,
        "sin_stock": sin_stock,
        "message": msg,
    }


def _normalizar_destinos_ot(
    cursor,
    destinos: Optional[List[Dict[str, Any]]],
    campo_id: Optional[int],
    lote_id: Optional[int],
    superficie_has: float,
    cultivo: str,
) -> List[Dict[str, Any]]:
    lista: List[Dict[str, Any]] = []
    for d in destinos or []:
        cid = int(d.get("campo_id") or 0) or None
        lid = int(d.get("lote_id") or 0) or None
        has = float(d.get("superficie_has") or 0)
        if not cid and not lid and has <= 0:
            continue
        if has < 0:
            raise ValueError("Las hectáreas de un destino no pueden ser negativas.")
        lista.append({
            "campo_id": cid, "lote_id": lid, "superficie_has": has,
            "cultivo": (d.get("cultivo") or cultivo or "").strip(),
        })
    if not lista:
        lista.append({
            "campo_id": int(campo_id or 0) or None, "lote_id": int(lote_id or 0) or None,
            "superficie_has": float(superficie_has or 0), "cultivo": (cultivo or "").strip(),
        })
    vistos = set()
    for d in lista:
        if d["lote_id"]:
            if d["lote_id"] in vistos:
                raise ValueError("Hay un lote repetido en los destinos de la OT.")
            vistos.add(d["lote_id"])
        d["campo_nombre"] = ""
        d["lote_nombre"] = ""
        if d["campo_id"]:
            cursor.execute("SELECT nombre FROM campos_agro WHERE id=?;", (d["campo_id"],))
            r = cursor.fetchone()
            d["campo_nombre"] = (r["nombre"] if r else "") or ""
        if d["lote_id"]:
            cursor.execute("SELECT nombre, campo_id FROM lotes_agro WHERE id=?;", (d["lote_id"],))
            r = cursor.fetchone()
            if r:
                d["lote_nombre"] = r["nombre"] or ""
                if not d["campo_id"] and r["campo_id"]:
                    d["campo_id"] = int(r["campo_id"])
                    cursor.execute("SELECT nombre FROM campos_agro WHERE id=?;", (d["campo_id"],))
                    rc = cursor.fetchone()
                    d["campo_nombre"] = (rc["nombre"] if rc else "") or ""
    return lista


def _crear_lineas_ot(
    cursor,
    empresa_id: int,
    ot_id: int,
    nro: str,
    *,
    fecha: str,
    fecha_aplic: str,
    tipo_labor: str,
    labor_cultural: str,
    contratista_nombre: str,
    campania_id: Optional[int],
    destinos: List[Dict[str, Any]],
    consumos: List[Dict[str, Any]],
):
    """Graba destinos, salidas de almacén, ot_consumos y líneas de costos de una OT."""
    campania_codigo = ""
    if campania_id:
        cursor.execute("SELECT codigo FROM campanias_agro WHERE id=?;", (campania_id,))
        r = cursor.fetchone()
        campania_codigo = (r["codigo"] if r else "") or ""
    for i, d in enumerate(destinos):
        cursor.execute(
            """
            INSERT INTO ot_destinos (ot_id, orden, campo_id, lote_id, superficie_has, cultivo)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (ot_id, i, d["campo_id"], d["lote_id"], d["superficie_has"], d["cultivo"]),
        )
        d["id"] = cursor.lastrowid
    total_has = sum(d["superficie_has"] for d in destinos)
    sin_stock = []
    lineas = 0

    for c in consumos:
        item_id = int(c.get("item_id") or 0)
        if not item_id:
            continue
        cursor.execute(
            "SELECT * FROM almacen_items WHERE id=? AND empresa_id=?;",
            (item_id, empresa_id),
        )
        item = cursor.fetchone()
        if not item:
            raise ValueError(f"Ítem {item_id} no encontrado en almacén.")
        item = dict(item)
        # solo categorías habilitadas para OT
        cat_cod = (item.get("categoria_codigo") or "").strip()
        if cat_cod:
            cursor.execute(
                "SELECT aplica_ot, nombre FROM almacen_categorias WHERE codigo=? LIMIT 1;",
                (cat_cod,),
            )
            cat = cursor.fetchone()
            if cat and not int(cat["aplica_ot"] or 0):
                raise ValueError(
                    f"«{item.get('nombre')}» es categoría «{cat['nombre']}» y no aplica a OT."
                )
        dosis = float(c.get("dosis_por_ha") or 0)
        cant = float(c.get("cantidad") or 0)
        if cant <= 0 and dosis > 0 and total_has > 0:
            cant = round(dosis * total_has, 6)
        if cant <= 0:
            continue
        tipo_item = item.get("tipo") or "producto"
        es_lab = tipo_item == "laboreo"

        if total_has > 0:
            partes = [round(cant * d["superficie_has"] / total_has, 6) for d in destinos]
            partes[-1] = round(cant - sum(partes[:-1]), 6)
        else:
            partes = [cant] + [0.0] * (len(destinos) - 1)
        stock_previo = float(item.get("stock_cantidad") or 0)
        stock = stock_previo

        for d, cant_d in zip(destinos, partes):
            if cant_d <= 0:
                continue
            stock = round(stock - cant_d, 6)
            cursor.execute(
                "UPDATE almacen_items SET stock_cantidad=? WHERE id=?;",
                (stock, item_id),
            )
            cursor.execute(
                """
                INSERT INTO almacen_movimientos
                (empresa_id, item_id, fecha, tipo_mov, cantidad, precio_unitario_neto, importe_neto,
                 stock_resultante, costo_prom_resultante, campania_id, lote_id, ot_id, observaciones)
                VALUES (?, ?, ?, 'egreso_ot', ?, 0, 0, ?, ?, ?, ?, ?, ?);
                """,
                (
                    empresa_id, item_id, fecha_aplic, cant_d,
                    stock, float(item.get("costo_promedio_neto") or 0),
                    campania_id, d["lote_id"], ot_id, f"Consumo OT {nro}",
                ),
            )
            mov_id = cursor.lastrowid
            cursor.execute(
                """
                INSERT INTO ot_consumos
                (ot_id, item_id, tipo_item, dosis_por_ha, cantidad, unidad,
                 costo_unitario_neto, costo_total_neto, movimiento_id, destino_id)
                VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?);
                """,
                (ot_id, item_id, tipo_item, dosis, cant_d, item.get("unidad") or "", mov_id, d["id"]),
            )
            consumo_id = cursor.lastrowid
            cursor.execute(
                """
                INSERT INTO margenes_access (
                    empresa_id, campania_codigo, cultivo, nro_orden, campo, lote, cantidad_has,
                    fecha_orden, laboreo, labor_cultural, contratista, producto, tipo, dosis_ha,
                    cantidad_total, fecha_aplicacion, unidad, almacen_item_id, ot_id, ot_consumo_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
                """,
                (
                    empresa_id, campania_codigo, d["cultivo"] or "",
                    int(nro) if nro.isdigit() else None,
                    d["campo_nombre"], d["lote_nombre"], float(d["superficie_has"] or 0), fecha,
                    (item.get("nombre") or "") if es_lab else "",
                    (labor_cultural or tipo_labor or "") if es_lab else "",
                    (contratista_nombre or "") if es_lab else "",
                    "" if es_lab else (item.get("nombre") or ""),
                    item.get("categoria") or "", dosis, cant_d, fecha_aplic,
                    item.get("unidad") or "", item_id, ot_id, consumo_id,
                ),
            )
            lineas += 1

        if not es_lab and stock < -1e-6:
            sin_stock.append({
                "nombre": item.get("nombre"),
                "stock_previo": round(stock_previo, 4),
                "pedido": round(cant, 4),
                "unidad": item.get("unidad") or "",
            })

    if not lineas:
        raise ValueError("Ninguna línea tiene cantidad. Indicá dosis y hectáreas o la cantidad.")
    return lineas, sin_stock


def _revertir_lineas_ot(cursor, ot_id: int) -> Dict[Any, Dict[str, Any]]:
    """Devuelve el stock y borra líneas, consumos y destinos de la OT.
    Retorna el costeo por (ítem, campo, lote) (None si esa línea no estaba costeada)
    y por ítem, para lotes que no existían antes de la edición."""
    costeos: Dict[Any, Optional[Dict[str, Any]]] = {}
    cursor.execute("SELECT * FROM margenes_access WHERE ot_id=? ORDER BY id;", (ot_id,))
    for r in cursor.fetchall():
        m = dict(r)
        if not (float(m.get("costo_ars") or 0) or float(m.get("costo_usd") or 0) or m.get("fecha_costeo")):
            costeos.setdefault((m.get("almacen_item_id"), m.get("campo") or "", m.get("lote") or ""), None)
            continue
        c = {
            "precio": float(m.get("precio") or 0),
            "moneda": m.get("moneda_costo") or "USD",
            "tc": float(m.get("tc") or 0),
            "origen": m.get("origen_costo") or "manual",
            "mov_ingreso_id": m.get("mov_ingreso_id"),
            "proveedor": m.get("proveedor") or "",
            "nro_comprobante": m.get("nro_remito") or "",
            "fecha_compra": m.get("fecha_compra") or "",
        }
        item = m.get("almacen_item_id")
        costeos[(item, m.get("campo") or "", m.get("lote") or "")] = c
        costeos.setdefault(item, c)
    cursor.execute(
        "SELECT id, item_id, cantidad FROM almacen_movimientos WHERE ot_id=? AND tipo_mov='egreso_ot';",
        (ot_id,),
    )
    for m in cursor.fetchall():
        cursor.execute(
            "UPDATE almacen_items SET stock_cantidad=ROUND(COALESCE(stock_cantidad,0)+?, 6) WHERE id=?;",
            (abs(float(m["cantidad"] or 0)), m["item_id"]),
        )
        cursor.execute("DELETE FROM almacen_movimientos WHERE id=?;", (m["id"],))
    cursor.execute("DELETE FROM margenes_access WHERE ot_id=?;", (ot_id,))
    cursor.execute("DELETE FROM ot_consumos WHERE ot_id=?;", (ot_id,))
    cursor.execute("DELETE FROM ot_destinos WHERE ot_id=?;", (ot_id,))
    return costeos


def editar_ot(
    cursor,
    empresa_id: int,
    ot_id: int,
    *,
    fecha: str,
    tipo_labor: str,
    contratista_nombre: str = "",
    contratista_cuit: str = "",
    campania_id: Optional[int] = None,
    campo_id: Optional[int] = None,
    lote_id: Optional[int] = None,
    superficie_has: float = 0.0,
    cultivo: str = "",
    observaciones: str = "",
    consumos: Optional[List[Dict[str, Any]]] = None,
    nro_ot: Optional[str] = None,
    fecha_aplicacion: str = "",
    labor_cultural: str = "",
    destinos: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Corrige una OT emitida y no confirmada: rehace stock y líneas, conservando el costeo."""
    cursor.execute("SELECT * FROM ordenes_trabajo WHERE id=? AND empresa_id=?;", (ot_id, empresa_id))
    ot = cursor.fetchone()
    if not ot:
        raise ValueError("OT no encontrada.")
    ot = dict(ot)
    if (ot.get("estado") or "") == "Anulada":
        raise ValueError("La OT está anulada; no se puede editar.")
    if int(ot.get("confirmada") or 0):
        raise ValueError("La OT está confirmada. Reabrila para poder editarla.")
    consumos = consumos or []
    if not any(int(c.get("item_id") or 0) for c in consumos):
        raise ValueError("Agregá al menos un producto o labor.")
    nro_actual = str(ot.get("nro_ot") or "").strip()
    nro = str(nro_ot or "").strip() or nro_actual
    if nro != nro_actual and _nro_ot_en_uso(cursor, empresa_id, nro):
        raise ValueError(f"La OT Nº {nro} ya existe.")
    fecha = (fecha or ot.get("fecha") or datetime.now().strftime("%Y-%m-%d"))[:10]
    fecha_aplic = (fecha_aplicacion or fecha)[:10]
    dests = _normalizar_destinos_ot(cursor, destinos, campo_id, lote_id, superficie_has, cultivo)
    total_has = round(sum(d["superficie_has"] for d in dests), 4)

    costeos = _revertir_lineas_ot(cursor, ot_id)
    cursor.execute(
        """
        UPDATE ordenes_trabajo
        SET nro_ot=?, fecha=?, tipo_labor=?, contratista_cuit=?, contratista_nombre=?,
            campania_id=?, campo_id=?, lote_id=?, superficie_has=?, cultivo=?,
            observaciones=?, fecha_aplicacion=?, labor_cultural=?,
            estado='Pendiente de costeo', costo_insumos_neto=0, costo_laboreos_neto=0, costo_total_neto=0
        WHERE id=?;
        """,
        (
            nro, fecha, tipo_labor or "", contratista_cuit or "", contratista_nombre or "",
            campania_id, dests[0]["campo_id"], dests[0]["lote_id"], total_has,
            dests[0]["cultivo"] or cultivo or "",
            observaciones or "", fecha_aplic, labor_cultural or "", ot_id,
        ),
    )
    lineas, sin_stock = _crear_lineas_ot(
        cursor, empresa_id, ot_id, nro,
        fecha=fecha, fecha_aplic=fecha_aplic, tipo_labor=tipo_labor,
        labor_cultural=labor_cultural, contratista_nombre=contratista_nombre,
        campania_id=campania_id, destinos=dests, consumos=consumos,
    )
    recosteadas = 0
    if costeos:
        cursor.execute("SELECT * FROM margenes_access WHERE ot_id=? ORDER BY id;", (ot_id,))
        for r in cursor.fetchall():
            m = dict(r)
            item = m.get("almacen_item_id")
            clave = (item, m.get("campo") or "", m.get("lote") or "")
            c = costeos[clave] if clave in costeos else costeos.get(item)
            if not c:
                continue
            try:
                costear_linea_ot(cursor, empresa_id, int(m["id"]), **c)
                recosteadas += 1
            except ValueError:
                pass
    _recalcular_totales_ot(cursor, ot_id)
    msg = f"OT {nro} actualizada con {lineas} línea(s)"
    if len(dests) > 1:
        msg += f" en {len(dests)} lotes"
    msg += "."
    if recosteadas:
        msg += f" Se conservó el costeo de {recosteadas} línea(s)."
    if sin_stock:
        msg += " Atención: algunos productos quedaron con stock negativo en el almacén."
    return {
        "status": "success",
        "ot_id": ot_id,
        "nro_ot": nro,
        "lineas": lineas,
        "sin_stock": sin_stock,
        "message": msg,
    }


def confirmar_ot(cursor, empresa_id: int, ot_id: int, confirmar: bool = True) -> Dict[str, Any]:
    cursor.execute("SELECT * FROM ordenes_trabajo WHERE id=? AND empresa_id=?;", (ot_id, empresa_id))
    ot = cursor.fetchone()
    if not ot:
        raise ValueError("OT no encontrada.")
    if (ot["estado"] or "") == "Anulada":
        raise ValueError("La OT está anulada.")
    cursor.execute(
        "UPDATE ordenes_trabajo SET confirmada=?, fecha_confirmacion=? WHERE id=?;",
        (
            1 if confirmar else 0,
            datetime.now().strftime("%Y-%m-%d %H:%M") if confirmar else None,
            ot_id,
        ),
    )
    accion = "confirmada" if confirmar else "reabierta para edición"
    return {"status": "success", "message": f"OT {ot['nro_ot']} {accion}."}


def detalle_ot(cursor, empresa_id: int, ot_id: int) -> Dict[str, Any]:
    """Cabecera, destinos y consumos (agrupados por ítem) para cargar la OT en el formulario."""
    cursor.execute("SELECT * FROM ordenes_trabajo WHERE id=? AND empresa_id=?;", (ot_id, empresa_id))
    ot = cursor.fetchone()
    if not ot:
        raise ValueError("OT no encontrada.")
    data = dict(ot)
    cursor.execute(
        """
        SELECT d.campo_id, d.lote_id, d.superficie_has, d.cultivo,
               c.nombre AS campo_nombre, l.nombre AS lote_nombre
        FROM ot_destinos d
        LEFT JOIN campos_agro c ON c.id = d.campo_id
        LEFT JOIN lotes_agro l ON l.id = d.lote_id
        WHERE d.ot_id=? ORDER BY d.orden, d.id;
        """,
        (ot_id,),
    )
    destinos = [dict(r) for r in cursor.fetchall()]
    if not destinos:
        destinos = [{
            "campo_id": data.get("campo_id"), "lote_id": data.get("lote_id"),
            "superficie_has": data.get("superficie_has") or 0, "cultivo": data.get("cultivo") or "",
        }]
    cursor.execute(
        """
        SELECT oc.item_id, MAX(oc.tipo_item) AS tipo_item, MAX(oc.unidad) AS unidad,
               MAX(oc.dosis_por_ha) AS dosis_por_ha, SUM(oc.cantidad) AS cantidad,
               MAX(i.nombre) AS nombre, MIN(oc.id) AS orden
        FROM ot_consumos oc
        LEFT JOIN almacen_items i ON i.id = oc.item_id
        WHERE oc.ot_id=?
        GROUP BY oc.item_id
        ORDER BY orden;
        """,
        (ot_id,),
    )
    data["destinos"] = destinos
    data["consumos"] = [dict(r) for r in cursor.fetchall()]
    return data


def _recalcular_totales_ot(cursor, ot_id: int) -> None:
    cursor.execute(
        """
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN COALESCE(costo_ars,0) != 0 OR COALESCE(costo_usd,0) != 0 THEN 1 ELSE 0 END) AS costeadas,
               COALESCE(SUM(CASE WHEN TRIM(COALESCE(producto,'')) != '' THEN costo_usd END), 0) AS insumos_usd,
               COALESCE(SUM(CASE WHEN TRIM(COALESCE(producto,'')) = '' THEN costo_ars END), 0) AS labores_ars,
               COALESCE(SUM(costo_usd), 0) AS total_usd
        FROM margenes_access WHERE ot_id=?;
        """,
        (ot_id,),
    )
    r = dict(cursor.fetchone())
    n = int(r["n"] or 0)
    costeadas = int(r["costeadas"] or 0)
    if n and costeadas >= n:
        estado = "Costeada"
    elif costeadas:
        estado = "Costeo parcial"
    else:
        estado = "Pendiente de costeo"
    cursor.execute(
        """
        UPDATE ordenes_trabajo
        SET costo_insumos_neto=?, costo_laboreos_neto=?, costo_total_neto=?, estado=?
        WHERE id=? AND COALESCE(estado,'') != 'Anulada';
        """,
        (
            round(float(r["insumos_usd"] or 0), 2), round(float(r["labores_ars"] or 0), 2),
            round(float(r["total_usd"] or 0), 2), estado, ot_id,
        ),
    )


def _es_linea_laboreo(linea: Dict[str, Any]) -> bool:
    return not (linea.get("producto") or "").strip()


def _item_de_linea(cursor, empresa_id: int, linea: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if linea.get("almacen_item_id"):
        cursor.execute(
            "SELECT * FROM almacen_items WHERE id=? AND empresa_id=?;",
            (linea["almacen_item_id"], empresa_id),
        )
        r = cursor.fetchone()
        if r:
            return dict(r)
    nombre = (linea.get("producto") or linea.get("laboreo") or "").strip()
    if not nombre:
        return None
    cursor.execute(
        """
        SELECT * FROM almacen_items
        WHERE empresa_id=? AND UPPER(TRIM(nombre))=UPPER(TRIM(?))
        ORDER BY COALESCE(activo,1) DESC, id LIMIT 1;
        """,
        (empresa_id, nombre),
    )
    r = cursor.fetchone()
    return dict(r) if r else None


def _cuit_contratista(cursor, empresa_id: int, linea: Dict[str, Any]) -> str:
    if linea.get("ot_id"):
        cursor.execute("SELECT contratista_cuit FROM ordenes_trabajo WHERE id=?;", (linea["ot_id"],))
        r = cursor.fetchone()
        if r and (r[0] or "").strip():
            return r[0].strip()
    nombre = (linea.get("contratista") or "").strip()
    if not nombre:
        return ""
    cursor.execute(
        """
        SELECT cuit FROM entidades
        WHERE COALESCE(empresa_id,1)=? AND COALESCE(es_proveedor,0)=1
          AND (UPPER(TRIM(razon_social))=UPPER(TRIM(?)) OR UPPER(TRIM(nombre_fantasia))=UPPER(TRIM(?)))
        LIMIT 1;
        """,
        (empresa_id, nombre, nombre),
    )
    r = cursor.fetchone()
    return (r[0] or "").strip() if r else ""


def _tc_sugerido(cursor, empresa_id: int, fecha: str) -> float:
    cursor.execute(
        """
        SELECT tc FROM margenes_access
        WHERE empresa_id=? AND COALESCE(tc,0) > 0 AND COALESCE(fecha_aplicacion,'') <= ?
        ORDER BY fecha_aplicacion DESC, id DESC LIMIT 1;
        """,
        (empresa_id, fecha or "9999-12-31"),
    )
    r = cursor.fetchone()
    return float(r[0]) if r else 0.0


def listar_lineas_ot(
    cursor,
    empresa_id: int,
    *,
    solo_pendientes: bool = True,
    nro_orden: Optional[int] = None,
    campania_codigo: str = "",
    ultimas_ot: int = 40,
) -> List[Dict[str, Any]]:
    """Líneas de OT (Access + nuevas) agrupables por Nº de orden, para costear."""
    where = " WHERE m.empresa_id=? AND m.nro_orden IS NOT NULL "
    params: List[Any] = [empresa_id]
    if nro_orden:
        where += " AND m.nro_orden=? "
        params.append(int(nro_orden))
    if campania_codigo:
        where += " AND m.campania_codigo=? "
        params.append(campania_codigo)
    pend = " AND COALESCE(m.costo_ars,0)=0 AND COALESCE(m.costo_usd,0)=0 "
    if solo_pendientes:
        where += pend
    anuladas = " AND (m.ot_id IS NULL OR COALESCE(o.estado,'') != 'Anulada') "
    cursor.execute(
        f"""
        SELECT DISTINCT m.nro_orden FROM margenes_access m
        LEFT JOIN ordenes_trabajo o ON o.id = m.ot_id
        {where} {anuladas}
        ORDER BY m.nro_orden DESC LIMIT ?;
        """,
        params + [max(1, min(int(ultimas_ot or 40), 500))],
    )
    nros = [r[0] for r in cursor.fetchall()]
    if not nros:
        return []
    marcas = ",".join("?" for _ in nros)
    cursor.execute(
        f"""
        SELECT m.id, m.nro_orden, m.campania_codigo, m.cultivo, m.campo, m.lote, m.cantidad_has,
               m.fecha_orden, m.fecha_aplicacion, m.laboreo, m.labor_cultural, m.contratista,
               m.producto, m.dosis_ha, m.cantidad_total, m.unidad, m.precio, m.tc,
               m.costo_ars, m.costo_usd, m.moneda_costo, m.origen_costo, m.proveedor,
               m.nro_remito, m.fecha_compra, m.almacen_item_id, m.ot_id, m.id_access,
               o.estado AS estado_ot
        FROM margenes_access m
        LEFT JOIN ordenes_trabajo o ON o.id = m.ot_id
        {where} {anuladas} AND m.nro_orden IN ({marcas})
        ORDER BY m.nro_orden DESC, CASE WHEN TRIM(COALESCE(m.producto,''))='' THEN 0 ELSE 1 END, m.id;
        """,
        params + nros,
    )
    return [dict(r) for r in cursor.fetchall()]


def opciones_costeo_linea(cursor, empresa_id: int, linea_id: int) -> Dict[str, Any]:
    """Compras del almacén y facturas del contratista para elegir el costo de una línea."""
    cursor.execute("SELECT * FROM margenes_access WHERE id=? AND empresa_id=?;", (linea_id, empresa_id))
    r = cursor.fetchone()
    if not r:
        raise ValueError("Línea de OT no encontrada.")
    linea = dict(r)
    es_lab = _es_linea_laboreo(linea)
    item = _item_de_linea(cursor, empresa_id, linea)
    fecha = (linea.get("fecha_aplicacion") or linea.get("fecha_orden") or "")[:10]
    cuit = _cuit_contratista(cursor, empresa_id, linea) if es_lab else ""

    ingresos: List[Dict[str, Any]] = []
    vistos = set()

    def _agregar_ingresos(sql: str, args: tuple) -> None:
        cursor.execute(sql, args)
        for m in cursor.fetchall():
            d = dict(m)
            if d["id"] in vistos:
                continue
            vistos.add(d["id"])
            ars = float(d.get("precio_unitario_neto") or 0)
            usd = float(d.get("precio_unitario_usd") or 0)
            d["tc"] = round(ars / usd, 4) if usd > 0 and ars > 0 else 0
            d["posterior"] = bool(fecha and (d.get("fecha") or "") > fecha)
            ingresos.append(d)

    base_sql = """
        SELECT m.id, m.fecha, m.cantidad, m.precio_unitario_neto,
               COALESCE(m.precio_unitario_usd,0) AS precio_unitario_usd,
               m.proveedor_cuit, m.proveedor_nombre, m.nro_comprobante,
               i.id AS item_id, i.nombre AS item_nombre, i.unidad
        FROM almacen_movimientos m JOIN almacen_items i ON i.id = m.item_id
        WHERE m.empresa_id=? AND m.tipo_mov='ingreso' AND COALESCE(m.precio_unitario_neto,0) > 0
    """
    if item:
        _agregar_ingresos(base_sql + " AND m.item_id=? ORDER BY m.fecha DESC, m.id DESC LIMIT 40;",
                          (empresa_id, item["id"]))
    if es_lab and cuit:
        _agregar_ingresos(
            base_sql + """ AND i.tipo='laboreo'
              AND REPLACE(COALESCE(m.proveedor_cuit,''),'-','')=REPLACE(?,'-','')
            ORDER BY m.fecha DESC, m.id DESC LIMIT 40;""",
            (empresa_id, cuit),
        )
    ingresos.sort(key=lambda d: (d.get("fecha") or "", d["id"]), reverse=True)

    facturas: List[Dict[str, Any]] = []
    if es_lab and cuit:
        cursor.execute(
            """
            SELECT id, fecha, tipo_comprobante, numero_comprobante, neto, iva, debe, total, observaciones
            FROM cuentas_corrientes
            WHERE COALESCE(empresa_id,1)=? AND REPLACE(entidad_id,'-','')=REPLACE(?,'-','')
              AND COALESCE(debe,0) > 0
              AND COALESCE(tipo_comprobante,'') NOT LIKE 'Ajuste%'
              AND COALESCE(tipo_comprobante,'') NOT LIKE 'Pago%'
            ORDER BY fecha DESC, id DESC LIMIT 20;
            """,
            (empresa_id, cuit),
        )
        for f in cursor.fetchall():
            d = dict(f)
            neto = float(d.get("neto") or 0)
            d["neto_estimado"] = neto <= 0
            d["neto_usar"] = round(neto if neto > 0 else float(d.get("debe") or 0) / 1.21, 2)
            d["comprobante"] = (d.get("numero_comprobante") or d.get("tipo_comprobante") or "").strip()
            facturas.append(d)

    sugerido = None
    cant = float(linea.get("cantidad_total") or 0)
    if item and not es_lab and cant > 0:
        try:
            sugerido = valuar_salida_insumo(cursor, empresa_id, int(item["id"]), cant)
        except ValueError:
            sugerido = None

    return {
        "linea": linea,
        "es_laboreo": es_lab,
        "item": {k: item.get(k) for k in ("id", "nombre", "unidad", "costo_promedio_neto", "costo_promedio_usd", "stock_cantidad")} if item else None,
        "contratista_cuit": cuit,
        "ingresos": ingresos,
        "facturas": facturas,
        "sugerido_criterio": sugerido,
        "tc_sugerido": _tc_sugerido(cursor, empresa_id, fecha),
    }


def costear_linea_ot(
    cursor,
    empresa_id: int,
    linea_id: int,
    *,
    precio: float,
    moneda: str = "USD",
    tc: float = 0.0,
    origen: str = "manual",
    mov_ingreso_id: Optional[int] = None,
    proveedor: str = "",
    nro_comprobante: str = "",
    fecha_compra: str = "",
) -> Dict[str, Any]:
    """Asigna el costo a una línea de OT. Costo = cantidad × precio unitario neto."""
    cursor.execute("SELECT * FROM margenes_access WHERE id=? AND empresa_id=?;", (linea_id, empresa_id))
    r = cursor.fetchone()
    if not r:
        raise ValueError("Línea de OT no encontrada.")
    linea = dict(r)
    precio = float(precio or 0)
    tc = float(tc or 0)
    moneda = "ARS" if (moneda or "").upper() in ("ARS", "$", "PESOS") else "USD"
    if precio < 0:
        raise ValueError("El precio no puede ser negativo.")
    cant = float(linea.get("cantidad_total") or 0)
    if cant <= 0:
        has = float(linea.get("cantidad_has") or 0)
        dosis = float(linea.get("dosis_ha") or 0) or (1.0 if _es_linea_laboreo(linea) else 0.0)
        cant = round(has * dosis, 4)
    if cant <= 0:
        raise ValueError("La línea no tiene cantidad (hectáreas × dosis).")
    if precio > 0 and tc <= 0:
        raise ValueError("Indicá el tipo de cambio para calcular pesos y dólares.")
    total = round(cant * precio, 2)
    if moneda == "USD":
        costo_usd, costo_ars = total, round(total * tc, 2)
        precio_ars, precio_usd = round(precio * tc, 4), precio
    else:
        costo_ars, costo_usd = total, round(total / tc, 2) if tc else 0.0
        precio_ars, precio_usd = precio, round(precio / tc, 4) if tc else 0.0

    if mov_ingreso_id:
        cursor.execute(
            "SELECT fecha, proveedor_nombre, nro_comprobante FROM almacen_movimientos WHERE id=? AND empresa_id=?;",
            (int(mov_ingreso_id), empresa_id),
        )
        m = cursor.fetchone()
        if m:
            fecha_compra = fecha_compra or (m["fecha"] or "")
            proveedor = proveedor or (m["proveedor_nombre"] or "")
            nro_comprobante = nro_comprobante or (m["nro_comprobante"] or "")

    cursor.execute(
        """
        UPDATE margenes_access
        SET cantidad_total=?, precio=?, tc=?, costo_ars=?, costo_usd=?, moneda_costo=?,
            origen_costo=?, mov_ingreso_id=?, proveedor=?, nro_remito=?, fecha_compra=?,
            fecha_costeo=?
        WHERE id=?;
        """,
        (
            cant, round(precio, 4), round(tc, 4), costo_ars, costo_usd, moneda,
            (origen or "manual")[:20], int(mov_ingreso_id) if mov_ingreso_id else None,
            proveedor or "", nro_comprobante or "", (fecha_compra or "")[:10],
            datetime.now().strftime("%Y-%m-%d %H:%M"), linea_id,
        ),
    )

    if linea.get("ot_consumo_id"):
        es_lab = _es_linea_laboreo(linea)
        cursor.execute(
            "UPDATE ot_consumos SET costo_unitario_neto=?, costo_total_neto=? WHERE id=?;",
            (
                precio_ars if es_lab else precio_usd,
                costo_ars if es_lab else costo_usd,
                linea["ot_consumo_id"],
            ),
        )
        cursor.execute(
            """
            UPDATE almacen_movimientos
            SET precio_unitario_neto=?, importe_neto=?, precio_unitario_usd=?
            WHERE id=(SELECT movimiento_id FROM ot_consumos WHERE id=?);
            """,
            (precio_ars, costo_ars, precio_usd, linea["ot_consumo_id"]),
        )
    if linea.get("ot_id"):
        _recalcular_totales_ot(cursor, int(linea["ot_id"]))
    return {
        "status": "success",
        "id": linea_id,
        "cantidad": cant,
        "precio": round(precio, 4),
        "moneda": moneda,
        "tc": round(tc, 4),
        "costo_ars": costo_ars,
        "costo_usd": costo_usd,
    }


def anular_ot(cursor, empresa_id: int, ot_id: int) -> Dict[str, Any]:
    """Anula una OT emitida en el sistema: devuelve el stock y borra sus líneas de costos."""
    cursor.execute("SELECT * FROM ordenes_trabajo WHERE id=? AND empresa_id=?;", (ot_id, empresa_id))
    ot = cursor.fetchone()
    if not ot:
        raise ValueError("OT no encontrada.")
    if (ot["estado"] or "") == "Anulada":
        raise ValueError("La OT ya está anulada.")
    if int(ot["confirmada"] or 0):
        raise ValueError("La OT está confirmada. Reabrila antes de anularla.")
    cursor.execute(
        "SELECT id, item_id, cantidad FROM almacen_movimientos WHERE ot_id=? AND tipo_mov='egreso_ot';",
        (ot_id,),
    )
    for m in cursor.fetchall():
        cursor.execute(
            "UPDATE almacen_items SET stock_cantidad=ROUND(COALESCE(stock_cantidad,0)+?, 6) WHERE id=?;",
            (abs(float(m["cantidad"] or 0)), m["item_id"]),
        )
        cursor.execute("DELETE FROM almacen_movimientos WHERE id=?;", (m["id"],))
    cursor.execute("UPDATE ot_consumos SET movimiento_id=NULL WHERE ot_id=?;", (ot_id,))
    cursor.execute("DELETE FROM margenes_access WHERE ot_id=?;", (ot_id,))
    cursor.execute(
        """
        UPDATE ordenes_trabajo
        SET estado='Anulada', costo_insumos_neto=0, costo_laboreos_neto=0, costo_total_neto=0
        WHERE id=?;
        """,
        (ot_id,),
    )
    return {"status": "success", "message": f"OT {ot['nro_ot']} anulada. El stock volvió al almacén."}


def costos_por_lote_campania(cursor, campania_id: int, empresa_id: int) -> List[Dict[str, Any]]:
    """Resumen de costos netos imputados por OT a cada lote de la campaña."""
    filtro = """
        FROM ordenes_trabajo ot
        LEFT JOIN ot_destinos d ON d.ot_id = ot.id
        WHERE ot.empresa_id = ? AND ot.campania_id = ?
          AND COALESCE(ot.estado,'') != 'Anulada'
    """
    cursor.execute(
        f"""
        SELECT COALESCE(d.lote_id, ot.lote_id) AS lote_id,
               COALESCE(d.campo_id, ot.campo_id) AS campo_id,
               COALESCE(SUM(COALESCE(d.superficie_has, ot.superficie_has)),0) AS has_trabajadas
        {filtro}
        GROUP BY 1, 2;
        """,
        (empresa_id, campania_id),
    )
    grupos: Dict[Any, Dict[str, Any]] = {}
    for r in cursor.fetchall():
        grupos[(r["lote_id"], r["campo_id"])] = {
            "lote_id": r["lote_id"], "campo_id": r["campo_id"],
            "has_trabajadas": float(r["has_trabajadas"] or 0),
            "insumos": 0.0, "laboreos": 0.0, "total": 0.0,
        }
    cursor.execute(
        """
        SELECT COALESCE(d.lote_id, ot.lote_id) AS lote_id,
               COALESCE(d.campo_id, ot.campo_id) AS campo_id,
               COALESCE(SUM(CASE WHEN TRIM(COALESCE(m.producto,'')) != '' THEN m.costo_usd END),0) AS insumos,
               COALESCE(SUM(CASE WHEN TRIM(COALESCE(m.producto,'')) = '' THEN m.costo_ars END),0) AS laboreos,
               COALESCE(SUM(m.costo_usd),0) AS total
        FROM margenes_access m
        JOIN ordenes_trabajo ot ON ot.id = m.ot_id
        LEFT JOIN ot_consumos oc ON oc.id = m.ot_consumo_id
        LEFT JOIN ot_destinos d ON d.id = oc.destino_id
        WHERE ot.empresa_id = ? AND ot.campania_id = ?
          AND COALESCE(ot.estado,'') != 'Anulada'
        GROUP BY 1, 2;
        """,
        (empresa_id, campania_id),
    )
    for r in cursor.fetchall():
        g = grupos.setdefault((r["lote_id"], r["campo_id"]), {
            "lote_id": r["lote_id"], "campo_id": r["campo_id"], "has_trabajadas": 0.0,
            "insumos": 0.0, "laboreos": 0.0, "total": 0.0,
        })
        g["insumos"] += float(r["insumos"] or 0)
        g["laboreos"] += float(r["laboreos"] or 0)
        g["total"] += float(r["total"] or 0)
    rows = []
    for g in grupos.values():
        g["lote_nombre"] = ""
        g["campo_nombre"] = ""
        if g["lote_id"]:
            cursor.execute("SELECT nombre FROM lotes_agro WHERE id=?;", (g["lote_id"],))
            r = cursor.fetchone()
            g["lote_nombre"] = (r["nombre"] if r else "") or ""
        if g["campo_id"]:
            cursor.execute("SELECT nombre FROM campos_agro WHERE id=?;", (g["campo_id"],))
            r = cursor.fetchone()
            g["campo_nombre"] = (r["nombre"] if r else "") or ""
        for k in ("insumos", "laboreos", "total"):
            g[k] = round(g[k], 2)
        has = g["has_trabajadas"]
        g["costo_por_ha"] = round(g["total"] / has, 2) if has > 0 else 0
        rows.append(g)
    rows.sort(key=lambda x: ((x["campo_nombre"] or "").lower(), (x["lote_nombre"] or "").lower()))
    return rows
