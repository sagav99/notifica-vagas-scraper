import json
from pathlib import Path

from notifica_vagas_scraper.fontes import instar

FIXTURES = Path(__file__).parent / "fixtures" / "instar"


def _carregar_fixture(nome: str) -> dict:
    with (FIXTURES / nome).open(encoding="utf-8") as f:
        return json.load(f)


def test_listar_municipios_instar_le_csv_real():
    municipios = instar.listar_municipios_instar()
    assert len(municipios) > 0
    assert all(m.uf in ("MG", "SP") for m in municipios)
    assert all(m.url_prefeitura.startswith("http") for m in municipios)


def test_url_dados_abertos():
    assert (
        instar.url_dados_abertos("https://www.araujos.mg.gov.br/", 2026)
        == "https://www.araujos.mg.gov.br/portal/dados-abertos/concursos/2026"
    )
    assert (
        instar.url_dados_abertos("https://www.araujos.mg.gov.br", 2026)
        == "https://www.araujos.mg.gov.br/portal/dados-abertos/concursos/2026"
    )


def test_listar_itens_abertos_filtra_situacao():
    payload = _carregar_fixture("araujos_mg_2026.json")
    abertos = instar.listar_itens_abertos(payload)
    assert len(abertos) > 0
    assert all(item["situacao"].lower() == "aberto" for item in abertos)
    # Achado real: item "Aberto" mas que não é vaga de verdade (eleição de
    # conselho tutelar) — a filtragem por situacao não resolve isso sozinha,
    # fica pro Gemini (gemini_texto.py) devolver vagas: [] pra esse caso.
    titulos = [item["titulo"] for item in abertos]
    assert any("CONSELHO TUTELAR" in t.upper() for t in titulos)


def test_listar_itens_abertos_sem_registro_nao_quebra():
    payload = _carregar_fixture("barbacena_mg_2026_sem_registro.json")
    assert instar.listar_itens_abertos(payload) == []


def test_listar_itens_abertos_ignora_concluido():
    payload = _carregar_fixture("buritis_mg_2026.json")
    abertos = instar.listar_itens_abertos(payload)
    assert all(item["situacao"].lower() != "concluído" for item in abertos)
