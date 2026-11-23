"""Fitness function de OBSERVABILIDADE.

Característica protegida: "quando a API fica lenta ou falha, conseguimos ver
em qual peça (HTTP, banco ou fila) o tempo foi gasto".

Isso só é possível se um pedido gerar UM trace com os spans das três peças.
O teste faz um GET /health/ready e confere:
  1. existe o span do servidor HTTP;
  2. existe o span da consulta ao PostgreSQL;
  3. existe o span do comando na fila (Redis/Valkey);
  4. os três têm o MESMO trace_id (estão "costurados" no mesmo trace).

Regressão que ele pega (vocês vão provocá-la no roteiro, passo 16):
alguém "otimiza" o /health/ready para checar banco e fila em paralelo, com
threads. Tudo continua funcionando, mas o contexto do trace NÃO passa
sozinho para a thread nova: os spans do banco e da fila caem em traces
separados, e a investigação de lentidão fica cega. O teste falha e o
pipeline não faz o deploy.

Quando o worker existir, esta mesma ideia vale para a fila: a API precisa
colocar o "traceparent" dentro da mensagem, senão o trace quebra entre a
API e o worker. Um teste parecido com este deve proteger isso.
"""
from opentelemetry.trace import SpanKind


def _sistema(span):
    return span.attributes.get("db.system")


def test_ready_gera_spans_http_banco_e_fila_no_mesmo_trace(cliente, spans):
    resposta = cliente.get("/health/ready")
    assert resposta.status_code == 200

    gerados = spans()
    nomes = [s.name for s in gerados]

    http = [s for s in gerados if s.kind == SpanKind.SERVER]
    banco = [s for s in gerados if _sistema(s) == "postgresql"]
    fila = [s for s in gerados if _sistema(s) == "redis"]

    assert http, f"nenhum span HTTP foi gerado: {nomes}"
    assert banco, f"nenhum span do PostgreSQL foi gerado: {nomes}"
    assert fila, f"nenhum span da fila (Redis) foi gerado: {nomes}"

    trace_http = http[0].context.trace_id
    assert banco[0].context.trace_id == trace_http, (
        "o span do banco não está no mesmo trace da requisição"
    )
    assert fila[0].context.trace_id == trace_http, (
        "o span da fila não está no mesmo trace da requisição"
    )
