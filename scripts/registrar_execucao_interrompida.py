#!/usr/bin/env python3
"""Registra em `public.execucoes_scraper` que o job de coleta do GitHub
Actions foi cancelado (timeout ou cancelamento manual) ou falhou de um
jeito que nenhum `rodar_*.py` individual chegou a capturar — passo final
(`if: always()`) do job `coletar` em `.github/workflows/scrape-diario.yml`.
Sem isso, um job morto por `timeout-minutes` não deixa rastro nenhum no
banco, só no histórico de runs do GitHub Actions (que expira).

Uso: python scripts/registrar_execucao_interrompida.py <script> <status_do_job>
`status_do_job` é o valor de `${{ job.status }}` do GitHub Actions
("cancelled" ou "failure" — só chamado quando um dos dois, ver o `if:`
do step no workflow).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from notifica_vagas_scraper import db


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        print(f"Uso: {argv[0]} <script> <status_do_job>", file=sys.stderr)
        sys.exit(1)

    script, status_job = argv[1], argv[2]
    motivo = f"Job do GitHub Actions terminou com status '{status_job}' (provável timeout-minutes ou cancelamento) sem passar por rastrear_execucao."
    db.registrar_execucao_interrompida(script, motivo=motivo)
    print(f"Registrado em execucoes_scraper: script={script!r} status_job={status_job!r}")


if __name__ == "__main__":
    main(sys.argv)
