#!/usr/bin/env python3
"""Preenche `municipios.latitude`/`longitude` que estão nulas, usando o
dataset público `kelvins/municipios-brasileiros` (GitHub, MIT, referência
comum em projetos cívicos brasileiros) como fonte — a API do IBGE usada
em `importar_municipios_ibge.py` não expõe coordenada (ver docstring de
lá), e por isso SP ficou com 0/645 município com coordenada desde a
importação (2026-08-31), enquanto MG (cadastrado via CSV próprio) já
tinha 853/853.

Validado contra o CSV de MG já em produção antes de usar pra SP
(2026-09-02): a mesma cidade tem lat/lon com ~5-10km de diferença entre
as duas fontes (ex: Viçosa/MG -20.7559,-42.8742 aqui vs -20.74042,
-42.885513 no CSV atual) — variação normal entre datasets de geocodificação
por centroide, não um erro; suficiente pra filtro de raio (`raio_km`),
que já opera em granularidade de município, não endereço exato.

**Nunca sobrescreve** `latitude`/`longitude` já preenchida — só entra
onde está nula, mesmo padrão de `descobrir_urls_prefeitura.py`.

Uso:
    python scripts/preencher_coordenadas.py --uf SP           # dry-run, só mostra o que mudaria
    python scripts/preencher_coordenadas.py --uf SP --commit  # grava de verdade
Requer DATABASE_URL no ambiente.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db

URL_DATASET = "https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/municipios.csv"


def baixar_coordenadas() -> dict[int, tuple[float, float]]:
    resposta = requests.get(URL_DATASET, timeout=30)
    resposta.raise_for_status()
    linhas = csv.DictReader(resposta.text.splitlines())
    return {
        int(linha["codigo_ibge"]): (float(linha["latitude"]), float(linha["longitude"]))
        for linha in linhas
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uf", required=True)
    parser.add_argument("--commit", action="store_true", help="grava de verdade; sem isso só mostra o que mudaria")
    args = parser.parse_args()
    uf = args.uf.upper()

    coordenadas = baixar_coordenadas()

    conn = db.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select codigo_ibge, nome from public.municipios where uf = %s and (latitude is null or longitude is null) order by nome",
                (uf,),
            )
            sem_coordenada = cur.fetchall()

        encontrados = [(codigo, nome) for codigo, nome in sem_coordenada if codigo in coordenadas]
        nao_encontrados = [(codigo, nome) for codigo, nome in sem_coordenada if codigo not in coordenadas]

        print(f"{uf}: {len(sem_coordenada)} município(s) sem coordenada, {len(encontrados)} encontrados no dataset, {len(nao_encontrados)} não encontrados.")
        if nao_encontrados:
            print("Não encontrados (ficam nulos, investigar depois se importar):")
            for codigo, nome in nao_encontrados:
                print(f"  {codigo} {nome}")

        if not args.commit:
            print("\nDry-run (sem --commit, nada foi gravado). Amostra do que seria escrito:")
            for codigo, nome in encontrados[:5]:
                lat, lon = coordenadas[codigo]
                print(f"  {nome} ({codigo}): {lat}, {lon}")
            return

        with conn.cursor() as cur:
            for codigo, _nome in encontrados:
                lat, lon = coordenadas[codigo]
                cur.execute(
                    "update public.municipios set latitude = %s, longitude = %s where codigo_ibge = %s and latitude is null and longitude is null",
                    (lat, lon, codigo),
                )
        conn.commit()
        print(f"Gravado: {len(encontrados)} município(s) de {uf} atualizados com coordenada.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
