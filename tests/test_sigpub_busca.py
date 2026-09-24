from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import sigpub_busca

FIXTURES = Path(__file__).parent / "fixtures" / "dom_amm_mg"


def _ler(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_obter_token_no_html_atual_devolve_none():
    """O site removeu o campo de token CSRF do form (achado 2026-09-24,
    ver docstring do módulo) — `obter_token` não deve mais achar nada."""
    html = _ler("busca_resultado_pedra_dourada_lista_nova_2026-09-24.html")
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    campo = soup.find("input", {"name": "busca_avancada[_token]"})
    assert campo is None


def test_parsear_resultados_pedra_dourada_estrutura_de_lista_nova():
    """Achado 2026-09-24: o site trocou `table#datatable` por
    `ul.lista-materias > li.materia-card` — fixture real, 25 cartões
    (1ª página de 88 matérias no total pra essa entidade)."""
    html = _ler("busca_resultado_pedra_dourada_lista_nova_2026-09-24.html")
    resultados = sigpub_busca.parsear_resultados(html)
    assert len(resultados) == 25
    primeiro = resultados[0]
    assert primeiro.entidade == "Prefeitura Municipal de Pedra Dourada"
    assert primeiro.orgao == "Prefeitura Municipal de Pedra Dourada"
    assert primeiro.codigo == "9578A4C5"
    assert primeiro.data_circulacao == date(2026, 9, 22)
    assert primeiro.url_load == f"{sigpub_busca.BASE_URL}/amm-mg/materia/9578A4C5"


def test_parsear_resultados_perdoes_acha_vaga_medico_real():
    """Achado real da auditoria de cobertura 2026-09-24: vaga de Médico
    de PSF (Edital 011/2026, Perdões/MG) que tinha ficado de fora do
    banco por causa deste mesmo bug de parsing — confirma que o parser
    corrigido acha o documento real."""
    html = _ler("busca_resultado_perdoes_processo_seletivo_2026-09-24.html")
    resultados = sigpub_busca.parsear_resultados(html)
    assert len(resultados) == 25
    titulos = [r.titulo for r in resultados]
    assert any("MÉDICO DE PSF" in t for t in titulos)
    assert all(r.entidade == "Prefeitura de Perdões" for r in resultados)


def test_parsear_resultados_sem_lista_devolve_lista_vazia():
    # Qualquer HTML sem `ul.lista-materias` (ex.: "nenhuma matéria
    # encontrada", ou página de erro) — nada aproveitável, sem exceção.
    assert sigpub_busca.parsear_resultados("<html><body>sem lista aqui</body></html>") == []


def test_buscar_monta_parametros_e_usa_mesma_sessao(monkeypatch):
    """Achado real 2026-09-24: o site renomeou `entidadeUsuaria`->`entidade`
    e `page`->`pagina`, mudou o formato de data pra ISO (`aaaa-mm-dd`) e
    removeu o campo de token — confirmado contra o form real do site."""
    capturado = {}

    class _RespostaFalsa:
        text = "<table id=\"datatable\"><tbody></tbody></table>"

        def raise_for_status(self):
            return None

    def _get(url, params=None, headers=None, timeout=None):
        capturado["url"] = url
        capturado["params"] = params
        return _RespostaFalsa()

    session = type("SessaoFalsa", (), {"get": staticmethod(_get)})()

    resultado = sigpub_busca.buscar(
        session,
        caminho_pesquisar="/amm-mg/pesquisar",
        entidade_id="1913769",
        termo="processo seletivo",
        data_inicio=date(2026, 6, 1),
        data_fim=date(2026, 9, 1),
    )
    assert "datatable" in resultado
    assert capturado["params"]["busca_avancada[entidade]"] == "1913769"
    assert capturado["params"]["busca_avancada[texto]"] == "processo seletivo"
    assert capturado["params"]["busca_avancada[dataInicio]"] == "2026-06-01"
    assert capturado["params"]["busca_avancada[dataFim]"] == "2026-09-01"
    assert "busca_avancada[_token]" not in capturado["params"]
    assert capturado["params"]["busca_avancada[pagina]"] == "1"


def test_resolver_url_materia_segue_redirect(monkeypatch):
    class _RespostaFalsa:
        url = f"{sigpub_busca.BASE_URL}/amm-mg/materia/1F85EB05/hash-longo"

        def raise_for_status(self):
            return None

    session = type("SessaoFalsa", (), {"get": staticmethod(lambda *a, **k: _RespostaFalsa())})()
    url_final = sigpub_busca.resolver_url_materia(
        session, f"{sigpub_busca.BASE_URL}/amm-mg/load/1F85EB05"
    )
    assert url_final == f"{sigpub_busca.BASE_URL}/amm-mg/materia/1F85EB05/hash-longo"
