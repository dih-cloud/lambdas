# ihtransfer-s3-infer

Lambda que copia arquivos de **CN** dos buckets S3 para um bucket **Google Cloud
Storage** e registra a data numa **planilha de acompanhamento**, disparada
automaticamente quando o arquivo chega no S3.

---

## Visão geral

```
       (ObjectCreated, *.csv)
 buckets shc-* (CN) ─────────► Lambda ihtransfer-s3-infer ─────────► gs://<GCS_BUCKET>/cns-to-process/<arquivo>
                              (us-east-1 e us-east-2)                    (Google Cloud Storage)
                                        │
                                        └──► planilha de acompanhamento: coluna "Last Update CN"
```

Para cada arquivo que chega:

1. **Normaliza o nome**: buckets que mandam formato próprio (ex.: Felício Rocho envia
   `DISP…CSV`) são renomeados para `cn_<hospital>_<ts>.csv` via `SPECIAL_BUCKET_HOSPITAL`
   (mapa bucket→hospital), pra casar pasta e planilha.
2. Copia para o bucket GCS `GCP_BUCKET`, escolhendo a pasta pelo nome:
   - contém algum marcador de `HISTORIC_FILENAME_MARKERS` → `GCP_FOLDER_HISTORIC`
   - caso contrário → `GCP_FOLDER`
   Como todos os hospitais de CN estão nos marcadores, na prática vão para
   `GCP_FOLDER_HISTORIC` (`cns-to-process`).
3. **Atualiza a planilha**: escreve a data de hoje (DD/MM/AAAA HH:MM, fuso Brasil) na
   coluna **`Last Update CN`** da linha do hospital, casando pelo nome
   (`cn` → `Indice` → `Nome do hospital`, normalizado). Falha aqui **não** quebra a cópia.
4. O objeto **original no S3 é sempre mantido**.
5. **Status ao vivo**: faz *upsert* da linha do bucket na aba **"Como funciona (Lambdas)"**
   (colunas `Último arquivo`, `Pasta GCS (destino)`, `Atualizado em`), mostrando pra onde
   cada bucket está mandando no momento.

> É a mesma planilha usada pela `transferBillingGCP` — lá ela preenche `Last Update
> Billing`; aqui, `Last Update CN`.

---

## Escopo atual (produção)

Implantada e ativa (código + update de planilha) em **us-east-2 e us-east-1** — mesmo
código nas duas regiões.

- **us-east-2** (buckets `cn_<hospital>_<ts>.csv`): `shc-ibcc`, `shc-uopeccan`,
  `shc-drarnaldo`, `shc-hmd`, `shc-ingest-imip`, `shc-ingest-hac-angelina`,
  `shc-hospitalbompastor`.
- **us-east-1**: `shc-hcor-patologia` (`cn_HCOR_`), `shc-ingest-hamor`
  (`cn_Hospital de Amor_`) e `shc-ingest-felicio-rocho` (`DISP…CSV` → renomeado para
  `cn_Felicio Rocho_<ts>.csv` via `SPECIAL_BUCKET_HOSPITAL`). O deploy aqui também
  **corrigiu** um bug de sintaxe pré-existente que deixava a função us-east-1 quebrada.
- Bucket GCS destino: `shc-infer-mt`. Todos os CN caem em `cns-to-process`.

> **Felício Rocho** está com a coleta parada desde ~junho (sem arquivos novos no S3),
> então o `Last Update CN` dele só será preenchido quando a coleta voltar. O código já
> está pronto para ele (rename por bucket).

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
