import medir_cobertura_municipios as medir


def test_uniao_dedup_por_codigo_ibge():
    # Achado real (2026-09-02, TAREFAS.md): somar o total de cada fonte
    # superestima a cobertura porque o mesmo município pode aparecer em
    # mais de uma fonte (ex: Confins/MG está em AMM-MG e Instar) -- a
    # união precisa contar Confins uma vez só.
    fonte_a = {(3118601, "Contagem", "MG"), (3106200, "Confins", "MG")}
    fonte_b = {(3106200, "Confins", "MG"), (3550308, "São Paulo", "SP")}

    uniao = fonte_a | fonte_b

    assert len(uniao) == 3
    assert (3106200, "Confins", "MG") in uniao


def test_carregar_csv_usa_codigo_ibge_como_chave():
    linhas = medir.carregar_csv(medir.FONTES_CSV["Actcon"])
    assert all(isinstance(codigo, int) for codigo, _, _ in linhas)
    assert len(linhas) > 0
