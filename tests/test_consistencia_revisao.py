from notifica_vagas_scraper import consistencia_revisao


def _vaga(id_, decisao, *, fonte_id="fonte-x", municipio_id=1, numero_edital="01/2026", orgao=None, cargo="Médico", municipio_nome="Bariri", municipio_uf="SP"):
    return {
        "id": id_,
        "cargo": cargo,
        "revisao_status": decisao,
        "fonte_id": fonte_id,
        "municipio_id": municipio_id,
        "numero_edital": numero_edital,
        "orgao": orgao,
        "municipio_nome": municipio_nome,
        "municipio_uf": municipio_uf,
    }


def test_achar_decisoes_divergentes_acha_vaga_fora_da_maioria_clara():
    # achado real (Bariri/SP, ver docs/auditoria_revisao_gemini_2026-09-11.md
    # no repo principal): 15 cargos médicos aprovados, 2 marcados incompleta
    # com o mesmo dado de entrada.
    vagas = [_vaga(f"v{i}", "aprovada") for i in range(15)] + [
        _vaga("v-incompleta-1", "incompleta"),
        _vaga("v-incompleta-2", "incompleta"),
    ]

    divergentes = consistencia_revisao.achar_decisoes_divergentes(vagas)

    ids = {v["id"] for v in divergentes}
    assert ids == {"v-incompleta-1", "v-incompleta-2"}
    assert all(v["decisao_majoritaria"] == "aprovada" for v in divergentes)
    assert divergentes[0]["contagem_grupo"] == {"aprovada": 15, "incompleta": 2}


def test_achar_decisoes_divergentes_ignora_grupo_pequeno_demais():
    # grupo com só 2 vagas (abaixo de TAMANHO_MINIMO_GRUPO) não tem
    # maioria confiável o bastante pra reavaliar.
    vagas = [_vaga("v1", "aprovada"), _vaga("v2", "rejeitada")]
    assert consistencia_revisao.achar_decisoes_divergentes(vagas) == []


def test_achar_decisoes_divergentes_ignora_empate():
    # 2 aprovadas, 2 rejeitadas — não dá pra saber qual lado é a
    # "maioria confiável", não reavalia nenhuma.
    vagas = [
        _vaga("v1", "aprovada"),
        _vaga("v2", "aprovada"),
        _vaga("v3", "rejeitada"),
        _vaga("v4", "rejeitada"),
    ]
    assert consistencia_revisao.achar_decisoes_divergentes(vagas) == []


def test_achar_decisoes_divergentes_nao_mistura_editais_diferentes():
    # mesmo numero_edital, mas fonte ou município diferente = documento
    # diferente na prática — não deve virar "irmã" de verdade.
    vagas = [
        _vaga("v1", "aprovada", municipio_id=1),
        _vaga("v2", "aprovada", municipio_id=1),
        _vaga("v3", "aprovada", municipio_id=1),
        _vaga("v4", "rejeitada", municipio_id=2),  # município diferente, mesmo edital "01/2026"
        _vaga("v5", "rejeitada", municipio_id=2),
    ]
    # grupo do município 2 só tem 2 vagas — abaixo do mínimo, não conta.
    assert consistencia_revisao.achar_decisoes_divergentes(vagas) == []


def test_achar_decisoes_divergentes_sem_grupo_nenhuma_vaga_diverge():
    vagas = [_vaga("v1", "aprovada"), _vaga("v2", "aprovada"), _vaga("v3", "aprovada")]
    assert consistencia_revisao.achar_decisoes_divergentes(vagas) == []


def test_achar_decisoes_divergentes_agrupa_por_orgao_quando_sem_numero_edital():
    # achado real (Vargem Grande Paulista/SP, checagem externa 2026-09-15,
    # TAREFAS.md): vaga do Vigia sem numero_edital não entrava no consenso
    # antes deste fallback — 2 rejeitadas, 1 aprovada, mesmo órgão.
    vagas = [
        _vaga("v-ortopedista", "aprovada", numero_edital=None, orgao="Prefeitura de Vargem Grande Paulista - SP"),
        _vaga("v-pediatra", "rejeitada", numero_edital=None, orgao="Prefeitura de Vargem Grande Paulista - SP"),
        _vaga("v-psiquiatra-infantil", "rejeitada", numero_edital=None, orgao="Prefeitura de Vargem Grande Paulista - SP"),
    ]

    divergentes = consistencia_revisao.achar_decisoes_divergentes(vagas)

    ids = {v["id"] for v in divergentes}
    assert ids == {"v-ortopedista"}
    assert divergentes[0]["decisao_majoritaria"] == "rejeitada"


