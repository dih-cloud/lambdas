# =============================================================================
#  LAMBDA: transferBillingGCP  (S3  ->  Google Cloud Storage)
# =============================================================================
#
#  O QUE FAZ
#  ---------
#  Disparada por evento S3 (ObjectCreated) em QUALQUER bucket que tenha o gatilho
#  configurado. Para cada arquivo que chega:
#    1) Se o nome contem "billing"  -> copia o arquivo para o bucket do Google.
#       Se NAO contem "billing"     -> ignora (nao faz nada).
#    2) Sobe para GCP_BUCKET, na pasta GCP_FOLDER (ex.: billing-to-process).
#
#  O bucket de origem vem do proprio evento (record["s3"]["bucket"]["name"]), entao
#  a MESMA funcao atende varios buckets: basta adicionar a notificacao de evento
#  (ObjectCreated) apontando para este Lambda em cada bucket desejado.
#
#  O objeto original no S3 e SEMPRE mantido (so a copia temporaria em /tmp e removida).
#
#
#  IMPORTANTE - DEPENDENCIA (google-cloud-storage)
#  -----------------------------------------------
#  Usa "google-cloud-storage", que NAO vem no runtime do Lambda. E preciso empacotar
#  via zip (build.ps1) ou anexar um Layer. Ver README.
#
#
#  VARIAVEIS DE AMBIENTE
#  ---------------------
#    GCP_BUCKET                 (obrig.)  bucket GCS de destino.   ex: <GCS_BUCKET>
#    GCP_FOLDER                 (obrig.)  pasta de destino.        ex: billing-to-process
#
#    -- credencial do Google: use UMA das duas formas --
#    GCP_CREDENTIALS_SECRET_ARN (recom.)  ARN no AWS Secrets Manager com o JSON.
#    GCP_CREDENTIALS            (altern.) JSON da service account colado (string).
#
#    -- opcional --
#    BILLING_KEYWORD            (default "billing")  palavra que dispara a copia.
#
#
#  PERMISSOES DA ROLE (IAM)
#  ------------------------
#    - AWSLambdaBasicExecutionRole (logs no CloudWatch)
#    - s3:GetObject nos buckets de origem (use "arn:aws:s3:::*/*" para atender
#      qualquer bucket, ou liste os buckets especificos)
#    - secretsmanager:GetSecretValue no secret (se usar GCP_CREDENTIALS_SECRET_ARN)
#
#  Cada bucket de origem tambem precisa: notificacao ObjectCreated -> este Lambda,
#  e permissao para o S3 invocar a funcao (o console adiciona isso automaticamente
#  ao criar o gatilho).
# =============================================================================

import json
import os
import urllib.parse

import boto3
from google.cloud import storage
from google.oauth2 import service_account


s3 = boto3.client("s3")

# Cache do cliente GCS: criado so na 1a invocacao e reusado depois (mais rapido).
_gcs_client = None


def _load_gcp_credentials_json():
    """JSON da service account: Secrets Manager (preferido) ou env var (fallback)."""
    secret_arn = os.environ.get("GCP_CREDENTIALS_SECRET_ARN")
    if secret_arn:
        sm = boto3.client("secretsmanager")
        resp = sm.get_secret_value(SecretId=secret_arn)
        raw = resp.get("SecretString") or resp["SecretBinary"]
        return json.loads(raw)

    raw = os.environ.get("GCP_CREDENTIALS")
    if not raw:
        raise RuntimeError(
            "Credenciais GCP ausentes: defina GCP_CREDENTIALS_SECRET_ARN "
            "ou GCP_CREDENTIALS nas variaveis de ambiente."
        )
    return json.loads(raw)


def _get_gcs_client():
    global _gcs_client
    if _gcs_client is None:
        info = _load_gcp_credentials_json()
        creds = service_account.Credentials.from_service_account_info(info)
        _gcs_client = storage.Client(project=info.get("project_id"), credentials=creds)
    return _gcs_client


def _process_object(bucket, key):
    """Filtra por 'billing' e copia o objeto para GCP_BUCKET/GCP_FOLDER."""
    filename = key.split("/")[-1]      # so o nome do arquivo (sem "pastas" do S3)
    lower = filename.lower()           # comparacao sempre em minusculo

    # So processa se o nome contiver a palavra-chave (default "billing").
    billing_kw = os.environ.get("BILLING_KEYWORD", "billing").lower()
    if billing_kw not in lower:
        print(f"[skip] '{key}' nao contem '{billing_kw}' no nome. Ignorado.")
        return "skipped"

    gcp_bucket = os.environ["GCP_BUCKET"]
    folder = os.environ["GCP_FOLDER"]
    dest_blob_name = f"{folder.rstrip('/')}/{filename}"   # <pasta>/<nome-original>
    print(f"[copy] s3://{bucket}/{key} -> gs://{gcp_bucket}/{dest_blob_name}")

    # Baixa o arquivo do S3 para /tmp e sobe para o bucket do Google.
    local_path = os.path.join("/tmp", filename)
    s3.download_file(bucket, key, local_path)
    try:
        gcs = _get_gcs_client()
        blob = gcs.bucket(gcp_bucket).blob(dest_blob_name)
        blob.upload_from_filename(local_path, timeout=600)  # 10 min p/ arquivos grandes
    finally:
        # Limpa so a copia temporaria no Lambda; o objeto no S3 fica intacto.
        try:
            os.remove(local_path)
        except OSError:
            pass

    return "copied"


def lambda_handler(event, context):
    results = []
    for record in event.get("Records", []):
        try:
            bucket = record["s3"]["bucket"]["name"]
            # A key vem URL-encoded (ex: espaco = "+"); precisamos decodificar.
            key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        except (KeyError, TypeError):
            print(f"[warn] registro sem os dados de S3 esperados: {record}")
            continue

        try:
            status = _process_object(bucket, key)
            results.append({"key": key, "status": status})
        except Exception as e:  # loga o erro e propaga para o Lambda registrar a falha
            print(f"[error] falha ao processar '{key}': {e}")
            results.append({"key": key, "status": "error", "error": str(e)})
            raise

    return {"processed": results}
