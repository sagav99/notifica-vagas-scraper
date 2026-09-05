from pathlib import Path

import rodar_instituto_mais as script

from notifica_vagas_scraper.fontes import instituto_mais

FIXTURES = Path(__file__).parent / "fixtures" / "instituto_mais"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def _item_itapeva() -> instituto_mais.ItemListagem:
    return instituto_mais.ItemListagem(
        concurso_id=10637,
        url="https://institutomais.org.br/Concursos/Detalhe/10637",
        titulo="Prefeitura Municipal de Itapeva / SP - Concurso Público - Edital nº 01/2026",
        status="andamento",
    )


class _RespostaFalsa:
    def __init__(self, *, text=None, content=None):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


def _fake_get_pagina_detalhe(html_pagina: str, url_pagina: str = "https://institutomais.org.br/Concursos/Detalhe/10637"):
    def _fake_get(url, headers=None, timeout=None):
        if url == url_pagina:
            return _RespostaFalsa(text=html_pagina)
        return _RespostaFalsa(content=b"pdf falso")

    return _fake_get


def test_processar_concurso_nao_descarta_o_medico_psiquiatra_mesmo_sem_gemini(monkeypatch):
    # achado central do desenho desta fonte (ver docstring de
    # fontes/instituto_mais.py e de rodar_instituto_mais.py): cargo vem do
    # HTML "Quadro de Vagas", não do Gemini — se o Gemini falhar (cota
    # esgotada, erro de rede), a vaga ainda é gravada (com salário
    # desconhecido), nunca perdida. PRIORIDADE #1 do produto (CLAUDE.md).
    monkeypatch.setattr(
        script.requests,
        "get",
        _fake_get_pagina_detalhe(_ler_fixture("institutomais_detalhe_itapeva_10637_medico_psiquiatra.html")),
    )
    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", lambda nome, uf: 3522208)

    cargos_gravados = []

    def _fake_inserir(conn, *, cargo, salario, salario_tipo, identificador_externo, **kwargs):
        cargos_gravados.append((cargo, salario, salario_tipo, identificador_externo))
        return {"vaga_id": len(cargos_gravados), "evidencia_id": len(cargos_gravados)}

    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    # simula Gemini indisponível (ex: cota esgotada) — `_extrair_com_gemini`
    # deve capturar isso e devolver {} em vez de propagar, pra não derrubar
    # o item inteiro (ver docstring de rodar_instituto_mais.py).
    monkeypatch.setattr(
        script.gemini_pdf,
        "extrair_vagas_de_pdf",
        lambda pdf_bytes: (_ for _ in ()).throw(script.gemini_pdf.ErroExtracaoGemini("cota esgotada")),
    )

    total = script.processar_concurso(conn=None, fonte_id="fonte-x", item=_item_itapeva(), municipio="Itapeva", uf="SP")

    assert total == 9
    cargos = {c for c, _s, _st, _i in cargos_gravados}
    assert "MÉDICO PSIQUIATRA" in cargos
    medico = next(c for c in cargos_gravados if c[0] == "MÉDICO PSIQUIATRA")
    assert medico[1] is None  # salário desconhecido, mas a vaga foi gravada
    identificadores = [i for _c, _s, _st, i in cargos_gravados]
    assert len(set(identificadores)) == 9  # nenhum id colidindo


