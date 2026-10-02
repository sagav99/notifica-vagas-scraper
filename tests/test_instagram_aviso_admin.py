from notifica_vagas_scraper.instagram.aviso_admin import link_divulgacao, montar_email


def test_link_tem_codigo_estavel_e_depende_do_segredo():
    a = link_divulgacao("abc", "s1")
    assert a == link_divulgacao("abc", "s1") and a != link_divulgacao("abc", "s2")
    assert a.startswith("https://medvagasapp.com.br/v/abc?d=") and "=" not in a.split("d=")[1]


def test_email_traz_vaga_e_link():
    assunto, corpo = montar_email({"cargo": "Médico", "municipio": "Leopoldina", "uf": "MG", "orgao": "Prefeitura"}, "https://x/v/1?d=z")
    assert "Leopoldina/MG" in assunto and "https://x/v/1?d=z" in corpo
