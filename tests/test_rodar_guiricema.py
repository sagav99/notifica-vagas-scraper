from datetime import date

import rodar_guiricema as script

from notifica_vagas_scraper import gemini_pdf
from notifica_vagas_scraper.fontes import guiricema_mg


class _RespostaFalsa:
    def __init__(self, *, content: bytes | None = None):
        self.content = content

    def raise_for_status(self):
        pass


def _item_27_2026() -> guiricema_mg.ItemListagem:
    return guiricema_mg.ItemListagem(
        processo_id=80642,
        titulo="EDITAL DE PROCESSO SELETIVO PUBLICO CADASTRO RESERVA Nº 27/2026",
        cargo="MÉDICO ESF (40 HORAS)",
        data_publicacao=date(2026, 9, 9),
        url_pdf=(
            "https://www.guiricema.mg.gov.br/wp-content/uploads/2026/09/"
            "EDITAL-DE-PROCESSO-SELETIVO-PUBLICO-CADASTRO-RESERVA-No-27-2026.pdf"
        ),
    )


def _extraido_gemini_edital_27_2026() -> dict:
    # achado real: o PDF cobre 2 cargos (Médico ESF 40h e Médico Clínico
    # 20h) mesmo a listagem só citando "MÉDICO ESF (40 HORAS)" — o Gemini
    # lê o PDF inteiro e devolve os 2, nenhum cargo descartado no meio do
    # caminho (prioridade #1: nunca perder especialidade médica).
    return {
        "numero_edital": "27/2026",
        "orgao": "Prefeitura Municipal de Guiricema",
        "data_publicacao": "2026-09-09",
        "inscricoes_inicio": None,
        "inscricoes_fim": None,
        "taxa_inscricao": None,
        "data_prova": None,
        "tipo_oportunidade": "processo_seletivo_temporario",
        "vagas": [
            {
                "cargo": "Médico ESF",
                "pagina": 1,
                "vagas_qtd": 1,
                "salario": 15000.0,
                "salario_tipo": "mensal",
                "requisitos": "Graduação em Medicina; registro no CRM",
                "carga_horaria": "40 horas semanais",
            },
            {
                "cargo": "Médico Clínico",
                "pagina": 2,
                "vagas_qtd": 1,
                "salario": 7500.0,
                "salario_tipo": "mensal",
                "requisitos": "Graduação em Medicina; registro no CRM",
                "carga_horaria": "20 horas semanais",
            },
        ],
    }


def test_processar_processo_le_pdf_direto_da_listagem_e_grava_2_cargos_medicos(monkeypatch):
    pdf_bytes = b"%PDF-1.4 conteudo falso pra teste, nunca lido de verdade (gemini mockado)"
    item = _item_27_2026()

    def _fake_get(url, headers=None, timeout=None):
        assert url == item.url_pdf
        return _RespostaFalsa(content=pdf_bytes)

    monkeypatch.setattr(script.requests, "get", _fake_get)
    # patch no módulo `gemini_pdf` de verdade (não em `script`, que não o
    # importa direto — quem chama é `processamento_pdf_gemini.py`, que
    # importou o MESMO objeto de módulo).
    monkeypatch.setattr(gemini_pdf, "extrair_vagas_de_pdf", lambda conteudo: _extraido_gemini_edital_27_2026())
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)

    gravados = []

    def _fake_inserir(conn, *, cargo, identificador_externo, url_evidencia, salario, salario_tipo, **kwargs):
        gravados.append((cargo, identificador_externo, url_evidencia, salario, salario_tipo))
        return {"vaga_id": 1, "evidencia_id": 1}

    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    total = script.processar_processo(conn=None, fonte_id="fonte-x", codigo_ibge=3129004, item=item)

    assert total == 2
    cargos = {linha[0] for linha in gravados}
    assert cargos == {"Médico ESF", "Médico Clínico"}
    for cargo, identificador, url_evidencia, salario, salario_tipo in gravados:
        assert identificador.startswith("guiricema-mg-80642-")
        assert url_evidencia == item.url_pdf
        assert salario_tipo == "mensal"


def test_main_filtra_so_edital_de_abertura_de_saude(monkeypatch):
    html_listagem = (
        "tests/fixtures/guiricema/listagem_processos_seletivos.html"
    )
    with open(html_listagem, encoding="utf-8") as arquivo:
        conteudo_listagem = arquivo.read()

    def _fake_get(url, headers=None, timeout=None):
        assert url == guiricema_mg.URL_LISTAGEM
        return type("Resp", (), {"text": conteudo_listagem, "raise_for_status": lambda self: None})()

    monkeypatch.setattr(script.requests, "get", _fake_get)

    processados = []

    def _fake_processar(conn, fonte_id, codigo_ibge, item):
        processados.append(item.processo_id)
        return 1

    monkeypatch.setattr(script, "processar_processo", _fake_processar)

    class _TransacaoFalsa:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _ConnFalsa:
        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

        def transaction(self):
            return _TransacaoFalsa()

    monkeypatch.setattr(script.db, "conectar", lambda: _ConnFalsa())
    monkeypatch.setattr(script.db, "buscar_codigo_ibge_local", lambda *a, **k: 3129004)
    monkeypatch.setattr(script.db, "upsert_fonte", lambda *a, **k: "fonte-x")
    monkeypatch.setattr(script.db, "listar_identificadores_processados", lambda *a, **k: set())

    script.main()

    # só o edital de abertura 27/2026 (Médico ESF) é processado — as
    # outras 14 publicações da fixture (convocações, atas, prorrogação,
    # cargos não-saúde) nunca chegam a `processar_processo`.
    assert processados == [80642]
