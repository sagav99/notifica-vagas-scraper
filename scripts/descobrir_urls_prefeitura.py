#!/usr/bin/env python3
"""Descoberta em lote de URL oficial de prefeitura (heurística .gov.br,
sem LLM/sem custo de token — só requests HTTP, ver
notifica_vagas_scraper.descoberta_prefeitura).

Lê municípios direto de `public.municipios` (fonte única — evita depender
de CSV por UF), testa candidatos em paralelo (ThreadPoolExecutor) e escreve
um CSV de resultado. Com --commit, também grava url_prefeitura (só para os
municípios com URL confirmada — nunca sobrescreve o que já estava
preenchido).

Uso:
    python scripts/descobrir_urls_prefeitura.py --uf SP --limit 30
    python scripts/descobrir_urls_prefeitura.py --uf SP --commit
Requer DATABASE_URL no ambiente (lê e, com --commit, grava no Supabase).
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from notifica_vagas_scraper import db
from notifica_vagas_scraper.descoberta_prefeitura import descobrir_url_prefeitura

DIR_SAIDA_PADRAO = Path(__file__).parent.parent.parent / "notifica-vagas" / "docs" / "dados"


def listar_municipios_sem_url(conn, uf: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "select codigo_ibge, nome, uf from public.municipios where uf = %s and url_prefeitura is null order by nome",
            (uf,),
        )
        return [{"codigo_ibge": r[0], "nome": r[1], "uf": r[2]} for r in cur.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uf", required=True)
    parser.add_argument("--saida", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None, help="testar só os N primeiros (debug)")
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--commit", action="store_true", help="grava url_prefeitura encontrada no Supabase")
    args = parser.parse_args()
    uf = args.uf.upper()
    saida = args.saida or (DIR_SAIDA_PADRAO / f"descoberta_urls_prefeitura_{uf.lower()}.csv")

    conn_leitura = db.conectar()
    try:
        municipios = listar_municipios_sem_url(conn_leitura, uf)
    finally:
        conn_leitura.close()

    if not municipios:
        raise SystemExit(f"Nenhum município de {uf} sem url_prefeitura (já processados, ou UF não cadastrada).")
    if args.limit:
        municipios = municipios[: args.limit]

    print(f"Testando {len(municipios)} município(s) com {args.workers} worker(s)...")
    t0 = time.time()
    resultados = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futuros = {
            executor.submit(descobrir_url_prefeitura, linha["nome"], linha["uf"]): linha
            for linha in municipios
        }
        for i, futuro in enumerate(as_completed(futuros), start=1):
            linha = futuros[futuro]
            resultado = futuro.result()
            resultados.append((linha["codigo_ibge"], resultado))
            if i % 25 == 0 or i == len(municipios):
                print(f"  {i}/{len(municipios)} processado(s)...")

    encontrados = [(cod, r) for cod, r in resultados if r.url]
    print(f"\nOk. {len(encontrados)}/{len(resultados)} URL(s) de prefeitura confirmada(s) em {time.time() - t0:.0f}s.")

    saida.parent.mkdir(parents=True, exist_ok=True)
    with saida.open("w", newline="", encoding="utf-8") as arquivo:
        writer = csv.writer(arquivo)
        writer.writerow(["codigo_ibge", "municipio", "uf", "url_prefeitura", "certificado_invalido", "motivo"])
        for codigo_ibge, r in resultados:
            writer.writerow([codigo_ibge, r.municipio, r.uf, r.url or "", r.certificado_invalido, r.motivo])
    print(f"Resultado completo salvo em {saida}")

    if args.commit and encontrados:
        conn = db.conectar()
        try:
            placeholders = ", ".join(["(%s, %s)"] * len(encontrados))
            parametros = [v for cod, r in encontrados for v in (int(cod), r.url)]
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    update public.municipios as m
                    set url_prefeitura = v.url
                    from (values {placeholders}) as v(codigo_ibge, url)
                    where m.codigo_ibge = v.codigo_ibge
                      and m.url_prefeitura is null
                    """,
                    parametros,
                )
                atualizados = cur.rowcount
            conn.commit()
            print(f"Gravado no Supabase: {atualizados} município(s) com url_prefeitura nova.")
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


if __name__ == "__main__":
    main()
