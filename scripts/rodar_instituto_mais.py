#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Instituto Mais (`institutomais.org.br`):
lê `/Concursos/ConcursosAbertos` (plataforma antiga — único índice de
descoberta conhecido, ver `fontes/instituto_mais.py`), processa só a seção
"Inscrições Abertas" (mesma decisão de escopo do INEPAM/IMAM: inscrição já
fechada = nada novo pra notificar), casa o município contra o título via
`fgv.encontrar_municipio` (títulos não seguem 1 padrão fixo o bastante pra
regex estrita, ex: "Prefeitura Municipal de Jarinu - ..." sem UF,
"Irmandade da Santa Casa ... de São José do Rio Preto / SP - ...", "FITO -
Fundação Instituto Tecnológico de Osasco / SP - ..." — mesmo motivo de
`rodar_ache_concursos.py` reusar essa função em vez de uma regex tipo
`msconcursos.extrair_municipio_uf`).

**Cargo/quantidade de vagas vêm da tabela HTML "Quadro de Vagas"**
(`instituto_mais.listar_quadro_vagas`), não do Gemini — achado real (ver
docstring do módulo): essa tabela existe em HTML puro, then o PDF/Gemini
fica reservado só pra completar salário/tipo de vínculo/datas. Isso é
deliberado e mais forte que o padrão de FGV/Actcon/Ache/INEPAM/PBH-IBFC
(que dependem 100% do Gemini pra saber até quais cargos existem): aqui,
**se o Gemini falhar (cota esgotada, erro de rede, PDF indisponível) a
vaga ainda é gravada com salário desconhecido**, em vez de ser perdida
inteira — prioridade #1 do produto (CLAUDE.md) é nunca perder cargo
médico de vista, e isso vale inclusive quando a camada de auditoria (Gemini)
está indisponível.

