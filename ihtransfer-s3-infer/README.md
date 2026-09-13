# ihtransfer-s3-infer

Lambda que copia arquivos de **CN** dos buckets S3 para um bucket **Google Cloud
Storage** e registra a data numa **planilha de acompanhamento**, disparada
automaticamente quando o arquivo chega no S3.

---

## Visão geral

```
       (ObjectCreated, *.csv)
 buckets shc-* (CN) ─────────► Lambda ihtransfer-s3-infer ─────────► gs://<GCS_BUCKET>/<pasta>/<arquivo>
                                    (us-east-2)                             (Google Cloud Storage)
                                        │
                                        └──► planilha de acompanhamento: coluna "Last Update CN"
```

Para cada arquivo que chega:

1. Copia para o bucket GCS `GCP_BUCKET`, escolhendo a pasta pelo nome do arquivo:
   - contém algum marcador de `HISTORIC_FILENAME_MARKERS` → `GCP_FOLDER_HISTORIC`
   - caso contrário → `GCP_FOLDER`
2. **Atualiza a planilha**: escreve a data de hoje (DD/MM/AAAA HH:MM, fuso Brasil) na
   coluna **`Last Update CN`** da linha do hospital, casando pelo nome do arquivo
   (`cn` → `Indice` → `Nome do hospital`, normalizado). Falha aqui **não** quebra a cópia.
3. O objeto **original no S3 é sempre mantido**.

> É a mesma planilha usada pela `transferBillingGCP` — lá ela preenche `Last Update
> Billing`; aqui, `Last Update CN`.

---

## Escopo atual (produção)

- Região implantada com o update de planilha: **us-east-2**.
- Buckets de origem (gatilho `s3:ObjectCreated:*`, sufixo `.csv`): `shc-ibcc`,
  `shc-uopeccan`, `shc-drarnaldo`, `shc-hmd`, `shc-ingest-imip`,
  `shc-ingest-hac-angelina`, `shc-hospitalbompastor`. Arquivos no formato
  `cn_<hospital>_<ts>.csv`.
- Bucket GCS destino: `shc-infer-mt` (pastas `cns-for-processing` / `cns-to-process`).

> **Nota us-east-1**: existe uma cópia desta função em us-east-1 (buckets
> `shc-hcor-patologia`, `shc-ingest-felicio-rocho`, `shc-ingest-hamor`) que **não** foi
> alterada aqui e está com um bug de sintaxe pré-existente — fora do escopo desta versão.
> Felício Rocho envia arquivos `DISP…` (sem `cn_<hospital>`), que **não** casam por nome.

---

## Variáveis de ambiente

| Nome | Obrig. | Exemplo | Descrição |
|---|---|---|---|
| `GCP_CREDENTIALS` | sim | `{...json...}` | JSON da service account do **GCS** (bucket destino) |
| `GCP_BUCKET` | sim | `shc-infer-mt` | Bucket GCS de destino |
| `GCP_FOLDER` | sim | `cns-for-processing` | Pasta padrão |
| `GCP_FOLDER_HISTORIC` | sim | `cns-to-process` | Pasta p/ arquivos que batem com os marcadores |
| `GCP_SHEETS_CREDENTIALS` | não | `{...json...}` | JSON de uma service account **separada** com Editor na planilha. Sem ela, o passo da planilha é pulado. |
| `SHEET_ID` | não | `1hWrSe…` | ID da planilha de acompanhamento (`/d/<ID>/edit`). |
| `SHEET_NAME` | não | — | Nome da aba; se ausente, usa a 1ª aba. |

As credenciais **não** ficam no repositório. São **duas contas separadas**: GCS
(`GCP_CREDENTIALS`) e planilha (`GCP_SHEETS_CREDENTIALS`).

> **Limite de 4KB das env vars**: as duas credenciais juntas estouram os 4KB do Lambda,
> então os JSONs são gravados **enxutos** (só `private_key`, `client_email`, `token_uri`
> e, no GCS, `project_id`) — o suficiente para o `google-auth` autenticar.

---

## Arquivos

| Arquivo | O quê |
|---|---|
| `lambda_function.py` | Código do Lambda (comentado) |
| `requirements.txt` | `google-cloud-storage` + `gspread` |
| `build.ps1` | Monta o zip com wheels **Linux** |
| `deploy.ps1` | Sobe o zip via S3 e ajusta a config |
| `.gitignore` | Impede commit de credencial e artefatos de build |

## Deploy

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
powershell -ExecutionPolicy Bypass -File .\deploy.ps1
```

As env vars (`GCP_*`, `SHEET_ID`) são definidas no console do Lambda — o deploy não as altera.
