#!/usr/bin/env python3
"""Entrypoint LOCAL (não roda em GitHub Actions) pra completar vaga
médica aprovada com dado faltando, usando o Codex CLI (`codex exec`,
cota da assinatura ChatGPT do usuário) — complementa
`auditar_completude_vagas.py` (Gemini + Serper), não substitui: essa
camada pesquisa na internet de verdade, navegando até achar o documento
certo, não só relendo o link já salvo.

Uso (rodar da raiz do repo scraper, com a venv ativada):
    python scripts/completude_codex.py

Requer:
- DATABASE_URL no ambiente (connection string do Postgres/Supabase).
- `codex` no PATH, já autenticado (`codex login`) — verifica no início
  e sai com erro claro se não estiver.

**Roda em loop contínuo, sem parar sozinho** (pedido do usuário,
2026-09-16: "até acabar os tokens") — processa TODA vaga médica
aprovada com campo faltando, em lotes de `TAMANHO_LOTE`, e quando não
sobra candidata nenhuma dorme `SLEEP_OCIOSO_S` e checa de novo (pode
surgir vaga nova incompleta a qualquer momento pela coleta normal).
Cada vaga é commitada individualmente — progresso nunca se perde, nem
se a rede cair, nem se o processo for morto/reiniciado no meio.

Erro classificado como cota/limite de uso esgotado
(`completude_codex.eh_erro_de_cota`) pausa por `SLEEP_COTA_ESGOTADA_S`
antes de tentar de novo — evita martelar a API sem parar quando a cota
da assinatura já era pra ter acabado. Erro pontual (rede, site fora do
ar, timeout de 1 vaga difícil) só pula pra próxima — a vaga que falhou
continua candidata na próxima passada (campo continua `null`), então
nada fica pra trás.

Ctrl+C (ou SIGTERM, como o `launchd` manda ao parar o job) encerra
limpo entre uma vaga e outra, nunca no meio de uma chamada ao Codex.
"""

from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from notifica_vagas_scraper import completude_codex, db

TAMANHO_LOTE = 15
SLEEP_OCIOSO_S = 600  # 10min sem candidata nenhuma -- reconfere depois
SLEEP_ERRO_PONTUAL_S = 30
SLEEP_COTA_ESGOTADA_S = 1800  # 30min -- cota de assinatura costuma resetar em janelas, não instantâneo

_PARAR = False


def _pedir_parada(signum, frame) -> None:
    global _PARAR
    print(f"\nSinal {signum} recebido — terminando depois da vaga atual (sem perder progresso já commitado).")
    _PARAR = True


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
    signal.signal(signal.SIGINT, _pedir_parada)
    signal.signal(signal.SIGTERM, _pedir_parada)
    _checar_codex_disponivel()

    conn = db.conectar()
    campo_preenchido = 0
    sem_alteracao = 0
    erro = 0

    try:
        while not _PARAR:
            candidatas = db.listar_vagas_medicas_incompletas(conn, minimo_campos_faltando=1, limite=TAMANHO_LOTE)

            if not candidatas:
                print(f"Nenhuma vaga incompleta agora. Dormindo {SLEEP_OCIOSO_S}s antes de reconferir.")
                for _ in range(SLEEP_OCIOSO_S):
                    if _PARAR:
                        break
                    time.sleep(1)
                continue

            print(f"Lote de {len(candidatas)} vaga(s) médica(s) aprovada(s) com dado incompleto.")

            for vaga in candidatas:
                if _PARAR:
                    break
                local = f"{vaga['nome']}/{vaga['uf']}"
                campos = completude_codex.campos_faltando(vaga)
                print(f"{vaga['cargo']} ({local}) — faltando: {campos}")

                try:
                    resultado = completude_codex.processar_vaga(conn, vaga)
                    conn.commit()  # commit por vaga: progresso sobrevive a queda de rede/kill no meio
                except completude_codex.ErroCompletudeCodex as exc:
                    conn.rollback()
                    if completude_codex.eh_erro_de_cota(str(exc)):
                        print(f"  -> parece cota/limite de uso esgotado ({exc}). Pausando {SLEEP_COTA_ESGOTADA_S}s.")
                        for _ in range(SLEEP_COTA_ESGOTADA_S):
                            if _PARAR:
                                break
                            time.sleep(1)
                    else:
                        print(f"  -> erro pontual, pulando (fica candidata na próxima passada): {exc}")
                        erro += 1
                        time.sleep(SLEEP_ERRO_PONTUAL_S)
                    continue

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
        f"\nEncerrado. Total desta sessão: {campo_preenchido} vaga(s) com campo preenchido, "
        f"{sem_alteracao} sem alteração, {erro} erro(s)."
    )


if __name__ == "__main__":
    with db.rastrear_execucao("completude_codex.py"):
        main()
