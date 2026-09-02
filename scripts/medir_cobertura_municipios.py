#!/usr/bin/env python3
"""Mede a cobertura real (deduplicada) de município monitorado por fonte
de lista fixa — TAREFAS.md do repo principal (2026-09-02): somar os
totais documentados por fonte superestima a cobertura real porque há
sobreposição entre fontes (ex: Instar e AMM-MG cobrem parte dos mesmos
municípios de MG). Faz a união (set, por `codigo_ibge`) das listas fixas
e reporta o total deduplicado por UF.

Cobre só as fontes com roster fixo e fechado (CSV ou lista inline no
código): AMM-MG, APM-SP, Instar, Actcon, WordPress. **Não inclui** IMESO,
FGV, IMAM, JCM, ACCESS, Ache Concursos — essas descobrem município
dinamicamente a cada execução (sem lista curada fechada), então não têm
"total de municípios monitorados" fixo pra somar aqui; o número final é
piso de cobertura confirmada, não cobertura total do produto.

Uso: python scripts/medir_cobertura_municipios.py
Não precisa de DATABASE_URL — só lê os CSVs/módulos já no repo.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from notifica_vagas_scraper.fontes.apm_sp import listar_entidades_apm_sp

DADOS = Path(__file__).parent.parent / "src" / "notifica_vagas_scraper" / "dados"

FONTES_CSV = {
    "AMM-MG": DADOS / "entidades_amm_mg.csv",
    "Instar": DADOS / "municipios_instar.csv",
    "Actcon": DADOS / "municipios_actcon.csv",
    "WordPress": DADOS / "municipios_wordpress.csv",
}

FONTES_SEM_LISTA_FIXA = ["IMESO", "FGV", "IMAM", "JCM", "ACCESS", "Ache Concursos"]


def carregar_csv(caminho: Path) -> set[tuple[int, str, str]]:
    with caminho.open(encoding="utf-8") as f:
        return {
            (int(linha["codigo_ibge"]), linha["nome"], linha["uf"])
            for linha in csv.DictReader(f)
        }


def main() -> None:
    por_fonte: dict[str, set[tuple[int, str, str]]] = {}
    for nome, caminho in FONTES_CSV.items():
        por_fonte[nome] = carregar_csv(caminho)

    por_fonte["APM-SP"] = {
        (e.codigo_ibge, e.nome, e.uf) for e in listar_entidades_apm_sp()
    }

    print("Municípios por fonte (bruto, sem deduplicar entre fontes):", file=sys.stderr)
    total_bruto = 0
    for nome, municipios in sorted(por_fonte.items(), key=lambda kv: -len(kv[1])):
        print(f"  {nome}: {len(municipios)}", file=sys.stderr)
        total_bruto += len(municipios)
    print(f"  TOTAL BRUTO (soma ingênua, superestima): {total_bruto}", file=sys.stderr)

    uniao: set[tuple[int, str, str]] = set()
    for municipios in por_fonte.values():
        uniao |= municipios

    print(f"\nTOTAL DEDUPLICADO (união real): {len(uniao)}", file=sys.stderr)
    print(f"Sobreposição: {total_bruto - len(uniao)} município(s) contado(s) em mais de 1 fonte", file=sys.stderr)

    por_uf = Counter(uf for _, _, uf in uniao)
    print("\nDeduplicado por UF:", file=sys.stderr)
    for uf, n in sorted(por_uf.items()):
        print(f"  {uf}: {n}", file=sys.stderr)

    contagem_fontes = Counter()
    for nome, municipios in por_fonte.items():
        for chave in municipios:
            contagem_fontes[chave] += 1
    duplicados = [chave for chave, n in contagem_fontes.items() if n > 1]
    if duplicados:
        print(f"\n{len(duplicados)} município(s) cobertos por mais de 1 fonte:", file=sys.stderr)
        for codigo, nome, uf in sorted(duplicados, key=lambda c: c[1]):
            fontes_do_municipio = [f for f, ms in por_fonte.items() if (codigo, nome, uf) in ms]
            print(f"  {nome}/{uf}: {', '.join(fontes_do_municipio)}", file=sys.stderr)

    print(
        f"\nAtenção: {', '.join(FONTES_SEM_LISTA_FIXA)} descobrem município "
        "dinamicamente (sem lista curada fechada) — não entram nessa conta. "
        "O total acima é piso de cobertura confirmada por lista fixa, não a "
        "cobertura total do produto.",
        file=sys.stderr,
    )

    escritor = csv.writer(sys.stdout)
    escritor.writerow(["codigo_ibge", "nome", "uf", "fontes"])
    for codigo, nome, uf in sorted(uniao, key=lambda c: (c[2], c[1])):
        fontes_do_municipio = sorted(f for f, ms in por_fonte.items() if (codigo, nome, uf) in ms)
        escritor.writerow([codigo, nome, uf, ";".join(fontes_do_municipio)])


if __name__ == "__main__":
    main()
