# =============================================================================
#  LAMBDA: transferBillingGCP  (S3 -> Google Cloud Storage + update planilha)
# =============================================================================
#  Disparada por evento S3 (ObjectCreated) em QUALQUER bucket com o gatilho.
#  Para cada arquivo cujo nome contem "billing":
#    1) copia o arquivo para GCP_BUCKET/GCP_FOLDER (ex.: billing-to-process);
#    2) atualiza a planilha: escreve a data de hoje (DD/MM/AAAA HH:MM) na coluna
#       "Last Update Billing" da linha do hospital correspondente;
#    3) faz upsert da linha do bucket na aba "Como funciona (Lambdas)" (status ao vivo:
#       ultimo arquivo, pasta GCS destino e data por bucket).
#  O objeto original no S3 e sempre mantido.
#
#
#  IMPORTANTE - DEPENDENCIAS
#  -------------------------
#  Usa "google-cloud-storage" e "gspread", que NAO vem no runtime do Lambda.
#  E preciso empacotar via zip (build.ps1) ou anexar um Layer.
#
#
#  VARIAVEIS DE AMBIENTE
#  ---------------------
#    GCP_BUCKET                 (obrig.)  bucket GCS de destino
#    GCP_FOLDER                 (obrig.)  pasta de destino (ex.: billing-to-process)
#    GCP_CREDENTIALS_SECRET_ARN | GCP_CREDENTIALS  (uma das duas) credencial do GCS
#    BILLING_KEYWORD            (opc, default "billing")
#
#    -- planilha (opcionais; SEM elas, o passo da planilha e' pulado) --
#    GCP_SHEETS_CREDENTIALS     JSON da service account com acesso de Editor a planilha
#                               (conta SEPARADA da credencial do GCS)
#    SHEET_ID                   ID real da planilha (do link /d/<ID>/edit)
#    SHEET_NAME                 (opc) nome da aba; default = 1a aba
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

STATUS_TAB = "Como funciona (Lambdas)"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

s3 = boto3.client("s3")

_gcs_client = None
_spreadsheet = None
_sheet_ws = None
_status_ws = None

# Coluna da planilha a atualizar e colunas usadas para casar o hospital.
BILLING_COL_HEADER = "Last Update Billing"
MATCH_COL_HEADERS = ("cn", "Indice", "Nome do hospital")


# ---------------------------- GCS (upload) -----------------------------------
def _load_gcp_credentials_json():
    """JSON da service account do GCS: Secrets Manager (preferido) ou env var."""
    secret_arn = os.environ.get("GCP_CREDENTIALS_SECRET_ARN")
    if secret_arn:
        sm = boto3.client("secretsmanager")
        resp = sm.get_secret_value(SecretId=secret_arn)
        raw = resp.get("SecretString") or resp["SecretBinary"]
        return json.loads(raw)
    raw = os.environ.get("GCP_CREDENTIALS")
    if not raw:
        raise RuntimeError("Defina GCP_CREDENTIALS_SECRET_ARN ou GCP_CREDENTIALS.")
    return json.loads(raw)


def _get_gcs_client():
    global _gcs_client
    if _gcs_client is None:
        info = _load_gcp_credentials_json()
        creds = service_account.Credentials.from_service_account_info(info)
        _gcs_client = storage.Client(project=info.get("project_id"), credentials=creds)
    return _gcs_client


# ---------------------------- Planilha (Sheets) ------------------------------
def _get_spreadsheet():
    """Spreadsheet (gspread), ou None se as env vars da planilha nao existirem.

    Usa uma credencial SEPARADA (GCP_SHEETS_CREDENTIALS) da usada no GCS.
    """
    global _spreadsheet
    if _spreadsheet is None:
        raw = os.environ.get("GCP_SHEETS_CREDENTIALS")
        sheet_id = os.environ.get("SHEET_ID")
        if not raw or not sheet_id:
            return None  # recurso da planilha desativado
        info = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=[SHEETS_SCOPE]
        )
        _spreadsheet = gspread.authorize(creds).open_by_key(sheet_id)
    return _spreadsheet


