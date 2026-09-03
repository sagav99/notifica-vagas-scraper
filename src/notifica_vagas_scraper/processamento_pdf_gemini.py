"""Miolo compartilhado de "baixar PDF do edital, extrair vagas com o
Gemini, gravar 1 vaga por cargo" — usado pelas fontes que descobrem
processo dinamicamente e só têm cargo/salário estruturado dentro do PDF
(ACCESS, IMAM, JCM; possivelmente outras no futuro). Antes ~60 linhas
quase idênticas em `scripts/rodar_access.py`/`rodar_imam.py`/
`rodar_jcm.py` (achado de code review, 2026-09-02) — um fix nesse fluxo
só pegava as 3 fontes se lembrasse de aplicar nas 3.

Cada `rodar_*.py` continua resolvendo sozinho o que é específico da sua
plataforma (listagem, município/UF, escolha do documento de edital —
`listar_documentos`/`escolher_edital` têm assinatura própria por fonte),
e só chama esta função a partir do momento em que já tem o PDF do edital
escolhido.
"""

from __future__ import annotations

from datetime import date

import requests

from notifica_vagas_scraper import db, gemini_pdf


def processar_pdf_e_gravar_vagas(
    conn,
    *,
    fonte_id: str,
    codigo_ibge: int,
    municipio_nome: str,
    uf: str,
    url_pdf: str,
    data_publicacao: date | None,
    orgao_fallback: str,
    numero_edital_fallback: str | None,
    id_prefix: str,
    processo_id: str | int,
    resumo_prefixo: str,
    user_agent: str,
) -> int:
    """Baixa `url_pdf`, extrai vagas com o Gemini e grava 1
    `vagas`/`vaga_evidencias` por cargo encontrado (dedup normal por
    `identificador_externo=f"{id_prefix}-{processo_id}-{slug_cargo}"`).
    Retorna quantas vagas foram processadas (0 se o Gemini não achou
    nenhuma)."""
    pdf_resposta = requests.get(url_pdf, headers={"User-Agent": user_agent}, timeout=60)
    pdf_resposta.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra '{resumo_prefixo}' ({url_pdf})")
        return 0

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=municipio_nome, uf=uf)
    orgao = extraido.get("orgao") or orgao_fallback
    numero_edital = extraido.get("numero_edital") or numero_edital_fallback
    tipo_oportunidade = extraido.get("tipo_oportunidade")

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
            identificador_externo=f"{id_prefix}-{processo_id}-{slug_cargo}",
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            salario_tipo=vaga.get("salario_tipo"),
            tipo_oportunidade=tipo_oportunidade,
            numero_edital=numero_edital,
            data_publicacao=data_publicacao,
            inscricoes_inicio=None,
            inscricoes_fim=None,
            status="aberta",
            resumo=f"{resumo_prefixo} — {cargo}" + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=url_pdf,
            tipo_documento="pdf",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total
