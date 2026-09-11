#!/usr/bin/env python3
"""Preenche `pagina_pdf`/`url_print_pagina` de evidência PDF antiga (de
antes de 2026-09-09, quando o Gemini passou a devolver a página de cada
cargo) — achado #E da análise de produto de 2026-09-10, decisão do
usuário: começar o backfill, priorizando vaga mais atual, gastando pouca
cota de Gemini (busca de texto sem IA primeiro, IA só como fallback
capado por execução).

Duas fases por execução:
1. Sem IA (`busca_pagina_pdf`, `pdfplumber.extract_text`): roda pra até
   `LIMITE_TOTAL` evidências, sem cota — pode processar muitas.
2. Com IA (`gemini_pdf.localizar_pagina_cargo`), só pras que a fase 1 não
   resolveu (cargo não achado ou ambíguo/PDF escaneado) — capado em
   `LIMITE_IA` por execução (padrão 20), pra não competir com a cota
   diária da extração/revisão principal.

Uso: python scripts/backfill_print_evidencias.py
Requer DATABASE_URL, GEMINI_API_KEY, NEXT_PUBLIC_SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY no ambiente.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import busca_pagina_pdf, db, evidencia_imagem, gemini_pdf

LIMITE_TOTAL = int(os.environ.get("BACKFILL_PRINT_LIMITE_TOTAL", "200"))
LIMITE_IA = int(os.environ.get("BACKFILL_PRINT_LIMITE_IA", "20"))
USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/1.0)"


def _slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")


def _gerar_e_gravar_print(
    conn, *, evidencia: dict, pagina: int, pdf_bytes: bytes, supabase_url: str, service_role_key: str
) -> bool:
    """Renderiza a `pagina`, sobe pro bucket e grava em
    `vaga_evidencias`. Devolve `False` sem gravar nada se a página não
    existir de verdade no PDF (Gemini pode alucinar um número fora do
    intervalo) ou a renderização/upload falhar — evidência fica pra
    tentar de novo numa próxima execução, não trava o lote inteiro."""
    try:
        imagem = evidencia_imagem.renderizar_pagina_pdf(pdf_bytes, pagina)
        caminho = f"backfill-{_slug(evidencia['url'])}-pagina-{pagina}.png"
        url_print = evidencia_imagem.subir_print_pagina(
            imagem, caminho=caminho, supabase_url=supabase_url, service_role_key=service_role_key
        )
    except (evidencia_imagem.ErroImagemEvidencia, requests.RequestException) as exc:
        print(f"    aviso: falha gerando/subindo print (página {pagina}): {exc}")
        return False

    db.atualizar_print_evidencia(conn, evidencia_id=evidencia["id"], pagina_pdf=pagina, url_print_pagina=url_print)
    conn.commit()
    return True


def main() -> None:
    supabase_url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_role_key:
        print("aviso: NEXT_PUBLIC_SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY não definidas — nada a fazer.")
        return

    conn = db.conectar()
    try:
        evidencias = db.listar_evidencias_pdf_sem_print(conn, limite=LIMITE_TOTAL)
        print(f"{len(evidencias)} evidência(s) pdf sem print (limite {LIMITE_TOTAL}, mais recentes primeiro).")

        resolvidas_sem_ia = 0
        pendentes_ia: list[tuple[dict, bytes]] = []

        for evidencia in evidencias:
            try:
                pdf_resposta = requests.get(evidencia["url"], headers={"User-Agent": USER_AGENT}, timeout=60)
                pdf_resposta.raise_for_status()
            except requests.RequestException as exc:
                print(f"  aviso: falha baixando '{evidencia['url'][:70]}': {exc}")
                continue
            pdf_bytes = pdf_resposta.content

            pagina = busca_pagina_pdf.localizar_pagina_pdf_sem_ia(pdf_bytes, evidencia["cargo"])
            if pagina is not None:
                if _gerar_e_gravar_print(
                    conn, evidencia=evidencia, pagina=pagina, pdf_bytes=pdf_bytes,
                    supabase_url=supabase_url, service_role_key=service_role_key,
                ):
                    resolvidas_sem_ia += 1
                    print(f"  [sem IA] {evidencia['cargo']!r}: página {pagina}")
            elif len(pendentes_ia) < LIMITE_IA:
                pendentes_ia.append((evidencia, pdf_bytes))

        print(f"{resolvidas_sem_ia} resolvida(s) sem IA. {len(pendentes_ia)} vão pro fallback de IA (limite {LIMITE_IA}).")

        resolvidas_com_ia = 0
        for evidencia, pdf_bytes in pendentes_ia:
            try:
                pagina = gemini_pdf.localizar_pagina_cargo(pdf_bytes, evidencia["cargo"])
            except (gemini_pdf.ErroExtracaoGemini, requests.RequestException) as exc:
                # 429/5xx (HTTPError) e timeout de rede (ReadTimeout, os 2
                # achados reais em produção 2026-09-11) vêm puros de
                # requests, não embrulhados em ErroExtracaoGemini (mesmo
                # padrão já corrigido em rodar_descoberta_google_search.py)
                # — qualquer um matava o processo inteiro no meio do lote,
                # perdendo até o resto sem IA já resolvido nesta execução
                # (só commitado por item, ver `_gerar_e_gravar_print`).
                # `RequestException` é a classe-mãe de HTTPError/Timeout/
                # ConnectionError — captura a família inteira de uma vez.
                print(f"  aviso: falha no Gemini pra '{evidencia['cargo']!r}': {exc}")
                continue
            if pagina is None:
                print(f"  [IA] {evidencia['cargo']!r}: não encontrado, pulando.")
                continue
            if _gerar_e_gravar_print(
                conn, evidencia=evidencia, pagina=pagina, pdf_bytes=pdf_bytes,
                supabase_url=supabase_url, service_role_key=service_role_key,
            ):
                resolvidas_com_ia += 1
                print(f"  [IA] {evidencia['cargo']!r}: página {pagina}")

        print(f"Total: {resolvidas_sem_ia + resolvidas_com_ia} evidência(s) atualizada(s) nesta execução.")
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("backfill_print_evidencias.py"):
        main()
