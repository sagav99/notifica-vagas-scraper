from datetime import date
from pathlib import Path

import rodar_proseleta_sul as script
from notifica_vagas_scraper.classificar_medico import eh_cargo_medico
from notifica_vagas_scraper.fontes import proseleta
from notifica_vagas_scraper.fontes.proseleta import Documento, TENANTS

FIXTURES = Path(__file__).parent / "fixtures" / "proseleta_sul"


def _ler(tenant: str, nome: str) -> str:
    return proseleta.decodificar((FIXTURES / tenant / nome).read_bytes())


def test_decodificar_cai_para_latin1():
    assert proseleta.decodificar("Saúde".encode("latin-1")) == "Saúde"
    assert proseleta.decodificar("Saúde".encode("utf-8")) == "Saúde"


def test_listagem_furb_municipio_orgao_uf_padrao_e_numero():
    itens = proseleta.listar_processos_abertos(_ler("furb", "abertos.html"), "furb", em_abertos=True)
    assert len(itens) == 7
    itapema = next(i for i in itens if i.processo_id == 577)
    assert itapema.municipio == "Itapema"
    assert itapema.uf == "SC"
    assert itapema.orgao == "Prefeitura Municipal de Itapema"
    assert itapema.numero_edital == "010/2026"
    assert itapema.tipo == "Processo Seletivo"
    assert itapema.inscricoes_inicio == date(2026, 10, 20)
    assert itapema.inscricoes_fim == date(2026, 11, 4)
    assert itapema.qtde_total == 27
    assert itapema.url == "https://concursos.furb.br/informacoes/577/"
    maravilha = next(i for i in itens if i.processo_id == 564)
    assert (maravilha.municipio, maravilha.uf) == ("Maravilha", "SC")
    assert next(i for i in itens if i.processo_id == 578).orgao == "Câmara Municipal de Pomerode"


def test_listagem_objetiva_municipio_uf_do_titulo():
    itens = proseleta.listar_processos_abertos(_ler("objetiva", "abertos.html"), "objetiva", em_abertos=True)
    por_id = {i.processo_id: i for i in itens}
    assert len(itens) == 7
    assert (por_id[2628].municipio, por_id[2628].uf) == ("Novo Cabrais", "RS")
    assert por_id[2628].orgao == "Prefeitura Municipal de Novo Cabrais"
    assert por_id[2628].numero_edital == "01/2026"
    assert (por_id[2629].municipio, por_id[2629].uf) == ("Ipê", "RS")
    assert (por_id[2624].municipio, por_id[2624].uf) == ("Ivaí", "PR")
    assert (por_id[2623].municipio, por_id[2623].uf) == ("Chapecó", "SC")
    assert (por_id[2627].municipio) == "Tio Hugo"


def test_listagem_objetiva_particulas_camara_e_entidade_sem_uf():
    itens = proseleta.listar_processos_abertos(_ler("objetiva", "andamento.html"), "objetiva")
    por_id = {i.processo_id: i for i in itens}
    assert por_id[2602].municipio == "Conceição do Almeida"
    assert por_id[2617].orgao == "Câmara Municipal de Guaporé"
    assert por_id[2604].municipio == "Porto Alegre"  # "EPTC - PORTO ALEGRE/RS"
    assert por_id[2531].municipio is None and por_id[2531].uf is None  # TRENSURB, sem /UF
    assert por_id[2531].inscricoes_fim is None


def test_listagem_univali_uf_padrao_sc_e_item_sem_datas():
    itens = proseleta.listar_processos_abertos(_ler("univali", "abertos.html"), "univali", em_abertos=True)
    por_id = {i.processo_id: i for i in itens}
    assert len(itens) == 3
    assert (por_id[47].municipio, por_id[47].uf) == ("Luiz Alves", "SC")
    assert por_id[47].numero_edital == "08/2026"
    assert por_id[47].qtde_total == 63
    assert por_id[45].municipio == "Itajaí"
    assert por_id[45].inscricoes_inicio is None and por_id[45].inscricoes_fim is None


def test_mesclar_listagens_dedup_por_processo_id():
    a = proseleta.listar_processos_abertos(_ler("objetiva", "abertos.html"), "objetiva", em_abertos=True)
    b = proseleta.listar_processos_abertos(_ler("objetiva", "andamento.html"), "objetiva")
    mesclado = proseleta.mesclar_listagens(a, b)
    ids = [i.processo_id for i in mesclado]
    assert len(ids) == len(set(ids))
    assert 2622 in ids  # Novo Xingu: só aparece em andamento (abre 30/10)
    assert next(i for i in mesclado if i.processo_id == 2628).em_abertos is True
    assert next(i for i in mesclado if i.processo_id == 2622).em_abertos is False