def test_processar_concurso_usa_salario_do_gemini_quando_cargo_bate(monkeypatch):
    monkeypatch.setattr(
        script.requests,
        "get",
        _fake_get_pagina_detalhe(_ler_fixture("institutomais_detalhe_itapeva_10637_medico_psiquiatra.html")),
    )
    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", lambda nome, uf: 3522208)

    vagas_gemini = {
        "orgao": "Prefeitura Municipal de Itapeva",
        "numero_edital": "01/2026",
        "tipo_oportunidade": "concurso_efetivo",
        "vagas": [
            # capitalização diferente do HTML de propósito — confirma que
            # o match usa normalizar_cargo (case/acento-insensível).
            {"cargo": "Médico Psiquiatra", "salario": 5721.43, "salario_tipo": "mensal", "requisitos": "CRM + título de especialista"},
        ],
    }
    monkeypatch.setattr(script.gemini_pdf, "extrair_vagas_de_pdf", lambda pdf_bytes: vagas_gemini)

    cargos_gravados = []

    def _fake_inserir(conn, *, cargo, salario, salario_tipo, **kwargs):
        cargos_gravados.append((cargo, salario, salario_tipo))
        return {"vaga_id": len(cargos_gravados), "evidencia_id": len(cargos_gravados)}

    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    total = script.processar_concurso(conn=None, fonte_id="fonte-x", item=_item_itapeva(), municipio="Itapeva", uf="SP")

    assert total == 9
    medico = next(c for c in cargos_gravados if c[0] == "MÉDICO PSIQUIATRA")
    assert medico[1] == 5721.43
    assert medico[2] == "mensal"
    # outros cargos sem correspondência no retorno (parcial) do Gemini
    # continuam gravados, só sem salário.
    psicologo = next(c for c in cargos_gravados if c[0] == "PSICÓLOGO")
    assert psicologo[1] is None


def test_processar_concurso_santa_casa_nao_descarta_nenhuma_das_2_especialidades_de_residencia(monkeypatch):
    item = instituto_mais.ItemListagem(
        concurso_id=10643,
        url="https://institutomais.org.br/Concursos/Detalhe/10643",
        titulo=(
            "Irmandade da Santa Casa de Misericórdia de São José do Rio Preto / SP - "
            "Programa de Residência Médica - P. S. nº 02/2026"
        ),
        status="andamento",
    )
    monkeypatch.setattr(
        script.requests,
        "get",
        _fake_get_pagina_detalhe(
            _ler_fixture("institutomais_detalhe_santacasa_sjrp_residencia_medica_10643.html"),
            url_pagina="https://institutomais.org.br/Concursos/Detalhe/10643",
        ),
    )
    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", lambda nome, uf: 3549904)
    monkeypatch.setattr(script.gemini_pdf, "extrair_vagas_de_pdf", lambda pdf_bytes: {"vagas": []})

    cargos_gravados = []

    def _fake_inserir(conn, *, cargo, **kwargs):
        cargos_gravados.append(cargo)
        return {"vaga_id": len(cargos_gravados), "evidencia_id": len(cargos_gravados)}

    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    total = script.processar_concurso(conn=None, fonte_id="fonte-x", item=item, municipio="São José do Rio Preto", uf="SP")

    assert total == 2
    assert set(cargos_gravados) == {"Cirurgia Geral", "Cirurgia Oncológica"}


def test_processar_concurso_sem_quadro_de_vagas_pula_sem_erro(monkeypatch):
    monkeypatch.setattr(script.requests, "get", lambda *a, **k: _RespostaFalsa(text="<html><body>sem quadro</body></html>"))
    inserir_chamado = []
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda *a, **k: inserir_chamado.append(1))

    total = script.processar_concurso(conn=None, fonte_id="fonte-x", item=_item_itapeva(), municipio="Itapeva", uf="SP")

    assert total == 0
    assert inserir_chamado == []


def test_processar_concurso_municipio_sem_codigo_ibge_pula_sem_erro(monkeypatch):
    monkeypatch.setattr(
        script.requests,
        "get",
        _fake_get_pagina_detalhe(_ler_fixture("institutomais_detalhe_itapeva_10637_medico_psiquiatra.html")),
    )
    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", lambda nome, uf: None)
    inserir_chamado = []
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda *a, **k: inserir_chamado.append(1))

    total = script.processar_concurso(conn=None, fonte_id="fonte-x", item=_item_itapeva(), municipio="Itapeva", uf="SP")

    assert total == 0
    assert inserir_chamado == []
