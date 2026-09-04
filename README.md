# lambdas

Funções AWS Lambda da Doctor Assistant. Cada pasta é uma função independente, com seu
próprio código, dependências e instruções de deploy.

| Lambda | Descrição |
|---|---|
| [`transferBillingGCP`](transferBillingGCP/) | Copia arquivos de billing de um bucket S3 para o Google Cloud Storage quando chegam. |
| [`ihtransfer-s3-infer`](ihtransfer-s3-infer/) | Copia arquivos do S3 para o Google Cloud Storage, roteando para pasta diária ou histórica conforme marcadores no nome do arquivo. |

> **Segurança:** credenciais (service accounts, tokens, access keys) **nunca** vão para
> este repositório — cada função lê as suas de variáveis de ambiente ou do AWS Secrets
> Manager. Veja o `.gitignore` de cada pasta.
