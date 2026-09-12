#!/usr/bin/env python3
"""Entrypoint do cron pra fonte IBAM-SP (`ibamsp-concursos.org.br`):
descobre processos com inscrição aberta em `/index/abertos/` e extrai
cargo/escolaridade/salário/carga horária/quantidade **direto da tabela
"Vagas" em HTML** da página de detalhe (`/informacoes/<id>/`) — mesma
plataforma ProSeleta da Avança SP/JCM/ACCESS, Gemini NÃO é necessário pro
caso comum (fica reservado pra auditoria, não para a coleta em si).

Diferente da Avança SP: o card de listagem não tem prefixo de órgão
("PREFEITURA MUNICIPAL DE ...") pra recortar — o município (quando existe)
é só o primeiro segmento do título cru ("MOGI MIRIM - CONCURSO PÚBLICO -
02/2026 - EDITAL 01"). Por isso o casamento usa `fgv.encontrar_municipio`
contra a lista de municípios de MG/SP já cadastrados (mesmo padrão de
`rodar_nosso_rumo.py`) em vez de whitelist de prefixo — item sem
município reconhecível (ex: Vestibular da Santa Casa de São Paulo) é
pulado sem alarme, não é erro.

Uso: python scripts/rodar_ibamsp.py
Requer DATABASE_URL no ambiente (não precisa de GEMINI_API_KEY pro caso
comum).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.fontes import fgv, ibamsp

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "IBAM-SP"

#: cobertura confirmada na investigação é só SP (N≥11 municípios), mas o
#: filtro cobre MG também por segurança — mesmo padrão de
#: rodar_nosso_rumo.py/rodar_instituto_mais.py: se a banca abrir concurso
#: em MG no futuro, é coberto automaticamente sem precisar tocar no parser.
UFS_DO_PROJETO = ["MG", "SP"]


def _montar_resumo(item: ibamsp.ItemListagem, vaga: ibamsp.VagaIbamSp) -> str:
    resumo = f"{item.tipo_processo} nº {item.numero_edital} — {vaga.cargo}"
    detalhes: list[str] = []
    if vaga.escolaridade:
        detalhes.append(vaga.escolaridade)
    if vaga.carga_horaria:
        detalhes.append(vaga.carga_horaria)
    if vaga.salario is None and vaga.salario_texto:
        # remuneração por hora/aula — não convertida pra mensal (ver
        # fontes/ibamsp.py), mas o texto original não pode se perder.
        detalhes.append(vaga.salario_texto)
    if vaga.cadastro_reserva:
        detalhes.append("cadastro de reserva")
    if detalhes:
        resumo += f" ({', '.join(detalhes)})"
    return resumo


def processar_processo(conn, fonte_id: str, item: ibamsp.ItemListagem, municipio: str, uf: str) -> int:
    codigo_ibge = db.buscar_codigo_ibge_local(conn, municipio, uf) or ibge.buscar_codigo_ibge(municipio, uf)
    if codigo_ibge is None:
        print(f"  aviso: município '{municipio}/{uf}' não encontrado no IBGE, pulando")
        return 0

    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    vagas_extraidas = ibamsp.listar_vagas_html(resposta.text)
    if not vagas_extraidas:
        print(f"  aviso: nenhuma vaga extraída de {item.url} (tabela ausente ou formato inesperado)")
        return 0

    documentos = ibamsp.listar_documentos(resposta.text)
    edital = ibamsp.escolher_edital(documentos)
    data_publicacao = edital.data if edital else None
    inscricoes_inicio, inscricoes_fim = ibamsp.extrair_periodo_inscricoes(resposta.text)

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=municipio, uf=uf)
    orgao = f"{item.tipo_processo} de {municipio}/{uf}"

    total = 0
    for vaga in vagas_extraidas:
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=ibamsp.identificador_externo(item.processo_id, vaga),
            orgao=orgao,
            cargo=vaga.cargo,
            salario=vaga.salario,
            salario_tipo="mensal" if vaga.salario is not None else None,
            tipo_oportunidade=None,
            numero_edital=item.numero_edital,
            data_publicacao=data_publicacao,
            inscricoes_inicio=inscricoes_inicio,
            inscricoes_fim=inscricoes_fim,
            status="aberta",
            resumo=_montar_resumo(item, vaga),
            url_evidencia=item.url,
            tipo_documento="pagina_html",
            texto_extraido=None,
            banca_organizadora="IBAM-SP",
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {vaga.cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    resposta = requests.get(f"{ibamsp.BASE_URL}/index/abertos/", headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    itens = ibamsp.listar_processos_abertos(resposta.text)

    print(f"{len(itens)} processo(s) com inscrição aberta no IBAM-SP.")

    conn = db.conectar()
    try:
        municipios = db.listar_nomes_municipios(conn, ufs=UFS_DO_PROJETO)
        print(f"{len(municipios)} município(s) de MG/SP carregados pra match.")

        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=ibamsp.BASE_URL, tipo="oficial", uf="SP")
        conn.commit()

        total_geral = 0
        for item in itens:
            # entidade sem município correspondente (ex: Vestibular da
            # Faculdade de Ciências Médicas da Santa Casa de São Paulo)
            # não mapeia 1:1 pra um município do produto, é pulada sem
            # aviso alarmante (esperado, não erro).
            match = fgv.encontrar_municipio(item.titulo, municipios)
            if match is None:
                continue
            municipio, uf = match

            print(f"Processando {item.titulo} -> {municipio}/{uf}...")
            try:
                # savepoint por item: erro num processo não deixa a
                # transação inteira do lote em estado abortado.
                with conn.transaction():
                    total_geral += processar_processo(conn, fonte_id, item, municipio, uf)
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
    with db.rastrear_execucao("rodar_ibamsp.py"):
        main()
