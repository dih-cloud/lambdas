# transferBillingGCP

Lambda que copia arquivos de **billing** de qualquer bucket **S3 (AWS)** para um bucket
**Google Cloud Storage**, disparado automaticamente quando o arquivo chega no S3.

---

## Visão geral

```
        (ObjectCreated)
 qualquer bucket S3 ──────────► Lambda transferBillingGCP ──────────► gs://<GCS_BUCKET>/<GCP_FOLDER>/<arquivo>
  (com o gatilho)                     (us-east-2)                            (Google Cloud Storage)
```

Quando um objeto é criado num bucket S3 que tem o gatilho:

1. Se o nome contém **`billing`** → copia para o bucket do Google. Se não contém → ignora.
2. Sobe para `GCP_BUCKET`, na pasta `GCP_FOLDER` (ex.: `billing-to-process`).
3. O objeto **original no S3 é sempre mantido** (não há delete).

> O bucket de origem vem do **próprio evento**, então a mesma função atende **vários
> buckets**: basta adicionar a notificação de evento apontando para este Lambda em cada
> bucket. **Todos** os arquivos com `billing` vão para a mesma pasta `GCP_FOLDER`.

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
| Buckets de origem (gatilho) | qualquer bucket com notificação `s3:ObjectCreated:*` → este Lambda |
| Bucket de destino (GCS) | `<GCS_BUCKET>` |
| Pasta de destino | `billing-to-process` |

---

## Variáveis de ambiente

| Nome | Obrig. | Exemplo | Descrição |
|---|---|---|---|
| `GCP_BUCKET` | sim | `<GCS_BUCKET>` | Bucket GCS destino |
| `GCP_FOLDER` | sim | `billing-to-process` | Pasta de destino |
| `GCP_CREDENTIALS` | * | `{...json...}` | JSON da service account (uma linha, com os `\n`) |
| `GCP_CREDENTIALS_SECRET_ARN` | * | `arn:aws:secretsmanager:...` | Alternativa mais segura ao `GCP_CREDENTIALS` |
| `BILLING_KEYWORD` | não | `billing` | Palavra que dispara a cópia (default `billing`) |

`*` = configure **uma** das duas formas de credencial. Se `GCP_CREDENTIALS_SECRET_ARN`
existir, ele tem prioridade e o `GCP_CREDENTIALS` é ignorado.

A credencial do Google (`gcp-credentials.json`) **não** está no repositório — vive na env
var `GCP_CREDENTIALS` da função (ou no Secrets Manager).

---

## Os buckets envolvidos (não confundir)

| Papel | Placeholder | Função |
|---|---|---|
| Origem (gatilho) | qualquer bucket | Onde os `.csv`/arquivos com `billing` chegam e disparam o Lambda |
| Destino (Google) | `<GCS_BUCKET>` | Bucket GCS para onde os arquivos são copiados |
| Do zip (deploy) | `<CODE_BUCKET>` | Guarda apenas o `billing-gcs.zip` do código para subir no Lambda |

O **bucket do zip** (`<CODE_BUCKET>`) **não faz parte do fluxo dos arquivos de billing** —
ele só é usado no deploy (o upload direto de ~10 MB para o Lambda costuma cair, então o
zip vai primeiro para o S3 e o Lambda o carrega de lá; ver `deploy.ps1`).

---

## Arquivos

| Arquivo | O quê |
|---|---|
| `lambda_function.py` | Código do Lambda (comentado) |
| `requirements.txt` | Dependência: `google-cloud-storage` |
| `build.ps1` | Monta `billing-gcs.zip` com os wheels **Linux** (necessário p/ o Lambda) |
| `deploy.ps1` | Sobe o zip via S3, ajusta timeout/memória e a permissão IAM |
| `.gitignore` | Impede commit da credencial e de artefatos de build |

---

## Deploy / atualizar

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

O `deploy.ps1` **não** mexe nas variáveis de ambiente (pra não expor a credencial) —
elas ficam no console do Lambda.

---

## Adicionar um novo bucket de origem

Pelo console S3 do bucket → **Properties → Event notifications → Create event
notification**: eventos **All object create events**, destino **Lambda function** →
`transferBillingGCP`. O console adiciona sozinho a permissão de invocação.

Ou via CLI:

```bash
aws s3api put-bucket-notification-configuration \
  --bucket <BUCKET> --region us-east-2 --profile eks-operator \
  --notification-configuration '{
    "LambdaFunctionConfigurations": [{
      "LambdaFunctionArn": "arn:aws:lambda:us-east-2:<ACCOUNT_ID>:function:transferBillingGCP",
      "Events": ["s3:ObjectCreated:*"]
    }]
  }'

aws lambda add-permission --function-name transferBillingGCP \
  --statement-id s3invoke-<BUCKET> --action lambda:InvokeFunction \
  --principal s3.amazonaws.com --source-arn arn:aws:s3:::<BUCKET> \
  --region us-east-2 --profile eks-operator
```

A role já tem `s3:GetObject` em `arn:aws:s3:::*/*`, então qualquer bucket novo já pode
ser lido sem alterar IAM.

---

## Permissões da role (IAM)

- `AWSLambdaBasicExecutionRole` — logs no CloudWatch
- `s3:GetObject` em `arn:aws:s3:::*/*` (qualquer bucket de origem) — política `ReadAnyBillingSource`
- `secretsmanager:GetSecretValue` no secret — **apenas** se usar `GCP_CREDENTIALS_SECRET_ARN`

O Lambda **não** roda em VPC, então tem acesso à internet para alcançar o Google.

> Se preferir restringir, troque o `Resource` da política por uma lista dos buckets
> específicos (ex.: `arn:aws:s3:::<SOURCE_BUCKET>/*`, ...) em vez de `*/*`.

---

## Testar

```bash
# subir um arquivo com "billing" no nome dispara a copia automaticamente
aws s3 cp billing_teste.csv s3://<BUCKET>/ --profile eks-operator

# conferir no destino
gsutil ls -l gs://<GCS_BUCKET>/billing-to-process/
```

Reprocessar um arquivo manualmente (sem re-subir): invocar a função com um evento S3
sintético apontando para a key existente.

```bash
aws lambda invoke --function-name transferBillingGCP \
  --payload '{"Records":[{"s3":{"bucket":{"name":"<BUCKET>"},"object":{"key":"ARQUIVO.csv"}}}]}' \
  --cli-binary-format raw-in-base64-out \
  --region us-east-2 --profile eks-operator resp.json && cat resp.json
```

Logs:

```bash
aws logs tail /aws/lambda/transferBillingGCP --region us-east-2 --profile eks-operator --since 1h --follow
```

---

## Notas

- Nome do arquivo é preservado no GCS: `<GCP_FOLDER>/<nome-original>`.
- Se o upload falhar, o handler faz `raise` → o Lambda marca falha e o S3 tenta de novo
  (considere uma DLQ se quiser rastrear falhas).
- O cliente GCS é criado uma vez por container e reaproveitado entre invocações.
