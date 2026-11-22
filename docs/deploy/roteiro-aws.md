# Roteiro de deploy na AWS: Ovitrampas API

Passo a passo, **pelo console da AWS**, para colocar a API no ar com pipeline de CI/CD, fitness functions e observabilidade. Segue a mesma lógica dos laboratórios das Aulas 12, 13 e 14 (Notas da Nuvem), adaptada ao nosso projeto.

> **Quem fez o quê:** o roteiro, os buildspecs, os testes e a task definition foram rascunhados com apoio de IA (Claude) e precisam ser revisados e entendidos pelo grupo antes da entrega. Nunca colem Account ID, ARNs ou senhas em ferramentas externas (PO-CESAR-021).

## Sumário

0. [Antes de começar](#0-antes-de-começar)
1. [Rede (VPC)](#1-rede-vpc)
2. [Security groups](#2-security-groups)
3. [Banco: RDS PostgreSQL](#3-banco-rds-postgresql)
4. [Fila: ElastiCache Serverless (Valkey)](#4-fila-elasticache-serverless-valkey)
5. [Repositório de imagens: ECR](#5-repositório-de-imagens-ecr)
6. [Grupos de log no CloudWatch](#6-grupos-de-log-no-cloudwatch)
7. [Roles do IAM](#7-roles-do-iam)
8. [Conexão com o GitHub](#8-conexão-com-o-github)
9. [Pipeline, parte 1: portões e imagem](#9-pipeline-parte-1-portões-e-imagem)
10. [ECS: cluster, task definition, ALB e service](#10-ecs-cluster-task-definition-alb-e-service)
11. [CloudFront (HTTPS) e trava do ALB](#11-cloudfront-https-e-trava-do-alb)
12. [Pipeline, parte 2: aprovação e deploy](#12-pipeline-parte-2-aprovação-e-deploy)
13. [Observabilidade](#13-observabilidade)
14. [Experimentos para as evidências](#14-experimentos-para-as-evidências)
15. [Mapa de evidências x nota](#15-mapa-de-evidências-x-nota)
16. [Custos, pausa e limpeza](#16-custos-pausa-e-limpeza)
17. [Problemas comuns](#17-problemas-comuns)
18. [Próximos passos (worker, frontend, banco)](#18-próximos-passos-worker-frontend-banco)

---

## 0. Antes de começar

**Região:** `us-east-2` (Ohio), onde a conta do grupo foi configurada. (Os laboratórios usaram `us-east-1`; os preços são praticamente iguais.) Confira no canto superior direito do console **antes de cada passo**: um recurso criado em outra região "some" para os outros passos.

**Alerta de custo (obrigatório):** *Billing and Cost Management → Budgets → Create budget → Use a template → Monthly cost budget*. Valor: **US$ 40**. E-mails: de pelo menos duas pessoas do grupo. Faça isso primeiro.

**Nomes que vamos usar** (para todo mundo do grupo falar a mesma língua):

| Recurso | Nome |
|---|---|
| VPC | `ovitrampas` |
| Security groups | `sg-ovitrampas-alb`, `sg-ovitrampas-api`, `sg-ovitrampas-rds`, `sg-ovitrampas-valkey` |
| Banco RDS | `ovitrampas-db` |
| Cache Valkey | `ovitrampas-fila` |
| Repositório ECR | `ovitrampas-api` |
| Cluster ECS / service | `ovitrampas` / `ovitrampas-api` |
| Task definition (família) | `ovitrampas-api` |
| Load balancer / target group | `alb-ovitrampas` / `tg-ovitrampas-api` |
| Projetos CodeBuild | `ovitrampas-seguranca`, `ovitrampas-testes`, `ovitrampas-imagem` |
| Pipeline | `ovitrampas-pipeline` |

**Anotações:** abram um arquivo **fora do Git** (bloco de notas, gerenciador de senhas) para guardar ARNs, endpoints e o valor do cabeçalho secreto do CloudFront. Nada disso vai para o repositório.

### Como fica no final

```
Navegador ──HTTPS──► CloudFront ──HTTP + cabeçalho secreto──► ALB (sub-redes públicas)
                                                               │  só aceita IPs do CloudFront
                                                               ▼
                                     ECS Fargate: task "ovitrampas-api"
                                     ┌───────────────────────────────────┐
                                     │ contêiner api (FastAPI :8000)     │──► RDS PostgreSQL  (sub-rede privada, TLS)
                                     │ contêiner adot (coletor OTel)     │──► ElastiCache Valkey (sub-rede privada, TLS)
                                     └───────────────────────────────────┘
                                          │ logs            │ traces e métricas
                                          ▼                 ▼
                                   CloudWatch Logs     X-Ray + CloudWatch Metrics

GitHub (main) ──► CodePipeline: Verificação [Segurança ‖ Testes] → Imagem (Trivy → ECR) → Aprovação → Deploy (ECS)
```

O diagrama completo e as justificativas estão em [`arquitetura-aws.md`](arquitetura-aws.md) e na ADR-007.

---

## 1. Rede (VPC)

**O que é:** a VPC é a "rede particular" do projeto dentro da AWS. Dentro dela há **sub-redes públicas** (têm rota para a internet) e **sub-redes privadas** (não têm). O banco e a fila ficam nas privadas: nem que alguém descubra o endereço, não há caminho da internet até eles.

*VPC → Create VPC → **VPC and more***:

- Name tag auto-generation: `ovitrampas`
- IPv4 CIDR: `10.0.0.0/16`
- Number of AZs: **2** · Public subnets: **2** · Private subnets: **2**
- NAT gateways: **None** (economia: um NAT custa cerca de US$ 32/mês; veja a decisão na ADR-007)
- VPC endpoints: **S3 Gateway** (é gratuito)
- DNS hostnames e DNS resolution: **ligados**

📸 **Evidência D1:** print do *Resource map* da VPC.

---

## 2. Security groups

**O que é:** um security group (SG) é um "porteiro" que fica na frente de cada recurso e só deixa entrar quem está na lista. Em vez de liberar endereços IP, liberamos **outro SG**: "só quem usa o SG da API pode falar com o banco".

*VPC → Security groups → Create security group* (todos na VPC `ovitrampas`). Crie os quatro **sem regras de entrada** primeiro, depois edite as regras, porque um SG referencia o outro.

| SG | Regra de entrada (Inbound) | Origem |
|---|---|---|
| `sg-ovitrampas-alb` | HTTP, TCP 80 | *Prefix list* `com.amazonaws.global.cloudfront.origin-facing` (só o CloudFront) |
| `sg-ovitrampas-api` | Custom TCP 8000 | `sg-ovitrampas-alb` |
| `sg-ovitrampas-rds` | PostgreSQL, TCP 5432 | `sg-ovitrampas-api` |
| `sg-ovitrampas-valkey` | Custom TCP **6379-6380** | `sg-ovitrampas-api` |

Saída (Outbound): deixe o padrão (tudo liberado).

> O ElastiCache Serverless usa **duas portas** (6379 e 6380). Liberar só a 6379 é um erro comum.

📸 **Evidência S1:** print das regras de entrada dos quatro SGs. Na apresentação de Segurança, isso mostra o **menor privilégio de rede**: cada peça só fala com a vizinha.

---

## 3. Banco: RDS PostgreSQL

**O que é:** o mesmo PostgreSQL do `docker compose`, só que a AWS cuida de disco, backup e atualizações.

*RDS → Create database → Standard create*:

- Engine: **PostgreSQL**, versão **16.x** (a mesma do compose)
- Templates: **Free tier** (se aparecer) ou *Dev/Test*
- DB instance identifier: `ovitrampas-db`
- Master username: `ovitrampas_admin`
- Credentials management: **Managed in AWS Secrets Manager** (a senha é gerada pela AWS e guardada no Secrets Manager. Ninguém do grupo precisa digitá-la ou vê-la)
- Instance: **db.t4g.micro** · Storage: **gp3, 20 GB** · em *Additional storage configuration*, **desmarque *Enable storage autoscaling*** (vem ligado por padrão, até 1.000 GiB)
- Availability: **Single-AZ** (é um MVP; veja o custo assumido na ADR-007)
- Connectivity: VPC `ovitrampas`, **DB subnet group `ovitrampas-db-privadas`** (veja abaixo), **Public access: No**, security group **`sg-ovitrampas-rds`** (remova o `default`)

> **Crie o DB subnet group antes** (*RDS → Subnet groups → Create DB subnet group*): nome `ovitrampas-db-privadas`, VPC `ovitrampas`, as duas AZs e **só as duas sub-redes com `private` no nome**. Se deixar "Create new DB Subnet Group" na tela do banco, o RDS pode incluir também as sub-redes públicas, e o desenho da arquitetura ("banco em sub-rede privada") deixa de bater com a realidade.
- Additional configuration → **Initial database name: `ovitrampas`** (sem isso, o banco não é criado)
- Backup: retenção de 7 dias · Encryption: **ligada** (padrão)

Leva uns 10 minutos. Depois anote:
- **Endpoint** (aba *Connectivity & security*), ex.: `ovitrampas-db.xxxx.us-east-2.rds.amazonaws.com`
- **ARN do segredo** (*Configuration → Master credentials ARN*), algo como `arn:aws:secretsmanager:us-east-2:<CONTA>:secret:rds!db-...`

> **Rotação da senha:** o segredo gerenciado pelo RDS troca a senha automaticamente (por padrão, a cada 7 dias). A task da API lê a senha **quando inicia**: depois de uma rotação, as tasks antigas continuam com a senha velha e o `/health/ready` passa a dar 503. No MVP, aumente o período em *Secrets Manager → o segredo → Rotation → Edit rotation* para o máximo, e anote na ADR. Depois de uma rotação, *ECS → service → Update → Force new deployment* resolve.

> **Os scripts de `db/init/` NÃO rodam no RDS.** Esse mecanismo é da imagem Docker do Postgres. O esquema de verdade vai precisar de migrações (ver [seção 18](#18-próximos-passos-worker-frontend-banco)).

---

## 4. Fila: ElastiCache Serverless (Valkey)

**O que é:** o Valkey é um "irmão" open source do Redis, que fala **o mesmo protocolo**: a biblioteca `redis` do Python conversa com ele sem mudar nada no código. A versão *serverless* cobra pelo uso, com mínimo de cerca de US$ 6/mês, e exige **TLS** (criptografia na conexão). Por isso a API tem a variável `REDIS_TLS=true`.

*ElastiCache → Valkey caches → Create Valkey cache*:

- Deployment option: **Serverless** · Creation method: **New cache**
- Name: `ovitrampas-fila`
- Connectivity: VPC `ovitrampas`, sub-redes **privadas**, security group **`sg-ovitrampas-valkey`**
- Mantenha os demais padrões.

Anote o **Endpoint** (sem a porta), ex.: `ovitrampas-fila-xxxx.serverless.use2.cache.amazonaws.com`.

### 4b. (Recomendado para Segurança) Usuário e senha na fila

Sem um grupo de usuários, qualquer coisa que passe pelo security group consegue usar a fila. Para ter também autenticação:

1. *ElastiCache → User management → Create user*: User ID e User name `ovitrampas-app`, engine **Valkey**, Access string `on ~* +@all`, senha forte (gere com `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
2. *User group management → Create user group*: `ovitrampas-usuarios`, engine Valkey, adicione `ovitrampas-app`.
3. *Valkey caches → ovitrampas-fila → Modify → Access control*: escolha o grupo `ovitrampas-usuarios`.
4. *Secrets Manager → Store a new secret → Other type of secret*: chaves `username` = `ovitrampas-app` e `password` = (a senha). Nome: `ovitrampas/valkey`. Anote o ARN.
5. Na task definition (passo 10.2), acrescente em `secrets`:
   ```json
   { "name": "REDIS_USERNAME", "valueFrom": "<ARN-DO-SEGREDO-VALKEY>:username::" },
   { "name": "REDIS_PASSWORD", "valueFrom": "<ARN-DO-SEGREDO-VALKEY>:password::" }
   ```
   O ARN desse segredo já tem lugar na política `politica-execucao-segredos.json` (passo 7.2), no marcador `<ARN-DO-SEGREDO-VALKEY>`.

---

## 5. Repositório de imagens: ECR

*ECR → Private registry → Repositories → Create repository*:

- Name: `ovitrampas-api`
- Image tag mutability: **Immutable** (uma tag nunca é reescrita: a tag `a1b2c3d4e5f6` sempre aponta para o mesmo código)
- Depois de criar: *Actions → Edit scan settings*: **Scan on push** (varredura básica gratuita, uma segunda opinião além do Trivy)
- *Lifecycle policy → Create rule*, ou cole o JSON de [`deploy/aws/ecr-lifecycle.json`](../../deploy/aws/ecr-lifecycle.json): guarda só as 10 imagens mais recentes

Anote a **URI**: `<CONTA>.dkr.ecr.us-east-2.amazonaws.com/ovitrampas-api`.

---

## 6. Grupos de log no CloudWatch

Criar à mão permite definir **por quanto tempo** os logs ficam guardados. Isso reduz custo e é **minimização de dados** (LGPD): log não deve ficar para sempre.

*CloudWatch → Log groups → Create log group*:

| Nome | Retenção | Para quê |
|---|---|---|
| `/ecs/ovitrampas-api` | 14 dias | saída (stdout) da API e do coletor |
| `/aws/ecs/application/metrics` | 14 dias | métricas que o coletor ADOT envia no formato EMF |

Em cada um: *Retention setting* **2 weeks**, *Log class* **Standard** (a classe *Infrequent Access* não extrai métricas do formato EMF) e *KMS key* vazio.

> **Os nomes não são enfeite.** O `/ecs/ovitrampas-api` está escrito na task definition (`awslogs-group`); se ele não existir, a task nem liga (`ResourceInitializationError ... log group does not exist`), porque a role de execução não tem permissão para criar grupos. O `/aws/ecs/application/metrics` está dentro da configuração padrão do coletor ADOT; se ele não existir, as métricas somem em silêncio. Criados automaticamente, os grupos ficariam com retenção "Never expire".

---

## 7. Roles do IAM

**O que é uma role:** uma "identidade" que um serviço da AWS assume para agir, recebendo **credenciais temporárias**. Nada de access key guardada (SEC11-BP07). Cada role recebe só o que precisa.

| Role | Quem assume | Para quê |
|---|---|---|
| `ovitrampas-codebuild-role` | CodeBuild | clonar o repositório e publicar a imagem no ECR |
| `ovitrampas-ecs-execution-role` | o "agente" do ECS | baixar a imagem, escrever logs, ler o segredo do banco **antes** de a API subir |
| `ovitrampas-api-task-role` | o **nosso código** rodando | enviar traces ao X-Ray e métricas ao CloudWatch |

### 7.1 Role do CodeBuild
*IAM → Roles → Create role → AWS service → CodeBuild*, **sem** políticas gerenciadas. Nome `ovitrampas-codebuild-role`. Depois, *Add permissions → Create inline policy → JSON*: cole [`deploy/aws/iam/politica-codebuild.json`](../../deploy/aws/iam/politica-codebuild.json), trocando `<CONTA>` e o ARN da conexão (o ARN você só terá no passo 8; volte aqui depois).

### 7.2 Role de execução do ECS
*Create role → AWS service → Elastic Container Service → **Elastic Container Service Task***, **sem** políticas gerenciadas. Nome `ovitrampas-ecs-execution-role`. Depois, *Add permissions → Create inline policy → JSON*: cole [`politica-execucao-segredos.json`](../../deploy/aws/iam/politica-execucao-segredos.json), trocando `<CONTA>` e os ARNs **completos** dos dois segredos (RDS e, se fez o passo 4b, Valkey; se não fez, apague essa linha e a vírgula da linha de cima). Nome da política: `ovitrampas-ecs-execution-politica`.

> Essa política faz o mesmo que a gerenciada `AmazonECSTaskExecutionRolePolicy`, só que **com cada recurso nomeado**: a gerenciada usa `"Resource": "*"` (qualquer imagem, qualquer log da conta). Aqui a role só baixa a imagem `ovitrampas-api`, só escreve no log `/ecs/ovitrampas-api` e só lê os dois segredos da API. Bom material para a comparação de menor privilégio na apresentação de Segurança.

### 7.3 Role da task da API
*Create role → Elastic Container Service Task*, **sem** políticas gerenciadas. Nome `ovitrampas-api-task-role`. Inline: [`politica-task-api-telemetria.json`](../../deploy/aws/iam/politica-task-api-telemetria.json) (troque `<CONTA>`).

> Repare: a role do **código** não consegue ler o segredo do banco (quem lê é a role de execução, que injeta a senha como variável de ambiente). Se alguém explorar uma falha na API, a role dela só consegue mandar traces. 📸 **Evidência S2:** print das três roles e das políticas.

---

## 8. Conexão com o GitHub

*Developer Tools → Settings → Connections → Create connection*: GitHub, nome `ovitrampas-github`. Na tela do GitHub, instale o **AWS Connector for GitHub** com **Only select repositories**: apenas `projeto6-ovitrampas-equipe1`.

> Quem instala precisa ser **administrador** do repositório (o dono, `joca-000`). Status final: **Available**. Anote o ARN e volte ao passo 7.1 para colocá-lo na política.

---

## 9. Pipeline, parte 1: portões e imagem

Primeiro criamos o pipeline **sem o deploy**. Motivo: o service do ECS precisa de uma imagem que já exista no ECR, e quem publica a imagem é o pipeline. Isso também evita ter que dar `docker push` do PC de alguém com uma access key.

*CodePipeline → Create pipeline → **Build custom pipeline***:

1. **Pipeline settings:** nome `ovitrampas-pipeline`, Execution mode **Queued**, **New service role**.
2. **Source:** *GitHub (via GitHub App)*, conexão `ovitrampas-github`, repositório `joca-000/projeto6-ovitrampas-equipe1`, branch **`main`**, Output artifact format **Full clone** (o gitleaks precisa do histórico).
3. **Build:** *AWS CodeBuild → Create project*:
   - Nome `ovitrampas-seguranca` · Environment: imagem gerenciada **Ubuntu, Standard, `aws/codebuild/standard:7.0`** · Service role: **Existing** → `ovitrampas-codebuild-role` (deixe marcada a opção que permite ao CodeBuild alterar a role) · Buildspec: **Use a buildspec file** → `buildspecs/seguranca.yml`.
   - *Continue to CodePipeline*.
4. **Test** e **Deploy:** *Skip* por enquanto. Crie o pipeline.

Agora *Edit* no pipeline:

- Renomeie o estágio `Build` para **`Verificacao`** e, **no mesmo estágio**, *Add action* (mesma ordem = rodam **em paralelo**): nome `Testes`, projeto novo **`ovitrampas-testes`** com as mesmas escolhas, **mais** *Additional configuration → **Privileged*** ligado (o buildspec sobe Postgres e Redis com docker), buildspec `buildspecs/testes.yml`, input `SourceArtifact`.
- Depois de `Verificacao`, *Add stage* **`Imagem`** com a ação `BuildTrivyEcr`: projeto novo **`ovitrampas-imagem`**, **Privileged** ligado, buildspec `buildspecs/imagem.yml`, variáveis de ambiente `ECR_REPOSITORY_URI` = a URI do passo 5 e `CONTAINER_NAME` = `api`. Input `SourceArtifact`, **output `ImagemArtifact`**.

*Save* → *Release change*. Esperado: tudo verde e uma imagem no ECR com a tag igual ao início do SHA do último commit da `main`.

📸 **Evidências C1 e C2:** pipeline verde e a imagem no ECR com a tag do commit (compare com o SHA no GitHub).

> Os relatórios dos testes ficam em *CodeBuild → Report groups → `ovitrampas-testes-fitness-functions`*. 📸 **C3.**

---

## 10. ECS: cluster, task definition, ALB e service

### 10.1 Cluster
*ECS → Clusters → Create cluster*: nome `ovitrampas`, infraestrutura **AWS Fargate (serverless)**. *Monitoring*: Container Insights é opcional (tem custo pequeno; ajuda na evidência de observabilidade).

### 10.2 Task definition
**O que é:** a "ficha técnica" da task: quais contêineres, quanta CPU e memória, quais variáveis, de onde vêm os segredos e para onde vão os logs. É o equivalente do bloco `api:` do `docker-compose.yml`.

Abra [`deploy/aws/taskdef-api.json`](../../deploy/aws/taskdef-api.json) num editor (**cópia local, não commite com os valores**) e troque:

| Marcador | Valor |
|---|---|
| `<CONTA>` | ID da conta (12 dígitos) |
| `<TAG-DO-PRIMEIRO-BUILD>` | a tag que apareceu no ECR no passo 9 |
| `<ENDPOINT-DO-RDS>` | passo 3 |
| `<ENDPOINT-DO-VALKEY>` | passo 4 (sem porta) |
| `<ARN-DO-SEGREDO-DO-RDS>` | passo 3 |
| `<VERSAO-ADOT>` | a tag mais recente em `gallery.ecr.aws/aws-observability/aws-otel-collector` (ex.: `v0.4x.0`). Nunca `latest`. |

*ECS → Task definitions → Create new task definition → **Create new task definition with JSON*** → cole → *Create*.

**Lendo o arquivo (o que cada parte faz):**
- Dois contêineres na mesma task: `api` (nosso código) e `adot` (o coletor OpenTelemetry da AWS). Eles compartilham a rede, então a API manda os traces para `localhost:4317` e o coletor repassa ao X-Ray. Isso se chama padrão **sidecar**.
- `secrets`: o ECS busca usuário e senha no Secrets Manager e entrega como variáveis de ambiente. A senha não aparece no console do ECS nem no JSON.
- `adot` tem `essential: false`: se o coletor cair, a API **continua** atendendo (só perde telemetria). Observabilidade não pode derrubar o sistema.
- `OTEL_PYTHON_FASTAPI_EXCLUDED_URLS=/health$`: o ALB chama `/health` a cada 30 s; sem isso, seriam ~2.900 traces inúteis por dia.
- `OTEL_PYTHON_LOG_CORRELATION=true`: cada linha de log ganha `trace_id=...`, para ir do log ao trace.

### 10.3 Target group
*EC2 → Target groups → Create target group*: tipo **IP addresses** (Fargate usa IP), nome `tg-ovitrampas-api`, protocolo HTTP, porta **8000**, VPC `ovitrampas`. Health check: path **`/health`**. Em *Advanced*: healthy threshold 2, interval 15 s. Não registre alvos (o ECS faz isso). *Create*.

### 10.4 Load balancer (ALB)
*EC2 → Load balancers → Create → Application Load Balancer*: nome `alb-ovitrampas`, **Internet-facing**, IPv4, VPC `ovitrampas`, as **duas sub-redes públicas**, security group **só** `sg-ovitrampas-alb`.
- Listener **HTTP:80**, ação padrão: **Return fixed response**, código **403**, corpo `acesso direto negado`.

Depois de criado: *Listeners → HTTP:80 → Add rule*: condição **HTTP header** `X-Origin-Verify` = **(um valor secreto aleatório)** → ação **Forward** para `tg-ovitrampas-api`. Gere o valor com `python -c "import secrets; print(secrets.token_urlsafe(32))"` e anote-o fora do Git.

> **Por quê:** o SG já só aceita IPs do CloudFront, mas *qualquer* distribuição CloudFront do mundo usa esses IPs. O cabeçalho secreto garante que só a **nossa** distribuição é atendida.

### 10.5 Service
*ECS → Clusters → ovitrampas → Services → Create*:
- Compute: **Launch type → FARGATE**, platform **LATEST**
- Task definition: `ovitrampas-api` (última revisão) · Service name `ovitrampas-api` · Desired tasks **1**
- Deployment options: **Rolling update**, min 100 % / max 200 % · *Deployment failure detection*: **Use the Amazon ECS deployment circuit breaker** + **Rollback on failure** (se a versão nova não ficar saudável, o ECS volta sozinho para a anterior)
- Networking: VPC `ovitrampas`, **sub-redes públicas**, SG **só** `sg-ovitrampas-api`, **Public IP: ligado** (sem NAT, é assim que a task baixa a imagem e envia telemetria; ninguém entra, porque o SG só aceita o ALB)
- Load balancing: **Application Load Balancer → Use an existing** `alb-ovitrampas`, contêiner `api 8000:8000`, listener existente **80:HTTP**, target group existente `tg-ovitrampas-api`. Health check grace period: 30 s.

Espere a task ficar **RUNNING** e o alvo **healthy** no target group (2 a 4 minutos).

📸 **Evidências D2 e D3:** service com 1/1 tasks rodando (aba *Deployments*) e target group com o alvo *healthy*.

---

## 11. CloudFront (HTTPS) e trava do ALB

**Por quê:** o ALB sem domínio próprio só fala HTTP. O CloudFront dá uma URL `https://xxxx.cloudfront.net` com certificado gratuito, e mais tarde servirá também o frontend React.

*CloudFront → Create distribution* (plano gratuito/pay-as-you-go, o que aparecer):
- Origin: o DNS do `alb-ovitrampas` · Protocol: **HTTP only**, porta 80
- **Add custom header:** `X-Origin-Verify` = (o mesmo valor do passo 10.4)
- Viewer protocol policy: **Redirect HTTP to HTTPS**
- Allowed HTTP methods: **GET, HEAD, OPTIONS, PUT, POST, PATCH, DELETE**
- Cache policy: **CachingDisabled** (é uma API: cada resposta é nova) · Origin request policy: **AllViewerExceptHostHeader**
- Web Application Firewall: **Do not enable** (WAF tem custo; fica como evolução)

Espere o status *Deployed* e teste:

```bash
curl https://<seu-id>.cloudfront.net/health          # {"status":"ok"}
curl https://<seu-id>.cloudfront.net/health/ready    # {"db":"ok","redis":"ok"}
curl http://<dns-do-alb>/health                      # deve FALHAR (timeout): o SG barra quem não é CloudFront
```

📸 **Evidência D4:** os três comandos e suas respostas, e a página `https://<seu-id>.cloudfront.net/docs` aberta no navegador.

---

## 12. Pipeline, parte 2: aprovação e deploy

*CodePipeline → ovitrampas-pipeline → Edit*:
1. Depois de `Imagem`, *Add stage* **`Aprovacao`** → ação **Manual approval** (opcional: um tópico SNS para avisar por e-mail).
2. *Add stage* **`Deploy`** → ação provider **Amazon ECS**: cluster `ovitrampas`, service `ovitrampas-api`, input artifact **`ImagemArtifact`**, image definitions file `imagedefinitions.json`.

**O que a ação de deploy faz:** lê o `imagedefinitions.json`, copia a task definition em uso, troca **só a imagem** do contêiner `api` (variáveis e segredos continuam), registra uma revisão nova e manda o service usá-la. O ECS sobe a task nova, espera o ALB dizer *healthy* e só então desliga a antiga (rolling update).

> Com a etapa de **aprovação manual**, o pipeline é de **entrega contínua** (alguém decide quando publicar). Sem ela, seria **implantação contínua**. Para o MVP do GEVACZ, com cliente real, a aprovação faz sentido. Registrem isso na apresentação.

Faça um PR qualquer (ex.: um ajuste no README), aprove e faça o merge. Acompanhe: Verificação → Imagem → **Aprovação (clique Review → Approve)** → Deploy.

📸 **Evidências C4 e C5:** pipeline verde de ponta a ponta e a aba *Deployments* do service com a revisão nova (imagem com a tag do commit).

---

## 13. Observabilidade

Os três pilares, e onde cada um aparece:

| Pilar | Onde ver | O que mostrar |
|---|---|---|
| **Logs** | CloudWatch → Log groups → `/ecs/ovitrampas-api` | linhas da API com `trace_id=` |
| **Traces** | CloudWatch → *X-Ray traces* → *Traces* e *Trace map* (o nome do menu muda de tempos em tempos) | um `GET /health/ready` com os spans `SELECT` e `PING` |
| **Métricas** | CloudWatch → Metrics → `AWS/ApplicationELB`, `AWS/ECS` e `ECS/AWSOTel/Application` | requisições, latência, erros 5xx, CPU/memória |

Gere tráfego antes de olhar:
```bash
for i in $(seq 1 30); do curl -s https://<seu-id>.cloudfront.net/health/ready; echo; sleep 1; done
```

### 13.1 Painel (dashboard)
*CloudWatch → Dashboards → Create dashboard* `ovitrampas`. Widgets sugeridos:
1. **RequestCount** e **HTTPCode_Target_5XX_Count** do ALB (soma, 1 min)
2. **TargetResponseTime** do ALB (p50 e p95)
3. **HealthyHostCount** do target group
4. **CPUUtilization** e **MemoryUtilization** do service (`AWS/ECS`)
5. **Disponibilidade (SLI)**, com *Math expression*: `100 * (1 - FILL(m2, 0) / m1)`, em que `m1` = RequestCount e `m2` = HTTPCode_Target_5XX_Count

**SLO proposto (para discutir com o grupo):** 99 % das requisições sem erro 5xx, numa janela de 7 dias. O widget 5 é o SLI que mede isso.

### 13.2 Alarmes
*CloudWatch → Alarms → Create alarm*, notificando um tópico SNS com o e-mail do grupo:
- `ovitrampas-api-sem-alvo-saudavel`: `HealthyHostCount` do `tg-ovitrampas-api` **< 1** por 2 períodos de 1 min
- `ovitrampas-api-erros-5xx`: `HTTPCode_Target_5XX_Count` **≥ 5** (soma) em 5 min

📸 **Evidências O1 a O4:** um trace aberto (com os spans), o *trace map*, uma linha de log com `trace_id` e o dashboard.

---

## 14. Experimentos para as evidências

Como no laboratório, vale mostrar **o pipeline barrando** problemas, não só o caminho feliz.

> **Dica de fluxo:** a `main` é protegida (merge só por PR). Para não encher a `main` de commits de teste, crie a branch `experimentos` e, **durante os experimentos**, troque a branch do estágio Source do pipeline para ela (*Edit → Source → Edit action*). No fim, volte para `main`.

| # | Experimento | O que fazer | Resultado esperado | Evidência |
|---|---|---|---|---|
| E1 | **Dependência vulnerável** | Acrescente `jinja2==3.1.2` ao `backend/requirements.txt` | Ação **Seguranca** vermelha no pip-audit (CVEs com correção) | C6: log do pip-audit |
| E2 | **Segredo commitado** | `echo 'AWS_ACCESS_KEY_ID=AKIAQ3EGRSTU7VWXYZ5A' > backend/config_local.py` (chave **falsa** do kit) | **Seguranca** vermelha no gitleaks, valor mascarado. Apagar o arquivo **não** resolve (o histórico guarda): crie um `.gitleaksignore` com o *Fingerprint* do log e a justificativa, como na Seção 10 da apostila | C7 e C8 |
| E3 | **Regressão de observabilidade** | Troque o corpo do `/health/ready` pela versão "otimizada" com threads (abaixo) | **Testes** vermelho com `o span do banco não está no mesmo trace da requisição`; **Imagem** e **Deploy** não rodam; o ECS continua na revisão anterior | C9 |
| E4 | **Rollback automático** (opcional) | No `backend/Dockerfile`, troque `--port 8000` por `--port 8001` | Testes passam (não usam o Dockerfile), o deploy roda, o ALB não acha a API, e o **circuit breaker** volta para a revisão anterior | C10: eventos do service |
| E5 | **Falha investigada pela telemetria** | Remova temporariamente a regra de entrada do `sg-ovitrampas-rds` | `/health/ready` passa a dar 503, o alarme de 5xx dispara e o log mostra `falha ao conectar no PostgreSQL` com `trace_id`. Abrindo esse trace: ~3 s de duração (o `connect_timeout`), só o span `PING` e **nenhum** `SELECT`, porque a consulta nem chegou a acontecer. Recoloque a regra | O5 |

Código do E3 (a "otimização" que quebra a observabilidade sem quebrar a funcionalidade):

```python
from concurrent.futures import ThreadPoolExecutor

def _checar_banco():
    with conectar_banco() as conn:
        conn.execute("SELECT 1")

def _checar_fila():
    conectar_fila().ping()

@app.get("/health/ready")
def ready(response: Response):
    # "Mais rápido": banco e fila em paralelo. Mas o contexto do trace
    # não passa sozinho para as threads, e os spans viram traces órfãos.
    with ThreadPoolExecutor() as executor:
        banco = executor.submit(_checar_banco)
        fila = executor.submit(_checar_fila)
        banco.result()
        fila.result()
    return {"db": "ok", "redis": "ok"}
```

Depois de cada experimento: `git revert` do commit e push.

---

## 15. Mapa de evidências x nota

| Item (pts) | Evidências | Onde explicar |
|---|---|---|
| **Deployment na nuvem (1)** | D1 VPC · D2 service rodando · D3 alvo saudável · D4 URL HTTPS funcionando e ALB inacessível direto | este roteiro |
| **Arquitetura da solução (1)** | Diagrama de implantação · C4 nível 2 atualizado · ADR-001, 006 e 007 | [`arquitetura-aws.md`](arquitetura-aws.md), `docs/adr/` |
| **Pipeline de CI/CD (2)** | C1–C5 caminho feliz · C6–C10 portões barrando · S2 roles com menor privilégio · CODEOWNERS e branch protegida | seções 9, 12 e 14 |
| **Observabilidade (1)** | O1–O4 logs, traces, métricas, dashboard · alarmes · O5 investigação de uma falha | seção 13 |

---

## 16. Custos, pausa e limpeza

**Estimativa mensal em `us-east-2`** (preços de tabela; **confiram no [AWS Pricing Calculator](https://calculator.aws/)**, porque os valores mudam):

| Recurso | Cálculo aproximado | US$/mês |
|---|---|---|
| ALB | ~US$ 0,0225/h + uso mínimo | ~18 |
| Fargate (0,5 vCPU + 1 GB, 24 h) | ~US$ 0,025/h | ~18 |
| IPs públicos IPv4 (ALB em 2 AZs + task) | 3 × US$ 0,005/h | ~11 |
| RDS db.t4g.micro + 20 GB gp3 | | ~14 |
| ElastiCache Serverless Valkey | mínimo de 100 MB | ~6 |
| Secrets Manager, ECR, CloudWatch, X-Ray, CodeBuild, CodePipeline, CloudFront | uso pequeno, boa parte no free tier | ~3–5 |
| **Total** | | **~70 (≈ US$ 2,30 por dia)** |

Contas novas (criadas depois de 15/07/2025) recebem créditos do free tier, que cobrem esse período. Verifiquem em *Billing → Credits*.

**Sugestão de calendário:** subir o ambiente em 25/11, deixar até o SR2 (05/12) e decidir com o GEVACZ se ele continua. Isso dá uns 10 dias, cerca de US$ 25.

**Pausar sem destruir** (ex.: fim de semana): service com **desired tasks = 0** (zera o Fargate) e *RDS → Actions → Stop temporarily* (a AWS religa sozinha depois de 7 dias). ALB, Valkey e IPs continuam cobrando.

**Limpeza total** (nesta ordem): service ECS (desired 0, depois *Delete*) → cluster → ALB → target group → distribuição CloudFront (*Disable*, esperar, *Delete*) → pipeline e os 3 projetos CodeBuild → bucket `codepipeline-us-east-2-...` (esvaziar e excluir) → ECR → Valkey → RDS (sem snapshot final, se não precisar dos dados) → segredos → log groups → roles → conexão GitHub (e desinstalar o app no GitHub) → VPC. Confiram o Budgets nos dias seguintes.

---

## 17. Problemas comuns

| Sintoma | Causa provável |
|---|---|
| Build falha logo no clone | role do CodeBuild sem `codeconnections:UseConnection` (passo 7.1), ou conexão não está *Available* |
| `Cannot connect to the Docker daemon` no build | projeto CodeBuild sem **Privileged** |
| Task para com `ResourceInitializationError ... secrets` | role de execução sem permissão no ARN **exato** do segredo, ou ARN com `:username::` errado |
| Task para com `CannotPullContainerError ... not authorized` | regra `BaixarSoAImagemDaApi` com região, conta ou nome do repositório errado |
| Task para com erro de log group | grupo `/ecs/ovitrampas-api` não existe nessa região, ou ARN errado na regra `EscreverSoNoLogDaApi` |
| Task para com `CannotPullContainerError` | task sem IP público (sub-rede pública + *Public IP* ligado) |
| `/health` funciona mas `/health/ready` dá 503 | olhe o log: SG do RDS/Valkey sem a regra vinda do `sg-ovitrampas-api`; Valkey sem a porta 6380; `REDIS_TLS` faltando |
| CloudFront devolve 403 `acesso direto negado` | cabeçalho `X-Origin-Verify` diferente entre CloudFront e regra do ALB |
| CloudFront devolve 504 | SG do ALB sem o prefix list do CloudFront |
| Deploy não estabiliza | nome do contêiner diferente de `api` no `CONTAINER_NAME`; veja os eventos do service e os logs da task |
| Nenhum trace no X-Ray | role da task sem as permissões do X-Ray; veja o log do contêiner `adot` (stream `adot/...`) |

---

## 18. Próximos passos (worker, frontend, banco)

- **Migrações do banco:** quando o modelo de dados (T7) existir, usar **Alembic**. As migrações rodam como uma task avulsa do ECS (mesma imagem, comando `alembic upgrade head`) **antes** do deploy, ou como mais uma ação no pipeline.
- **Worker de ML:** pasta `worker/` com Dockerfile próprio, repositório ECR `ovitrampas-worker`, task definition **sem** ALB (1 vCPU / 2 GB bastam: a inferência do YOLO na CPU levou menos de 1 s por foto, ver `docs/evidencias/ml/tempos-inferencia.md`), SG próprio liberado no RDS e no Valkey. No pipeline: o `imagem.yml` passa a publicar as duas imagens e gera dois arquivos (`imagedefinitions-api.json` e `imagedefinitions-worker.json`), com uma ação de deploy por service.
- **Trace atravessando a fila:** a API deve colocar o `traceparent` do OpenTelemetry dentro da mensagem e o worker deve continuar o trace a partir dele, com uma fitness function igual à de `tests/test_fitness_observabilidade.py` protegendo isso.
- **Frontend React:** `npm run build` → bucket S3 privado → segunda origem no mesmo CloudFront (comportamento padrão `/*` para o S3, `/api/*` e `/health*` para o ALB). Uma quarta ação no pipeline publica no S3 e invalida o cache.
- **LGPD:** antes de guardar contato de morador, avaliar a região `sa-east-1` (São Paulo) e incluir a fitness function de "telefone/e-mail nunca em spans e logs".
