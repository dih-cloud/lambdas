# transferBillingGCP

Lambda que copia arquivos de **billing** de um bucket **S3 (AWS)** para um bucket
**Google Cloud Storage**, disparado automaticamente quando o arquivo chega no S3.

---

## Visão geral

```
                    (ObjectCreated, *.csv)
 <SOURCE_BUCKET>  ─────────────────────►  Lambda transferBillingGCP  ─────►  gs://<GCS_BUCKET>/<pasta>/<arquivo>
   (bucket S3)                                    (us-east-2)                        (Google Cloud Storage)
```

Quando um objeto é criado no bucket S3 de origem, o Lambda:

1. Olha o **nome** do arquivo.
2. Se contém `billing` → copia para o bucket do Google. Se não contém → ignora.
3. Escolhe a **pasta** de destino no GCS conforme a lista `MATCH_KEYWORDS` (no código):
   - nome contém alguma palavra da lista (ex.: `ibcc`) → `GCP_FOLDER_HISTORIC`
   - caso contrário → `GCP_FOLDER`
4. O objeto **original no S3 é sempre mantido** (não há delete).

---

## Ambiente atual (produção)

| Item | Valor |
|---|---|
| Conta AWS | `<ACCOUNT_ID>` |
| Região | `us-east-2` |
| Função | `transferBillingGCP` |
| Runtime | Python 3.12 · Handler `lambda_function.lambda_handler` |
| Timeout / Memória / /tmp | 120 s · 512 MB · 2 GB |
| Role | `<LAMBDA_ROLE_NAME>` |
| Bucket de origem (gatilho) | `<SOURCE_BUCKET>` · evento `s3:ObjectCreated:*` · sufixo `.csv` |
| Bucket de destino (GCS) | `<GCS_BUCKET>` |
| Pasta padrão / especial | `billing-to-process` / `billing-for-process` |
| Lista de match (no código) | `MATCH_KEYWORDS = ["ibcc"]` |

> Como os arquivos de `<SOURCE_BUCKET>` se chamam `billing_IBCC_*.csv`, eles contêm
> `ibcc` e caem em **`billing-for-process`**.

---

## Arquivos

| Arquivo | O quê |
|---|---|
| `lambda_function.py` | Código do Lambda (todo comentado) |
| `requirements.txt` | Dependência: `google-cloud-storage` |
| `build.ps1` | Monta `billing-gcs.zip` com os wheels **Linux** (necessário p/ o Lambda) |
| `deploy.ps1` | Sobe o zip via S3, ajusta timeout/memória e a permissão IAM |
| `.gitignore` | Impede commit da credencial e de artefatos de build |

> A credencial do Google (`gcp-credentials.json`) **não** está no repositório — ela vive
> na variável de ambiente `GCP_CREDENTIALS` da função (ou no Secrets Manager).

---

## Variáveis de ambiente

| Nome | Obrig. | Exemplo | Descrição |
|---|---|---|---|
| `GCP_BUCKET` | sim | `<GCS_BUCKET>` | Bucket GCS destino |
| `GCP_FOLDER` | sim | `billing-to-process` | Pasta padrão |
| `GCP_FOLDER_HISTORIC` | sim | `billing-for-process` | Pasta p/ quem bate com `MATCH_KEYWORDS` |
| `GCP_CREDENTIALS` | * | `{...json...}` | JSON da service account (uma linha, com os `\n`) |
| `GCP_CREDENTIALS_SECRET_ARN` | * | `arn:aws:secretsmanager:...` | Alternativa mais segura ao `GCP_CREDENTIALS` |
| `BILLING_KEYWORD` | não | `billing` | Palavra que dispara a cópia (default `billing`) |

`*` = configure **uma** das duas formas de credencial. Se o `GCP_CREDENTIALS_SECRET_ARN`
existir, ele tem prioridade e o `GCP_CREDENTIALS` é ignorado.

A lista de hospitais/palavras que roteiam para a pasta especial fica **no código**
(`MATCH_KEYWORDS`, logo abaixo dos imports), porque é editada com frequência:

