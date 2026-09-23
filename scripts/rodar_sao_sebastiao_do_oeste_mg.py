#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Prefeitura de São Sebastião do Oeste/MG:
CMS Joomla/K2 (`saosebastiaodooeste.mg.gov.br`), organização terceirizada
pela IDEAP mas sem descoberta de tenant nenhuma (1 único município fixo).
Ver docstring de `fontes/sao_sebastiao_do_oeste_mg.py` pros achados
completos (listagem mistura vários tipos de publicação; extração de
cargo/vagas/salário/requisitos 100% determinística via
`pdfplumber.extract_tables()`, sem Gemini; achado real de que a numeração
do edital reinicia entre secretarias, motivando dedup por URL completa).

Uso: python scripts/rodar_sao_sebastiao_do_oeste_mg.py
Requer DATABASE_URL no ambiente (sem GEMINI_API_KEY — extração 100%
determinística, sem chamada ao Gemini).
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pdfplumber
import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.fontes import sao_sebastiao_do_oeste_mg as fonte

# achado real da investigação: o 406 inicial contra este site era só
# falta de header Accept/Accept-Language "de browser" — cabeçalhos
# completos abaixo evitam o falso bloqueio (não é anti-bot de verdade).
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
}
FONTE_NOME = "Prefeitura de São Sebastião do Oeste/MG"

#: teto de páginas da listagem paginada (`?start=N`) — 20 páginas cobre
#: 240 itens (histórico real tinha ~192 na investigação); evita loop
#: infinito se a paginação nunca "acabar" de verdade.
MAX_PAGINAS = 20


def _extrair_texto_e_tabelas(conteudo_pdf: bytes) -> tuple[str, list[list[list[str | None]]]]:
    with pdfplumber.open(BytesIO(conteudo_pdf)) as pdf:
        texto = "\n".join((pagina.extract_text() or "") for pagina in pdf.pages)
        tabelas = [tabela for pagina in pdf.pages for tabela in pagina.extract_tables()]
    return texto, tabelas


def _listar_todos_os_itens() -> list[fonte.ItemListagem]:
    itens: list[fonte.ItemListagem] = []
    for pagina in range(MAX_PAGINAS):
        params = {"start": pagina * fonte.ITENS_POR_PAGINA} if pagina else None
        resposta = requests.get(fonte.URL_LISTAGEM, params=params, headers=HEADERS, timeout=20)
        resposta.raise_for_status()
        itens_pagina = fonte.listar_processos(resposta.text)
        if not itens_pagina:
            break
        itens.extend(itens_pagina)
        if len(itens_pagina) < fonte.ITENS_POR_PAGINA:
            break
    return itens


def processar_item(conn, fonte_id: str, codigo_ibge: int, item: fonte.ItemListagem) -> int:
    resposta = requests.get(item.url, headers=HEADERS, timeout=20)
    resposta.raise_for_status()

    documentos = fonte.listar_documentos(resposta.text)
    anexo = fonte.escolher_pdf_anexo(documentos)
    if anexo is None:
        print(f"    aviso: '{item.titulo}' sem PDF localizável, pulando")
        return 0

    pdf_resposta = requests.get(anexo.url, headers=HEADERS, timeout=60)
    pdf_resposta.raise_for_status()

    texto, tabelas = _extrair_texto_e_tabelas(pdf_resposta.content)
    edital = fonte.extrair_edital(texto, tabelas)
    if not edital.cargos:
        print(f"    aviso: nenhum cargo reconhecido no ANEXO I de '{item.titulo}' ({anexo.url})")
        return 0

    numero_edital = edital.numero_edital
    total = 0
    for cargo in edital.cargos:
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=fonte.identificador_externo(item.url, cargo.nome),
            orgao=fonte.ORGAO_PADRAO,
            cargo=cargo.nome,
            salario=cargo.vencimento_inicial,
            salario_tipo="mensal" if cargo.vencimento_inicial is not None else None,
            tipo_oportunidade="processo_seletivo_temporario",
            numero_edital=numero_edital,
            data_publicacao=item.data_publicacao,
            inscricoes_inicio=edital.inscricoes_inicio,
            inscricoes_fim=edital.inscricoes_fim,
            status="aberta",
            resumo=f"{item.titulo} — {cargo.nome} ({cargo.requisitos})",
            url_evidencia=anexo.url,
            tipo_documento="pdf",
            texto_extraido=None,
            numero_vagas=cargo.numero_vagas,
            taxa_inscricao=cargo.taxa_inscricao,
            carga_horaria=cargo.carga_horaria,
            requisitos=cargo.requisitos,
            banca_organizadora=edital.banca_organizadora,
            tem_prova=edital.tem_prova,
            exige_curriculo=edital.exige_curriculo,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        salario_str = f"R$ {cargo.vencimento_inicial:.2f}" if cargo.vencimento_inicial else "salário não identificado"
        print(f"    {cargo.nome} ({salario_str}): vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    itens = _listar_todos_os_itens()
    candidatos = [i for i in itens if fonte.eh_edital_abertura(i.titulo)]
    print(f"{len(candidatos)} edital(is) de abertura de {len(itens)} item(ns) listado(s).")

    conn = db.conectar()
    try:
        codigo_ibge = db.buscar_codigo_ibge_local(conn, fonte.MUNICIPIO, fonte.UF) or ibge.buscar_codigo_ibge(
            fonte.MUNICIPIO, fonte.UF
        )
        if codigo_ibge is None:
            print(f"aviso: município '{fonte.MUNICIPIO}/{fonte.UF}' não encontrado no IBGE, abortando")
            return
        db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=fonte.MUNICIPIO, uf=fonte.UF)

        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=fonte.BASE_URL, tipo="oficial", uf=fonte.UF)
        conn.commit()

        total_geral = 0
        for item in candidatos:
            print(f"Processando '{item.titulo}' ({item.url})...")
            try:
                # savepoint por item: erro num edital não deixa a
                # transação inteira do lote em estado abortado.
                with conn.transaction():
                    total_geral += processar_item(conn, fonte_id, codigo_ibge, item)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 item derrubar o lote inteiro
                print(f"  ERRO processando '{item.titulo}': {exc}")

        db.registrar_cobertura_municipio(
            conn,
            fonte="sao_sebastiao_do_oeste_mg",
            municipio_id=codigo_ibge,
            status="coberto" if candidatos else "sem_dados",
        )
        conn.commit()

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_sao_sebastiao_do_oeste_mg.py"):
        main()
