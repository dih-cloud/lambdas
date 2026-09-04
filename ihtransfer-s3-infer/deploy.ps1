# Atualiza o codigo da funcao ihtransfer-s3-infer (via S3) e ajusta a config.
# Pre-requisitos: AWS CLI configurado e build.ps1 ja rodado.
#
# Por que via S3? O upload direto (--zip-file fileb://) de pacotes de ~10 MB costuma
# cair ("Connection was closed"). Subir para o S3 e apontar o Lambda e mais confiavel.
#
# Uso:  powershell -ExecutionPolicy Bypass -File .\deploy.ps1

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# ----------------- CONFIG (preencha com os valores do seu ambiente) -----------------
$Profile       = "<AWS_PROFILE>"
$Region        = "<REGION>"                          # ex.: us-east-1
$FunctionName  = "ihtransfer-s3-infer"
$CodeBucket    = "<CODE_BUCKET>"                      # bucket S3 p/ guardar o zip
$CodeKey       = "ihtransfer-s3-infer/ihtransfer.zip"
# ------------------------------------------------------------------------------------

$zip = Join-Path $here "ihtransfer.zip"
if (-not (Test-Path $zip)) { throw "ihtransfer.zip nao encontrado. Rode build.ps1 primeiro." }

Write-Host "==> Subindo o zip para o S3..." -ForegroundColor Cyan
aws s3 cp $zip "s3://$CodeBucket/$CodeKey" --region $Region --profile $Profile

Write-Host "==> Atualizando o codigo da funcao a partir do S3..." -ForegroundColor Cyan
aws lambda update-function-code `
    --function-name $FunctionName `
    --s3-bucket $CodeBucket --s3-key $CodeKey `
    --region $Region --profile $Profile | Out-Null
aws lambda wait function-updated --function-name $FunctionName --region $Region --profile $Profile

Write-Host "==> Ajustando timeout/memoria..." -ForegroundColor Cyan
aws lambda update-function-configuration `
    --function-name $FunctionName `
    --timeout 900 --memory-size 512 --ephemeral-storage "Size=2048" `
    --region $Region --profile $Profile | Out-Null
aws lambda wait function-updated --function-name $FunctionName --region $Region --profile $Profile

Write-Host ""
Write-Host "OK. Funcao $FunctionName atualizada." -ForegroundColor Green
Write-Host "As variaveis de ambiente (GCP_*, WEBAPP_URL) sao definidas no console e" -ForegroundColor Yellow
Write-Host "NAO sao alteradas por este script (para nao expor segredos)." -ForegroundColor Yellow
