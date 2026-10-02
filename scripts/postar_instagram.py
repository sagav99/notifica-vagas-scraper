"""Post diário no Instagram: escolhe a vaga, gera o carrossel de 4 slides e publica.

Uso:
  python scripts/postar_instagram.py --dry-run   # gera as 4 imagens + legenda em saida_instagram/, não publica nem grava no banco
  python scripts/postar_instagram.py             # publica de verdade (workflow postar-instagram.yml)
  python scripts/postar_instagram.py --forcar    # ignora "já postou hoje"

No máximo 1 post por dia (`posts_instagram.dia` único); falha também ocupa o
dia — não repete sozinho.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from notifica_vagas_scraper.db import conectar
from notifica_vagas_scraper.instagram.aviso_admin import avisar_admin
from notifica_vagas_scraper.instagram.card import montar_htmls, renderizar_pngs
from notifica_vagas_scraper.instagram.legenda import montar_legenda
from notifica_vagas_scraper.instagram.publicar import ErroPublicacao, hospedar_imagem, publicar_carrossel_no_instagram
from notifica_vagas_scraper.instagram.selecao import selecionar_vaga, vagas_preview

SAIDA = Path("saida_instagram")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--forcar", action="store_true")
    args = parser.parse_args()

    hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()

    with conectar() as conn:
        if not args.dry_run and not args.forcar:
            with conn.cursor() as cur:
                cur.execute("select 1 from public.posts_instagram where dia = %s", (hoje,))
                if cur.fetchone():
                    print(f"Já existe post (ou tentativa) em {hoje}; nada a fazer.")
                    return 0

        escolha = selecionar_vaga(conn, hoje)
        if not escolha:
            print("Sem vaga candidata hoje; não posta.")
            return 0
        tipo, vaga = escolha
        extra = vagas_preview(conn, vaga["id"], limite=2)
        legenda = montar_legenda(vaga, tipo, hoje)
        htmls = montar_htmls(vaga, tipo, hoje, extra)
        pngs = renderizar_pngs(htmls, SAIDA / f"card-{hoje}.png")
        print(f"[{tipo}] {vaga['cargo']} — {vaga['municipio']}/{vaga['uf']} -> {len(pngs)} slides ({[p.name for p in pngs]})")

        if args.dry_run:
            (SAIDA / f"legenda-{hoje}.txt").write_text(legenda, encoding="utf-8")
            print(legenda)
            return 0

        with conn.cursor() as cur:
            cur.execute(
                "insert into public.posts_instagram (dia, vaga_id, tipo, legenda) values (%s, %s, %s, %s) "
                "on conflict do nothing returning id",
                (hoje, vaga["id"], tipo, legenda),
            )
            linha = cur.fetchone()
        conn.commit()
        if not linha:
            print("Conflito ao registrar o post (dia ou vaga já usados); abortando sem publicar.")
            return 0

        try:
            imagens_url = [
                hospedar_imagem(png, f"card-{hoje}-{str(vaga['id'])[:8]}-{i}.png") for i, png in enumerate(pngs, start=1)
            ]
            media_id = publicar_carrossel_no_instagram(imagens_url, legenda)
        except (ErroPublicacao, Exception) as erro:  # noqa: BLE001 — registra qualquer falha e sai != 0
            with conn.cursor() as cur:
                cur.execute("update public.posts_instagram set status = 'erro', erro = %s where id = %s", (str(erro)[:500], linha[0]))
            conn.commit()
            print(f"ERRO ao publicar: {erro}", file=sys.stderr)
            return 1

        with conn.cursor() as cur:
            cur.execute(
                "update public.posts_instagram set status = 'publicado', ig_media_id = %s, imagem_url = %s, publicado_em = now() where id = %s",
                (media_id, imagens_url[0], linha[0]),
            )
        conn.commit()
        print(f"Publicado (carrossel): media {media_id}")
        avisar_admin(vaga)
        return 0


if __name__ == "__main__":
    sys.exit(main())
