#!/usr/bin/env python3
"""Backtest do monitor de convocações com publicações PASSADAS.

Pega convocações reais já publicadas numa janela, extrai nomes de candidatos delas (verdade
conhecida) e confere se o monitor, buscando SÓ pelo nome (sem município/concurso), reencontra
a mesma publicação. Mede recall; conta também publicações extras (homônimos/outras menções).
Nomes ficam só em memória; a saída traz apenas números e códigos de matéria.

Uso: python scripts/backtest_convocacoes.py --uf MG --inicio 2026-10-01 --fim 2026-10-15 [--amostra 30]
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db  # noqa: F401  (import garante o mesmo ambiente)
from notifica_vagas_scraper.fontes import convocacoes as conv
from notifica_vagas_scraper.fontes import diarios_sul

# "123 ... NOME COMPLETO EM CAIXA ALTA" ou "Nome Completo Capitalizado" depois de número/ordinal
NOME_CAIXA = re.compile(r"(?:^|\s)(?:\d{1,4}\s*[ºo°.\-–)]*\s+)(?:\d[\d/.\-]*\s+)?([A-ZÁÉÍÓÚÂÊÔÃÕÇ]{3,}(?: (?:DE|DA|DO|DAS|DOS|E|[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,})){2,5})(?=\s|$|[,;(])")
NOME_MISTO = re.compile(r"(?:^|\s)(?:\d{1,4}\s*[ºo°.\-–)]*\s+)(?:\d[\d/.\-]*\s+)?([A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]{2,}(?: (?:de|da|do|das|dos|e|[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]{2,})){2,5})(?=\s|$|[,;(])")
PALAVRAS_LIXO = re.compile(r"(CERTID|T[IÍ]TULO|ELEITOR|TRIBUNAL|JUSTI[CÇ]A|DOCUMENTO|INFORMA|APURA|VOTA|SAN[CÇ]|INFRA|SERVI[CÇ]O|TEMPO|DECLARA|COMPROVANTE|EXAME|SA[UÚ]DE|EDUCA|FORMA[CÇ]|DIPLOMA|CARTEIRA|REGISTRO|ESTADUAL|FEDERAL|PREFEITO|SECRET|PUBLIC|PROVA|NOTA|VAGAS|ANEXO|ART|LEI|CPF|RG |ADMISS|NOMEA|CONTRAT|DEFERIDO|INDEFERIDO|HOMOLOG|PREFEITURA|MUNICIPAL|EDITAL|CONVOCA|CONCURSO|SECRETARIA|CARGO|MEDICO|MÉDICO|ENFERMEIR|PROFESSOR|CLASSIFICA|INSCRI|PROCESSO|ANEXO|DECRETO|PORTARIA|ESTADO|JULHO|AGOSTO|SETEMBRO|OUTUBRO)", re.I)


def extrair_nomes(texto: str, limite: int = 2) -> list[str]:
    nomes: list[str] = []
    for padrao in (NOME_CAIXA, NOME_MISTO):
        for m in padrao.finditer(texto):
            nome = m[1].strip()
            if conv._ASSINATURA.match(diarios_sul.normalizar(texto[m.end(1) : m.end(1) + 40])):
                continue  # assinante do ato (prefeito etc.), não candidato
            primeiro, ultimo = nome.split()[0].lower(), nome.split()[-1].lower()
            if primeiro in {"da", "das", "do", "dos", "de", "e"} or ultimo in {"da", "das", "do", "dos", "de", "e", "para", "em"}:
                continue
            if PALAVRAS_LIXO.search(nome) or len(nome.split()) < 3 or nome in nomes:
                continue
            nomes.append(nome)
    random.shuffle(nomes)
    return nomes[:limite]


def pausa() -> None:
    time.sleep(0.8)


def backtest_sigpub(uf: str, inicio: date, fim: date, amostra: int) -> None:
    """Verdade conhecida = convocações achadas por termos INDEPENDENTES da varredura do monitor;
    depois roda o pipeline de produção (`processar_sigpub`, nomes de todos de uma vez, sem concurso)."""
    sessao = requests.Session()
    dias = [inicio + timedelta(days=n) for n in range((fim - inicio).days + 1)]
    independentes: dict[str, object] = {}
    for dia in dias:
        for termo in ("concurso público", "processo seletivo", "edital", "candidatos", "classificados", "lista"):
            for pagina in range(1, 13):
                cs = conv.sigpub_busca.parsear_resultados(conv._buscar_sigpub(sessao, uf, termo, dia, dia, pagina))
                pausa()
                for c in cs:
                    if "prefeitura" in c.entidade.lower() and conv._TITULO_CONVOCACAO.search(diarios_sul.normalizar(c.titulo)):
                        independentes.setdefault(c.codigo, c)
                if len(cs) < 25:
                    break
    cartoes = list(independentes.values())
    random.shuffle(cartoes)
    casos: list[tuple[str, str]] = []
    for c in cartoes:
        if len(casos) >= amostra:
            break
        texto = conv._sem_cabecalho(conv.texto_da_materia(sessao, uf, c.codigo))
        pausa()
        for nome in extrair_nomes(texto, 1):
            casos.append((nome, c.codigo))
    print(f"{uf}: {len(cartoes)} convocações independentes; {len(casos)} nomes de teste.", flush=True)
    acs = [conv.Acompanhamento(f"bt{i}", "u", nome, None, None) for i, (nome, _) in enumerate(casos)]
    achados, lidas, _ = conv.processar_sigpub(sessao, uf, dias, acs, set(), pausa=pausa)
    achou = 0
    perdidos = []
    for i, (nome, codigo) in enumerate(casos):
        if any(a.acompanhamento_id == f"bt{i}" and a.chave == f"sigpub-{uf.lower()}:{codigo}" for a in achados):
            achou += 1
        else:
            perdidos.append(codigo)
    extras = len(achados) - achou
    print(f"{uf}: pipeline leu {lidas} publicações; recall {achou}/{len(casos)} ({100 * achou / max(1, len(casos)):.0f}%); avisos extras: {extras}.", flush=True)
    for codigo in perdidos:
        print(f"   não achou: {conv.url_materia(uf, codigo)}", flush=True)


def backtest_sc(inicio: date, fim: date, amostra: int) -> None:
    sessao = requests.Session()
    dias = (fim - inicio).days
    zips = diarios_sul.listar_zips_sc(dias, hoje=fim, sessao=sessao)
    pubs = []
    for z in zips:
        r = sessao.get(z["url"], headers={"User-Agent": diarios_sul.USER_AGENT}, timeout=180)
        r.raise_for_status()
        pubs += diarios_sul.ler_zip_sc(r.content)
    convoc = [p for p in pubs if conv._TITULO_CONVOCACAO.search(diarios_sul.normalizar(p.titulo))]
    random.shuffle(convoc)
    casos = []
    for p in convoc:
        if len(casos) >= amostra:
            break
        for nome in extrair_nomes(diarios_sul.texto_limpo(p.texto), 1):
            casos.append((nome, p))
    print(f"SC: {len(pubs)} publicações, {len(convoc)} de convocação; {len(casos)} nomes de teste.")
    acompanhamentos = [conv.Acompanhamento(f"bt{i}", "u", nome, None, None) for i, (nome, _) in enumerate(casos)]
    achados = conv.achados_sc(pubs, acompanhamentos)
    achou = sum(1 for i, (_, p) in enumerate(casos) if any(a.acompanhamento_id == f"bt{i}" and a.chave == f"ciga-sc:{p.codigo}" for a in achados))
    extras = len(achados) - achou
    print(f"SC: recall {achou}/{len(casos)} ({100 * achou / max(1, len(casos)):.0f}%); avisos extras: {extras}.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uf", required=True, choices=["MG", "PR", "RS", "SC", "SP"])
    ap.add_argument("--inicio", required=True)
    ap.add_argument("--fim", required=True)
    ap.add_argument("--amostra", type=int, default=30)
    ap.add_argument("--semente", type=int, default=7)
    args = ap.parse_args()
    random.seed(args.semente)
    inicio, fim = date.fromisoformat(args.inicio), date.fromisoformat(args.fim)
    if args.uf == "SC":
        backtest_sc(inicio, fim, args.amostra)
    else:
        backtest_sigpub(args.uf, inicio, fim, args.amostra)


if __name__ == "__main__":
    main()
