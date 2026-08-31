#!/usr/bin/env python3
"""Importa o catálogo de municípios de uma UF direto da API pública do IBGE
(sem depender de CSV local) para `public.municipios`.

Usado hoje pra SP (sem catálogo local, diferente de MG que já tinha CSV
com lat/lon). Não traz latitude/longitude — a API de localidades do IBGE
não expõe isso; ficam nulas até haver necessidade real (ver TAREFAS.md).

Uso: python scripts/importar_municipios_ibge.py --uf SP
Requer DATABASE_URL no ambiente.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from notifica_vagas_scraper import db, ibge


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uf", required=True)
    args = parser.parse_args()
    uf = args.uf.upper()

    municipios = ibge.listar_municipios(uf)
    if not municipios:
        raise SystemExit(f"IBGE não retornou municípios para UF={uf}")

    conn = db.conectar()
    try:
        placeholders = ", ".join(["(%s, %s, %s)"] * len(municipios))
        parametros = [v for m in municipios for v in (m["codigo_ibge"], m["nome"], uf)]
        with conn.cursor() as cur:
            cur.execute(
                f"""
                insert into public.municipios (codigo_ibge, nome, uf)
                values {placeholders}
                on conflict (codigo_ibge) do update set
                    nome = excluded.nome,
                    uf = excluded.uf
                """,
                parametros,
            )
        conn.commit()
        print(f"Ok. {len(municipios)} município(s) de {uf} importado(s)/atualizado(s) via API do IBGE.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
