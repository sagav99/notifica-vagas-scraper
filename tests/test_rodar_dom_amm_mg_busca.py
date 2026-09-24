from datetime import date

import rodar_dom_amm_mg_busca as script
from notifica_vagas_scraper.fontes.sigpub_busca import ResultadoBusca


def _entidades_fake(n):
    return [
        script.dom_amm_mg.EntidadeAmmMg(codigo_ibge=i, nome=f"Município {i}", uf="MG", entidade_id=str(i))
        for i in range(n)
    ]


def test_lote_do_dia_e_deterministico():
    entidades = _entidades_fake(161)
    hoje = date(2026, 9, 1)
    lote1 = script.selecionar_lote_do_dia(entidades, hoje=hoje)
    lote2 = script.selecionar_lote_do_dia(entidades, hoje=hoje)
    assert lote1 == lote2


def test_lote_do_dia_respeita_batch_size():
    entidades = _entidades_fake(161)
    lote = script.selecionar_lote_do_dia(entidades, hoje=date(2026, 9, 1))
    assert 0 < len(lote) <= script.BATCH_SIZE


def test_cobertura_completa_ao_longo_dos_dias():
    # Rodando em dias diferentes o suficiente, toda entidade deve aparecer
    # em pelo menos um lote — cobertura completa ao longo do ciclo de
    # rotação, não só um subconjunto fixo esquecido pra sempre.
    entidades = _entidades_fake(161)
    vistos = set()
    for dia in range(1, 30):
        lote = script.selecionar_lote_do_dia(entidades, hoje=date(2026, 1, 1).replace(day=1) if dia > 28 else date(2026, 1, dia))
        vistos.update(e.codigo_ibge for e in lote)
    assert vistos == {e.codigo_ibge for e in entidades}


def test_lote_nunca_ultrapassa_o_total():
    entidades = _entidades_fake(10)
    for dia in range(1, 15):
        lote = script.selecionar_lote_do_dia(entidades, hoje=date(2026, 1, dia))
        assert len(lote) <= len(entidades)
        assert len(lote) > 0


def _resultado_fake(codigo: str) -> ResultadoBusca:
    return ResultadoBusca(
        entidade="X", titulo="Y", orgao="Z", data_circulacao=None,
        codigo=codigo, url_load=f"https://exemplo/materia/{codigo}",
    )


class _RespostaFalsa:
    text = "<html></html>"
    url = "https://exemplo/materia/final"

    def raise_for_status(self):
        pass


def test_verificar_canario_tenta_todos_resultados_ate_achar_vaga(monkeypatch):
    """Achado real 2026-09-24: o resultado mais recente (posição 0) pode
    ser legitimamente uma retificação sem tabela de cargo (0 vagas
    correto) — o canário não pode declarar falha só por causa disso
    quando outro resultado da mesma busca tem vaga de verdade."""
    resultados = [_resultado_fake("AAA"), _resultado_fake("BBB")]
    monkeypatch.setattr(script.sigpub_busca, "buscar", lambda *a, **k: "<html></html>")
    monkeypatch.setattr(script.sigpub_busca, "parsear_resultados", lambda html: resultados)
    monkeypatch.setattr(script.sigpub_busca, "resolver_url_materia", lambda sessao, url: url)
    monkeypatch.setattr(script.requests, "get", lambda *a, **k: _RespostaFalsa())

    # 1º resultado (AAA) não tem vaga, 2º (BBB) tem — canário deve
    # continuar tentando em vez de parar no primeiro.
    monkeypatch.setattr(
        script.dom_amm_mg, "parsear_materia",
        lambda texto, url: [] if "AAA" in url else [object()],
    )

    assert script.verificar_canario() is True


def test_verificar_canario_falha_quando_nenhum_resultado_tem_vaga(monkeypatch):
    resultados = [_resultado_fake("AAA"), _resultado_fake("BBB")]
    monkeypatch.setattr(script.sigpub_busca, "buscar", lambda *a, **k: "<html></html>")
    monkeypatch.setattr(script.sigpub_busca, "parsear_resultados", lambda html: resultados)
    monkeypatch.setattr(script.sigpub_busca, "resolver_url_materia", lambda sessao, url: url)
    monkeypatch.setattr(script.requests, "get", lambda *a, **k: _RespostaFalsa())
    monkeypatch.setattr(script.dom_amm_mg, "parsear_materia", lambda texto, url: [])

    assert script.verificar_canario() is False


def test_verificar_canario_falha_sem_nenhum_resultado(monkeypatch):
    monkeypatch.setattr(script.sigpub_busca, "buscar", lambda *a, **k: "<html></html>")
    monkeypatch.setattr(script.sigpub_busca, "parsear_resultados", lambda html: [])

    assert script.verificar_canario() is False
