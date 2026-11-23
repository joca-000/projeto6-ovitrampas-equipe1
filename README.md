# projeto6-ovitrampas-equipe1
Sistema para automatização da leitura, coleta, análise e visualização de dados das ovitrampas utilizadas no monitoramento do Aedes aegypti no Recife. Projeto desenvolvido pela Equipe 1 na disciplina Projeto 6 — Sistemas de Informação.

## Rodar no seu computador
Veja [`docs/ambiente-local.md`](docs/ambiente-local.md) (`docker compose up -d`).

## Testes e fitness functions
Precisam de um PostgreSQL e um Redis no ar (o `docker compose up -d db redis` serve) e das variáveis do `.env`:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
# Se o seu .env tiver outras senhas, exporte POSTGRES_PASSWORD e REDIS_PASSWORD antes.
python -m pytest -v
```

| Arquivo | O que garante |
|---|---|
| `tests/test_health.py` | a API responde `/health` e `/health/ready` |
| `tests/test_fitness_observabilidade.py` | um pedido gera spans do HTTP, do banco e da fila **no mesmo trace** |
| `tests/test_fitness_seguranca.py` | erros não vazam detalhes e nenhum span contém senha |

## Deploy na AWS (CI/CD)
- Roteiro passo a passo: [`docs/deploy/roteiro-aws.md`](docs/deploy/roteiro-aws.md)
- Arquitetura de implantação: [`docs/deploy/arquitetura-aws.md`](docs/deploy/arquitetura-aws.md)
- Decisão: [`docs/adr/adr-007-deploy-aws-ecs-codepipeline.md`](docs/adr/adr-007-deploy-aws-ecs-codepipeline.md)
- Pipeline: `buildspecs/` (Segurança, Testes, Imagem) · Task definition e políticas IAM: `deploy/aws/`
- .
