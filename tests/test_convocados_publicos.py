from notifica_vagas_scraper.fontes import convocados_publicos as cp

VAGA = cp.VagaAlvo("v1", "MG", "Itajubá", "Médico PSF", "01/2025")

LISTA = """PREFEITURA MUNICIPAL DE ITAJUBÁ CONVOCAÇÃO Nº 12/2026
O Prefeito Municipal convoca os candidatos aprovados no Concurso Público nº 01/2025, para o cargo de Médico PSF,
para apresentarem documentos no prazo de 5 dias úteis:
1º - MARIA DA SILVA SANTOS CPF ***.123.456-** 
2º - JOSÉ AUGUSTO PEREIRA CPF ***.987.654-**
Itajubá, 03 de novembro de 2026. JOÃO DA COSTA Prefeito Municipal"""


def test_extrai_nomes_cpf_e_classificacao():
    r = cp.extrair_convocados(LISTA)
    nomes = {c.nome: c for c in r}
    assert "Maria da Silva Santos" in nomes and "José Augusto Pereira" in nomes
    assert nomes["Maria da Silva Santos"].classificacao == 1
    assert nomes["Maria da Silva Santos"].cpf_publicado == "***.123.456-**"


def test_assinatura_do_prefeito_nao_entra():
    assert all("Costa" not in c.nome for c in cp.extrair_convocados(LISTA))


def test_cabecalho_nao_vira_nome():
    nomes = [c.nome for c in cp.extrair_convocados(LISTA)]
    assert not any("Prefeitura" in n or "Convoca" in n for n in nomes)


def test_cargo_nao_medico_e_descartado():
    texto = "cargo de Enfermeiro:\n1º - ANA PAULA LIMA CPF ***.111.222-**"
    assert cp.extrair_convocados(texto) == []


def test_lista_colada_por_numero():
    texto = "cargo de Médico 86 JOSIANE RIBEIRO DE ALMEIDA 87 MARIA DA SILVA SANTOS 89 PEDRO LIMA"
    nomes = [c.nome for c in cp.extrair_convocados(texto)]
    assert "Josiane Ribeiro de Almeida" in nomes and "Pedro Lima" in nomes


def test_casar_vaga_por_numero_e_municipio():
    v = cp.casar_vaga([VAGA], uf="MG", entidade="Prefeitura de Itajubá", titulo="Convocação 12/2026", texto=LISTA)
    assert v is VAGA


def test_outro_municipio_nao_casa():
    assert cp.casar_vaga([VAGA], uf="MG", entidade="Prefeitura de Lavras", titulo="Convocação", texto=LISTA) is None


def test_edital_diferente_nao_casa():
    texto = LISTA.replace("01/2025", "07/2024")
    assert cp.casar_vaga([VAGA], uf="MG", entidade="Prefeitura de Itajubá", titulo="Convocação", texto=texto) is None
