# =============================================================================
#  LAMBDA: S3  ->  Google Cloud Storage  (arquivos de "billing")
# =============================================================================
#
#  O QUE FAZ
#  ---------
#  Toda vez que um arquivo cai no bucket S3 (evento ObjectCreated), este Lambda:
#    1) Olha o NOME do arquivo.
#    2) Se o nome contem "billing"  -> copia o arquivo para o bucket do Google.
#       Se NAO contem "billing"     -> ignora (nao faz nada).
#    3) Escolhe a PASTA de destino no Google, olhando MATCH_KEYWORDS:
#          - nome contem ALGUMA das palavras de MATCH_KEYWORDS (ex: "ibcc")
#                                                        -> pasta GCP_FOLDER_HISTORIC
#          - caso contrario                              -> pasta GCP_FOLDER
#
#  O objeto ORIGINAL no S3 e SEMPRE mantido (nunca deletado apos a copia).
#
#
#  IMPORTANTE - DEPENDENCIA (google-cloud-storage)
#  -----------------------------------------------
#  Este codigo usa a biblioteca "google-cloud-storage", que NAO vem no Lambda.
#  Colar so este arquivo no editor NAO basta: e preciso anexar a biblioteca via
#  LAYER ou subir um .zip com as dependencias. Duas opcoes:
#     A) Rode o build.ps1 (deste projeto) que gera billing-gcs.zip ja com tudo,
#        e faca upload do zip na Lambda (Code -> Upload from -> .zip file).
#     B) Crie um Lambda Layer com google-cloud-storage e anexe a funcao; ai sim
#        voce pode colar SOMENTE este arquivo no editor inline.
#
#
#  VARIAVEIS DE AMBIENTE (Configuration -> Environment variables)
#  --------------------------------------------------------------
#    GCP_BUCKET                 (obrig.)  bucket do Google.        ex: <GCS_BUCKET>
#    GCP_FOLDER                 (obrig.)  pasta padrao.            ex: billing-to-process
#    GCP_FOLDER_HISTORIC        (obrig.)  pasta especial (match).  ex: billing-to-process
#
#    -- credencial do Google: use UMA das duas formas abaixo --
#    GCP_CREDENTIALS_SECRET_ARN (recom.)  ARN do AWS Secrets Manager que guarda
#                                         o JSON da service account (mais seguro).
#    GCP_CREDENTIALS            (altern.) o JSON da service account colado inteiro
#                                         como texto (fica visivel no console).
#
#    -- opcionais --
#    BILLING_KEYWORD            (default "billing")  palavra que dispara a copia
#
#  >>> A lista de palavras/hospitais que mandam o arquivo para a pasta especial
#      (GCP_FOLDER_HISTORIC) fica NO CODIGO, na constante MATCH_KEYWORDS logo
#      abaixo dos imports. Edite ali (uma ou varias palavras).
#
#
#  CONFIG SUGERIDA DA FUNCAO
#  -------------------------
#    Runtime.......: Python 3.12
#    Handler.......: lambda_function.lambda_handler
#    Timeout.......: 300 s (5 min)  -> arquivos grandes de billing
#    Memory........: 512 MB
#    Ephemeral /tmp: 2048 MB        -> o arquivo e baixado em /tmp antes de subir
#
#
#  PERMISSOES DA ROLE DE EXECUCAO (IAM)
#  ------------------------------------
#    - s3:GetObject           no bucket de origem
#    - secretsmanager:GetSecretValue  no secret    (se usar o Secrets Manager)
#    - logs basicos (AWSLambdaBasicExecutionRole)
# =============================================================================

import json
import os
import urllib.parse

import boto3                                    # SDK da AWS (ja vem no runtime Lambda)
from google.cloud import storage                # cliente do Google Cloud Storage
from google.oauth2 import service_account       # autenticacao via service account


# =============================================================================
#  >>> EDITE AQUI <<<  Lista de palavras/hospitais (case-insensitive).
#  Se o NOME do arquivo contem ALGUMA destas palavras, ele vai para a pasta
#  especial (GCP_FOLDER_HISTORIC). Se nao bater com nenhuma, vai para GCP_FOLDER.
#
#  Exemplos:
#     - uma so:     MATCH_KEYWORDS = ["ibcc"]
#     - varias:     MATCH_KEYWORDS = ["ibcc", "uopeccan", "imip"]
#     - nenhuma:    MATCH_KEYWORDS = []          (tudo vai para GCP_FOLDER)
# =============================================================================
MATCH_KEYWORDS = ["ibcc"]


# Cliente S3 criado uma vez (reaproveitado entre invocacoes do mesmo container).
s3 = boto3.client("s3")

# Cache do cliente GCS: criado so na 1a invocacao e reusado depois (mais rapido).
_gcs_client = None


