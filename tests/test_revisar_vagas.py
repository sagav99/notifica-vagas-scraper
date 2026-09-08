from datetime import date
from unittest.mock import Mock

import revisar_vagas
from notifica_vagas_scraper import revisao_ia


def _vaga(inscricoes_inicio=None, inscricoes_fim=None):
    return {"inscricoes_inicio": inscricoes_inicio, "inscricoes_fim": inscricoes_fim}


def test_cronologia_valida():
    resultado = revisar_vagas.checar_cronologia_inscricoes(
        _vaga(date(2026, 1, 28), date(2026, 2, 3))
    )
    assert resultado.startswith("Válido")


def test_cronologia_invalida():
    # Bug real encontrado em produção (2026-09-01): Gemini afirmou que
    # 28/01 é posterior a 03/02, o que é falso — a checagem em Python tem
    # que acertar o caso oposto de verdade (fim antes do início).
    resultado = revisar_vagas.checar_cronologia_inscricoes(
        _vaga(date(2026, 2, 3), date(2026, 1, 28))
    )
    assert resultado.startswith("INVÁLIDO")


def test_cronologia_datas_iguais_e_valida():
    resultado = revisar_vagas.checar_cronologia_inscricoes(
        _vaga(date(2026, 2, 3), date(2026, 2, 3))
    )
    assert resultado.startswith("Válido")


def test_cronologia_sem_data_nao_afirma_nada():
    resultado = revisar_vagas.checar_cronologia_inscricoes(_vaga(None, None))
    assert resultado.startswith("Sem data")


def test_montar_dados_inclui_checagem():
    vaga = {
        "municipio_nome": "Cidade",
        "municipio_uf": "MG",
        "orgao": "Prefeitura",
        "cargo": "Médico",
        "salario": None,
        "numero_edital": "01/2026",
        "data_publicacao": date(2026, 1, 1),
        "inscricoes_inicio": date(2026, 1, 28),
        "inscricoes_fim": date(2026, 2, 3),
        "status": "aberta",
        "resumo": "resumo",
        "evidencias": [],
    }
    dados = revisar_vagas.montar_dados_para_revisao(vaga)
    assert dados["checagem_cronologica_pre_computada"].startswith("Válido")


def _vaga_minima(cargo: str) -> dict:
    return {
        "id": cargo, "municipio_nome": "Cidade", "municipio_uf": "MG", "orgao": "Prefeitura",
        "cargo": cargo, "salario": None, "numero_edital": None, "data_publicacao": None,
        "inscricoes_inicio": None, "inscricoes_fim": None, "status": "aberta", "resumo": None,
        "evidencias": [],
    }


def test_main_para_no_429_sem_rejeitar_as_restantes(monkeypatch):
    """Achado em produção, 2026-09-08: sem parar aqui, cada vaga seguinte
    também batia 429 e virava "rejeitada" sem revisão real nenhuma."""
    vagas = [_vaga_minima("Enfermeiro"), _vaga_minima("Médico Cardiologista"), _vaga_minima("Dentista")]
    monkeypatch.setattr(revisar_vagas.db, "conectar", lambda: Mock())

    def _decidir(dados):
        if dados["cargo"] == "Médico Cardiologista":
            raise revisao_ia.CotaGeminiEsgotadaError("429")
        return {"decisao": "aprovada", "motivo": "ok"}

    monkeypatch.setattr(revisar_vagas.db, "listar_vagas_pendentes", lambda conn: vagas)
    monkeypatch.setattr(revisar_vagas.revisao_ia, "decidir_revisao", _decidir)
    aplicadas = []
    monkeypatch.setattr(revisar_vagas.db, "aplicar_revisao", lambda conn, **kw: aplicadas.append(kw["vaga_id"]))

    revisar_vagas.main()

    assert aplicadas == ["Enfermeiro"]
