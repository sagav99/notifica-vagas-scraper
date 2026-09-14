from pathlib import Path

import rodar_institutoconsulplan as script

FIXTURES = Path(__file__).parent / "fixtures" / "institutoconsulplan"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


class _RespostaFalsa:
    def __init__(self, *, text=None, content=None):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


def _fake_get_html_depois_pdf(html: str, pdf_bytes: bytes = b"pdf falso"):
    """PDF é identificado só pela extensão da URL (mesmo padrão do site
    real: `cdnsite.institutoconsulplan.org.br/concursos/<id>/<hash>.pdf`),
    então qualquer URL `.pdf` devolve o PDF falso, e qualquer outra
    devolve a página do cliente."""

    def _fake_get(url, headers=None, timeout=None):
        if url.endswith(".pdf"):
            return _RespostaFalsa(content=pdf_bytes)
        return _RespostaFalsa(text=html)

    return _fake_get


def _fake_inserir_capturando(chamadas: list):
    def _fake(conn, **kwargs):
        chamadas.append(kwargs)
        return {"vaga_id": f"vaga-{len(chamadas)}", "evidencia_id": f"evid-{len(chamadas)}", "vaga_criada": True}

    return _fake


def test_processar_cliente_balsamo_nao_descarta_nenhuma_das_5_especialidades_medicas(monkeypatch):
    # edital mais denso em cargo médico disponível nas fixtures desta
    # fonte (Bálsamo/SP, edital nº 01/2026) — prioridade #1 do produto:
    # nenhuma especialidade pode sumir. Cargo/salário vêm 100% do Gemini
    # aqui (sem "Quadro de Vagas" em HTML, diferente de IBGP/Instituto
    # Mais) — o mock abaixo reproduz a extração real confirmada na
    # investigação (TAREFAS.md, item Instituto Consulplan).
    html = _ler_fixture("balsamo_sp_pagina_concurso.html")
    monkeypatch.setattr(script.requests, "get", _fake_get_html_depois_pdf(html))
    monkeypatch.setattr(script.db, "buscar_codigo_ibge_local", lambda conn, nome, uf: None)
    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", lambda nome, uf: 3505153)
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)

    vagas_gemini = [
        {"cargo": "Médico - Clínico ESF", "salario": 12000.0, "salario_tipo": "mensal", "vagas_qtd": 2},
        {"cargo": "Médico - Ginecologista", "salario": 12000.0, "salario_tipo": "mensal", "vagas_qtd": 1},
        {"cargo": "Médico - Pediatra", "salario": 12000.0, "salario_tipo": "mensal", "vagas_qtd": 1},
        {"cargo": "Médico Plantonista", "salario": 1500.0, "salario_tipo": "plantao", "vagas_qtd": 4},
        {"cargo": "Médico - Psiquiatra", "salario": 12000.0, "salario_tipo": "mensal", "vagas_qtd": 1},
    ]
    monkeypatch.setattr(
        script.gemini_pdf,
        "extrair_vagas_de_pdf",
        lambda pdf_bytes: {
            "numero_edital": "1/2026",
            "orgao": "Prefeitura Municipal de Bálsamo/SP",
            "tipo_oportunidade": "concurso_efetivo",
            "inscricoes_inicio": "2026-06-30",
            "inscricoes_fim": "2026-07-20",
            "vagas": vagas_gemini,
        },
    )

    chamadas: list = []
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir_capturando(chamadas))

    total = script.processar_cliente(None, "fonte-id", "https://www.institutoconsulplan.org.br/pref-balsamo2026")

    assert total == 5
    assert len(chamadas) == 5
    cargos = {c["cargo"] for c in chamadas}
    assert cargos == {
        "Médico - Clínico ESF",
        "Médico - Ginecologista",
        "Médico - Pediatra",
        "Médico Plantonista",
        "Médico - Psiquiatra",
    }
    # escolheu o edital de abertura (o 1º dos 14 documentos listados),
    # não uma retificação/análise de isenção posterior.
    assert all(
        c["url_evidencia"] == "https://cdnsite.institutoconsulplan.org.br/concursos/1317/2681128237564ab8aa9e06ae626fe973.pdf"
        for c in chamadas
    )
    assert all(c["numero_edital"] == "1/2026" for c in chamadas)
    assert all(c["banca_organizadora"] == "Instituto Consulplan" for c in chamadas)


