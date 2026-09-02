#!/usr/bin/env python3
"""Entrypoint do cron pra fonte ACCESS: descobre processos com inscrição
aberta em `/index/abertos/` (sem lista curada de município, igual
IMESO/IMAM/JCM), acha a versão mais recente do edital entre as
publicações e usa Gemini pra ler o PDF. Mesma plataforma ProSeleta da
JCM Concursos — reaproveita `fontes/proseleta.py` inteiro, só a extração
da listagem é específica (`fontes/access.py`).

**Prioridade "saúde/médicos" do projeto (2026-09-01, ver TAREFAS.md)**:
essa banca já mostrou editais com dezenas de especialidades médicas
(Contagem/MG, ~40 especialidades) — acompanhar de perto.

Entidade que não é prefeitura/câmara (ex: universidade federal, instituto
de previdência) é pulada — não mapeia 1:1 pra um município.

Uso: python scripts/rodar_access.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf, ibge
from notifica_vagas_scraper.fontes import access

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Instituto ACCESS"


def processar_processo(conn, item: access.ItemListagem) -> int:
    codigo_ibge = ibge.buscar_codigo_ibge(item.municipio, item.uf)
    if codigo_ibge is None:
        print(f"  aviso: município '{item.municipio}/{item.uf}' não encontrado no IBGE, pulando")
        return 0

    # ACCESS atua em várias UFs (RJ/SP/MG/GO/SC vistos numa mesma amostra)
    # — `upsert_fonte` só casa por nome+url, então chamar por item (com o
    # uf de cada um) é seguro: só grava o `uf` na 1ª vez, mesmo padrão da
    # FGV (outra banca nacional, ver rodar_fgv.py).
    fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=access.BASE_URL, tipo="oficial", uf=item.uf)

    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    documentos = access.listar_documentos(resposta.text)
    edital = access.escolher_edital(documentos)
    if edital is None:
        print(f"  aviso: '{item.tipo_processo} {item.numero_edital}' sem documentos listados")
        return 0

    pdf_resposta = requests.get(edital.url_pdf, headers={"User-Agent": USER_AGENT}, timeout=60)
    pdf_resposta.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra '{item.tipo_processo} {item.numero_edital}' ({edital.url_pdf})")
        return 0

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=item.municipio, uf=item.uf)
    orgao = extraido.get("orgao") or f"{item.orgao} de {item.municipio}/{item.uf}"
    numero_edital = extraido.get("numero_edital") or item.numero_edital

    total = 0
    for vaga in extraido["vagas"]:
        cargo = vaga.get("cargo")
        if not cargo:
            continue
        slug_cargo = "".join(c if c.isalnum() else "-" for c in cargo.lower()).strip("-")
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=f"access-{item.processo_id}-{slug_cargo}",
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            numero_edital=numero_edital,
            data_publicacao=edital.data,
            inscricoes_inicio=None,
            inscricoes_fim=None,
            status="aberta",
            resumo=f"{item.tipo_processo} nº {item.numero_edital} — {cargo}"
            + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=edital.url_pdf,
            tipo_documento="pdf",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


#: ACCESS atua em várias UFs (RJ/SP/MG/GO/SC vistos numa mesma amostra),
#: mas o projeto cobre só MG/SP (ver CLAUDE.md) — filtra aqui, não em
#: `fontes/access.py`, que é genérico pra qualquer UF.
UFS_DO_PROJETO = {"MG", "SP"}


def main() -> None:
    resposta = requests.get(f"{access.BASE_URL}/index/abertos/", headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    todos = access.listar_processos_abertos(resposta.text)
    itens = [i for i in todos if i.uf in UFS_DO_PROJETO]

    print(f"{len(itens)} processo(s) com inscrição aberta em MG/SP (de {len(todos)} no Brasil todo).")

    conn = db.conectar()
    try:
        total_geral = 0
        for item in itens:
            print(f"Processando {item.municipio}/{item.uf} — {item.tipo_processo} {item.numero_edital}...")
            try:
                with conn.transaction():
                    total_geral += processar_processo(conn, item)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 processo derrubar o lote inteiro
                print(f"  ERRO processando '{item.tipo_processo} {item.numero_edital}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_access.py"):
        main()
