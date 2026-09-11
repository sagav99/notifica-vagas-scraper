from unittest.mock import Mock

import verificar_consistencia_revisao as script
from notifica_vagas_scraper import revisao_ia


def _vaga(id_, decisao, *, fonte_id="fonte-x", municipio_id=1, numero_edital="01/2026", cargo="Médico"):
    return {
        "id": id_, "cargo": cargo, "revisao_status": decisao,
        "fonte_id": fonte_id, "municipio_id": municipio_id, "numero_edital": numero_edital,
        "municipio_nome": "Bariri", "municipio_uf": "SP", "orgao": "Prefeitura",
        "salario": None, "salario_tipo": None, "data_publicacao": None,
        "inscricoes_inicio": None, "inscricoes_fim": None, "status": "aberta",
        "resumo": None, "evidencias": [],
    }


def test_main_corrige_vaga_que_diverge_quando_gemini_muda_de_ideia(monkeypatch):
    # achado real: Bariri/SP, 2 de 17 cargos médicos idênticos marcados
    # "incompleta" — 2ª passada com contexto de irmãs deve corrigir pra
    # "aprovada" quando o Gemini reconsidera.
    vagas = [_vaga(f"v{i}", "aprovada") for i in range(15)] + [_vaga("v-divergente", "incompleta")]
    monkeypatch.setattr(script.db, "conectar", lambda: Mock())
    monkeypatch.setattr(script.db, "listar_vagas_revisadas_para_consistencia", lambda conn: vagas)

    prompts_recebidos = []

    def _decidir(dados, *, contexto_irmas=None, **kw):
        prompts_recebidos.append(contexto_irmas)
        return {"decisao": "aprovada", "motivo": "consenso confirma, sem inconsistência real"}

    monkeypatch.setattr(script.revisao_ia, "decidir_revisao", _decidir)
    aplicadas = []
    monkeypatch.setattr(script.db, "aplicar_revisao", lambda conn, **kw: aplicadas.append(kw))

    script.main()

    assert len(aplicadas) == 1
    assert aplicadas[0]["vaga_id"] == "v-divergente"
    assert aplicadas[0]["decisao"] == "aprovada"
    assert "2ª passada" in aplicadas[0]["motivo"]
    assert len(prompts_recebidos) == 1
    assert prompts_recebidos[0] is not None and "01/2026" in prompts_recebidos[0]


def test_main_nao_grava_nada_quando_gemini_confirma_decisao_original(monkeypatch):
    vagas = [_vaga(f"v{i}", "aprovada") for i in range(15)] + [_vaga("v-divergente", "incompleta")]
    monkeypatch.setattr(script.db, "conectar", lambda: Mock())
    monkeypatch.setattr(script.db, "listar_vagas_revisadas_para_consistencia", lambda conn: vagas)
    monkeypatch.setattr(
        script.revisao_ia, "decidir_revisao",
        lambda dados, **kw: {"decisao": "incompleta", "motivo": "problema real confirmado mesmo com o contexto"},
    )
    aplicadas = []
    monkeypatch.setattr(script.db, "aplicar_revisao", lambda conn, **kw: aplicadas.append(kw))

    script.main()

    assert aplicadas == []


def test_main_para_no_429_sem_perder_as_restantes(monkeypatch):
    vagas = [_vaga(f"v{i}", "aprovada") for i in range(15)] + [
        _vaga("v-divergente-1", "incompleta"),
        _vaga("v-divergente-2", "rejeitada", numero_edital="02/2026"),
    ] + [_vaga(f"w{i}", "aprovada", numero_edital="02/2026") for i in range(14)]
    monkeypatch.setattr(script.db, "conectar", lambda: Mock())
    monkeypatch.setattr(script.db, "listar_vagas_revisadas_para_consistencia", lambda conn: vagas)

    chamadas = {"n": 0}

    def _decidir(dados, **kw):
        chamadas["n"] += 1
        raise revisao_ia.CotaGeminiEsgotadaError("429")

    monkeypatch.setattr(script.revisao_ia, "decidir_revisao", _decidir)
    monkeypatch.setattr(script.db, "aplicar_revisao", lambda conn, **kw: None)

    script.main()

    assert chamadas["n"] == 1


def test_main_nao_aplica_rejeicao_por_falha_tecnica_da_2a_chamada(monkeypatch):
    # achado da auditoria de segurança (2026-09-11): decidir_revisao
    # devolve "rejeitada" tanto quando o Gemini decide isso de verdade
    # quanto quando a chamada falha (motivo com prefixo "[revisão
    # automática]") — sem distinguir os dois casos, uma vaga já
    # "aprovada" seria derrubada por uma falha técnica na 2ª passada, não
    # por reavaliação real.
    vagas = [_vaga(f"v{i}", "aprovada") for i in range(15)] + [_vaga("v-divergente", "incompleta")]
    monkeypatch.setattr(script.db, "conectar", lambda: Mock())
    monkeypatch.setattr(script.db, "listar_vagas_revisadas_para_consistencia", lambda conn: vagas)
    monkeypatch.setattr(
        script.revisao_ia, "decidir_revisao",
        lambda dados, **kw: {
            "decisao": "rejeitada",
            "motivo": "[revisão automática] Erro ao consultar o Gemini, rejeitada por padrão: timeout",
        },
    )
    aplicadas = []
    monkeypatch.setattr(script.db, "aplicar_revisao", lambda conn, **kw: aplicadas.append(kw))

    script.main()

    assert aplicadas == []


def test_main_sem_divergencia_nao_chama_gemini(monkeypatch):
    vagas = [_vaga(f"v{i}", "aprovada") for i in range(15)]
    monkeypatch.setattr(script.db, "conectar", lambda: Mock())
    monkeypatch.setattr(script.db, "listar_vagas_revisadas_para_consistencia", lambda conn: vagas)
    chamado = {"sim": False}
    monkeypatch.setattr(script.revisao_ia, "decidir_revisao", lambda *a, **k: chamado.update(sim=True))

    script.main()

    assert chamado["sim"] is False