```python
MATCH_KEYWORDS = ["ibcc"]                 # individual
MATCH_KEYWORDS = ["ibcc", "uopeccan"]     # lista
MATCH_KEYWORDS = []                        # nada vai para a pasta especial
```

---

## Como fazer deploy / atualizar

A dependência `google-cloud-storage` tem código nativo, então o pacote precisa ser
montado com os **wheels do Linux** (alvo do Lambda), não os do Windows.

```powershell
# 1) montar o billing-gcs.zip (codigo + dependencias Linux)
powershell -ExecutionPolicy Bypass -File .\build.ps1

# 2) subir para o Lambda (via S3), ajustar config e IAM
powershell -ExecutionPolicy Bypass -File .\deploy.ps1
```

> Sem pip local? Dá para montar com Docker:
> `docker run --rm -v ${PWD}:/var/task public.ecr.aws/sam/build-python3.12 pip install -r requirements.txt -t build`

O `deploy.ps1` **não** mexe nas variáveis de ambiente (pra não expor a credencial no
script) — elas são mantidas no console do Lambda.

---

## Gatilho S3 (já configurado)

Notificação no bucket `<SOURCE_BUCKET>` → Lambda `transferBillingGCP`, eventos
`s3:ObjectCreated:*`, sufixo `.csv`. Para replicar em outro bucket:

```bash
aws s3api put-bucket-notification-configuration \
  --bucket <BUCKET> --region us-east-2 --profile eks-operator \
  --notification-configuration '{
    "LambdaFunctionConfigurations": [{
      "LambdaFunctionArn": "arn:aws:lambda:us-east-2:<ACCOUNT_ID>:function:transferBillingGCP",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": {"Key": {"FilterRules": [{"Name":"suffix","Value":".csv"}]}}
    }]
  }'
```

E conceder ao S3 permissão de invocar a função (uma vez por bucket):

```bash
aws lambda add-permission --function-name transferBillingGCP \
  --statement-id s3invoke-<BUCKET> --action lambda:InvokeFunction \
  --principal s3.amazonaws.com --source-arn arn:aws:s3:::<BUCKET> \
  --region us-east-2 --profile eks-operator
```

Lembre-se de dar `s3:GetObject` da role no novo bucket (o `deploy.ps1` faz isso para
`<SOURCE_BUCKET>`).

---

## Permissões da role (IAM)

- `AWSLambdaBasicExecutionRole` — logs no CloudWatch
- `s3:GetObject` no bucket de origem (`<SOURCE_BUCKET>/*`)
- `secretsmanager:GetSecretValue` no secret — **apenas** se usar `GCP_CREDENTIALS_SECRET_ARN`

O Lambda **não** roda em VPC, então tem acesso à internet para alcançar o Google.

---

## Testar

```bash
# subir um .csv com "billing" no nome dispara a copia automaticamente
aws s3 cp billing_IBCC_teste.csv s3://<SOURCE_BUCKET>/ --profile eks-operator

# conferir no destino
gsutil ls -l gs://<GCS_BUCKET>/billing-for-process/
```

Reprocessar um arquivo manualmente (sem re-subir): invocar a função com um evento S3
sintético apontando para a key existente.

```bash
aws lambda invoke --function-name transferBillingGCP \
  --payload '{"Records":[{"s3":{"bucket":{"name":"<SOURCE_BUCKET>"},"object":{"key":"ARQUIVO.csv"}}}]}' \
  --cli-binary-format raw-in-base64-out \
  --region us-east-2 --profile eks-operator resp.json && cat resp.json
```

Logs:

```bash
aws logs tail /aws/lambda/transferBillingGCP --region us-east-2 --profile eks-operator --since 1h --follow
```

---

## Notas

- Nome do arquivo é preservado no GCS: `<pasta>/<nome-original>`.
- Se o upload falhar, o handler faz `raise` → o Lambda marca falha e o S3 tenta de novo
  (considere uma DLQ se quiser rastrear falhas).
- O cliente GCS é criado uma vez por container e reaproveitado entre invocações.
