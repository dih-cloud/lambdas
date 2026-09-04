# ihtransfer-s3-infer

Lambda que copia arquivos de um bucket **S3 (AWS)** para um bucket **Google Cloud
Storage**, escolhendo a pasta de destino conforme o **nome do arquivo**. Disparada
automaticamente quando o arquivo chega no S3.

---

## Visão geral

```
       (ObjectCreated)
 <SOURCE_BUCKET> ───────────► Lambda ihtransfer-s3-infer ───────────► gs://<GCS_BUCKET>/<pasta>/<arquivo>
   (bucket S3)                                                              (Google Cloud Storage)
```

Para cada arquivo que chega no S3:

1. Decide a **pasta** de destino no GCS pelo nome do arquivo:
   - contém algum marcador de `HISTORIC_FILENAME_MARKERS` (no código) → `GCP_FOLDER_HISTORIC`
   - caso contrário → `GCP_FOLDER`
2. Baixa do S3 para `/tmp` e sobe para o bucket GCS (o **original no S3 é mantido**).
3. Extrai um `doctor_name` do nome (2º campo separado por `_`) para um **registro
   opcional** num Web App (Apps Script) — **desativado por padrão**.

> Diferente do `transferBillingGCP`, esta função **não** filtra por `billing`: ela
> processa todos os arquivos do evento e roteia pela lista de marcadores.

---

## Marcadores de roteamento (no código)

A lista fica em `lambda_function.py`, logo abaixo dos imports, porque é editada com
frequência:

```python
HISTORIC_FILENAME_MARKERS = (
    "imip", "uopeccan", "ibcc", "felicio rocho", "amor",
    "angelina", "arnaldo", "hmd", "hcor", "hbompastor",
)
```

Comparação é case-insensitive (`casefold`). Se o nome do arquivo contém qualquer um
desses textos, o arquivo vai para `GCP_FOLDER_HISTORIC`; senão, para `GCP_FOLDER`.

---

## Variáveis de ambiente

| Nome | Obrig. | Descrição |
|---|---|---|
| `GCP_CREDENTIALS` | sim | JSON da service account (uma linha, com os `\n`) |
| `GCP_BUCKET` | sim | Bucket GCS de destino |
| `GCP_FOLDER` | sim | Pasta padrão (arquivos "diários") |
| `GCP_FOLDER_HISTORIC` | sim | Pasta para arquivos que batem com os marcadores |
| `WEBAPP_URL` | não | URL do Web App (Apps Script) para o registro opcional. **Contém token secreto no path** — por isso fica em env var, nunca no código. Vazio = recurso desativado. |

> A função implantada também usa `SHEET_ID` / `SHEET_NAME` (variação do registro via
> planilha). Esses não são consumidos por este código — o registro aqui é o bloco
> opcional via `WEBAPP_URL`, que está comentado.

A credencial do Google (`gcp-credentials.json`) **não** está no repositório — ela vive
na env var `GCP_CREDENTIALS` (ou no Secrets Manager).

---

## Arquivos

| Arquivo | O quê |
|---|---|
| `lambda_function.py` | Código do Lambda (comentado) |
| `requirements.txt` | `google-cloud-storage` + `requests` |
| `build.ps1` | Monta `ihtransfer.zip` com os wheels **Linux** |
| `deploy.ps1` | Sobe o zip via S3 e ajusta timeout/memória |
| `.gitignore` | Impede commit da credencial e de artefatos de build |

---

## Deploy / atualizar

As dependências têm código nativo, então o pacote precisa ser montado com os **wheels
do Linux** (alvo do Lambda), não os do Windows.

```powershell
# 1) montar o ihtransfer.zip (codigo + dependencias Linux)
powershell -ExecutionPolicy Bypass -File .\build.ps1

# 2) preencher os placeholders no deploy.ps1 e subir
powershell -ExecutionPolicy Bypass -File .\deploy.ps1
```

> Sem pip local? Dá para montar com Docker:
> `docker run --rm -v ${PWD}:/var/task public.ecr.aws/sam/build-python3.12 pip install -r requirements.txt -t build`

O `deploy.ps1` **não** mexe nas variáveis de ambiente (pra não expor segredos) — elas
ficam no console do Lambda.

---

## Config sugerida da função

- Runtime: Python 3.12 · Handler `lambda_function.lambda_handler`
- Timeout: 900 s · Memória: 512 MB · `/tmp`: 2 GB
- Sem VPC (precisa de internet para alcançar o Google)
- Role: `AWSLambdaBasicExecutionRole` + `s3:GetObject` no bucket de origem

---

## Notas

- Nome do arquivo é preservado no GCS: `<pasta>/<nome-original>`.
- Nomes fora do formato `<a>_<doctor_name>_<resto>` são pulados (log "Formato inesperado").
- O objeto original no S3 é mantido (só a cópia em `/tmp` é removida).
