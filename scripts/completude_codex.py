#!/usr/bin/env python3
"""Entrypoint LOCAL (não roda em GitHub Actions) pra completar vaga
médica aprovada com dado faltando, usando o Codex CLI (`codex exec`,
cota da assinatura ChatGPT do usuário) — complementa
`auditar_completude_vagas.py` (Gemini + Serper), não substitui: essa
camada pesquisa na internet de verdade, navegando até achar o documento
certo, não só relendo o link já salvo.

Uso (rodar da raiz do repo scraper, com a venv ativada):
    python scripts/completude_codex.py [--limite N]

Requer:
- DATABASE_URL no ambiente (connection string do Postgres/Supabase).
- `codex` no PATH, já autenticado (`codex login`) — verifica no início
  e sai com erro claro se não estiver.

Pensado pra rodar manualmente 1x por dia (ou quantas vezes o usuário
quiser) até a cota da assinatura Codex esgotar — cada vaga custa ~2min
e ~40-50k tokens de cota (bem mais caro por chamada que Gemini/Claude),
por isso o limite padrão por execução é pequeno.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from notifica_vagas_scraper import completude_codex, db

LIMITE_PADRAO_POR_EXECUCAO = 15


def _checar_codex_disponivel() -> None:
    if shutil.which("codex") is None:
        print("ERRO: `codex` não encontrado no PATH. Instale/rode `codex login` antes de usar este script.")
        sys.exit(1)
    resultado = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=15)
    # achado 2026-09-16: `codex login status` escreve em stderr, não stdout.
    saida = resultado.stdout + resultado.stderr
    if "Logged in" not in saida:
        print(f"ERRO: Codex CLI não está autenticado ({saida.strip()}). Rode `codex login` primeiro.")
        sys.exit(1)


def main() -> None:
    limite = LIMITE_PADRAO_POR_EXECUCAO
    if "--limite" in sys.argv:
        limite = int(sys.argv[sys.argv.index("--limite") + 1])

    _checar_codex_disponivel()

    conn = db.conectar()
    campo_preenchido = 0
    sem_alteracao = 0
    erro = 0

    try:
        candidatas = db.listar_vagas_medicas_incompletas(conn, minimo_campos_faltando=1, limite=limite)
        print(f"{len(candidatas)} vaga(s) médica(s) aprovada(s) com dado incompleto (candidatas desta execução).")

        for indice, vaga in enumerate(candidatas, start=1):
            local = f"{vaga['nome']}/{vaga['uf']}"
            campos = completude_codex.campos_faltando(vaga)
            print(f"[{indice}/{len(candidatas)}] {vaga['cargo']} ({local}) — faltando: {campos}")

            resultado = completude_codex.processar_vaga(conn, vaga)
            conn.commit()  # commit por vaga: progresso sobrevive se a cota do Codex acabar no meio

            if resultado == "campo_preenchido":
                campo_preenchido += 1
                print("  -> campo(s) preenchido(s)")
            elif resultado == "erro_codex":
                erro += 1
                print("  -> erro ao chamar o Codex, pulando")
            else:
                sem_alteracao += 1
                print("  -> sem alteração (confiança baixa ou nada confirmado)")

    finally:
        conn.close()

    print(
        f"\nResumo: {campo_preenchido} vaga(s) com campo preenchido, "
        f"{sem_alteracao} sem alteração, {erro} erro(s)."
    )


if __name__ == "__main__":
    with db.rastrear_execucao("completude_codex.py"):
        main()
