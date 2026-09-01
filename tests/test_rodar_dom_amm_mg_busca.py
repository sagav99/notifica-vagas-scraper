from datetime import date

import rodar_dom_amm_mg_busca as script


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