def test_deve_processar_filtra_por_fim_das_inscricoes():
    itens = proseleta.listar_processos_abertos(_ler("objetiva", "andamento.html"), "objetiva")
    por_id = {i.processo_id: i for i in itens}
    hoje = date(2026, 10, 27)
    assert script.deve_processar(por_id[2622], hoje)  # abre no futuro
    assert script.deve_processar(por_id[2628], hoje)
    assert not script.deve_processar(por_id[2620], hoje)  # fechou 19/10
    assert not script.deve_processar(por_id[2531], hoje)  # sem datas e fora de "abertos"


def test_tabela_furb_itapema_mapeia_colunas_e_acha_7_medicos():
    vagas = proseleta.listar_vagas_tabela(_ler("furb", "detalhe_itapema_577.html"))
    assert len(vagas) == 23
    medicas = [v for v in vagas if eh_cargo_medico(v.cargo)]
    assert len(medicas) == 7  # relatório: Itapema 7 médicos
    motorista = next(v for v in vagas if v.cargo == "MOTORISTA")
    assert motorista.codigo == "991050"
    assert motorista.escolaridade == "Fundamental"
    assert str(motorista.salario) == "2229.54"
    assert str(motorista.taxa_inscricao) == "40.00"
    assert motorista.carga_horaria == "44h"
    assert motorista.quantidade == 1 and motorista.cadastro_reserva
    fono = next(v for v in vagas if v.cargo == "FONOAUDIÓLOGO")
    assert fono.quantidade is None and fono.cadastro_reserva


def test_tabela_furb_navegantes_73_cargos_nenhum_medico_descartado():
    vagas = proseleta.listar_vagas_tabela(_ler("furb", "detalhe_navegantes_579.html"))
    assert len(vagas) == 73
    medicas = [v for v in vagas if eh_cargo_medico(v.cargo)]
    # relatório: 19 médicos no edital; 17 passam no filtro e 3 (Regulador,
    # Mastologista, Infectologista 20h) já eram rejeitados no banco -- aqui
    # o parser entrega TODAS as linhas, o filtro é só eh_cargo_medico.
    assert len(medicas) == 19  # Regulador, Mastologista e Infectologista incluídos
    nomes = {_n.cargo.lower() for _n in medicas}
    assert any("mastolog" in n for n in nomes)
    assert any("infectolog" in n for n in nomes)
    assert all(v.salario is not None for v in medicas)


def test_tabela_objetiva_sem_salario_nem_qtde():
    vagas = proseleta.listar_vagas_tabela(_ler("objetiva", "detalhe_novo_cabrais_2628.html"))
    assert vagas
    assert all(v.salario is None and v.quantidade is None for v in vagas)
    medicas = [v.cargo for v in vagas if eh_cargo_medico(v.cargo)]
    assert len(medicas) == 2  # Médico do PSF e Pediatra
    assert all(v.taxa_inscricao is not None for v in vagas)


def test_tabela_objetiva_ipe_4_medicos():
    vagas = proseleta.listar_vagas_tabela(_ler("objetiva", "detalhe_ipe_2629.html"))
    assert len([v for v in vagas if eh_cargo_medico(v.cargo)]) == 4


def test_tabela_univali_luiz_alves_4_medicos_com_vencimentos():
    vagas = proseleta.listar_vagas_tabela(_ler("univali", "detalhe_luiz_alves_47.html"))
    medicas = [v for v in vagas if eh_cargo_medico(v.cargo)]
    nomes = " | ".join(v.cargo for v in medicas).lower()
    assert "ginecolog" in nomes and "pediatr" in nomes
    assert len(medicas) == 4  # Auditor, Saúde Mental, Ginecologista/Obstetra, Pediatra
    assert all(v.salario is not None for v in medicas)


def test_tabela_univali_itajai_act_sem_coluna_salario():
    vagas = proseleta.listar_vagas_tabela(_ler("univali", "detalhe_itajai_act_45_sem_datas.html"))
    assert vagas and all(v.salario is None for v in vagas)


def _doc(titulo, dia):
    return Documento(titulo=titulo, data=date(2026, 10, dia), url_pdf=f"https://x/{dia}.pdf")


