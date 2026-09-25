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
    cursor.execute("PRAGMA table_info(almacen_movimientos);")
    cols_mov = {r[1] for r in cursor.fetchall()}
    if "precio_unitario_usd" not in cols_mov:
        cursor.execute("ALTER TABLE almacen_movimientos ADD COLUMN precio_unitario_usd REAL DEFAULT 0;")
    _aplicar_costos_usd_access(cursor)


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


def siguiente_nro_ot(cursor, empresa_id: int, anio: Optional[int] = None) -> str:
    anio = anio or datetime.now().year
    prefix = f"OT-{anio}-"
    cursor.execute(
        """
        SELECT nro_ot FROM ordenes_trabajo
        WHERE empresa_id=? AND nro_ot LIKE ?
        ORDER BY id DESC LIMIT 1;
        """,
        (empresa_id, f"{prefix}%"),
    )
    row = cursor.fetchone()
    seq = 1
    if row and row["nro_ot"]:
        try:
            seq = int(str(row["nro_ot"]).split("-")[-1]) + 1
        except ValueError:
            seq = 1
    return f"{prefix}{seq:05d}"


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
) -> Dict[str, Any]:
    """
    Emite OT y descuenta del almacén (productos y/o laboreos) a costo promedio neto.
    cada consumo: {item_id, cantidad?, dosis_por_ha?}
    """
    consumos = consumos or []
    fecha = (fecha or datetime.now().strftime("%Y-%m-%d"))[:10]
    nro = nro_ot or siguiente_nro_ot(cursor, empresa_id)

    cursor.execute(
        """
        INSERT INTO ordenes_trabajo
        (empresa_id, nro_ot, fecha, tipo_labor, contratista_cuit, contratista_nombre,
         campania_id, campo_id, lote_id, superficie_has, cultivo, estado, observaciones)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Emitida', ?);
        """,
        (
            empresa_id, nro, fecha, tipo_labor or "",
            contratista_cuit or "", contratista_nombre or "",
            campania_id, campo_id, lote_id, superficie_has, cultivo or "",
            observaciones or "",
        ),
    )
    ot_id = cursor.lastrowid
    costo_prod = 0.0
    costo_lab = 0.0

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
        if cant <= 0 and dosis > 0 and superficie_has > 0:
            cant = round(dosis * superficie_has, 6)
        if cant <= 0:
            continue
        stock = float(item.get("stock_cantidad") or 0)
        if cant > stock + 1e-6:
            raise ValueError(
                f"Stock insuficiente de '{item.get('nombre')}': hay {stock}, se piden {cant}."
            )
        if (item.get("tipo") or "") != "laboreo":
            valuacion = valuar_salida_insumo(cursor, empresa_id, item_id, cant)
            costo_u = float(valuacion["costo_unitario"] or 0)
            costo_t = float(valuacion["costo_total"] or 0)
        else:
            costo_u = float(item.get("costo_promedio_neto") or 0)
            costo_t = round(cant * costo_u, 2)
        nuevo_stock = round(stock - cant, 6)

        cursor.execute(
            "UPDATE almacen_items SET stock_cantidad=? WHERE id=?;",
            (nuevo_stock, item_id),
        )
        cursor.execute(
            """
            INSERT INTO almacen_movimientos
            (empresa_id, item_id, fecha, tipo_mov, cantidad, precio_unitario_neto, importe_neto,
             stock_resultante, costo_prom_resultante, campania_id, lote_id, ot_id, observaciones)
            VALUES (?, ?, ?, 'egreso_ot', ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                empresa_id, item_id, fecha, cant, costo_u, costo_t,
                nuevo_stock, costo_u, campania_id, lote_id, ot_id,
                f"Consumo OT {nro}",
            ),
        )
        mov_id = cursor.lastrowid
        tipo_item = item.get("tipo") or "producto"
        cursor.execute(
            """
            INSERT INTO ot_consumos
            (ot_id, item_id, tipo_item, dosis_por_ha, cantidad, unidad,
             costo_unitario_neto, costo_total_neto, movimiento_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                ot_id, item_id, tipo_item, dosis, cant, item.get("unidad") or "",
                costo_u, costo_t, mov_id,
            ),
        )
        if tipo_item == "laboreo":
            costo_lab += costo_t
        else:
            costo_prod += costo_t

    insumos_usd = round(costo_prod, 2)
    labores_ars = round(costo_lab, 2)
    cursor.execute(
        """
        UPDATE ordenes_trabajo
        SET costo_insumos_neto=?, costo_laboreos_neto=?, costo_total_neto=?
        WHERE id=?;
        """,
        (insumos_usd, labores_ars, insumos_usd, ot_id),
    )
    return {
        "status": "success",
        "ot_id": ot_id,
        "nro_ot": nro,
        "costo_insumos_neto": insumos_usd,
        "costo_laboreos_neto": labores_ars,
        "costo_total_neto": insumos_usd,
        "message": f"OT {nro} emitida. Insumos en U$S (moneda constante). Laboreos en pesos netos.",
    }


def costos_por_lote_campania(cursor, campania_id: int, empresa_id: int) -> List[Dict[str, Any]]:
    """Resumen de costos netos imputados por OT a cada lote de la campaña."""
    cursor.execute(
        """
        SELECT
            ot.lote_id,
            l.nombre AS lote_nombre,
            c.nombre AS campo_nombre,
            COALESCE(SUM(ot.costo_insumos_neto),0) AS insumos,
            COALESCE(SUM(ot.costo_laboreos_neto),0) AS laboreos,
            COALESCE(SUM(ot.costo_total_neto),0) AS total,
            COALESCE(SUM(ot.superficie_has),0) AS has_trabajadas
        FROM ordenes_trabajo ot
        LEFT JOIN lotes_agro l ON l.id = ot.lote_id
        LEFT JOIN campos_agro c ON c.id = ot.campo_id
        WHERE ot.empresa_id = ? AND ot.campania_id = ?
          AND COALESCE(ot.estado,'') != 'Anulada'
        GROUP BY ot.lote_id, l.nombre, c.nombre
        ORDER BY c.nombre, l.nombre;
        """,
        (empresa_id, campania_id),
    )
    rows = []
    for r in cursor.fetchall():
        d = dict(r)
        has = float(d.get("has_trabajadas") or 0)
        total = float(d.get("total") or 0)
        d["costo_por_ha"] = round(total / has, 2) if has > 0 else 0
        rows.append(d)
    return rows
