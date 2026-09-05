import io
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import descobrir_pci

MUNICIPIOS = [
    ("Contagem", "MG"),
    ("Tocantins", "MG"),
    ("Abaeté", "MG"),
    ("Itapecerica da Serra", "SP"),
    ("Ilhabela", "SP"),
]


def test_casa_municipio_real_com_marcador_de_uf_correto():
    match = descobrir_pci.casar_municipio_com_guarda_de_uf(
        "Prefeitura de Contagem - MG abre concurso público", "MG", MUNICIPIOS
    )
    assert match == ("Contagem", "MG")


def test_rejeita_estado_homonimo_de_municipio():
    # Achado real: "Tocantins" é município de MG mas também nome de
    # estado -- "Governo do Tocantins - TO" não tem nada a ver com o
    # município mineiro.
    match = descobrir_pci.casar_municipio_com_guarda_de_uf(
        "Governo do Tocantins - TO retifica edital de concurso", "MG", MUNICIPIOS
    )
    assert match is None


def test_rejeita_municipio_prefixo_de_outro_sem_separador():
    # Achado real: "Abaeté" (MG) bate como substring de "Abaetetuba" (PA)
    # sem espaço/separador entre os dois -- a guarda de prefixo do
    # encontrar_municipio (que só pega "nome + espaço + do/da") não cobre
    # esse caso, por isso a checagem extra do marcador "- UF" é necessária.
    match = descobrir_pci.casar_municipio_com_guarda_de_uf(
        "Prefeitura de Abaetetuba - PA divulga retificação", "MG", MUNICIPIOS
    )
    assert match is None


def test_aceita_titulo_sem_marcador_de_uf_nenhum():
    match = descobrir_pci.casar_municipio_com_guarda_de_uf(
        "Prefeitura de Ilhabela abre concurso público", "SP", MUNICIPIOS
    )
    assert match == ("Ilhabela", "SP")


def test_buscar_links_externos_ignora_pci_e_redes_sociais():
    # Achado real (2026-09-02, TAREFAS.md): notícia sempre linka de volta
    # pra prefeitura/autarquia e pra banca real -- é isso que interessa,
    # não os links internos da própria PCI nem redes sociais.
    html = """
    <html><body>
      <a href="https://www.pciconcursos.com.br/concursos/mg/">voltar</a>
      <a href="https://www.facebook.com/pciconcursos">facebook</a>
      <a href="https://www.paracatu.mg.gov.br/noticia/123">Prefeitura de Paracatu</a>
      <a href="https://www.ibgpconcursos.com.br/concurso/456">Edital no site da banca</a>
    </body></html>
    """
    resposta_mock = Mock(text=html)
    resposta_mock.raise_for_status = Mock()
    with patch("descobrir_pci.requests.get", return_value=resposta_mock) as get_mock:
        hosts = descobrir_pci.buscar_links_externos(
            "https://www.pciconcursos.com.br/noticias/prefeitura-de-paracatu-mg"
        )
    get_mock.assert_called_once()
    assert hosts == ["www.paracatu.mg.gov.br", "www.ibgpconcursos.com.br"]


def test_main_persiste_sinal_e_marca_coberto_por_fonte_conhecida(monkeypatch):
    # achado real de 2026-09-05 (relatório de operação autônoma): a PCI só
    # imprimia CSV, nunca persistia. Este teste garante que main() grava
    # em sinais_descoberta_externa (via db.registrar_sinal_descoberta) e
    # marca "coberto" quando o link externo já bate um domínio de
    # public.fontes já cadastrado.
    conn_falso = Mock()

    monkeypatch.setattr(descobrir_pci.db, "conectar", lambda: conn_falso)
    monkeypatch.setattr(
        descobrir_pci.db,
        "listar_municipios_com_codigo",
        lambda conn, ufs=None: [(3106200, "Paracatu", "MG"), (3300100, "Angra dos Reis", "MG")],
    )
    monkeypatch.setattr(
        descobrir_pci.db, "listar_dominios_fontes_conhecidas", lambda conn: {"www.ibgpconcursos.com.br"}
    )
    monkeypatch.setattr(
        descobrir_pci,
        "buscar_noticias",
        lambda uf_lower: (
            [("https://www.pciconcursos.com.br/noticias/prefeitura-de-paracatu-mg", "Prefeitura de Paracatu - MG")]
            if uf_lower == "mg"
            else []
        ),
    )
    monkeypatch.setattr(
        descobrir_pci, "buscar_links_externos", lambda url: ["www.paracatu.mg.gov.br", "www.ibgpconcursos.com.br"]
    )

    chamadas = []
    monkeypatch.setattr(
        descobrir_pci.db,
        "registrar_sinal_descoberta",
        lambda conn, **kw: chamadas.append(kw) or True,
    )

    with redirect_stdout(io.StringIO()):
        descobrir_pci.main()

    assert len(chamadas) == 1
    chamada = chamadas[0]
    assert chamada["fonte_descoberta"] == "pci_concursos"
    assert chamada["municipio_id"] == 3106200
    assert chamada["coberto_por_fonte_oficial"] is True
    assert set(chamada["dominios_externos"]) == {"www.paracatu.mg.gov.br", "www.ibgpconcursos.com.br"}
    conn_falso.commit.assert_called_once()
    conn_falso.close.assert_called_once()


def test_main_nao_persiste_quando_municipio_sem_codigo_ibge_no_cadastro(monkeypatch):
    conn_falso = Mock()
    monkeypatch.setattr(descobrir_pci.db, "conectar", lambda: conn_falso)
    # município retornado pelo match não está na lista com código (caso
    # defensivo, não deveria acontecer na prática já que a lista de
    # códigos vem da mesma consulta que gera os nomes usados no match).
    monkeypatch.setattr(descobrir_pci.db, "listar_municipios_com_codigo", lambda conn, ufs=None: [])
    monkeypatch.setattr(descobrir_pci.db, "listar_dominios_fontes_conhecidas", lambda conn: set())
    monkeypatch.setattr(descobrir_pci, "buscar_noticias", lambda uf_lower: [])

    chamadas = []
    monkeypatch.setattr(
        descobrir_pci.db, "registrar_sinal_descoberta", lambda conn, **kw: chamadas.append(kw) or True
    )

    with redirect_stdout(io.StringIO()):
        descobrir_pci.main()

    assert chamadas == []
    conn_falso.commit.assert_called_once()
