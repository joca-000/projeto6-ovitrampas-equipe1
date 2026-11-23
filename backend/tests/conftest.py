"""Preparação comum a todos os testes (o pytest lê este arquivo primeiro).

O que acontece aqui, na ordem:
1. Variáveis de ambiente de teste: apontam para um Postgres e um Redis
   DESCARTÁVEIS (no pipeline, o CodeBuild sobe os dois em contêineres).
2. OpenTelemetry com um exportador "em memória": em vez de mandar os spans
   para um coletor na rede, ele guarda numa lista do Python, para o teste
   poder olhar o que foi gerado.
3. Instrumentação do psycopg e do redis ANTES de importar a API, como faz o
   comando opentelemetry-instrument em produção.
"""
import os

import pytest

# 1. Valores padrão para rodar no PC. Se a variável já existir (pipeline),
#    o setdefault não mexe nela.
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_USER", "ovitrampas")
os.environ.setdefault("POSTGRES_PASSWORD", "senha-descartavel-do-ci")
os.environ.setdefault("POSTGRES_DB", "ovitrampas")
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("REDIS_PASSWORD", "senha-descartavel-do-ci")

# 2. Spans ficam em memória, sem coletor nem X-Ray.
from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

_exportador = InMemorySpanExporter()
_provedor = TracerProvider()
_provedor.add_span_processor(SimpleSpanProcessor(_exportador))
trace.set_tracer_provider(_provedor)

# 3. Instrumentar antes de importar a aplicação.
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: E402
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor  # noqa: E402
from opentelemetry.instrumentation.redis import RedisInstrumentor  # noqa: E402

PsycopgInstrumentor().instrument(enable_commenter=False)
RedisInstrumentor().instrument()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

FastAPIInstrumentor.instrument_app(app)


@pytest.fixture
def cliente():
    """Um 'navegador de mentira' que faz pedidos HTTP direto na API, sem rede."""
    return TestClient(app)


@pytest.fixture
def spans():
    """Limpa a lista de spans antes do teste e devolve uma função para lê-la."""
    _exportador.clear()
    return _exportador.get_finished_spans
