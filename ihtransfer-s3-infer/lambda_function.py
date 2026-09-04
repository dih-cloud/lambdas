# =============================================================================
#  LAMBDA: ihtransfer-s3-infer  (S3  ->  Google Cloud Storage)
# =============================================================================
#
#  O QUE FAZ
#  ---------
#  Disparada por evento S3 (ObjectCreated). Para cada arquivo que chega:
#    1) Decide a PASTA de destino no GCS pelo nome do arquivo:
#         - nome contem algum marcador de HISTORIC_FILENAME_MARKERS -> GCP_FOLDER_HISTORIC
#         - caso contrario                                          -> GCP_FOLDER
#    2) Baixa o arquivo do S3 para /tmp e sobe para o bucket do GCS.
#    3) Extrai um "doctor_name" do nome do arquivo (2o campo separado por "_")
#       para um registro OPCIONAL num Web App (Apps Script) -- desativado por padrao.
#
#  O objeto original no S3 e mantido (so a copia temporaria em /tmp e removida).
#
#
#  IMPORTANTE - DEPENDENCIAS
#  -------------------------
#  Usa "google-cloud-storage" e "requests", que NAO vem no runtime do Lambda.
#  E preciso empacotar via zip/Layer (ver build.ps1 / README).
#
#
#  VARIAVEIS DE AMBIENTE
#  ---------------------
#    GCP_CREDENTIALS       (obrig.)  JSON da service account (string, com os \n)
#    GCP_BUCKET            (obrig.)  bucket GCS de destino
#    GCP_FOLDER            (obrig.)  pasta padrao (arquivos "diarios")
#    GCP_FOLDER_HISTORIC   (obrig.)  pasta para arquivos que batem com os marcadores
#    WEBAPP_URL            (opc.)    URL do Web App (Apps Script) p/ o registro
#                                    opcional. Contem token secreto no path, por isso
#                                    NAO fica hardcoded -> configurar por env var.
#                                    Ex.: https://script.google.com/macros/s/<DEPLOY_ID>/exec
# =============================================================================

import json
import os
import urllib.parse
from datetime import datetime, timezone

import boto3
import requests
from google.cloud import storage
from google.oauth2 import service_account

# =============================================================================
#  >>> EDITE AQUI <<<  Marcadores no nome do arquivo (case-insensitive).
#  Se o nome do arquivo contem ALGUM destes textos, o arquivo vai para a pasta
#  historica (GCP_FOLDER_HISTORIC). Caso contrario, vai para GCP_FOLDER.
#  (Normalmente sao apelidos/identificadores de origem, ex.: nomes de hospitais.)
# =============================================================================
HISTORIC_FILENAME_MARKERS = (
    "imip",
    "uopeccan",
    "ibcc",
    "felicio rocho",
    "amor",
    "angelina",
    "arnaldo",
    "hmd",
    "hcor",
    "hbompastor",
)

s3_client = boto3.client("s3")

# Cliente GCS montado a partir da service account fornecida na env var.
GCP_CREDENTIALS_JSON = json.loads(os.environ["GCP_CREDENTIALS"])
credentials = service_account.Credentials.from_service_account_info(GCP_CREDENTIALS_JSON)
gcp_client = storage.Client(credentials=credentials)

GCP_BUCKET = os.environ["GCP_BUCKET"]
GCP_FOLDER = os.environ["GCP_FOLDER"]
GCP_FOLDER_HISTORIC = os.environ["GCP_FOLDER_HISTORIC"]

# URL do Web App (Apps Script) para o registro opcional. Fica FORA do codigo
# porque o path carrega um token secreto -- configure em WEBAPP_URL. Vazio = desativado.
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")


def resolve_gcp_folder(filename: str) -> str:
    """Retorna a pasta de destino no GCS conforme os marcadores no nome."""
    normalized = filename.casefold()
    if any(marker.casefold() in normalized for marker in HISTORIC_FILENAME_MARKERS):
        return GCP_FOLDER_HISTORIC
    return GCP_FOLDER


def lambda_handler(event, context):
    for record in event["Records"]:
        s3_key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        filename = os.path.basename(s3_key)

        target_folder = resolve_gcp_folder(filename)
        is_historic = target_folder == GCP_FOLDER_HISTORIC
        print(f"Arquivo: {filename} -> {'historico' if is_historic else 'diario'} ({target_folder})")

        # Espera nome no formato "<algo>_<doctor_name>_<resto>"; extrai o 2o campo.
        parts = filename.split("_", 2)
        if len(parts) < 3:
            print(f"Formato inesperado: {filename}")
            continue
        doctor_name = parts[1]

        # Baixa do S3 e sobe para o GCS (mantem o original no S3).
        tmp = f"/tmp/{filename}"
        s3_client.download_file(record["s3"]["bucket"]["name"], s3_key, tmp)
        bucket = gcp_client.bucket(GCP_BUCKET)
        bucket.blob(f"{target_folder}/{filename}").upload_from_filename(tmp)
        os.remove(tmp)

        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

        # ---- Registro OPCIONAL num Web App (Apps Script). Desativado por padrao. ----
        # Requer a env var WEBAPP_URL configurada. Descomente para ativar.
        # if WEBAPP_URL:
        #     payload = {"name": doctor_name, "timestamp": timestamp}
        #     try:
        #         resp = requests.post(WEBAPP_URL, data=payload, timeout=10)
        #         if resp.status_code == 200 and resp.text.strip() == "OK":
        #             print(f"{doctor_name} registrado com sucesso em {timestamp}")
        #         else:
        #             print("Falha ao registrar no Web App:", resp.status_code, resp.text)
        #     except Exception as e:
        #         print("Erro ao chamar Web App:", str(e))

    return {"statusCode": 200, "body": "Processamento concluido"}
