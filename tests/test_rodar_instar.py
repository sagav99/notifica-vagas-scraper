import rodar_instar as script


def test_tem_sinal_saude_detecta_medico_no_titulo_ou_descricao():
    assert script._tem_sinal_saude([{"titulo": "PROCESSO SELETIVO 05/2026 (MÉDICO)", "descricao": ""}])
    assert script._tem_sinal_saude(
        [{"titulo": "Processo Seletivo", "descricao": "contratação temporária de médico pediatra"}]
    )


def test_tem_sinal_saude_devolve_false_sem_palavra_de_saude():
    assert not script._tem_sinal_saude([{"titulo": "Motorista Categoria D", "descricao": "vaga de motorista"}])


def test_tem_sinal_saude_lista_vazia_devolve_false():
    assert not script._tem_sinal_saude([])
