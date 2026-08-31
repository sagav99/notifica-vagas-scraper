#!/usr/bin/env python3
"""Importa o catálogo de municípios (CSV) para `public.municipios`.

Fonte padrão: docs/dados/municipios_mg.csv no repo principal (notifica-vagas),
gerado a partir da API do IBGE. Upsert por codigo_ibge — idempotente, roda
quantas vezes precisar sem duplicar nem sobrescrever url_prefeitura/
url_diario_oficial já preenchidas (ver notifica_vagas_scraper.db.upsert_municipio).

Uso: python scripts/importar_municipios.py [--csv CAMINHO]
Requer DATABASE_URL no ambiente.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from notifica_vagas_scraper import db

CSV_PADRAO = Path(__file__).parent.parent.parent / "notifica-vagas" / "docs" / "dados" / "municipios_mg.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=CSV_PADRAO)
    args = parser.parse_args()

    if not args.csv.exists():
        raise SystemExit(f"CSV não encontrado: {args.csv}")

    with args.csv.open(newline="", encoding="utf-8") as arquivo:
        linhas = [
            (
                int(linha["codigo_ibge"]),
                linha["nome"],
                linha["uf"],
                float(linha["latitude"]) if linha.get("latitude") else None,
                float(linha["longitude"]) if linha.get("longitude") else None,
            )
            for linha in csv.DictReader(arquivo)
        ]

    conn = db.conectar()
    try:
        # Upsert em 1 round-trip via pooler (VALUES em lote), em vez de 1
        # round-trip por município — 853 individuais estourou timeout de rede.
        placeholders = ", ".join(["(%s, %s, %s, %s, %s)"] * len(linhas))
        parametros = [valor for linha in linhas for valor in linha]
        with conn.cursor() as cur:
            cur.execute(
                f"""
                insert into public.municipios (codigo_ibge, nome, uf, latitude, longitude)
                values {placeholders}
                on conflict (codigo_ibge) do update set
                    nome = excluded.nome,
                    uf = excluded.uf,
                    latitude = coalesce(excluded.latitude, public.municipios.latitude),
                    longitude = coalesce(excluded.longitude, public.municipios.longitude)
                """,
                parametros,
            )
        conn.commit()
        print(f"Ok. {len(linhas)} município(s) importado(s)/atualizado(s) de {args.csv}.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
