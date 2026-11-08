"""Publica no Instagram o carrossel do resumo semanal: sábado = abertura, domingo = fechamento (workflow postar-resumo-semanal.yml, 12h Brasília).

Os slides e a legenda são gerados pelo repositório do site (workflow `instagram-resumo-gerar.yml`, 11h30) e ficam na tabela
`posts_resumo_instagram_slides`. Aqui só se publica, e só se:
  - o PDF do dia já foi marcado como enviado ao grupo (`pdf_grupo_vagas.enviado`): é o sinal de que o usuário conferiu os dados;
    sem isso o post NÃO sai e o admin recebe um e-mail (use --sem-trava para publicar mesmo assim);
  - o post do dia ainda não foi publicado (`posts_resumo_instagram`, um registro por dia+tipo).

Uso: python scripts/postar_resumo_semanal.py [--tipo abertura|fechamento] [--data AAAA-MM-DD] [--dry-run] [--sem-trava]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import tempfile
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from notifica_vagas_scraper.db import conectar
from notifica_vagas_scraper.instagram.publicar import hospedar_imagem, publicar_carrossel_no_instagram

SLIDES_ESPERADOS = 7


def tipo_do_dia(dia: date) -> str | None:
    return {5: "abertura", 6: "fechamento"}.get(dia.weekday())


def avisar_admin(assunto: str, corpo: str) -> bool:
    """E-mail ao admin (Brevo). Nunca derruba o fluxo: sem configuração, só registra no log."""
    chave, remetente, destino = os.environ.get("BREVO_API_KEY"), os.environ.get("BREVO_REMETENTE_EMAIL"), os.environ.get("ADMIN_EMAIL")
    if not (chave and remetente and destino):
        print("Aviso ao admin ignorado: faltam BREVO_API_KEY/BREVO_REMETENTE_EMAIL/ADMIN_EMAIL.")
        return False
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps({"sender": {"email": remetente, "name": "Med Vagas"}, "to": [{"email": destino}], "subject": assunto,
                         "htmlContent": f"<p>{html.escape(corpo)}</p>"}).encode(),
        headers={"api-key": chave, "content-type": "application/json", "accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            return True
    except Exception as erro:  # noqa: BLE001
        print(f"Aviso ao admin falhou: {erro}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tipo", choices=("abertura", "fechamento"))
    ap.add_argument("--data", help="AAAA-MM-DD (padrão: hoje, Brasília)")
    ap.add_argument("--dry-run", action="store_true", help="só confere (trava, slides, legenda); não publica")
    ap.add_argument("--sem-trava", action="store_true", help="publica mesmo sem o PDF do dia marcado como enviado")
    a = ap.parse_args()
    dia = date.fromisoformat(a.data) if a.data else datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    tipo = a.tipo or tipo_do_dia(dia)
    if not tipo:
        print(f"{dia} não é sábado nem domingo; informe --tipo.")
        return 2
    rotulo = "sábado (vagas novas)" if tipo == "abertura" else "domingo (vagas que fecham)"

    with conectar() as conn:
        if conn.execute("select 1 from public.posts_resumo_instagram where dia = %s and tipo = %s and status = 'publicado'", (dia, tipo)).fetchone():
            print(f"O post de {tipo} de {dia} já foi publicado; nada a fazer.")
            return 0
        enviado = conn.execute("select coalesce(bool_or(enviado), false) from public.pdf_grupo_vagas where pdf_data = %s and tipo = %s", (dia, tipo)).fetchone()[0]
        if not enviado and not a.sem_trava:
            msg = (f"O post do resumo de {rotulo} ({dia:%d/%m}) NÃO foi publicado: o PDF do dia ainda não está marcado como enviado ao grupo "
                   f"(sinal de que os dados foram conferidos). Depois de enviar, peça para marcar como enviado e rode o workflow "
                   f"'Postar resumo semanal no Instagram' na mão, ou use sem_trava.")
            print(msg)
            if not a.dry_run:
                avisar_admin(f"Post do resumo de {tipo} não saiu (PDF não marcado como enviado)", msg)
            return 0
        arquivos = {nome: bytes(dados) for nome, dados in conn.execute(
            "select nome, conteudo from public.posts_resumo_instagram_slides where dia = %s and tipo = %s", (dia, tipo)).fetchall()}
        slides = sorted(n for n in arquivos if n.startswith("slide-") and n.endswith(".png"))
        if len(slides) != SLIDES_ESPERADOS or "legenda.txt" not in arquivos:
            msg = f"Faltam os slides do post de {tipo} de {dia}: achei {len(slides)} de {SLIDES_ESPERADOS} e legenda={'legenda.txt' in arquivos}. O workflow 'gerar slides' do site rodou?"
            print(msg, file=sys.stderr)
            if not a.dry_run:
                avisar_admin(f"Post do resumo de {tipo} não saiu (slides ausentes)", msg)
            return 1
        legenda = arquivos["legenda.txt"].decode("utf-8").strip()
        print(f"[{tipo}] {dia}: {len(slides)} slides, PDF enviado={enviado}")
        if a.dry_run:
            print(legenda)
            return 0

        try:
            with tempfile.TemporaryDirectory() as tmp:
                urls = []
                for i, nome in enumerate(slides, start=1):
                    caminho = Path(tmp) / nome
                    caminho.write_bytes(arquivos[nome])
                    urls.append(hospedar_imagem(caminho, f"resumo-{tipo}-{dia}-{i}.png"))
            media_id = publicar_carrossel_no_instagram(urls, legenda)
        except Exception as erro:  # noqa: BLE001
            conn.execute(
                "insert into public.posts_resumo_instagram (dia, tipo, status, erro) values (%s, %s, 'erro', %s) "
                "on conflict (dia, tipo) do update set status = 'erro', erro = excluded.erro", (dia, tipo, str(erro)[:500]))
            conn.commit()
            print(f"ERRO ao publicar: {erro}", file=sys.stderr)
            avisar_admin(f"Erro ao postar o resumo de {tipo} no Instagram", str(erro)[:500])
            return 1
        conn.execute(
            "insert into public.posts_resumo_instagram (dia, tipo, status, ig_media_id) values (%s, %s, 'publicado', %s) "
            "on conflict (dia, tipo) do update set status = 'publicado', ig_media_id = excluded.ig_media_id, erro = null", (dia, tipo, media_id))
        conn.commit()
    print(f"Publicado (carrossel): media {media_id}")
    avisar_admin(f"Resumo de {tipo} publicado no Instagram", f"O carrossel do resumo de {rotulo} ({dia:%d/%m}) foi publicado no @medvagasapp (media {media_id}). Link do bot para os Stories: https://medvagasapp.com.br/w/stories")
    return 0


if __name__ == "__main__":
    sys.exit(main())
