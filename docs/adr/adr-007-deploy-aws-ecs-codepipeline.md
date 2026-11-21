# ADR-007: Implantação na AWS com ECS Fargate, CodePipeline e OpenTelemetry

- **Status:** Proposta (21/11/2026). Vira "Aceita" quando o grupo revisar o PR.
- **Decisores:** Grupo 1 — Projeto 6
- **Relação com outras ADRs:** implanta a arquitetura da ADR-001 (API + worker + fila) com as fotos no PostgreSQL (ADR-006).

## Contexto

A AV2 cobra deployment na nuvem, arquitetura da solução, pipeline de CI/CD (peso 2) e estrutura de observabilidade. A disciplina de Arquitetura Nativa em Nuvem ensinou a stack da AWS nos laboratórios (ECS Fargate, ECR, ALB, CloudFront, CodePipeline/CodeBuild, OpenTelemetry). O grupo tem uma conta AWS própria, sem ambiente criado. Faltam duas semanas até o SR2 (05/12/2026). Hoje a `main` tem só a API com `/health` e `/health/ready`, mais PostgreSQL e Redis.

## Decisão

1. **Provedor e região:** AWS, `us-east-1`, a mesma dos laboratórios.
2. **Execução:** ECS **Fargate**, com um service para a API atrás de um **ALB**, e **CloudFront** na frente para ter HTTPS sem domínio próprio. O worker de ML entra depois como um segundo service, sem ALB.
3. **Dados:** **RDS PostgreSQL** db.t4g.micro Single-AZ, com a senha gerenciada no **Secrets Manager**. A fila é o **ElastiCache Serverless (Valkey)**, compatível com o protocolo do Redis e com TLS obrigatório.
4. **Rede sem NAT Gateway:** as tasks ficam em sub-redes **públicas** com IP público, mas o security group só aceita o ALB. Banco e fila ficam em sub-redes **privadas**.
5. **CI/CD:** **CodePipeline + CodeBuild**, com portões nesta ordem: Segurança (gitleaks, pip-audit, Bandit) ‖ Testes (incluindo as fitness functions de observabilidade e de segurança) → Imagem (Trivy, push no ECR com tag = SHA) → Aprovação manual → Deploy no ECS (rolling update com circuit breaker e rollback).
6. **Observabilidade:** instrumentação automática do OpenTelemetry (`opentelemetry-instrument`). Um coletor **ADOT** roda como sidecar na task e envia traces ao **X-Ray** e métricas ao **CloudWatch**. Os logs vão ao CloudWatch Logs, com `trace_id` em cada linha e retenção de 14 dias.
7. **Infraestrutura criada pelo console**, seguindo um roteiro versionado (`docs/deploy/roteiro-aws.md`), como nos laboratórios.

## Alternativas consideradas

| Alternativa | Por que não agora |
|---|---|
| GitHub Actions no lugar do CodePipeline | Grátis e popular, mas foge da stack avaliada na disciplina; exigiria federação OIDC para não usar access keys |
| Render / Railway / Fly.io | Mais simples, mas sem ECS/ALB/CodePipeline para demonstrar; menos controle de rede e segurança |
| NAT Gateway + tasks em sub-redes privadas | Mais isolamento, mas cerca de US$ 32/mês a mais. O SG que só aceita o ALB dá proteção equivalente para a entrada |
| Aurora Serverless (como no lab) | Mais caro que um db.t4g.micro para o volume do MVP |
| Redis como contêiner no ECS | Sem disco persistente no Fargate: a fila se perderia a cada reinício; é mais uma peça para proteger |
| Terraform (infraestrutura como código) | Reproduzível, mas a curva de aprendizado não cabe no prazo. Fica como evolução |
| `sa-east-1` (São Paulo) | Dados no Brasil ajudam na LGPD, mas custam mais caro. No MVP ainda não há dado pessoal de morador |

## Consequências

**Positivas**
- Cada merge na `main` passa pelos mesmos portões e chega ao ar sem passo manual além da aprovação. Não há access key em lugar nenhum.
- As fitness functions transformam Segurança e Observabilidade (características da AV1) em verificações automáticas.
- A tag da imagem igual ao SHA do commit permite saber exatamente que código está no ar.
- O worker, o frontend e as migrações se encaixam no mesmo pipeline sem redesenho.

**Negativas / custos assumidos**
- Custo estimado de **cerca de US$ 70/mês** (ALB, Fargate e IPs públicos são a maior parte). Mitigação: alerta no Budgets, pausa fora de uso e limpeza depois do SR2, se o GEVACZ não assumir.
- Banco Single-AZ: uma falha da zona derruba o sistema até a restauração (aceitável para o MVP).
- A rotação automática da senha do RDS exige um novo deploy da API depois de cada rotação.
- Região fora do Brasil: rever antes de guardar contato de moradores (LGPD).
- Infraestrutura criada à mão pode divergir do roteiro. Mitigação: prints de cada passo e revisão em dupla.

**Revisitar quando:** o worker e o frontend entrarem; o GEVACZ decidir hospedar o sistema; houver dado pessoal real; o custo passar do orçamento.
