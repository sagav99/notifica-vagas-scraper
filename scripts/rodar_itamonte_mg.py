#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Prefeitura de Itamonte/MG (`edital.php`,
site próprio — banca CONSCAM só aparece dentro dos PDFs, não é um índice
central de descoberta como as demais fontes deste projeto).

Cargo/vagas/carga horária/salário vêm 100% de extração determinística de
PDF (`itamonte_mg.extrair_tabela_funcoes`/`mesclar_versoes`) — **sem
Gemini**: os PDFs da CONSCAM são texto real (não escaneado), e a tabela
"Funções | Vagas | Carga Horária | Salário Base | Requisitos | Taxa de
Inscrição" é estruturada o bastante pra não precisar de camada de
auditoria/IA aqui. Ver docstring de `fontes/itamonte_mg.py` pro achado
completo (link de download sem extensão que na real é um ZIP com várias
versões do mesmo edital, merge por seção entre rerratificações, etc).

Uso: python scripts/rodar_itamonte_mg.py
Requer DATABASE_URL no ambiente.
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pdfplumber
import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.fontes import itamonte_mg

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Prefeitura de Itamonte"
ORGAO_PADRAO = f"Prefeitura Municipal de {itamonte_mg.MUNICIPIO}"


def _extrair_texto_pdf(conteudo_pdf: bytes) -> str:
    # `use_text_flow=True` preserva a ordem do stream de conteúdo do PDF
    # (a mesma ordem "linha da tabela por linha da tabela" que os regexes
    # de `itamonte_mg.py` esperam) em vez da reconstrução espacial padrão
    # do pdfplumber, que embaralha as colunas desta tabela em específico
    # (achado real, ver docstring do módulo).
    with pdfplumber.open(BytesIO(conteudo_pdf)) as pdf:
        return "\n".join((pagina.extract_text(use_text_flow=True) or "") for pagina in pdf.pages)


def _montar_resumo(item: itamonte_mg.ItemListagem, funcao: itamonte_mg.FuncaoTabela) -> str:
    resumo = f"{item.titulo} — {funcao.cargo}"
    detalhes: list[str] = []
    if funcao.vagas is not None:
        detalhes.append(f"{funcao.vagas} vaga(s)")
    if funcao.carga_horaria:
        detalhes.append(funcao.carga_horaria)
    if funcao.requisitos:
        detalhes.append(funcao.requisitos)
    if detalhes:
        resumo += f" ({'; '.join(detalhes)})"
    return resumo


def processar_item(conn, fonte_id: str, item: itamonte_mg.ItemListagem) -> int:
    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=60)
    resposta.raise_for_status()

    pdfs_bytes = itamonte_mg.extrair_pdfs(resposta.content)
    if not pdfs_bytes:
        print(f"  aviso: '{item.titulo}' não é PDF nem ZIP de PDFs (formato inesperado), pulando")
        return 0

    textos = [_extrair_texto_pdf(pdf_bytes) for pdf_bytes in pdfs_bytes]

    funcoes = itamonte_mg.mesclar_versoes(textos)
    if not funcoes:
        print(f"  aviso: '{item.titulo}' sem tabela de funções reconhecida em nenhuma versão do PDF, pulando")
        return 0

    codigo_ibge = db.buscar_codigo_ibge_local(
        conn, itamonte_mg.MUNICIPIO, itamonte_mg.UF
    ) or ibge.buscar_codigo_ibge(itamonte_mg.MUNICIPIO, itamonte_mg.UF)
    if codigo_ibge is None:
        print(f"  aviso: município '{itamonte_mg.MUNICIPIO}/{itamonte_mg.UF}' não encontrado no IBGE, pulando")
        return 0

    datas = itamonte_mg.mesclar_datas(textos)
    numero_processo = itamonte_mg.extrair_numero_processo(item.titulo)

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=itamonte_mg.MUNICIPIO, uf=itamonte_mg.UF)

    total = 0
    for funcao in funcoes:
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=itamonte_mg.identificador_externo(numero_processo, funcao.cargo),
            orgao=ORGAO_PADRAO,
            cargo=funcao.cargo,
            salario=funcao.salario,
            salario_tipo="mensal" if funcao.salario is not None else None,
            tipo_oportunidade="processo_seletivo_temporario",
            numero_edital=numero_processo,
            data_publicacao=datas.data_publicacao,
            inscricoes_inicio=datas.inscricoes_inicio,
            inscricoes_fim=datas.inscricoes_fim,
            status="aberta",
            resumo=_montar_resumo(item, funcao),
            url_evidencia=item.url,
            tipo_documento="pdf",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        salario_str = f"R$ {funcao.salario:.2f}" if funcao.salario else "salário não identificado"
        print(f"    {funcao.cargo} ({salario_str}, {funcao.carga_horaria or 'carga horária não informada'}): vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    resposta = requests.get(itamonte_mg.URL_LISTAGEM, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    # a página declara charset iso-8859-1 no <meta>, mas o conteúdo real
    # (pelo menos o item mais recente, o que interessa pro cron) é UTF-8 —
    # ver docstring de `fontes/itamonte_mg.py`.
    resposta.encoding = resposta.apparent_encoding

    itens = itamonte_mg.listar_documentos(resposta.text)
    processos = itamonte_mg.filtrar_processos_seletivos(itens)
    print(f"{len(processos)} processo(s) seletivo(s)/concurso(s) de {len(itens)} ato(s) listados em edital.php.")

    conn = db.conectar()
    try:
        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=itamonte_mg.URL_LISTAGEM, tipo="oficial", uf=itamonte_mg.UF)
        conn.commit()

        total_geral = 0
        for item in processos:
            print(f"Processando {item.titulo} ({item.url})...")
            try:
                # savepoint por item: erro num processo não deixa a
                # transação inteira do lote em estado abortado.
                with conn.transaction():
                    total_geral += processar_item(conn, fonte_id, item)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 processo derrubar o lote inteiro
                print(f"  ERRO processando '{item.titulo}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_itamonte_mg.py"):
        main()
