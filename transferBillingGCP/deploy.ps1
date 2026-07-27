# Atualiza o codigo da funcao transferBillingGCP (via S3) e ajusta config + IAM.
# Pre-requisitos: AWS CLI configurado (profile eks-operator) e build.ps1 ja rodado.
#
# Por que via S3? O upload direto (--zip-file fileb://) de ~10 MB costuma cair
# ("Connection was closed"). Subir para o S3 e apontar o Lambda e mais confiavel.
#
# Uso:  powershell -ExecutionPolicy Bypass -File .\deploy.ps1

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# ----------------- CONFIG (valores reais do ambiente) -----------------
$Profile       = "eks-operator"
$Region        = "us-east-2"
$FunctionName  = "transferBillingGCP"
$RoleName      = "<LAMBDA_ROLE_NAME>"
$SourceBucket  = "<SOURCE_BUCKET>"                     # bucket de origem (gatilho S3)
$CodeBucket    = "<CODE_BUCKET>"       # bucket p/ guardar o zip
$CodeKey       = "transferBillingGCP/billing-gcs.zip"
# ----------------------------------------------------------------------

$zip = Join-Path $here "billing-gcs.zip"
if (-not (Test-Path $zip)) { throw "billing-gcs.zip nao encontrado. Rode build.ps1 primeiro." }

Write-Host "==> Subindo o zip para o S3..." -ForegroundColor Cyan
aws s3 cp $zip "s3://$CodeBucket/$CodeKey" --region $Region --profile $Profile

Write-Host "==> Atualizando o codigo da funcao a partir do S3..." -ForegroundColor Cyan
aws lambda update-function-code `
    --function-name $FunctionName `
    --s3-bucket $CodeBucket --s3-key $CodeKey `
    --region $Region --profile $Profile | Out-Null
aws lambda wait function-updated --function-name $FunctionName --region $Region --profile $Profile

Write-Host "==> Ajustando timeout/memoria/ephemeral..." -ForegroundColor Cyan
aws lambda update-function-configuration `
    --function-name $FunctionName `
    --timeout 120 --memory-size 512 --ephemeral-storage "Size=2048" `
    --region $Region --profile $Profile | Out-Null
aws lambda wait function-updated --function-name $FunctionName --region $Region --profile $Profile

Write-Host "==> Garantindo permissao s3:GetObject na role..." -ForegroundColor Cyan
$policy = @"
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "ReadBillingSource", "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::$SourceBucket/*" }
  ]
}
"@
$tmp = Join-Path $env:TEMP "s3policy.json"
$policy | Out-File -FilePath $tmp -Encoding ascii -Force
aws iam put-role-policy --role-name $RoleName --policy-name ReadShcIbccBillings `
    --policy-document "file://$tmp" --profile $Profile
Remove-Item $tmp -Force

Write-Host ""
Write-Host "OK. Funcao $FunctionName atualizada." -ForegroundColor Green
Write-Host "Lembrete: as variaveis de ambiente (GCP_*) sao definidas no console e" -ForegroundColor Yellow
Write-Host "NAO sao alteradas por este script (para nao expor a credencial)." -ForegroundColor Yellow
