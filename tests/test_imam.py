from collections import Counter
from pathlib import Path

from notifica_vagas_scraper.fontes import imam

FIXTURES = Path(__file__).parent / "fixtures" / "imam"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_listar_processos_conta_por_status():
    itens = imam.listar_processos(_ler_fixture("listagem.html"))
    contagem = Counter(i.status for i in itens)
    assert contagem["Novo"] == 1
    assert contagem["Em andamento (inscrições encerradas)"] == 19
    assert contagem["Concluído"] == 20


def test_listar_processos_extrai_entidade_titulo_e_id():
    itens = imam.listar_processos(_ler_fixture("listagem.html"))
    novo = next(i for i in itens if i.status == "Novo")

    assert novo.processo_id == "3380A52456F3A4BB"
    assert novo.entidade == "Câmara de Divisa Nova"
    assert novo.titulo_processo == "Concurso Edital 001/2026"
    assert novo.url == (
        "https://www.imam.org.br/sitenoticia/"
        "processo_seletivo_detalhes.aspx?id=3380A52456F3A4BB"
    )


def test_extrair_municipio_remove_prefixo_de_entidade():
    assert imam.extrair_municipio("Prefeitura Municipal de Congonhal") == "Congonhal"
    assert imam.extrair_municipio("Prefeitura de Itabirito") == "Itabirito"
    assert imam.extrair_municipio("Câmara de Divisa Nova") == "Divisa Nova"
    assert imam.extrair_municipio("Câmara Municipal de Ipatinga") == "Ipatinga"


def test_extrair_municipio_devolve_none_pra_entidade_nao_mapeavel():
    assert imam.extrair_municipio("Consórcio Intermunicipal de Saúde") is None


def test_listar_documentos_resolve_url_relativa_e_dedup():
    url_pagina = (
        "https://www.imam.org.br/sitenoticia/"
        "processo_seletivo_detalhes.aspx?id=E6C235E9178B7C71"
    )
    documentos = imam.listar_documentos(_ler_fixture("detalhe_congonhal.html"), url_pagina)

    assert len(documentos) == 14
    primeiro = documentos[0]
    assert primeiro.titulo == "Gabarito Provisório"
    assert primeiro.data.isoformat() == "2026-08-31T09:00:00"
    assert primeiro.url_pdf == (
        "https://www.imam.org.br/documentos/"
        "GabaritoProvisorioCongonhal90639237584896453006.pdf"
    )


def test_escolher_edital_prefere_versao_consolidada_mais_recente():
    url_pagina = (
        "https://www.imam.org.br/sitenoticia/"
        "processo_seletivo_detalhes.aspx?id=E6C235E9178B7C71"
    )
    documentos = imam.listar_documentos(_ler_fixture("detalhe_congonhal.html"), url_pagina)
    edital = imam.escolher_edital(documentos)

    assert edital is not None
    assert edital.titulo == "Edital com alterações da retificação nº 01"


def test_escolher_edital_lista_vazia_devolve_none():
    assert imam.escolher_edital([]) is None
