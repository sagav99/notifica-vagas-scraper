from datetime import date
from pathlib import Path

import rodar_sao_sebastiao_do_oeste_mg as script

from notifica_vagas_scraper.fontes import sao_sebastiao_do_oeste_mg as fonte

FIXTURES = Path(__file__).parent / "fixtures" / "sao_sebastiao_do_oeste_mg"


class _RespostaFalsa:
    def __init__(self, *, content: bytes | None = None, text: str | None = None):
        self.content = content
        self.text = text

    def raise_for_status(self):
        pass


def _item_edital_01_2026() -> fonte.ItemListagem:
    return fonte.ItemListagem(
        titulo="Edital de Processo Seletivo Simplificado nº 01/2026",
        url=(
            "https://www.saosebastiaodooeste.mg.gov.br/processos-seletivos/item/"
            "4769-edital-de-processo-seletivo-simplificado-n-01-2026"
        ),
        data_publicacao=date(2026, 7, 1),
    )


def test_processar_item_le_o_pdf_anexos_e_grava_5_cargos(monkeypatch):
    item = _item_edital_01_2026()
    item_html = (FIXTURES / "item_4769_edital_simplificado_01_2026.html").read_text(encoding="utf-8")
    pdf_bytes = (FIXTURES / "edital_simplificado_01_2026_anexos_cargos_salarios.pdf").read_bytes()

    def _fake_get(url, params=None, headers=None, timeout=None):
        if url == item.url:
            return _RespostaFalsa(text=item_html)
        assert url == "https://saosebastiaodooeste.mg.gov.br/media/k2/attachments/editalprocessoseletivosim012026anexos.pdf"
        return _RespostaFalsa(content=pdf_bytes)

    monkeypatch.setattr(script.requests, "get", _fake_get)

    gravados = []

    def _fake_inserir(conn, *, cargo, identificador_externo, salario, salario_tipo, banca_organizadora, tem_prova, **kwargs):
        gravados.append((cargo, identificador_externo, salario, salario_tipo, banca_organizadora, tem_prova))
        return {"vaga_id": 1, "evidencia_id": 1}

    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    total = script.processar_item(conn=None, fonte_id="fonte-x", codigo_ibge=3163706, item=item)

    assert total == 5
    cargos = {linha[0] for linha in gravados}
    # achado de prioridade máxima do produto: nenhuma especialidade de
    # saúde do ANEXO I é descartada, incluindo Médico.
    assert cargos == {
        "Auxiliar de Saúde Bucal – ESB",
        "Enfermeiro – ESF",
        "Médico – ESF",
        "Odontólogo – ESB",
        "Técnico em Enfermagem – ESF",
    }
    for cargo, identificador, salario, salario_tipo, banca, tem_prova in gravados:
        assert identificador.startswith("sao-sebastiao-do-oeste-mg-")
        assert salario_tipo == "mensal"
        assert salario is not None
        assert banca == "IDEAP – Instituto de Desenvolvimento Social, Empresarial e de Administração Pública"
        assert tem_prova is True


def test_processar_item_sem_pdf_devolve_zero(monkeypatch):
    item = _item_edital_01_2026()

    def _fake_get(url, params=None, headers=None, timeout=None):
        return _RespostaFalsa(text="<html><body>sem anexos aqui</body></html>")

    monkeypatch.setattr(script.requests, "get", _fake_get)

    total = script.processar_item(conn=None, fonte_id="fonte-x", codigo_ibge=3163706, item=item)
    assert total == 0


def test_main_filtra_so_editais_de_abertura(monkeypatch):
    html_pagina1 = (FIXTURES / "listagem_processos_seletivos_pagina1.html").read_text(encoding="utf-8")

    def _fake_get(url, params=None, headers=None, timeout=None):
        assert url == fonte.URL_LISTAGEM
        # só a 1ª página tem conteúdo nesta fixture — páginas seguintes
        # devolvem vazio (fim da listagem, encerra o loop de paginação).
        if params is None:
            return _RespostaFalsa(text=html_pagina1)
        return _RespostaFalsa(text="<html><body></body></html>")

    monkeypatch.setattr(script.requests, "get", _fake_get)

    processados = []

    def _fake_processar(conn, fonte_id, codigo_ibge, item):
        processados.append(item.titulo)
        return 1

    monkeypatch.setattr(script, "processar_item", _fake_processar)

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
    monkeypatch.setattr(script.db, "buscar_codigo_ibge_local", lambda *a, **k: 3163706)
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)
    monkeypatch.setattr(script.db, "upsert_fonte", lambda *a, **k: "fonte-x")
    monkeypatch.setattr(script.db, "registrar_cobertura_municipio", lambda *a, **k: None)

    script.main()

    # achado real: nenhum dos 12 itens da página 1 é edital de abertura
    # (só gabarito/resultado/retificação/despacho/convocação/extrato) —
    # não é bug, é a fixture real capturada num momento sem edital novo
    # na 1ª página.
    assert processados == []
