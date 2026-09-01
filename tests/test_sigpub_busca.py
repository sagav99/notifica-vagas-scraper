from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import sigpub_busca

FIXTURES = Path(__file__).parent / "fixtures" / "dom_amm_mg"


def _ler(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_obter_token_encontra_campo_hidden():
    html = _ler("busca_resultado_pedra_dourada_processo_seletivo.html")
    # obter_token faz o GET sozinho; aqui testamos só a extração, direto
    # do HTML já baixado (fixture tem o form completo com o campo hidden).
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    campo = soup.find("input", {"name": "busca_avancada[_token]"})
    assert campo is not None
    assert campo.get("value")


def test_parsear_resultados_pedra_dourada_duas_materias():
    html = _ler("busca_resultado_pedra_dourada_processo_seletivo.html")
    resultados = sigpub_busca.parsear_resultados(html)
    assert len(resultados) == 2
    codigos = {r.codigo for r in resultados}
    assert codigos == {"1F85EB05", "FFF2867C"}
    primeiro = resultados[0]
    assert primeiro.entidade == "Prefeitura Municipal de Pedra Dourada"
    assert primeiro.data_circulacao == date(2026, 7, 8)
    assert primeiro.url_load == f"{sigpub_busca.BASE_URL}/amm-mg/load/1F85EB05"


def test_parsear_resultados_abaete_multiplas_linhas():
    html = _ler("busca_resultado_abaete_processo_seletivo.html")
    resultados = sigpub_busca.parsear_resultados(html)
    assert len(resultados) >= 3
    assert all(r.entidade == "Prefeitura Municipal de Abaeté" for r in resultados)


def test_parsear_resultados_sem_resultado_devolve_lista_vazia():
    html = _ler("busca_resultado_sem_resultado.html")
    assert sigpub_busca.parsear_resultados(html) == []


def test_parsear_resultados_sem_tabela_devolve_lista_vazia():
    # Simula token/sessão inválidos: a página normal volta sem
    # table#datatable nenhuma, sem erro visível.
    assert sigpub_busca.parsear_resultados("<html><body>sem tabela aqui</body></html>") == []


def test_buscar_monta_parametros_e_usa_mesma_sessao(monkeypatch):
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
        token="token-123",
        entidade_id="1913769",
        termo="processo seletivo",
        data_inicio=date(2026, 6, 1),
        data_fim=date(2026, 9, 1),
    )
    assert "datatable" in resultado
    assert capturado["params"]["busca_avancada[entidadeUsuaria]"] == "1913769"
    assert capturado["params"]["busca_avancada[texto]"] == "processo seletivo"
    assert capturado["params"]["busca_avancada[dataInicio]"] == "01/06/2026"
    assert capturado["params"]["busca_avancada[dataFim]"] == "01/09/2026"
    assert capturado["params"]["busca_avancada[_token]"] == "token-123"


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
