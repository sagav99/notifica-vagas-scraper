"""Publica no Instagram um carrossel já pronto (slide-N.png + legenda.txt), sem vaga e sem gravar em `posts_instagram`.

Uso (workflow postar-pasta-instagram.yml): python scripts/postar_pasta_instagram.py <pasta> [--dry-run]
Serve para posts que não são de uma vaga (ex.: carrossel do resumo semanal de sábado/domingo).
"""

from __future__ import annotations

import sys
from pathlib import Path

from notifica_vagas_scraper.instagram.publicar import hospedar_imagem, publicar_carrossel_no_instagram


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("uso: postar_pasta_instagram.py <pasta> [--dry-run]", file=sys.stderr)
        return 2
    pasta = Path(args[0])
    pngs = sorted(pasta.glob("slide-*.png"))
    legenda = (pasta / "legenda.txt").read_text(encoding="utf-8").strip()
    if not pngs:
        print(f"Nenhum slide-*.png em {pasta}", file=sys.stderr)
        return 1
    print(f"{len(pngs)} slides em {pasta}")
    if "--dry-run" in sys.argv:
        print(legenda)
        return 0
    urls = [hospedar_imagem(png, f"{pasta.name}-{i}.png") for i, png in enumerate(pngs, start=1)]
    print(f"Publicado (carrossel): media {publicar_carrossel_no_instagram(urls, legenda)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
