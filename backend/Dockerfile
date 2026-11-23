# Receita para construir a imagem da API.
# Cada linha cria uma "camada"; o Docker reaproveita camadas que não mudaram.

# Imagem base vinda do ECR Public (espelho oficial do Docker Hub mantido pela
# AWS): o CodeBuild não esbarra no limite de downloads anônimos do Docker Hub.
FROM public.ecr.aws/docker/library/python:3.12-slim

# PYTHONUNBUFFERED=1: o print/log sai na hora (senão o CloudWatch recebe atrasado).
# PYTHONDONTWRITEBYTECODE=1: não cria arquivos .pyc dentro do contêiner.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copia SÓ o requirements primeiro: se o código mudar mas as dependências
# não, o Docker pula a instalação (build bem mais rápido).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Roda como usuário comum, não como root: se alguém explorar uma falha na
# API, ele não ganha poder total dentro do contêiner. O UID numérico (10001)
# permite ao ECS/Kubernetes conferir que não é root.
RUN useradd --system --uid 10001 --no-create-home appuser
USER 10001

EXPOSE 8000

# "opentelemetry-instrument" é um "embrulho": ele liga a instrumentação
# (FastAPI, psycopg, redis, logging) e DEPOIS chama o uvicorn. Para onde os
# traces vão é decidido pelas variáveis OTEL_* (no compose: lugar nenhum;
# na AWS: o coletor ADOT que roda ao lado, na mesma task).
# 0.0.0.0 = aceita conexões de fora do contêiner (o ALB precisa alcançá-lo).
CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
