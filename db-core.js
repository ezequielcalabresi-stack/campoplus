// CAmpo+ Core Database Engine (SQLite con sql.js y Persistencia Atómica Global)
const STORAGE_KEY = "campoplus_master_db_v1";

let globalDb = null;
let dbLoadedPromise = null;

function getDbInstance() {
    if (globalDb) return Promise.resolve(globalDb);
    if (dbLoadedPromise) return dbLoadedPromise;

    dbLoadedPromise = new Promise(async (resolve, reject) => {
        try {
            const SQL = await initSqlJs({
                locateFile: file => `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.8.0/${file}`
            });

            let savedDbHex = localStorage.getItem(STORAGE_KEY);
            
            if (savedDbHex) {
                try {
                    let uInt8Array = new Uint8Array(savedDbHex.match(/.{1,2}/g).map(byte => parseInt(byte, 16)));
                    globalDb = new SQL.Database(uInt8Array);
                    console.info("✅ [DB Core] Base de datos restaurada desde localStorage.");
                } catch(err) {
                    console.warn("⚠️ [DB Core] Backup en localStorage corrupto. Reinicializando desde archivo físico...");
                    savedDbHex = null;
                }
            }

            if (!savedDbHex) {
                const response = await fetch('campoplus.db');
                const buffer = await response.arrayBuffer();
                globalDb = new SQL.Database(new Uint8Array(buffer));
                persistDb();
                console.info("✅ [DB Core] Inicializada desde campoplus.db físico.");
            }

            inicializarYBlindarEsquema(globalDb);
            resolve(globalDb);
        } catch (error) {
            console.error("🚨 [DB Core Error crítico]:", error);
            reject(error);
        }
    });

    return dbLoadedPromise;
}

// PERSISTENCIA ATÓMICA AUTOMÁTICA
function persistDb() {
    if (!globalDb) return;
    try {
        let binaryArray = globalDb.export();
        let hexString = Array.from(binaryArray).map(b => b.toString(16).padStart(2, '0')).join('');
        localStorage.setItem(STORAGE_KEY, hexString);
    } catch (e) {
        console.error("🚨 [DB Core Error al persistir]:", e);
    }
}

// WRAPPER DE EJECUCIÓN SEGURA (Garantiza persistencia en cada escritura)
function ejecutarSQL(sqlQuery, params = []) {
    if (!globalDb) throw new Error("Base de datos no inicializada.");
    try {
        globalDb.run(sqlQuery, params);
        persistDb(); // <-- Guarda automáticamente tras CADA cambio
    } catch (e) {
        console.error("🚨 [SQL Error]:", e, "Query:", sqlQuery);
        throw e;
    }
}

function inicializarYBlindarEsquema(db) {
    db.run(`CREATE TABLE IF NOT EXISTS entidades (
        cuit TEXT PRIMARY KEY, razon_social TEXT, nombre_fantasia TEXT, domicilio TEXT,
        localidad TEXT, codigo_postal TEXT, provincia_id TEXT, condicion_iva TEXT,
        excluido_rg830 INTEGER DEFAULT 0, es_proveedor INTEGER DEFAULT 0,
        es_cliente INTEGER DEFAULT 0, es_arrendatario INTEGER DEFAULT 0
    );`);

    db.run(`CREATE TABLE IF NOT EXISTS cuentas_corrientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, entidad_id TEXT, tipo_comprobante TEXT,
        numero_comprobante TEXT, fecha TEXT, vencimiento TEXT, neto REAL, iva REAL,
        percepcion_iibb REAL, total REAL, estado TEXT DEFAULT 'Pendiente', usuario_registro TEXT
    );`);

    db.run(`CREATE TABLE IF NOT EXISTS asientos_contables (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, concepto TEXT, cuenta TEXT,
        debe REAL, haber REAL, referencia TEXT, usuario_registro TEXT
    );`);
    
    persistDb();
}