Uso: python scripts/rodar_instituto_mais.py
Requer DATABASE_URL no ambiente; GEMINI_API_KEY é usado quando disponível
mas sua ausência/falha não impede a gravação dos cargos (só deixa
salário/tipo de vínculo em branco, ver `processar_concurso`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf, ibge
from notifica_vagas_scraper.fontes import fgv
from notifica_vagas_scraper.fontes import instituto_mais

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Instituto Mais"

#: portfólio confirmado na investigação é só SP, mas o filtro cobre MG
#: também por segurança (mesmo padrão de rodar_ache_concursos.py) — se a
#: banca abrir concurso em MG no futuro, é coberto automaticamente.
UFS_DO_PROJETO = ["MG", "SP"]


def _montar_resumo(item: instituto_mais.ItemListagem, vaga: instituto_mais.VagaQuadro) -> str:
    resumo = f"{item.titulo} — {vaga.cargo}"
    detalhes: list[str] = []
    if vaga.vagas is not None:
        detalhes.append(f"{vaga.vagas} vaga(s) no momento" if vaga.vagas else "0 vaga(s) no momento")
    if vaga.requisitos:
        detalhes.append(vaga.requisitos)
    if detalhes:
        resumo += f" ({'; '.join(detalhes)})"
    return resumo


def _extrair_com_gemini(url_pdf: str) -> dict:
    """Isola a chamada de rede+Gemini pra nunca deixar uma falha aqui
    derrubar a gravação dos cargos (que já vêm do HTML, ver docstring do
    módulo) — devolve `{}` (equivalente a "nenhum dado extra") em qualquer
    erro, só registrando o aviso."""
    try:
        pdf_resposta = requests.get(url_pdf, headers={"User-Agent": USER_AGENT}, timeout=60)
        pdf_resposta.raise_for_status()
        return gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    except (requests.exceptions.RequestException, gemini_pdf.ErroExtracaoGemini) as exc:
        print(f"  aviso: falha ao extrair salário via Gemini de {url_pdf}: {exc}")
        return {}


def processar_concurso(conn, fonte_id: str, item: instituto_mais.ItemListagem, municipio: str, uf: str) -> int:
    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    quadro = instituto_mais.listar_quadro_vagas(resposta.text)
    if not quadro:
        # sem "Quadro de Vagas" em HTML — layout inesperado ou concurso já
        # migrado pra plataforma nova (Blazor, ver docstring do módulo);
        # não inventamos fallback não testado, só registramos e pulamos.
        print(f"  aviso: '{item.titulo}' sem 'Quadro de Vagas' em HTML, pulando")
        return 0

    codigo_ibge = ibge.buscar_codigo_ibge(municipio, uf)
    if codigo_ibge is None:
        print(f"  aviso: município '{municipio}/{uf}' não encontrado no IBGE, pulando")
        return 0

    documentos = instituto_mais.listar_documentos(resposta.text)
    edital = instituto_mais.escolher_edital(documentos)

    extraido: dict = {}
    if edital is not None:
        extraido = _extrair_com_gemini(edital.url_pdf)
    else:
        print(f"  aviso: '{item.titulo}' sem documento de edital identificado, seguindo só com o HTML")

    vagas_gemini_por_cargo = {
        instituto_mais.normalizar_cargo(vaga["cargo"]): vaga
        for vaga in extraido.get("vagas", [])
        if vaga.get("cargo")
    }

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=municipio, uf=uf)
    orgao = extraido.get("orgao") or item.titulo
    numero_edital = extraido.get("numero_edital") or instituto_mais.extrair_numero_edital(item.titulo)
    tipo_oportunidade = extraido.get("tipo_oportunidade")
    data_publicacao = extraido.get("data_publicacao")
    inscricoes_inicio = extraido.get("inscricoes_inicio")
    inscricoes_fim = extraido.get("inscricoes_fim")
    url_evidencia = edital.url_pdf if edital is not None else item.url
    tipo_documento = "pdf" if edital is not None else "pagina_html"

    total = 0
    for vaga in quadro:
        vaga_gemini = vagas_gemini_por_cargo.get(instituto_mais.normalizar_cargo(vaga.cargo))
        salario = vaga_gemini.get("salario") if vaga_gemini else None
        salario_tipo = vaga_gemini.get("salario_tipo") if vaga_gemini else None
        requisitos = vaga.requisitos or (vaga_gemini.get("requisitos") if vaga_gemini else None)
        vaga_com_requisitos = instituto_mais.VagaQuadro(
            codigo=vaga.codigo, cargo=vaga.cargo, vagas=vaga.vagas, requisitos=requisitos
        )

        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=instituto_mais.identificador_externo(item.concurso_id, vaga.cargo),
            orgao=orgao,
            cargo=vaga.cargo,
            salario=salario,
            salario_tipo=salario_tipo,
            tipo_oportunidade=tipo_oportunidade,
            numero_edital=numero_edital,
            data_publicacao=data_publicacao,
            inscricoes_inicio=inscricoes_inicio,
            inscricoes_fim=inscricoes_fim,
            status="aberta",
            resumo=_montar_resumo(item, vaga_com_requisitos),
            url_evidencia=url_evidencia,
            tipo_documento=tipo_documento,
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        salario_str = f"R$ {salario:.2f}" if salario else "salário não identificado"
        print(f"    {vaga.cargo} ({salario_str}): vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    resposta = requests.get(
        f"{instituto_mais.BASE_URL_ANTIGA}/Concursos/ConcursosAbertos",
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    resposta.raise_for_status()
    todos = instituto_mais.listar_concursos(resposta.text)
    abertos = [i for i in todos if i.status == "aberta"]

    print(f"{len(abertos)} concurso(s) com inscrição aberta (de {len(todos)} listados nas 3 seções).")

    conn = db.conectar()
    try:
        municipios = db.listar_nomes_municipios(conn, ufs=UFS_DO_PROJETO)
        print(f"{len(municipios)} município(s) de MG/SP carregados pra match.")

        fonte_id = db.upsert_fonte(
            conn, nome=FONTE_NOME, url=instituto_mais.BASE_URL_ANTIGA, tipo="oficial", uf="SP"
        )
        conn.commit()

        total_geral = 0
        for item in abertos:
            match = fgv.encontrar_municipio(item.titulo, municipios)
            if match is None:
                print(f"  aviso: município não identificado em '{item.titulo}', pulando")
                continue
            municipio, uf = match

            print(f"Processando {item.titulo} -> {municipio}/{uf} ({item.url})...")
            try:
                # savepoint por item: erro num concurso não deixa a
                # transação inteira do lote em estado abortado.
                with conn.transaction():
                    total_geral += processar_concurso(conn, fonte_id, item, municipio, uf)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 concurso derrubar o lote inteiro
                print(f"  ERRO processando '{item.titulo}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_instituto_mais.py"):
        main()
