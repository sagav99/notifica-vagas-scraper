import json
from datetime import date

import pytest

from notifica_vagas_scraper.fontes import actcon


class _RespostaFalsa:
    def __init__(self, texto: str, status_code: int = 200):
        self.text = texto
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


HTML_LISTAGEM_COM_ASHX = """
<script src="/ajaxpro/proc_sel_lis,App_Web_abc123.ashx"></script>
"""

HTML_DETALHE_COM_ASHX = """
<script src="/ajaxpro/proc_sel_vis,App_Web_xyz789.ashx"></script>
"""

DATATABLE_RESPOSTA = (
    'new Ajax.Web.DataTable('
    '[["CDPROCSELETIVO","System.Decimal"],["NUPROCSELETIVO","System.Int32"],'
    '["NUANOPROCSELETIVO","System.Int32"],["NMTIPO","System.String"],'
    '["NMSITUACAO","System.String"],["NMUNIDADE","System.String"]],'
    '[[56,2,2026,"Processo Seletivo Simplificado","Em andamento","Secretaria de Saúde"],'
    '[12,1,2019,"Concurso Público","Encerrado","Prefeitura Municipal"]]'
    ');/*comentario final*/'
)

PUBLICACOES_RESPOSTA = json.dumps(
    '<ul class="list-group"> \n'
    '<li class="list-group-item"> \n'
    '<div><a target="_blank" class="item-editais" '
    'href="abrir_arquivo.aspx?cdLocal=18&amp;arquivo={AAA}.pdf">31/08/2026 - Decreto de homologação</a></div> \n'
    '</li> \n'
    '<li class="list-group-item"> \n'
    '<div><a target="_blank" class="item-editais" '
    'href="abrir_arquivo.aspx?cdLocal=18&amp;arquivo={BBB}.pdf">01/06/2026 - Edital de Abertura 02/2026</a></div> \n'
    '</li>'
)


def test_parsear_datatable_extrai_linhas_como_dicionarios():
    linhas = actcon._parsear_datatable(DATATABLE_RESPOSTA)
    assert len(linhas) == 2
    assert linhas[0]["NMSITUACAO"] == "Em andamento"
    assert linhas[1]["NMSITUACAO"] == "Encerrado"


def test_listar_processos_seletivos_filtra_so_em_andamento(monkeypatch):
    chamadas = {"get": 0, "post": 0}

    def _get(url, **kwargs):
        chamadas["get"] += 1
        return _RespostaFalsa(HTML_LISTAGEM_COM_ASHX)

    def _post(url, **kwargs):
        chamadas["post"] += 1
        assert "ajaxpro/proc_sel_lis,App_Web_abc123.ashx" in url
        assert kwargs["headers"]["X-AjaxPro-Method"] == "GetListaDados"
        return _RespostaFalsa(DATATABLE_RESPOSTA)

    monkeypatch.setattr(actcon.requests, "get", _get)
    monkeypatch.setattr(actcon.requests, "post", _post)

    processos = actcon.listar_processos_seletivos("https://exemplo.mg.gov.br")
    assert len(processos) == 1
    assert processos[0].cd == 56
    assert processos[0].situacao == "Em andamento"
    assert processos[0].titulo == "Processo Seletivo Simplificado 2/2026"


def test_listar_processos_sem_ashx_devolve_vazio(monkeypatch):
    monkeypatch.setattr(actcon.requests, "get", lambda *a, **k: _RespostaFalsa("<html>sem ajaxpro aqui</html>"))
    assert actcon.listar_processos_seletivos("https://exemplo.mg.gov.br") == []


def test_listar_publicacoes_extrai_data_titulo_e_url_pdf(monkeypatch):
    def _get(url, **kwargs):
        return _RespostaFalsa(HTML_DETALHE_COM_ASHX)

    def _post(url, **kwargs):
        assert "ajaxpro/proc_sel_vis,App_Web_xyz789.ashx" in url
        assert kwargs["headers"]["X-AjaxPro-Method"] == "GetPublicacoes"
        return _RespostaFalsa(PUBLICACOES_RESPOSTA)

    monkeypatch.setattr(actcon.requests, "get", _get)
    monkeypatch.setattr(actcon.requests, "post", _post)

    publicacoes = actcon.listar_publicacoes("https://exemplo.mg.gov.br", 56)
    assert len(publicacoes) == 2
    assert publicacoes[0].data == date(2026, 8, 31)
    assert publicacoes[0].titulo == "Decreto de homologação"
    assert publicacoes[1].titulo == "Edital de Abertura 02/2026"
    assert publicacoes[1].url_pdf.startswith("https://exemplo.mg.gov.br/abrir_arquivo.aspx")


def test_listar_publicacoes_vazio_quando_sem_ashx(monkeypatch):
    monkeypatch.setattr(actcon.requests, "get", lambda *a, **k: _RespostaFalsa("<html></html>"))
    assert actcon.listar_publicacoes("https://exemplo.mg.gov.br", 1) == []


def test_listar_municipios_actcon_le_csv_real():
    municipios = actcon.listar_municipios_actcon()
    assert len(municipios) >= 4
    assert all(m.uf == "MG" for m in municipios)
    nomes = {m.nome for m in municipios}
    assert "Itabira" in nomes
