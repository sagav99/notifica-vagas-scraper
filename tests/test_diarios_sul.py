import io
import json
import zipfile

from notifica_vagas_scraper.fontes import diarios_sul as d


def test_candidata_abertura_com_cargo_medico_e_contexto_de_vaga():
    texto = "A Prefeitura abre inscrições do processo seletivo. Cargo: Médico Clínico Geral, 20h semanais, R$ 11.612,00."
    assert d.eh_candidata("EDITAL DE PROCESSO SELETIVO Nº 01/2026", texto, "Edital")


def test_nao_candidata_convocacao_ou_exame_admissional_ou_veterinario():
    assert not d.eh_candidata("CONVOCAÇÃO Nº 3", "processo seletivo médico 20h", "Edital")
    assert not d.eh_candidata("C O N V O C A Ç Ã O 11/2026", "processo seletivo médico 20h", "Outras")
    assert not d.eh_candidata("EDITAL PSS 01/2026", "inscrições; exame médico admissional, 40 horas", "Edital")
    assert not d.eh_candidata("EDITAL PSS 01/2026", "inscrições para Médico Veterinário 40h R$ 5.000", "Edital")
    assert not d.eh_candidata("EDITAL DE CHAMAMENTO Nº 66/2026 DO CONCURSO PÚBLICO Nº 001/2025", "médico 20h", "Edital")
    assert not d.eh_candidata("DECRETO 10/2026", "processo seletivo médico 20 horas", "Decretos")


def test_categoria_descartada_nao_e_candidata():
    assert not d.eh_candidata("ATA", "processo seletivo médico 20h R$ 1", "Ata de registro de preços")


def test_retificado_continua_candidata():
    texto = "Retificação do edital de concurso público. Médico Clínico Geral ESF, 40h, R$ 15.000."
    assert d.eh_candidata("EDITAL DE CONCURSO PÚBLICO Nº 01/2026 - RETIFICADO", texto, "Concursos")


def test_nome_municipio_da_entidade():
    assert d.nome_municipio_da_entidade("Prefeitura Municipal de Palmeira") == "Palmeira"
    assert d.nome_municipio_da_entidade("Prefeitura Municipal de Monte Alegre dos Campos") == "Monte Alegre dos Campos"
    assert d.nome_municipio_da_entidade("Consórcio Intermunicipal de Saúde") is None
    assert d.nome_municipio_da_entidade("Câmara Municipal de Erechim") is None


def test_ler_zip_sc_aceita_json_nulo_e_publicacoes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.json", json.dumps({"autopublicacoes": None}))
        zf.writestr(
            "b.json",
            json.dumps({"autopublicacoes": [{"codigo": 1, "titulo": "T", "municipio": "Ilhota", "categoria": "Concursos", "texto": "<p>oi</p>"}]}),
        )
    pubs = d.ler_zip_sc(buf.getvalue())
    assert len(pubs) == 1 and pubs[0].municipio == "Ilhota" and pubs[0].codigo == "1"
