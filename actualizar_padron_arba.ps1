# Copia los TXT del mes a Render. El servidor los importa solo.
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$mm = Get-Date -Format "MMyyyy"
$ret = Join-Path $dir ("PadronRGSRet" + $mm + ".TXT")
$per = Join-Path $dir ("PadronRGSPer" + $mm + ".TXT")
if (-not (Test-Path -LiteralPath $ret) -or -not (Test-Path -LiteralPath $per)) {
    Write-Output "Sin TXT de $mm en la carpeta. Bajalos de ARBA y listo."
    exit 0
}
scp -o BatchMode=yes -o StrictHostKeyChecking=accept-new $ret $per srv-dam73etbedkc73aajtf0@ssh.oregon.render.com:/var/data/padrones_arba/
Write-Output "Copiados. El servidor importa solo si el padron anterior ya vencio."
