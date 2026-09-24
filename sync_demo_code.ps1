# Sync código desde el proyecto Silo Chico hacia CampoPlus-Demo
# (sin bases de datos ni padrones). Reaplica parches de demo.
# Uso: .\sync_demo_code.ps1

$ErrorActionPreference = "Stop"

$DemoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SrcRoot  = Join-Path (Split-Path -Parent $DemoRoot) "Campo + Silo CHico"

if (-not (Test-Path $SrcRoot)) {
    Write-Error "No se encuentra el proyecto origen: $SrcRoot"
}

Write-Host "Origen : $SrcRoot"
Write-Host "Destino: $DemoRoot"

$xd = @("__pycache__", "padrones_arba", ".git", ".cursor", "agent-transcripts", "terminals", "CampoPlus-Demo")
$xf = @(
    "*.db", "*.db-shm", "*.db-wal", "*.db.bak", "*.bak",
    "PadronRGS*.TXT", "PadronRGS*.txt", "padron_unificado.json",
    "main - copia.py", "tablas1xlsx.xlsx", "retenciones.xlsx",
    "Dockerfile", "Procfile", "railway.toml", ".dockerignore", ".gitignore",
    "README-DEPLOY.md", "sync_demo_code.ps1", "requirements.txt"
)

robocopy $SrcRoot $DemoRoot /E /XD $xd /XF $xf /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
# robocopy: 0/1 = OK
if ($LASTEXITCODE -ge 8) {
    Write-Error "robocopy fallo con codigo $LASTEXITCODE"
}

# --- Reaplicar parches solo en la copia demo ---
$main = Join-Path $DemoRoot "main.py"
$mainText = Get-Content -Raw -Encoding UTF8 $main

$seedEmpOld = @'
            [
                ("Silo Chico S.A.", "30717802868", "silochico", "Chivilcoy"),
                ("Ezequiel Calabresi", "20270684271", "cala", "Chivilcoy"),
            ],
'@
$seedEmpNew = @'
            [
                ("CAmpo+ Demo S.A.", "30999999999", "demo", "Buenos Aires"),
            ],
'@
if ($mainText.Contains("Silo Chico S.A.")) {
    $mainText = $mainText.Replace($seedEmpOld, $seedEmpNew)
    $mainText = $mainText.Replace(
        "VALUES (1, 'Silo Chico S.A.', '30711166250', 'Responsable Inscripto', 'Chivilcoy', 'contacto@silochico.com.ar', '');",
        "VALUES (1, 'CAmpo+ Demo S.A.', '30999999999', 'Responsable Inscripto', 'Buenos Aires', 'demo@campoplus.local', '');"
    )
}

# PORT / uvicorn (Railway)
if ($mainText -notmatch 'environ\.get\("PORT"') {
    $mainText = $mainText.Replace(
        'uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)',
        "_port = int(os.environ.get(`"PORT`", `"8000`"))`r`n    uvicorn.run(`"main:app`", host=`"0.0.0.0`", port=_port, reload=False)"
    )
} else {
    # Ya tenía PORT: asegurar reload=False
    $mainText = $mainText.Replace('reload=True)', 'reload=False)')
}

Set-Content -Path $main -Value $mainText -Encoding UTF8 -NoNewline

$index = Join-Path $DemoRoot "Index.html"
$idx = Get-Content -Raw -Encoding UTF8 $index
if ($idx -notmatch "Entorno demostraci") {
    $idx = $idx.Replace(
        "<body class=`"bg-slate-100 font-sans`">`r`n`r`n    <div class=`"max-w-7xl",
        "<body class=`"bg-slate-100 font-sans`">`r`n`r`n    <div class=`"bg-amber-500 text-amber-950 text-center text-sm font-semibold py-2 px-4 shadow`">`r`n        Entorno demostración — datos de ejemplo. No contiene información de clientes reales.`r`n    </div>`r`n`r`n    <div class=`"max-w-7xl"
    )
    if ($idx -notmatch "Entorno demostraci") {
        $idx = $idx.Replace(
            "<body class=`"bg-slate-100 font-sans`">`n`n    <div class=`"max-w-7xl",
            "<body class=`"bg-slate-100 font-sans`">`n`n    <div class=`"bg-amber-500 text-amber-950 text-center text-sm font-semibold py-2 px-4 shadow`">`n        Entorno demostración — datos de ejemplo. No contiene información de clientes reales.`n    </div>`n`n    <div class=`"max-w-7xl"
        )
    }
    Set-Content -Path $index -Value $idx -Encoding UTF8 -NoNewline
}

# Etiquetas UI obvias
foreach ($pair in @(
    @("InformacionProductiva1.html", " ha Silo Chico", " ha"),
    @("ConsultasBalance.html", "(Silo Chico S.A.)", "(Demo)"),
    @("Contabilidad.html", "de Silo Chico S.A.", "(Demo)"),
    @("Contabilidad.html", "Contabilidad General - Silo Chico S.A.", "Contabilidad General - Demo"),
    @("Contabilidad.html", "Plan de Cuentas Oficial - Silo Chico S.A.", "Plan de Cuentas Oficial - Demo")
)) {
    $fp = Join-Path $DemoRoot $pair[0]
    if (Test-Path $fp) {
        $t = Get-Content -Raw -Encoding UTF8 $fp
        if ($t.Contains($pair[1])) {
            Set-Content -Path $fp -Value ($t.Replace($pair[1], $pair[2])) -Encoding UTF8 -NoNewline
        }
    }
}

New-Item -ItemType Directory -Force -Path (Join-Path $DemoRoot "tablas") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DemoRoot "padrones_arba") | Out-Null

Write-Host "OK: codigo sincronizado y parches demo reaplicados."
Write-Host "Verifica que no haya .db:"
Get-ChildItem $DemoRoot -Recurse -Filter "*.db" -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
