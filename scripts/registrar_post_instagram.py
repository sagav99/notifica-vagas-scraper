"""Registra na lista de "já postadas" um post feito à mão no Instagram (tipo 'manual').

Uso: python scripts/registrar_post_instagram.py <vaga-id-completo> [AAAA-MM-DD]
A seleção automática (`selecao.py`) pula toda vaga registrada, e também a mesma
cargo+município sob outro id.
"""

from __future__ import annotations

import sys
from datetime import date

from notifica_vagas_scraper.db import conectar


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    dia = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into public.posts_instagram (dia, vaga_id, tipo, status, legenda, publicado_em) "
            "values (%s, %s, 'manual', 'publicado', 'post feito à mão', now()) on conflict do nothing returning id",
            (dia, sys.argv[1]),
        )
        print("registrado" if cur.fetchone() else "já estava registrado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
