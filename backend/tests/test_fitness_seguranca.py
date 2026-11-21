"""Fitness functions de SEGURANÇA no comportamento da API.

Complementam as ferramentas do pipeline (gitleaks, pip-audit, Bandit, Trivy),
que olham o código e a imagem. Estas olham o que a API REVELA quando roda.

1. Erro não vaza detalhe: se o banco cair, quem chamou recebe só "erro".
   Host, porta, usuário e mensagem da exceção vão para o log (que só o grupo
   lê no CloudWatch), nunca para a resposta HTTP.
2. Telemetria também é dado: nenhum atributo de span pode conter senha.
   Os traces vão para o X-Ray; se uma senha aparecesse lá, qualquer pessoa
   com acesso ao console de observabilidade passaria a conhecê-la.

Quando o módulo de Notificação existir (contato do morador = dado pessoal,
LGPD), acrescente aqui um teste igual ao 2 garantindo que telefone e e-mail
nunca aparecem em spans nem em logs.
"""
import logging
import os


def test_erro_no_banco_nao_vaza_detalhes(cliente, monkeypatch, caplog):
    # Porta 1: ninguém escuta nela, então a conexão é recusada na hora.
    monkeypatch.setenv("POSTGRES_PORT", "1")

    with caplog.at_level(logging.ERROR, logger="api"):
        resposta = cliente.get("/health/ready")

    assert resposta.status_code == 503
    # A resposta tem que ser EXATAMENTE isto, nada a mais.
    assert resposta.json() == {"db": "erro", "redis": "ok"}

    corpo = resposta.text.lower()
    for proibido in (os.environ["POSTGRES_HOST"], os.environ["POSTGRES_USER"],
                     "connection", "traceback", "psycopg"):
        assert proibido.lower() not in corpo, f"a resposta revela '{proibido}'"

    # O detalhe NÃO sumiu: foi para o log, que é onde deve estar.
    assert "falha ao conectar no PostgreSQL" in caplog.text


def test_spans_nao_contem_senhas(cliente, spans):
    cliente.get("/health/ready")

    senhas = [os.environ["POSTGRES_PASSWORD"], os.environ.get("REDIS_PASSWORD", "")]
    senhas = [s for s in senhas if s]

    for span in spans():
        for chave, valor in span.attributes.items():
            for senha in senhas:
                assert senha not in str(valor), (
                    f"o span '{span.name}' expõe uma senha no atributo '{chave}'"
                )
