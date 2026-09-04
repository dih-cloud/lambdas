# Monta o pacote de deploy (ihtransfer.zip) para AWS Lambda (Python 3.12, x86_64).
# Baixa os wheels manylinux (Linux) para as libs com extensao nativa funcionarem no Lambda.
# Uso:  powershell -ExecutionPolicy Bypass -File .\build.ps1

$ErrorActionPreference = "Stop"
$here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$build = Join-Path $here "build"
$zip   = Join-Path $here "ihtransfer.zip"

if (Test-Path $build) { Remove-Item $build -Recurse -Force }
if (Test-Path $zip)   { Remove-Item $zip -Force }
New-Item -ItemType Directory -Path $build | Out-Null

Write-Host "==> Instalando dependencias (wheels Linux)..." -ForegroundColor Cyan
pip install `
    --platform manylinux2014_x86_64 `
    --implementation cp `
    --python-version 3.12 `
    --only-binary=:all: `
    --target $build `
    -r (Join-Path $here "requirements.txt")

Write-Host "==> Copiando codigo do handler..." -ForegroundColor Cyan
Copy-Item (Join-Path $here "lambda_function.py") $build

Write-Host "==> Compactando -> $zip" -ForegroundColor Cyan
# Python zipfile e mais robusto que Compress-Archive quando o antivirus segura arquivos.
python -c "import shutil; shutil.make_archive(r'$($zip -replace '\.zip$','')','zip',r'$build')"

Write-Host "OK: $zip" -ForegroundColor Green
