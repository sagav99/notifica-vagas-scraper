from decimal import Decimal
from pathlib import Path

import pdfplumber

from notifica_vagas_scraper.fontes import itamonte_mg

FIXTURES = Path(__file__).parent / "fixtures" / "itamonte_mg"


def _ler_html(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8", errors="replace")


def _ler_pdf_bytes(nome: str) -> bytes:
    return (FIXTURES / nome).read_bytes()


def _texto_pdf(nome: str) -> str:
    # `use_text_flow=True` preserva a ordem do stream de conteúdo do PDF
    # (mesma ordem "linha da tabela por linha da tabela" que os regexes do
    # módulo esperam) em vez da reconstrução espacial padrão do
    # pdfplumber, que embaralha as colunas desta tabela em específico
    # (achado real, ver docstring de `itamonte_mg.py`).
    with pdfplumber.open(FIXTURES / nome) as pdf:
        return "\n".join((pagina.extract_text(use_text_flow=True) or "") for pagina in pdf.pages)


TEXTO_ORIGINAL = None
TEXTO_1A = None
TEXTO_2A = None


def _textos_ps_001():
    global TEXTO_ORIGINAL, TEXTO_1A, TEXTO_2A
    if TEXTO_ORIGINAL is None:
        TEXTO_ORIGINAL = _texto_pdf("ps_001_2026_edital_original.pdf")
        TEXTO_1A = _texto_pdf("ps_001_2026_1a_rerratificacao.pdf")
        TEXTO_2A = _texto_pdf("ps_001_2026_2a_rerratificacao.pdf")
    return TEXTO_ORIGINAL, TEXTO_1A, TEXTO_2A


# --- listar_documentos (/edital.php) ----------------------------------------


def test_listar_documentos_conta_todos_os_itens_da_listagem():
    itens = itamonte_mg.listar_documentos(_ler_html("edital_php_listagem.html"))
    # listagem real tem mais de mil atos (licitação + processo seletivo).
    assert len(itens) > 1000


def test_listar_documentos_pagina_sem_h5_devolve_lista_vazia():
    assert itamonte_mg.listar_documentos("<html><body>nada aqui</body></html>") == []


def test_listar_documentos_extrai_data_titulo_e_url_do_primeiro_item():
    itens = itamonte_mg.listar_documentos(_ler_html("edital_php_listagem.html"))
    primeiro = itens[0]
    assert primeiro.titulo == "Processo 095/2026 Pregão 060"
    assert primeiro.data is not None
    assert primeiro.url.startswith("https://www.itamonte.mg.gov.br/imagens/licitacao/")


# --- eh_processo_seletivo / filtrar_processos_seletivos ---------------------


def test_filtrar_processos_seletivos_nao_pega_licitacao():
    itens = itamonte_mg.listar_documentos(_ler_html("edital_php_listagem.html"))
    processos = itamonte_mg.filtrar_processos_seletivos(itens)
    for item in processos:
        assert "pregão" not in item.titulo.lower()
        assert "dispensa" not in item.titulo.lower()
        assert "credenciamento" not in item.titulo.lower()


def test_filtrar_processos_seletivos_acha_o_ps_001_2026():
    itens = itamonte_mg.listar_documentos(_ler_html("edital_php_listagem.html"))
    processos = itamonte_mg.filtrar_processos_seletivos(itens)
    alvo = next(p for p in processos if "001/2026" in p.titulo)
    assert "Rerratificações" in alvo.titulo
    assert alvo.url == "https://www.itamonte.mg.gov.br/imagens/licitacao/6f90c194798b02ccf0cfac0f8453a529."


def test_eh_processo_seletivo_reconhece_concurso_publico_tambem():
    assert itamonte_mg.eh_processo_seletivo("Concurso Público nº 01/2027")
    assert not itamonte_mg.eh_processo_seletivo("Processo 095/2026 Pregão 060")


def test_extrair_numero_processo():
    assert itamonte_mg.extrair_numero_processo("Edital e Rerratificações Processo Seletivo 001/2026") == "001/2026"
    assert itamonte_mg.extrair_numero_processo("Processo 095/2026 Pregão 060") is None


# --- detectar_tipo_arquivo / extrair_pdfs (link sem extensão -> ZIP) --------


def test_detectar_tipo_arquivo_reconhece_zip_por_magic_bytes():
    conteudo = _ler_pdf_bytes("ps_001_2026_pacote.zip")
    assert itamonte_mg.detectar_tipo_arquivo(conteudo) == "zip"


def test_detectar_tipo_arquivo_reconhece_pdf_por_magic_bytes():
    conteudo = _ler_pdf_bytes("ps_001_2026_edital_original.pdf")
    assert itamonte_mg.detectar_tipo_arquivo(conteudo) == "pdf"


def test_detectar_tipo_arquivo_desconhecido_nao_derruba():
    assert itamonte_mg.detectar_tipo_arquivo(b"nem pdf nem zip") == "desconhecido"


def test_extrair_pdfs_de_zip_devolve_os_3_pdfs():
    conteudo = _ler_pdf_bytes("ps_001_2026_pacote.zip")
    pdfs = itamonte_mg.extrair_pdfs(conteudo)
    assert len(pdfs) == 3
    for pdf_bytes in pdfs:
        assert pdf_bytes[:4] == b"%PDF"


def test_extrair_pdfs_de_pdf_direto_devolve_ele_mesmo():
    conteudo = _ler_pdf_bytes("ps_001_2026_edital_original.pdf")
    pdfs = itamonte_mg.extrair_pdfs(conteudo)
    assert pdfs == [conteudo]


def test_extrair_pdfs_conteudo_desconhecido_devolve_lista_vazia():
    assert itamonte_mg.extrair_pdfs(b"lixo qualquer") == []


# --- ordem_versao_documento --------------------------------------------------


def test_ordem_versao_documento_original_e_zero():
    original, primeira, segunda = _textos_ps_001()
    assert itamonte_mg.ordem_versao_documento(original) == 0


def test_ordem_versao_documento_1a_rerratificacao_e_um():
    original, primeira, segunda = _textos_ps_001()
    assert itamonte_mg.ordem_versao_documento(primeira) == 1


def test_ordem_versao_documento_2a_rerratificacao_e_dois():
    original, primeira, segunda = _textos_ps_001()
    assert itamonte_mg.ordem_versao_documento(segunda) == 2


# --- extrair_tabela_funcoes (edital mais denso das fixtures: PS 001/2026) ---
#
# PRIORIDADE #1 do produto (CLAUDE.md): nenhuma especialidade (médica ou
# não) pode ser descartada silenciosamente.


def test_extrair_tabela_funcoes_original_acha_as_2_secoes_e_15_cargos():
    original, _primeira, _segunda = _textos_ps_001()
    tabela = itamonte_mg.extrair_tabela_funcoes(original)
    assert set(tabela.keys()) == {"Ensino Médio e/ou Técnico", "Ensino Superior"}
    assert len(tabela["Ensino Médio e/ou Técnico"]) == 3
    assert len(tabela["Ensino Superior"]) == 12


def test_extrair_tabela_funcoes_original_nao_descarta_nenhum_medico():
    original, _primeira, _segunda = _textos_ps_001()
    tabela = itamonte_mg.extrair_tabela_funcoes(original)
    medicos = {f.cargo: f for f in tabela["Ensino Superior"] if "dico" in f.cargo.lower()}
    assert len(medicos) == 2

    caps = medicos["Médico – CAPS"]
    assert caps.vagas == 1
    assert caps.carga_horaria == "40 Horas Semanais"
    assert caps.salario == Decimal("7964.38")
    assert "CRM/MG" in caps.requisitos

    esf = medicos["Médico - ESF"]
    assert esf.vagas == 6
    assert esf.carga_horaria == "40 Horas Semanais"
    assert esf.salario == Decimal("17802.73")
    assert "CRM/MG" in esf.requisitos


def test_extrair_tabela_funcoes_1a_rerratificacao_reduz_carga_horaria_do_medico_caps():
    _original, primeira, _segunda = _textos_ps_001()
    tabela = itamonte_mg.extrair_tabela_funcoes(primeira)
    caps = next(f for f in tabela["Ensino Superior"] if f.cargo == "Médico – CAPS")
    assert caps.carga_horaria == "20 Horas Semanais"
    assert caps.salario == Decimal("7964.38")  # salário não muda, só a carga horária

    esf = next(f for f in tabela["Ensino Superior"] if f.cargo == "Médico - ESF")
    assert esf.carga_horaria == "40 Horas Semanais"  # ESF não muda


def test_extrair_tabela_funcoes_1a_rerratificacao_renomeia_alguns_cargos():
    _original, primeira, _segunda = _textos_ps_001()
    tabela = itamonte_mg.extrair_tabela_funcoes(primeira)
    cargos = {f.cargo for f in tabela["Ensino Superior"]}
    assert "Educador Físico - NASF" in cargos
    assert "Farmacêutico NASF" in cargos


def test_extrair_tabela_funcoes_2a_rerratificacao_nao_toca_tabela_de_cargos():
    _original, _primeira, segunda = _textos_ps_001()
    tabela = itamonte_mg.extrair_tabela_funcoes(segunda)
    assert tabela == {}


def test_extrair_tabela_funcoes_texto_sem_tabela_devolve_vazio():
    assert itamonte_mg.extrair_tabela_funcoes("texto qualquer sem tabela nenhuma") == {}


# --- mesclar_versoes ---------------------------------------------------------


def test_mesclar_versoes_pega_carga_horaria_mais_recente_do_medico_caps():
    # achado central desta fonte (ver docstring do módulo): a 1ª
    # rerratificação reduz a carga horária do Médico – CAPS de 40h pra
    # 20h, e a 2ª rerratificação (mais recente ainda) não toca a tabela
    # de cargos — o valor vigente tem que ser o da 1ª rerratificação.
    original, primeira, segunda = _textos_ps_001()
    funcoes = itamonte_mg.mesclar_versoes([original, primeira, segunda])

    assert len(funcoes) == 15  # nenhuma seção duplicada, nenhum cargo perdido

    caps = next(f for f in funcoes if f.cargo == "Médico – CAPS")
    assert caps.vagas == 1
    assert caps.carga_horaria == "20 Horas Semanais"
    assert caps.salario == Decimal("7964.38")

    esf = next(f for f in funcoes if f.cargo == "Médico - ESF")
    assert esf.vagas == 6
    assert esf.carga_horaria == "40 Horas Semanais"
    assert esf.salario == Decimal("17802.73")


def test_mesclar_versoes_mantem_secao_nao_reimpressa_pela_rerratificacao():
    # "Ensino Médio e/ou Técnico" só existe no edital original — nenhuma
    # rerratificação a reimprime, então tem que sobreviver ao merge.
    original, primeira, segunda = _textos_ps_001()
    funcoes = itamonte_mg.mesclar_versoes([original, primeira, segunda])
    tecnicos = {f.cargo for f in funcoes if "Técnico" in f.cargo or "Auxiliar" in f.cargo}
    assert tecnicos == {"Auxiliar de Saúde Bucal", "Técnico de Enfermagem – CAPS", "Técnico de Enfermagem – ESF"}


def test_mesclar_versoes_e_independente_da_ordem_de_entrada():
    original, primeira, segunda = _textos_ps_001()
    embaralhado = [segunda, original, primeira]
    funcoes = itamonte_mg.mesclar_versoes(embaralhado)
    caps = next(f for f in funcoes if f.cargo == "Médico – CAPS")
    assert caps.carga_horaria == "20 Horas Semanais"


def test_mesclar_versoes_lista_vazia_devolve_lista_vazia():
    assert itamonte_mg.mesclar_versoes([]) == []


# --- extrair_datas / mesclar_datas ------------------------------------------


def test_extrair_datas_do_edital_original():
    original, _primeira, _segunda = _textos_ps_001()
    datas = itamonte_mg.extrair_datas(original)
    assert datas.data_publicacao.isoformat() == "2026-08-14"
    assert datas.inscricoes_inicio.isoformat() == "2026-08-17"
    assert datas.inscricoes_fim.isoformat() == "2026-08-24"


def test_extrair_datas_da_2a_rerratificacao_so_tem_periodo_reaberto():
    _original, _primeira, segunda = _textos_ps_001()
    datas = itamonte_mg.extrair_datas(segunda)
    assert datas.data_publicacao is None
    assert datas.inscricoes_inicio.isoformat() == "2026-09-04"
    assert datas.inscricoes_fim.isoformat() == "2026-09-14"


def test_mesclar_datas_usa_publicacao_original_e_periodo_mais_recente():
    # achado real (ver docstring do módulo): a 2ª rerratificação reabre o
    # prazo de inscrição até 14/09/2026 — tem que vencer sobre o período
    # original (17 a 24/08/2026).
    original, primeira, segunda = _textos_ps_001()
    datas = itamonte_mg.mesclar_datas([original, primeira, segunda])
    assert datas.data_publicacao.isoformat() == "2026-08-14"
    assert datas.inscricoes_inicio.isoformat() == "2026-09-04"
    assert datas.inscricoes_fim.isoformat() == "2026-09-14"


def test_mesclar_datas_lista_vazia_devolve_tudo_none():
    datas = itamonte_mg.mesclar_datas([])
    assert datas.data_publicacao is None
    assert datas.inscricoes_inicio is None
    assert datas.inscricoes_fim is None


# --- normalizar_cargo / identificador_externo -------------------------------


def test_normalizar_cargo_ignora_acento_e_maiuscula():
    assert itamonte_mg.normalizar_cargo("Médico – CAPS") == itamonte_mg.normalizar_cargo("MÉDICO – CAPS")


def test_identificador_externo_estavel_e_sem_acento():
    ident = itamonte_mg.identificador_externo("001/2026", "Médico - ESF")
    assert ident == "itamonte-mg-001-2026-medico-esf"


def test_identificador_externo_sem_numero_de_processo_nao_quebra():
    ident = itamonte_mg.identificador_externo(None, "Médico - ESF")
    assert ident == "itamonte-mg-sem-numero-medico-esf"
