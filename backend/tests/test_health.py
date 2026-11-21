"""Testes funcionais: a API responde o que promete."""


def test_health_responde_ok(cliente):
    resposta = cliente.get("/health")
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok"}


def test_ready_com_banco_e_fila_no_ar(cliente):
    resposta = cliente.get("/health/ready")
    assert resposta.status_code == 200
    assert resposta.json() == {"db": "ok", "redis": "ok"}
