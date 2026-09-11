from notifica_vagas_scraper import busca_pagina_pdf


def test_encontra_pagina_unica():
    paginas = ["Edital de abertura.", "Anexo I - Médico Pediatra - 2 vagas", "Anexo II - requisitos gerais"]
    assert busca_pagina_pdf.localizar_pagina_por_texto(paginas, "Médico Pediatra") == 2


def test_ignora_acentos_e_maiusculas():
    paginas = ["MEDICO PEDIATRA - vagas: 2"]
    assert busca_pagina_pdf.localizar_pagina_por_texto(paginas, "Médico Pediatra") == 1


def test_ambiguo_em_mais_de_uma_pagina_devolve_none():
    # mesmo padrão real de pbh_ibfc.py: mesma especialidade em jornadas
    # diferentes, cada uma em página própria -- não dá pra escolher sem IA.
    paginas = ["Médico Pediatra - 12h - 2 vagas", "Médico Pediatra - 24h - 3 vagas"]
    assert busca_pagina_pdf.localizar_pagina_por_texto(paginas, "Médico Pediatra") is None


def test_nao_encontrado_devolve_none():
    paginas = ["Enfermeiro - 5 vagas", "Técnico de Enfermagem - 3 vagas"]
    assert busca_pagina_pdf.localizar_pagina_por_texto(paginas, "Médico Pediatra") is None


def test_pagina_sem_texto_extraivel_ignorada():
    # PDF escaneado -- pdfplumber devolve None pra página sem texto.
    paginas = [None, "Médico Pediatra - 2 vagas", None]
    assert busca_pagina_pdf.localizar_pagina_por_texto(paginas, "Médico Pediatra") == 2


def test_cargo_vazio_devolve_none():
    assert busca_pagina_pdf.localizar_pagina_por_texto(["qualquer texto"], "") is None
