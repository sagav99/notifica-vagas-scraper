from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import institutoconsulplan as ic

FIXTURES = Path(__file__).parent / "fixtures" / "institutoconsulplan"


def _ler(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


# --- sitemap.xml (hub de descoberta) ---


def test_listar_urls_sitemap_traz_as_47_urls_reais():
    xml = _ler("sitemap.xml")
    urls = ic.listar_urls_sitemap(xml)
    assert len(urls) == 47
    assert "https://www.institutoconsulplan.org.br/pref-alvinopolis2026" in urls
    assert "https://www.institutoconsulplan.org.br/pref-balsamo2026" in urls


def test_listar_urls_clientes_exclui_so_as_institucionais_conhecidas():
    xml = _ler("sitemap.xml")
    clientes = ic.listar_urls_clientes(xml)
    # 47 URLs no total, 8 institucionais fixas excluídas -> 39 candidatas.
    assert len(clientes) == 39
    for url in ic.URLS_INSTITUCIONAIS:
        assert url not in clientes
    # slug opaco (sem prefixo/ano previsível) continua sendo candidato —
    # não é papel de `listar_urls_clientes` decidir MG/SP, só excluir o
    # conjunto institucional fixo.
    assert "https://www.institutoconsulplan.org.br/unai2026" in clientes
    assert "https://www.institutoconsulplan.org.br/prefalagoa2026" in clientes
    assert "https://www.institutoconsulplan.org.br/saaeb26" in clientes


# --- identificar_cliente (título -> órgão/cidade/UF, sem geocoding) ---


def test_identificar_cliente_balsamo_sp():
    html = _ler("balsamo_sp_pagina_concurso.html")
    cliente = ic.identificar_cliente(html, "https://www.institutoconsulplan.org.br/pref-balsamo2026")
    assert cliente is not None
    assert cliente.slug == "pref-balsamo2026"
    assert cliente.orgao == "Prefeitura Municipal de Bálsamo/SP"
    assert cliente.cidade == "Bálsamo"
    assert cliente.uf == "SP"


def test_identificar_cliente_unai_mg_camara():
    html = _ler("unai_mg_camara_pagina_concurso.html")
    cliente = ic.identificar_cliente(html, "https://www.institutoconsulplan.org.br/unai2026")
    assert cliente is not None
    assert cliente.orgao == "Câmara Municipal de Unaí/MG"
    assert cliente.cidade == "Unaí"
    assert cliente.uf == "MG"


def test_identificar_cliente_ipremb_orgao_com_multiplos_de():
    # achado real: "IPREMB - Instituto de Previdência Social do Município
    # de Betim/MG" tem mais de 1 " de " no texto — o regex greedy do
    # órgão precisa achar a ÚLTIMA ocorrência (a que precede a
    # cidade/UF), não a primeira.
    html = _ler("ipremb_betim_mg_pagina_sem_publicacao.html")
    cliente = ic.identificar_cliente(html, "https://www.institutoconsulplan.org.br/ipremb2026")
    assert cliente is not None
    assert cliente.orgao == "IPREMB - Instituto de Previdência Social do Município de Betim/MG"
    assert cliente.cidade == "Betim"
    assert cliente.uf == "MG"


def test_identificar_cliente_hub_historico_devolve_none():
    # título é só "Instituto Consulplan" (sem separador nem "de Cidade/UF")
    # — página institucional, não é cliente.
    html = _ler("concursos_listagem_hub_historico.html")
    assert ic.identificar_cliente(html, "https://www.institutoconsulplan.org.br/Concursos") is None


def test_identificar_cliente_titulo_sem_padrao_esperado_devolve_none():
    assert ic.identificar_cliente("<html><head><title>Página qualquer</title></head></html>", "x") is None
    assert ic.identificar_cliente("<html><head></head></html>", "x") is None


# --- pagina_sem_publicacao (concurso pré-lançamento) ---


def test_pagina_sem_publicacao_ipremb_ainda_nao_lancou():
    html = _ler("ipremb_betim_mg_pagina_sem_publicacao.html")
    assert ic.pagina_sem_publicacao(html) is True


def test_pagina_sem_publicacao_balsamo_ja_tem_documentos():
    html = _ler("balsamo_sp_pagina_concurso.html")
    assert ic.pagina_sem_publicacao(html) is False


# --- listar_documentos / escolher_edital_abertura ---


def test_listar_documentos_balsamo_traz_os_14_documentos_reais():
    html = _ler("balsamo_sp_pagina_concurso.html")
    documentos = ic.listar_documentos(html)
    assert len(documentos) == 14

    primeiro = documentos[0]
    assert primeiro.titulo == "Edital nº 1/2026 - Abertura"
    assert primeiro.data == date(2026, 6, 29)
    assert primeiro.url_pdf == (
        "https://cdnsite.institutoconsulplan.org.br/concursos/1317/2681128237564ab8aa9e06ae626fe973.pdf"
    )


def test_listar_documentos_unai_traz_o_unico_documento_real():
    html = _ler("unai_mg_camara_pagina_concurso.html")
    documentos = ic.listar_documentos(html)
    assert len(documentos) == 1
    assert documentos[0].titulo == "Edital nº 1/2026 - Abertura"
    assert documentos[0].data == date(2026, 8, 26)


def test_listar_documentos_ipremb_sem_publicacao_devolve_vazio():
    html = _ler("ipremb_betim_mg_pagina_sem_publicacao.html")
    assert ic.listar_documentos(html) == []


def test_escolher_edital_abertura_balsamo_acha_o_de_abertura_entre_14():
    html = _ler("balsamo_sp_pagina_concurso.html")
    documentos = ic.listar_documentos(html)
    edital = ic.escolher_edital_abertura(documentos)
    assert edital is not None
    assert edital.titulo == "Edital nº 1/2026 - Abertura"
    assert edital.data == date(2026, 6, 29)


def test_escolher_edital_abertura_unai_com_1_documento_so():
    html = _ler("unai_mg_camara_pagina_concurso.html")
    documentos = ic.listar_documentos(html)
    edital = ic.escolher_edital_abertura(documentos)
    assert edital is not None
    assert edital.titulo == "Edital nº 1/2026 - Abertura"


def test_escolher_edital_abertura_sem_documentos_devolve_none():
    assert ic.escolher_edital_abertura([]) is None


def test_escolher_edital_abertura_sem_titulo_abertura_cai_pro_edital_mais_antigo():
    documentos = [
        ic.Documento(titulo="Retificação I ao Edital nº 01/2026", data=date(2026, 2, 1), url_pdf="x"),
        ic.Documento(titulo="Edital nº 01/2026", data=date(2026, 1, 1), url_pdf="y"),
    ]
    edital = ic.escolher_edital_abertura(documentos)
    assert edital is not None
    assert edital.url_pdf == "y"


def test_escolher_edital_abertura_sem_titulo_edital_cai_pro_mais_antigo_de_todos():
    documentos = [
        ic.Documento(titulo="Comunicado", data=date(2026, 2, 1), url_pdf="x"),
        ic.Documento(titulo="Aviso", data=date(2026, 1, 1), url_pdf="y"),
    ]
    edital = ic.escolher_edital_abertura(documentos)
    assert edital is not None
    assert edital.url_pdf == "y"


# --- extrair_numero_edital_do_titulo / identificador_externo ---


def test_extrair_numero_edital_do_titulo():
    assert ic.extrair_numero_edital_do_titulo("Edital nº 1/2026 - Abertura") == "1/2026"
    assert ic.extrair_numero_edital_do_titulo("Retificação I") is None


def test_identificador_externo_normaliza_cargo():
    ident = ic.identificador_externo("pref-balsamo2026", "Médico - Ginecologista/Obstetra")
    assert ident == "institutoconsulplan-pref-balsamo2026-médico---ginecologista-obstetra"