# -----------------------------------------------------------------------------
# Carrega o JSON de credenciais da service account do Google.
# Prioridade: Secrets Manager (mais seguro) -> env var em texto (fallback).
# -----------------------------------------------------------------------------
def _load_gcp_credentials_json():
    secret_arn = os.environ.get("GCP_CREDENTIALS_SECRET_ARN")
    if secret_arn:
        # Le o segredo do AWS Secrets Manager e faz parse do JSON.
        sm = boto3.client("secretsmanager")
        resp = sm.get_secret_value(SecretId=secret_arn)
        raw = resp.get("SecretString") or resp["SecretBinary"]
        return json.loads(raw)

    # Fallback: JSON colado direto na variavel de ambiente GCP_CREDENTIALS.
    raw = os.environ.get("GCP_CREDENTIALS")
    if not raw:
        raise RuntimeError(
            "Credenciais GCP ausentes: defina GCP_CREDENTIALS_SECRET_ARN "
            "ou GCP_CREDENTIALS nas variaveis de ambiente."
        )
    return json.loads(raw)


# -----------------------------------------------------------------------------
# Monta (uma unica vez) o cliente autenticado do Google Cloud Storage.
# -----------------------------------------------------------------------------
def _get_gcs_client():
    global _gcs_client
    if _gcs_client is None:
        info = _load_gcp_credentials_json()
        creds = service_account.Credentials.from_service_account_info(info)
        _gcs_client = storage.Client(project=info.get("project_id"), credentials=creds)
    return _gcs_client


# -----------------------------------------------------------------------------
# Decide a pasta de destino no GCS com base no nome do arquivo.
# Usa a lista MATCH_KEYWORDS (definida no topo do arquivo).
# Se o nome do arquivo contem ALGUMA dessas palavras -> pasta GCP_FOLDER_HISTORIC.
# Caso contrario -> pasta GCP_FOLDER.
# Retorna (nome_da_pasta, deu_match).
# -----------------------------------------------------------------------------
def _target_folder(filename_lower):
    for kw in MATCH_KEYWORDS:
        if kw.strip() and kw.strip().lower() in filename_lower:
            return os.environ["GCP_FOLDER_HISTORIC"], True   # bateu com a lista
    return os.environ["GCP_FOLDER"], False                   # nenhuma bateu


# -----------------------------------------------------------------------------
# Processa UM objeto do S3: filtra por "billing", escolhe a pasta, copia p/ GCS.
# -----------------------------------------------------------------------------
def _process_object(bucket, key):
    filename = key.split("/")[-1]      # so o nome do arquivo (sem "pastas" do S3)
    lower = filename.lower()           # comparacoes sempre em minusculo

    # 1) So processa se o nome contiver a palavra-chave (default "billing").
    billing_kw = os.environ.get("BILLING_KEYWORD", "billing").lower()
    if billing_kw not in lower:
        print(f"[skip] '{key}' nao contem '{billing_kw}' no nome. Ignorado.")
        return "skipped"

    # 2) Escolhe a pasta de destino (padrao x especial por MATCH_KEYWORDS).
    folder, matched = _target_folder(lower)
    dest_blob_name = f"{folder.rstrip('/')}/{filename}"   # <pasta>/<nome-original>

    gcp_bucket = os.environ["GCP_BUCKET"]
    print(f"[copy] s3://{bucket}/{key} -> gs://{gcp_bucket}/{dest_blob_name} "
          f"(match={matched})")

    # 3) Baixa o arquivo do S3 para /tmp (area temporaria do Lambda).
    local_path = os.path.join("/tmp", filename)
    s3.download_file(bucket, key, local_path)

    try:
        # 4) Sobe o arquivo de /tmp para o bucket do Google.
        gcs = _get_gcs_client()
        blob = gcs.bucket(gcp_bucket).blob(dest_blob_name)
        blob.upload_from_filename(local_path, timeout=600)  # 10 min p/ arquivos grandes
    finally:
        # 5) Limpa o /tmp (o container pode ser reaproveitado em outra invocacao).
        #    Obs: isso apaga so a copia temporaria no Lambda; o objeto no S3 fica intacto.
        try:
            os.remove(local_path)
        except OSError:
            pass

    # O objeto original no S3 e mantido (nao ha delete).
    return "copied"


# -----------------------------------------------------------------------------
# PONTO DE ENTRADA do Lambda. O S3 pode mandar varios registros em um evento;
# tratamos todos. Se um falhar, damos raise para o Lambda marcar falha (retry/DLQ).
# -----------------------------------------------------------------------------
def lambda_handler(event, context):
    results = []
    for record in event.get("Records", []):
        # Extrai bucket e key (chave/nome) do objeto que gerou o evento.
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
