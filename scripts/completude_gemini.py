#!/usr/bin/env python3
"""Entrypoint pra completar vaga médica aprovada com dado faltando E
conferir a natureza da vaga (é concurso/PSS de emprego, ou residência
médica/fellowship/estágio disfarçado de vaga?), usando a API do Gemini
com as tools nativas `google_search`+`url_context` (busca real na
internet e leitura de página) — substitui o uso normal de
`completude_codex.py` (pedido do usuário, 2026-09-18: pausar o Codex,
cota da assinatura ChatGPT esgotando rápido) sem perder a capacidade de
pesquisar/navegar de verdade, e com a ideia explícita de no futuro trocar
só o cliente do modelo pela API do Claude.

Uso (rodar da raiz do repo scraper, com a venv ativada):
    python scripts/completude_gemini.py

Requer:
- DATABASE_URL e GEMINI_API_KEY no ambiente.

Compartilha a cota diária do Gemini com `gemini_pdf`/`gemini_texto`/
`revisao_ia` (mesmo `quota_gemini.proximo_modelo()`) — por isso roda em
lote pequeno e para (não em loop contínuo como `completude_codex.py`,
que tinha cota própria da assinatura ChatGPT). Rode várias vezes ao dia
(cron ou manual) em vez de deixar rodando.

`CotaGeminiEsgotadaError` (HTTP 429) interrompe o lote inteiro na hora —
mesmo critério de `revisar_vagas.py`/`auditar_completude_vagas.py`, nunca
insiste depois que a cota diária acabou.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from notifica_vagas_scraper import completude_gemini, db
from notifica_vagas_scraper.revisao_ia import CotaGeminiEsgotadaError

TAMANHO_LOTE = 15


def main() -> None:
    conn = db.conectar()
    campo_preenchido = 0
    rejeitada_nao_e_concurso = 0
    sem_alteracao = 0
    erro = 0

    try:
        candidatas = db.listar_vagas_medicas_para_completude_gemini(conn, limite=TAMANHO_LOTE)
        print(f"{len(candidatas)} vaga(s) médica(s) aprovada(s) nesta execução.")

        for vaga in candidatas:
            local = f"{vaga['nome']}/{vaga['uf']}"
            campos = completude_gemini.campos_faltando(vaga)
            print(f"{vaga['cargo']} ({local}) — faltando: {campos or '(nada, só confere natureza)'}")

            try:
                resultado = completude_gemini.processar_vaga(conn, vaga)
                conn.commit()
            except CotaGeminiEsgotadaError as exc:
                conn.rollback()
                print(f"  -> cota do Gemini esgotada ({exc}). Encerrando o lote aqui.")
                break
            except Exception as exc:
                conn.rollback()
                print(f"  -> erro inesperado, pulando: {exc!r}")
                erro += 1
                continue

            if resultado == "campo_preenchido":
                campo_preenchido += 1
                print("  -> campo(s) preenchido(s) e/ou link do PDF do edital adicionado")
            elif resultado == "rejeitada_nao_e_concurso":
                rejeitada_nao_e_concurso += 1
                print("  -> NÃO é vaga de emprego (residência/fellowship/estágio) — revertida pra rejeitada")
            elif resultado == "erro_gemini":
                erro += 1
                print("  -> erro ao chamar o Gemini, pulando")
            else:
                sem_alteracao += 1
                print("  -> sem alteração (confiança baixa ou nada novo confirmado)")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print(
        f"\nEncerrado. {campo_preenchido} vaga(s) com campo/link preenchido, "
        f"{rejeitada_nao_e_concurso} revertida(s) por não ser vaga de emprego, "
        f"{sem_alteracao} sem alteração, {erro} erro(s)."
    )


if __name__ == "__main__":
    with db.rastrear_execucao("completude_gemini.py"):
        main()