def test_processar_cliente_unai_sem_cargo_medico_ainda_grava_os_cargos_existentes(monkeypatch):
    # Unaí/MG (Câmara) é o caso confirmado "sem médico" da investigação —
    # o pipeline segue gravando os cargos normais, sem inventar nada.
    html = _ler_fixture("unai_mg_camara_pagina_concurso.html")
    monkeypatch.setattr(script.requests, "get", _fake_get_html_depois_pdf(html))
    monkeypatch.setattr(script.db, "buscar_codigo_ibge_local", lambda conn, nome, uf: 3170701)
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)
    monkeypatch.setattr(
        script.gemini_pdf,
        "extrair_vagas_de_pdf",
        lambda pdf_bytes: {
            "numero_edital": "1/2026",
            "orgao": "Câmara Municipal de Unaí/MG",
            "vagas": [
                {"cargo": "Assistente Legislativo", "salario": 2500.0, "salario_tipo": "mensal"},
                {"cargo": "Procurador Legislativo", "salario": 8000.0, "salario_tipo": "mensal"},
            ],
        },
    )

    chamadas: list = []
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir_capturando(chamadas))

    total = script.processar_cliente(None, "fonte-id", "https://www.institutoconsulplan.org.br/unai2026")

    assert total == 2
    cargos = {c["cargo"] for c in chamadas}
    assert cargos == {"Assistente Legislativo", "Procurador Legislativo"}
    assert not any("médico" in c.lower() for c in cargos)


def test_processar_cliente_ipremb_pula_placeholder_sem_erro(monkeypatch):
    # concurso pré-lançamento (achado real: IPREMB/Betim-MG e mais 3
    # clientes MG novos) — não é falha, é caso a pular sem tentar baixar
    # PDF nenhum nem chamar Gemini.
    html = _ler_fixture("ipremb_betim_mg_pagina_sem_publicacao.html")

    chamadas_get = []

    def _fake_get(url, headers=None, timeout=None):
        chamadas_get.append(url)
        return _RespostaFalsa(text=html)

    monkeypatch.setattr(script.requests, "get", _fake_get)

    inserir_chamado = []
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda *a, **k: inserir_chamado.append(1))
    gemini_chamado = []
    monkeypatch.setattr(
        script.gemini_pdf, "extrair_vagas_de_pdf", lambda pdf_bytes: gemini_chamado.append(1) or {"vagas": []}
    )

    total = script.processar_cliente(None, "fonte-id", "https://www.institutoconsulplan.org.br/ipremb2026")

    assert total == 0
    assert inserir_chamado == []
    assert gemini_chamado == []
    # só 1 chamada de rede: a da própria página do cliente, nunca tentou
    # baixar PDF nenhum.
    assert len(chamadas_get) == 1


def test_processar_cliente_fora_do_escopo_mg_sp_pula_sem_baixar_pdf(monkeypatch):
    html = (
        "<html><head><title>Prefeitura Municipal de Angra dos Reis/RJ "
        "— Instituto Consulplan</title></head><body></body></html>"
    )
    chamadas_get = []

    def _fake_get(url, headers=None, timeout=None):
        chamadas_get.append(url)
        return _RespostaFalsa(text=html)

    monkeypatch.setattr(script.requests, "get", _fake_get)
    inserir_chamado = []
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda *a, **k: inserir_chamado.append(1))

    total = script.processar_cliente(None, "fonte-id", "https://www.institutoconsulplan.org.br/angra2026")

    assert total == 0
    assert inserir_chamado == []
    assert len(chamadas_get) == 1


def test_processar_cliente_pagina_institucional_devolve_0_sem_erro(monkeypatch):
    html = _ler_fixture("concursos_listagem_hub_historico.html")
    monkeypatch.setattr(script.requests, "get", lambda url, headers=None, timeout=None: _RespostaFalsa(text=html))
    total = script.processar_cliente(None, "fonte-id", "https://www.institutoconsulplan.org.br/Concursos")
    assert total == 0


def test_processar_cliente_sem_municipio_no_ibge_pula_sem_erro(monkeypatch):
    html = _ler_fixture("balsamo_sp_pagina_concurso.html")
    monkeypatch.setattr(script.requests, "get", _fake_get_html_depois_pdf(html))
    monkeypatch.setattr(script.db, "buscar_codigo_ibge_local", lambda conn, nome, uf: None)
    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", lambda nome, uf: None)
    inserir_chamado = []
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda *a, **k: inserir_chamado.append(1))

    total = script.processar_cliente(None, "fonte-id", "https://www.institutoconsulplan.org.br/pref-balsamo2026")

    assert total == 0
    assert inserir_chamado == []