def test_achar_decisoes_divergentes_nao_junta_vaga_sem_edital_nem_orgao():
    # sem numero_edital E sem orgao preenchido: cada vaga fica isolada
    # (chave única por id) — nunca deveria formar grupo, mesmo com 3+
    # vagas idênticas na mesma fonte/município.
    vagas = [
        _vaga(f"v{i}", "aprovada" if i < 2 else "rejeitada", numero_edital=None, orgao=None)
        for i in range(3)
    ]
    assert consistencia_revisao.achar_decisoes_divergentes(vagas) == []


def test_achar_decisoes_divergentes_nao_mistura_orgaos_diferentes_sem_edital():
    vagas = [
        _vaga("v1", "aprovada", numero_edital=None, orgao="Prefeitura de A"),
        _vaga("v2", "aprovada", numero_edital=None, orgao="Prefeitura de A"),
        _vaga("v3", "rejeitada", numero_edital=None, orgao="Prefeitura de B"),  # órgão diferente
        _vaga("v4", "rejeitada", numero_edital=None, orgao="Prefeitura de B"),
    ]
    # cada grupo (A e B) só tem 2 vagas — abaixo do mínimo, não conta.
    assert consistencia_revisao.achar_decisoes_divergentes(vagas) == []


def test_montar_contexto_irmas_usa_orgao_quando_sem_numero_edital():
    vaga = {
        **_vaga("v-pediatra", "rejeitada", numero_edital=None, orgao="Prefeitura de Vargem Grande Paulista - SP"),
        "decisao_majoritaria": "aprovada",
        "contagem_grupo": {"aprovada": 1, "rejeitada": 2},
    }
    contexto = consistencia_revisao.montar_contexto_irmas(vaga)
    assert "órgão Prefeitura de Vargem Grande Paulista - SP" in contexto
    assert "Bariri/SP" in contexto


def test_montar_contexto_irmas_nunca_inclui_motivo_literal_de_vaga_irma():
    # achado real da auditoria de 2026-09-19
    # (docs/auditoria_revisao_gemini_2026-09-19.md no repo principal):
    # "Médico - Medicina de Emergência (24 Horas)" foi rejeitada citando
    # "processo de seleção para Residência Médica" — motivo de uma vaga
    # irmã COMPLETAMENTE diferente do mesmo edital PBH 01/2025
    # ("Medicina de Emergência (12 Horas)", rejeitada por "período de
    # inscrições expirado"). O contexto de irmãs só pode carregar a
    # CONTAGEM estatística por decisão — nunca o texto de `revisao_motivo`
    # de nenhuma vaga, mesmo que ele esteja presente no dict da vaga (a
    # função tem que ignorá-lo por completo).
    motivo_vaga_irma_nao_relacionada = "processo de seleção para Residência Médica"
    vaga = {
        **_vaga("v-medicina-emergencia-24h", "rejeitada", cargo="Médico - Medicina de Emergência (24 Horas)"),
        "revisao_motivo": motivo_vaga_irma_nao_relacionada,  # motivo da PRÓPRIA vaga, não deve vazar tampouco
        "decisao_majoritaria": "aprovada",
        "contagem_grupo": {"aprovada": 10, "rejeitada": 1},
    }

    contexto = consistencia_revisao.montar_contexto_irmas(vaga)

    assert motivo_vaga_irma_nao_relacionada not in contexto
    assert "Residência Médica" not in contexto
    assert "residência médica" not in contexto.lower()


def test_montar_contexto_irmas_descreve_consenso():
    vaga = {
        **_vaga("v-incompleta-1", "incompleta"),
        "decisao_majoritaria": "aprovada",
        "contagem_grupo": {"aprovada": 15, "incompleta": 2},
    }
    contexto = consistencia_revisao.montar_contexto_irmas(vaga)
    assert "01/2026" in contexto
    assert "Bariri/SP" in contexto
    assert "15 aprovada" in contexto
    assert "2 incompleta" in contexto
    assert "aprovada" in contexto.split("maioria foi decidida como")[1]
