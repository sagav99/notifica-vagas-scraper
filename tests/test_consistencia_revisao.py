from notifica_vagas_scraper import consistencia_revisao


def _vaga(id_, decisao, *, fonte_id="fonte-x", municipio_id=1, numero_edital="01/2026", cargo="Médico", municipio_nome="Bariri", municipio_uf="SP"):
    return {
        "id": id_,
        "cargo": cargo,
        "revisao_status": decisao,
        "fonte_id": fonte_id,
        "municipio_id": municipio_id,
        "numero_edital": numero_edital,
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
