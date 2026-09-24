# CampoPlus-Demo — cómo subir a Railway (sin tocar Silo Chico)

Esta carpeta es una **copia de presentación**. No incluye `campoplus.db` ni padrones.
La base de datos se crea **vacía** en el servidor al primer arranque, con empresa:

- Razón social: `CAmpo+ Demo S.A.`
- CUIT: `30999999999`
- Tenant: `demo`

El proyecto real (`Campo + Silo CHico`) no se modifica ni se despliega desde aquí.

---

## 1. Probar en local (opcional)

```powershell
cd "...\campo +\CampoPlus-Demo"
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Abrí http://localhost:8000/Index.html — debe verse el banner ámbar de demostración.

---

## 2. Subir a Railway

1. Creá cuenta en https://railway.app
2. **New Project** → **Deploy from GitHub** (recomendado) o **Empty Project** + subir esta carpeta.
3. Si usás GitHub:
   - Inicializá git solo en `CampoPlus-Demo` (no en Silo Chico).
   - Subí el repo (`.gitignore` ya excluye `*.db`).
   - En Railway: Connect Repo → Deploy.
4. Railway detecta el `Dockerfile` (`railway.toml` apunta a él).
5. Cuando termine el deploy, abrí **Settings → Networking → Generate Domain**.
6. Entrá a `https://TU-DOMINIO.up.railway.app/Index.html`

Variable de entorno: Railway inyecta `PORT` solo; no hace falta configurar otra cosa.

---

## 3. Datos en la presentación

- Arranca **sin** proveedores, saldos ni contratos: cargás en vivo frente al cliente.
- Un **redeploy** puede borrar la DB (disco efímero). Para conservar lo cargado en la charla:
  - Railway → **Volumes** → montar volumen en `/app` o ruta del archivo `campoplus.db`.

---

## 4. Actualizar el demo con correcciones del proyecto real

Desde PowerShell, en esta carpeta:

```powershell
.\sync_demo_code.ps1
```

Copia código desde `Campo + Silo CHico` **sin** bases ni padrones, y vuelve a aplicar la semilla demo + banner + `PORT`.

---

## Importante

| Carpeta | Uso |
|---------|-----|
| `Campo + Silo CHico` | Trabajo diario y datos reales |
| `CampoPlus-Demo` | Solo presentación / hosting |

Nunca copies `campoplus.db` de Silo Chico a esta carpeta ni a Railway.