def _get_sheet_ws():
    """Aba principal (Pagina1 / SHEET_NAME) com os hospitais e Last Update Billing."""
    global _sheet_ws
    if _sheet_ws is None:
        sh = _get_spreadsheet()
        if sh is None:
            return None
        name = os.environ.get("SHEET_NAME")
        _sheet_ws = sh.worksheet(name) if name else sh.sheet1
    return _sheet_ws


def _get_status_ws():
    """Aba de STATUS ATUAL; None se ausente (recurso desativado)."""
    global _status_ws
    if _status_ws is None:
        sh = _get_spreadsheet()
        if sh is None:
            return None
        try:
            _status_ws = sh.worksheet(STATUS_TAB)
        except gspread.WorksheetNotFound:
            _status_ws = False
    return _status_ws or None


def _update_status(src_bucket, filename, destino):
    """Upsert da linha do bucket na aba de status: Ultimo arquivo / Pasta / data."""
    ws = _get_status_ws()
    if ws is None:
        return
    rows = ws.get_all_values()
    target = None
    for i, row in enumerate(rows, start=1):
        if row and row[0].strip() == src_bucket:
            target = i
            break
    ts = _today_br()
    if target:
        ws.update(range_name=f"D{target}:F{target}", values=[[filename, destino, ts]])
    else:
        ws.append_row([src_bucket, "", "Billing", filename, destino, ts])


def _norm(s):
    """minusculo, sem acentos e sem nao-alfanumericos (para casar nomes)."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _today_br():
    # Brasil e' UTC-3 o ano todo (sem horario de verao desde 2019). Data + hora.
    return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")


def _update_sheet_billing(filename):
    """Escreve a data de hoje em 'Last Update Billing' na linha do hospital."""
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
    if BILLING_COL_HEADER not in idx:
        print(f"[sheet] coluna '{BILLING_COL_HEADER}' nao encontrada no cabecalho.")
        return
    billing_i = idx[BILLING_COL_HEADER]
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
    ws.update_cell(target_row, billing_i + 1, today)  # gspread e' 1-based
    print(f"[sheet] linha {target_row} -> {BILLING_COL_HEADER} = {today}")


# ---------------------------- Fluxo principal --------------------------------
def _process_object(bucket, key):
    filename = key.split("/")[-1]
    lower = filename.lower()

    billing_kw = os.environ.get("BILLING_KEYWORD", "billing").lower()
    if billing_kw not in lower:
        print(f"[skip] '{key}' nao contem '{billing_kw}' no nome. Ignorado.")
        return "skipped"

    gcp_bucket = os.environ["GCP_BUCKET"]
    folder = os.environ["GCP_FOLDER"]
    dest_blob_name = f"{folder.rstrip('/')}/{filename}"
    print(f"[copy] s3://{bucket}/{key} -> gs://{gcp_bucket}/{dest_blob_name}")

    local_path = os.path.join("/tmp", filename)
    s3.download_file(bucket, key, local_path)
    try:
        gcs = _get_gcs_client()
        blob = gcs.bucket(gcp_bucket).blob(dest_blob_name)
        blob.upload_from_filename(local_path, timeout=600)
    finally:
        try:
            os.remove(local_path)
        except OSError:
            pass

    # Atualiza a planilha DEPOIS da copia. Falha aqui NAO quebra o processo.
    try:
        _update_sheet_billing(filename)
    except Exception as e:  # noqa: BLE001
        print(f"[sheet][warn] falha ao atualizar planilha para '{filename}': {e}")

    # Atualiza a tabela de STATUS ATUAL (ultimo arquivo/pasta por bucket).
    try:
        _update_status(bucket, filename, f"{gcp_bucket}/{folder}")
    except Exception as e:  # noqa: BLE001
        print(f"[status][warn] {e}")

    return "copied"


def lambda_handler(event, context):
    results = []
    for record in event.get("Records", []):
        try:
            bucket = record["s3"]["bucket"]["name"]
            key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        except (KeyError, TypeError):
            print(f"[warn] registro sem os dados de S3 esperados: {record}")
            continue
        try:
            status = _process_object(bucket, key)
            results.append({"key": key, "status": status})
        except Exception as e:
            print(f"[error] falha ao processar '{key}': {e}")
            results.append({"key": key, "status": "error", "error": str(e)})
            raise
    return {"processed": results}
