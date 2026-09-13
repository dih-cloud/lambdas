# =============================================================================
#  LAMBDA: ihtransfer-s3-infer  (S3 -> Google Cloud Storage + update planilha CN)
# =============================================================================
#  Disparada por evento S3 (ObjectCreated) nos buckets de CN.
#  Para cada arquivo que chega:
#    1) copia para o bucket GCS GCP_BUCKET, escolhendo a pasta pelo nome:
#         - nome contem algum marcador de HISTORIC_FILENAME_MARKERS -> GCP_FOLDER_HISTORIC
#         - caso contrario                                          -> GCP_FOLDER
#    2) atualiza a planilha de acompanhamento: escreve a data de hoje
#       (DD/MM/AAAA HH:MM) na coluna "Last Update CN" da linha do hospital,
#       casando pelo nome do arquivo (cn -> Indice -> Nome do hospital).
#  O objeto original no S3 e mantido (so a copia temporaria em /tmp e removida).
#
#
#  DEPENDENCIAS: google-cloud-storage e gspread (empacotar via zip/Layer).
#
#  VARIAVEIS DE AMBIENTE
#    GCP_CREDENTIALS       (obrig.)  JSON da service account do GCS (bucket shc-infer-mt)
#    GCP_BUCKET            (obrig.)  bucket GCS de destino
#    GCP_FOLDER            (obrig.)  pasta padrao
#    GCP_FOLDER_HISTORIC   (obrig.)  pasta para arquivos que batem com os marcadores
#    -- planilha (opcionais; sem elas o passo da planilha e' pulado) --
#    GCP_SHEETS_CREDENTIALS  JSON de uma service account com Editor na planilha
#    SHEET_ID                ID da planilha de acompanhamento (/d/<ID>/edit)
#    SHEET_NAME (opc)        nome da aba; default = 1a aba
# =============================================================================

import json
import os
import unicodedata
import urllib.parse
from datetime import datetime, timezone, timedelta

import boto3
import gspread
from google.cloud import storage
from google.oauth2 import service_account

# Arquivos que tiverem qualquer um desses textos no nome vao para a pasta historica.
HISTORIC_FILENAME_MARKERS = (
    "imip", "uopeccan", "ibcc", "felicio rocho", "amor",
    "angelina", "arnaldo", "hmd", "hcor", "hbompastor",
)

# Planilha: coluna a atualizar e colunas usadas para casar o hospital.
CN_COL_HEADER = "Last Update CN"
MATCH_COL_HEADERS = ("cn", "Indice", "Nome do hospital")

s3_client = boto3.client("s3")

_gcs_client = None
_sheet_ws = None


# ---------------------------- GCS (upload) -----------------------------------
def _get_gcs_client():
    global _gcs_client
    if _gcs_client is None:
        info = json.loads(os.environ["GCP_CREDENTIALS"])
        creds = service_account.Credentials.from_service_account_info(info)
        _gcs_client = storage.Client(project=info.get("project_id"), credentials=creds)
    return _gcs_client


def resolve_gcp_folder(filename):
    normalized = filename.casefold()
    if any(m.casefold() in normalized for m in HISTORIC_FILENAME_MARKERS):
        return os.environ["GCP_FOLDER_HISTORIC"]
    return os.environ["GCP_FOLDER"]


# ---------------------------- Planilha (Sheets) ------------------------------
def _get_sheet_ws():
    """Worksheet da planilha, ou None se as env vars da planilha nao existirem."""
    global _sheet_ws
    if _sheet_ws is None:
        raw = os.environ.get("GCP_SHEETS_CREDENTIALS")
        sheet_id = os.environ.get("SHEET_ID")
        if not raw or not sheet_id:
            return None
        info = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)
        name = os.environ.get("SHEET_NAME")
        _sheet_ws = sh.worksheet(name) if name else sh.sheet1
    return _sheet_ws


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _today_br():
    # Brasil e' UTC-3 o ano todo (sem horario de verao desde 2019). Data + hora.
    return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")


def _update_sheet_cn(filename):
    """Escreve a data de hoje em 'Last Update CN' na linha do hospital."""
    ws = _get_sheet_ws()
    if ws is None:
        print("[sheet] SHEET_ID/GCP_SHEETS_CREDENTIALS ausentes; planilha nao atualizada.")
        return

    rows = ws.get_all_values()
    if not rows:
        print("[sheet] planilha vazia.")
        return

    header = rows[0]
    idx = {h: i for i, h in enumerate(header)}
    if CN_COL_HEADER not in idx:
        print(f"[sheet] coluna '{CN_COL_HEADER}' nao encontrada no cabecalho.")
        return
    cn_col = idx[CN_COL_HEADER]
    match_cols = [idx[h] for h in MATCH_COL_HEADERS if h in idx]

    fn = _norm(filename)
    target_row = None
    for r, row in enumerate(rows[1:], start=2):   # 1-based; +1 do cabecalho
        for c in match_cols:
            val = row[c] if c < len(row) else ""
            if val and _norm(val) in fn:
                target_row = r
                break
        if target_row:
            break

    if not target_row:
        print(f"[sheet] hospital nao identificado no nome '{filename}'.")
        return

    today = _today_br()
    ws.update_cell(target_row, cn_col + 1, today)  # gspread e' 1-based
    print(f"[sheet] linha {target_row} -> {CN_COL_HEADER} = {today}")


# ---------------------------- Fluxo principal --------------------------------
def lambda_handler(event, context):
    for record in event.get("Records", []):
        s3_key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        filename = os.path.basename(s3_key)

        target_folder = resolve_gcp_folder(filename)
        is_historic = target_folder == os.environ["GCP_FOLDER_HISTORIC"]
        print(f"Arquivo: {filename} -> {'historico' if is_historic else 'diario'} ({target_folder})")

        # Copia S3 -> GCS (mantem o original no S3).
        tmp = f"/tmp/{filename}"
        s3_client.download_file(record["s3"]["bucket"]["name"], s3_key, tmp)
        try:
            bucket = _get_gcs_client().bucket(os.environ["GCP_BUCKET"])
            bucket.blob(f"{target_folder}/{filename}").upload_from_filename(tmp)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

        # Atualiza a planilha (Last Update CN). Falha aqui NAO quebra a copia.
        try:
            _update_sheet_cn(filename)
        except Exception as e:  # noqa: BLE001
            print(f"[sheet][warn] falha ao atualizar planilha para '{filename}': {e}")

    return {"statusCode": 200, "body": "Processamento concluido"}
