"""API do Projeto 6 (Ovitrampas / GEVACZ) - versão mínima.

Por enquanto só responde "estou vivo" (/health) e "meus vizinhos estão vivos"
(/health/ready). Os módulos do monólito (acesso, registro, contagens,
indicadores, notificação) entram aqui depois, cada um na sua pasta.

O mesmo código roda em dois lugares, e só as VARIÁVEIS DE AMBIENTE mudam:
- no seu PC (docker compose): Postgres e Redis em contêineres, sem TLS;
- na AWS (ECS): RDS e ElastiCache (Valkey), com conexões criptografadas (TLS).

Observabilidade: este arquivo NÃO importa nada do OpenTelemetry. Quem gera os
traces é o comando "opentelemetry-instrument" (ver Dockerfile), que "embrulha"
o FastAPI, o psycopg e o redis antes de o código abaixo rodar. Por isso cada
pedido HTTP vira um trace com um span do HTTP, um do SELECT e um do PING.
"""
import logging
import os

import psycopg  # conversa com o PostgreSQL
import redis    # conversa com o Redis (e com o Valkey da AWS, que fala o mesmo protocolo)
from fastapi import FastAPI, Response

log = logging.getLogger("api")
logging.basicConfig(level=logging.INFO)

# O objeto "app" é o programa inteiro. O uvicorn recebe os pedidos HTTP da
# rede e entrega cada um para ele.
app = FastAPI(title="Ovitrampas API")


def _env_bool(nome: str, padrao: bool = False) -> bool:
    """Lê uma variável de ambiente como verdadeiro/falso ("true", "1", "sim")."""
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in ("1", "true", "sim", "yes")


def conectar_banco() -> psycopg.Connection:
    """Abre uma conexão com o PostgreSQL usando só variáveis de ambiente.

    POSTGRES_SSLMODE:
    - "prefer" (padrão): tenta TLS e aceita sem TLS. Serve para o compose local.
    - "require": exige TLS. É o que usamos no RDS, para a senha e os dados
      não trafegarem abertos dentro da VPC.
    """
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
        sslmode=os.environ.get("POSTGRES_SSLMODE", "prefer"),
        connect_timeout=3,
    )


def conectar_fila() -> redis.Redis:
    """Cria o cliente da fila (Redis local ou ElastiCache Valkey na AWS).

    REDIS_TLS=true liga a criptografia, obrigatória no ElastiCache Serverless.
    REDIS_USERNAME e REDIS_PASSWORD são opcionais: vazios = sem autenticação
    (na AWS, nesse caso, quem protege a fila é o security group).
    """
    return redis.Redis(
        host=os.environ["REDIS_HOST"],
        port=int(os.environ.get("REDIS_PORT", "6379")),
        username=os.environ.get("REDIS_USERNAME") or None,
        password=os.environ.get("REDIS_PASSWORD") or None,
        ssl=_env_bool("REDIS_TLS"),
        socket_connect_timeout=3,
        socket_timeout=3,
    )


@app.get("/health")
def health():
    """Liveness: 'o processo da API está de pé?'. Não consulta ninguém.

    É esta rota que o load balancer (ALB) da AWS chama a cada 30 segundos para
    decidir se manda tráfego para a task.
    """
    # O FastAPI converte este dicionário Python em JSON: {"status":"ok"}
    return {"status": "ok"}


@app.get("/health/ready")
def ready(response: Response):
    """Readiness: 'a API consegue falar com o banco e com a fila?'."""
    checks = {}

    try:
        with conectar_banco() as conn:
            conn.execute("SELECT 1")  # pergunta mais simples possível
        checks["db"] = "ok"
    except Exception:
        # O detalhe do erro vai para o log, não para quem chamou
        # (mensagens de erro podem revelar host, usuário, etc.).
        # Há um teste (fitness function de segurança) que garante isso.
        log.exception("falha ao conectar no PostgreSQL")
        checks["db"] = "erro"

    try:
        conectar_fila().ping()  # o Redis responde PONG
        checks["redis"] = "ok"
    except Exception:
        log.exception("falha ao conectar no Redis")
        checks["redis"] = "erro"

    if "erro" in checks.values():
        response.status_code = 503  # "serviço indisponível"
    return checks
