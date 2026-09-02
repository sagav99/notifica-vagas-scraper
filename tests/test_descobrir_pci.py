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
