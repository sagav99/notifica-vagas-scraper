#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Prefeitura de Sorocaba/SP: portal de
notícias em WordPress (`noticias.sorocaba.sp.gov.br`), banca organizadora
Fundação VUNESP. Ver docstring de `fontes/sorocaba_sp.py` pros achados
completos (descoberta via listagem recente, não `search=`; PDF do edital
extraído de forma 100% determinística via `pdfplumber.extract_tables()`,
sem Gemini; achado do bloqueio temporário de "período eleitoral" do
WordPress sobre o post do Concurso 01/2026).

Uso: python scripts/rodar_sorocaba.py
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
from notifica_vagas_scraper.fontes import sorocaba_sp

USER_AGENT = sorocaba_sp.USER_AGENT
FONTE_NOME = "Prefeitura de Sorocaba/SP"
POR_PAGINA = 20


def _baixar_pdf_do_post(post: sorocaba_sp.Post) -> bytes | None:
    url_pdf = sorocaba_sp.extrair_pdf_do_html(post.conteudo_html)
    if url_pdf is None:
        resposta_media = requests.get(
            sorocaba_sp.URL_MEDIA,
            params={"parent": post.id},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        resposta_media.raise_for_status()
        dados_media = resposta_media.json()
        if not isinstance(dados_media, list):
            return None
        url_pdf = sorocaba_sp.escolher_pdf_de_media(dados_media)

    if url_pdf is None:
        return None

    resposta_pdf = requests.get(url_pdf, headers={"User-Agent": USER_AGENT}, timeout=60)
    resposta_pdf.raise_for_status()
    return resposta_pdf.content


def _extrair_texto_e_tabelas(conteudo_pdf: bytes) -> tuple[str, list[list[list[str | None]]]]:
    with pdfplumber.open(BytesIO(conteudo_pdf)) as pdf:
        texto = "\n".join((pagina.extract_text() or "") for pagina in pdf.pages)
        tabelas = [tabela for pagina in pdf.pages for tabela in pagina.extract_tables()]
    return texto, tabelas


def processar_post(conn, fonte_id: str, codigo_ibge: int, post: sorocaba_sp.Post) -> int:
    conteudo_pdf = _baixar_pdf_do_post(post)
    if conteudo_pdf is None:
        print(f"    aviso: '{post.titulo}' sem PDF de edital localizável (nem no post, nem em media)")
        return 0

    texto, tabelas = _extrair_texto_e_tabelas(conteudo_pdf)
    edital = sorocaba_sp.extrair_edital(texto, tabelas)
    if not edital.cargos:
        print(f"    aviso: nenhum cargo reconhecido na tabela do PDF de '{post.titulo}'")
        return 0

    total = 0
    for cargo in edital.cargos:
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=sorocaba_sp.identificador_externo(edital.numero_edital, cargo.nome),
            orgao=sorocaba_sp.ORGAO_PADRAO,
            cargo=cargo.nome,
            salario=cargo.valor_hora,
            salario_tipo="hora",
            tipo_oportunidade="concurso_efetivo",
            numero_edital=edital.numero_edital,
            data_publicacao=post.data,
            inscricoes_inicio=None,
            inscricoes_fim=None,
            status="aberta",
            resumo=f"{post.titulo} — {cargo.nome} ({cargo.requisitos})",
            url_evidencia=post.link,
            tipo_documento="pdf",
            texto_extraido=None,
            numero_vagas=cargo.numero_vagas,
            carga_horaria=f"{cargo.carga_horaria_semanal}h semanais" if cargo.carga_horaria_semanal else None,
            requisitos=cargo.requisitos,
            banca_organizadora=edital.banca_organizadora,
            tem_prova=edital.tem_prova,
            exige_curriculo=edital.exige_curriculo,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo.nome}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    resposta = requests.get(
        sorocaba_sp.URL_POSTS,
        params={"per_page": POR_PAGINA, "orderby": "date", "order": "desc"},
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    resposta.raise_for_status()
    dados = resposta.json()
    if not isinstance(dados, list):
        print("aviso: resposta inesperada de /wp-json/wp/v2/posts, abortando")
        return

    posts = sorocaba_sp.listar_posts_recentes(dados)
    candidatos = [p for p in posts if sorocaba_sp.eh_post_candidato(p)]
    print(f"{len(candidatos)} post(s) candidato(s) a edital de concurso/processo seletivo de {len(posts)} listado(s).")

    conn = db.conectar()
    try:
        codigo_ibge = db.buscar_codigo_ibge_local(
            conn, sorocaba_sp.MUNICIPIO, sorocaba_sp.UF
        ) or ibge.buscar_codigo_ibge(sorocaba_sp.MUNICIPIO, sorocaba_sp.UF)
        if codigo_ibge is None:
            print(f"aviso: município '{sorocaba_sp.MUNICIPIO}/{sorocaba_sp.UF}' não encontrado no IBGE, abortando")
            return
        db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=sorocaba_sp.MUNICIPIO, uf=sorocaba_sp.UF)

        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=sorocaba_sp.BASE_URL, tipo="oficial", uf=sorocaba_sp.UF)
        conn.commit()

        total_geral = 0
        for post in candidatos:
            print(f"Processando '{post.titulo}' ({post.link})...")
            try:
                # savepoint por post: erro num post não deixa a transação
                # inteira do lote em estado abortado.
                with conn.transaction():
                    total_geral += processar_post(conn, fonte_id, codigo_ibge, post)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 post derrubar o lote inteiro
                print(f"  ERRO processando '{post.titulo}': {exc}")

        # ver docstring do módulo sobre o achado de bloqueio de "período
        # eleitoral" — sem sinal pra distinguir "genuinamente sem post" de
        # "post existe mas está temporariamente bloqueado", por isso só
        # 'coberto'/'sem_dados' aqui, sem status 'erro' (migration 024).
        db.registrar_cobertura_municipio(
            conn,
            fonte="sorocaba_sp",
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
    with db.rastrear_execucao("rodar_sorocaba.py"):
        main()