def test_escolher_edital_sul_nos_detalhes_reais():
    itapema = proseleta.escolher_edital_sul(proseleta.listar_documentos(_ler("furb", "detalhe_itapema_577.html")))
    assert itapema.titulo.lower().startswith("edital n.º 010/2026")  # consolidado "COM ERRATA", não a ERRATA 01/02
    navegantes = proseleta.escolher_edital_sul(proseleta.listar_documentos(_ler("furb", "detalhe_navegantes_579.html")))
    assert navegantes.titulo.upper().startswith("EDITAL N.º 0172/2026")  # não a resposta à impugnação
    ipe = proseleta.escolher_edital_sul(proseleta.listar_documentos(_ler("objetiva", "detalhe_ipe_2629.html")))
    assert "abertura" in ipe.titulo.lower()  # não o Edital 01-A
    luiz = proseleta.escolher_edital_sul(proseleta.listar_documentos(_ler("univali", "detalhe_luiz_alves_47.html")))
    assert luiz.titulo == "Edital nº 08/2026 - Concurso Público"
    itajai = proseleta.escolher_edital_sul(proseleta.listar_documentos(_ler("univali", "detalhe_itajai_act_45_sem_datas.html")))
    assert itajai.titulo.startswith("Edital nº 076-2026")  # não "Resultado dos Pedidos de Isenção"


def test_escolher_edital_sul_exclusoes_sinteticas():
    docs = [
        _doc("Edital n.º 01/2026 - Abertura", 10),
        _doc("Retificação do Edital n.º 01/2026", 12),
        _doc("Resposta às solicitações de impugnação do edital", 20),
        _doc("Resultado definitivo de isenção - Edital 01/2026", 21),
        _doc("Edital de convocação", 22),
    ]
    assert proseleta.escolher_edital_sul(docs).titulo == "Edital n.º 01/2026 - Abertura"


def test_escolher_edital_sul_fallback_generico():
    docs = [_doc("Cronograma", 10), _doc("Regulamento do concurso", 11)]
    assert proseleta.escolher_edital_sul(docs) is not None


def test_escolher_edital_original_nao_mudou():
    docs = [_doc("Edital 1", 10), _doc("Edital de convocação", 11), _doc("Retificação do edital", 12)]
    assert proseleta.escolher_edital(docs).titulo == "Retificação do edital"


def test_tipo_oportunidade_e_identificador():
    furb = proseleta.listar_processos_abertos(_ler("furb", "abertos.html"), "furb")
    assert script.tipo_oportunidade(next(i for i in furb if i.processo_id == 577)) == "processo_seletivo_temporario"
    assert script.tipo_oportunidade(next(i for i in furb if i.processo_id == 564)) == "concurso_efetivo"
    assert script.identificador_externo(TENANTS["furb"], 577, "991050", "X") == "proseleta-furb-577-991050"


def test_processar_processo_dry_run_nao_escreve_e_cobre_todo_medico(monkeypatch):
    html = _ler("univali", "detalhe_luiz_alves_47.html")
    monkeypatch.setattr(script, "_baixar", lambda url: html)
    monkeypatch.setattr(script.time, "sleep", lambda s: None)
    monkeypatch.setattr(script, "_resolver_codigo_ibge", lambda conn, m, uf: 4209409)
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda *a, **k: (_ for _ in ()).throw(AssertionError("escreveu")))
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: (_ for _ in ()).throw(AssertionError("escreveu")))
    item = proseleta.listar_processos_abertos(_ler("univali", "abertos.html"), "univali", em_abertos=True)[0]
    esperado = len([v for v in proseleta.listar_vagas_tabela(html) if eh_cargo_medico(v.cargo)])
    assert script.processar_processo(None, None, TENANTS["univali"], item, set(), dry_run=True) == esperado


def test_processar_processo_grava_cada_medico_e_pula_ja_processados(monkeypatch):
    html = _ler("objetiva", "detalhe_novo_cabrais_2628.html")
    monkeypatch.setattr(script, "_baixar", lambda url: html)
    monkeypatch.setattr(script.time, "sleep", lambda s: None)
    monkeypatch.setattr(script, "_resolver_codigo_ibge", lambda conn, m, uf: 4312252)
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)
    gravadas = []

    def _fake_inserir(conn, **kw):
        gravadas.append(kw)
        return {"vaga_id": "v", "evidencia_id": "e"}

    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)
    item = next(
        i
        for i in proseleta.listar_processos_abertos(_ler("objetiva", "abertos.html"), "objetiva", em_abertos=True)
        if i.processo_id == 2628
    )
    assert script.processar_processo(None, "f", TENANTS["objetiva"], item, set()) == 2
    assert len(gravadas) == 2
    assert all(g["municipio_id"] == 4312252 and g["tipo_documento"] == "pdf" for g in gravadas)
    assert all(g["url_evidencia"].endswith(".pdf") for g in gravadas)
    assert all(g["salario"] is None and g["salario_tipo"] is None for g in gravadas)
    assert gravadas[0]["numero_edital"] == "01/2026"

    ja = {g["identificador_externo"] for g in gravadas}
    gravadas.clear()
    assert script.processar_processo(None, "f", TENANTS["objetiva"], item, ja) == 0
    assert gravadas == []
