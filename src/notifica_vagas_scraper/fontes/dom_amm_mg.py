"""Parser de matérias do Diário Oficial dos Municípios Mineiros (AMM-MG).

Estrutura investigada (ver docs/fixtures/dom_amm_mg/ no repo principal e
docs/investigacao_fontes_2026-08-31.md): cada matéria é uma página HTML com
uma tag <article> contendo cabeçalho (entidade/órgão), título do ato, corpo
(às vezes com uma ou mais tabelas HTML de cargo/vagas/salário), assinatura,
"Publicado por" + "Código Identificador", e rodapé com data de publicação e
edição. Sem API/RSS; sem bloqueio anti-bot encontrado (HTML server-rendered
simples, sem precisar de browser headless).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from bs4 import BeautifulSoup

from ..formatacao import parsear_salario_brl

BASE_URL = "https://www.diariomunicipal.com.br"


@dataclass
class VagaExtraida:
    orgao: str
    cargo: str
    vagas_qtd: int | None
    salario: Decimal | None
    carga_horaria: str | None
    requisitos: str | None
    taxa_inscricao: str | None
    numero_edital: str | None
    tipo_processo: str | None  # "Processo Seletivo Público" / "Processo Seletivo Simplificado" / etc.
    data_publicacao: date | None
    edicao: str | None
    inscricoes_inicio: date | None
    inscricoes_fim: date | None
    codigo_identificador: str | None
    url: str


def _normalizar_cabecalho(texto: str) -> str:
    sem_asterisco = texto.replace("*", "").strip()
    sem_acento = unicodedata.normalize("NFKD", sem_asterisco).encode("ascii", "ignore").decode("ascii")
    return sem_acento.strip().lower()


def _parsear_data(texto: str) -> date | None:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _extrair_numero_edital(texto: str) -> tuple[str | None, str | None]:
    match = re.search(
        r"(Processo Seletivo (?:P[úu]blico|Simplificado)|Concurso P[úu]blico)\s*n[ºo°]?\s*([\d./]+)",
        texto,
        re.IGNORECASE,
    )
    if not match:
        return None, None
    return match.group(2).strip(), match.group(1).strip().title()


def _extrair_codigo_identificador(texto: str) -> str | None:
    match = re.search(r"C[óo]digo Identificador:?\s*([A-Za-z0-9]+)", texto)
    return match.group(1).strip() if match else None


def _extrair_publicacao(texto: str) -> tuple[date | None, str | None]:
    match = re.search(
        r"publicad[ao] no Di[áa]rio Oficial dos Munic[íi]pios Mineiros no dia\s*(\d{2}/\d{2}/\d{4})\.?\s*Edi[çc][ãa]o\s*(\d+)",
        texto,
        re.IGNORECASE,
    )
    if not match:
        return None, None
    return _parsear_data(match.group(1)), match.group(2).strip()


def _extrair_periodo_inscricoes(texto: str) -> tuple[date | None, date | None]:
    match = re.search(
        r"do dia\s*(\d{2}/\d{2}/\d{4}).{0,40}?do dia\s*(\d{2}/\d{2}/\d{4})",
        texto,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None, None
    return _parsear_data(match.group(1)), _parsear_data(match.group(2))


def _extrair_orgao(texto: str) -> str | None:
    match = re.search(r"(PREFEITURA MUNICIPAL DE [^\n]+|C[ÂA]MARA MUNICIPAL DE [^\n]+)", texto)
    return match.group(1).strip() if match else None


def parsear_materia(html: str, url: str) -> list[VagaExtraida]:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article")
    if article is None:
        return []

    texto_completo = article.get_text("\n", strip=True)

    orgao = _extrair_orgao(texto_completo) or ""
    numero_edital, tipo_processo = _extrair_numero_edital(texto_completo)
    codigo_identificador = _extrair_codigo_identificador(texto_completo)
    data_publicacao, edicao = _extrair_publicacao(texto_completo)
    inscricoes_inicio, inscricoes_fim = _extrair_periodo_inscricoes(texto_completo)

    vagas: list[VagaExtraida] = []

    for tabela in article.find_all("table"):
        linhas = tabela.find_all("tr")
        if not linhas:
            continue

        cabecalho = [_normalizar_cabecalho(c.get_text(strip=True)) for c in linhas[0].find_all(["td", "th"])]
        if "funcoes" not in cabecalho and "funcao" not in cabecalho:
            continue  # tabela não é de cargos/vagas

        def indice(*nomes: str) -> int | None:
            for nome in nomes:
                if nome in cabecalho:
                    return cabecalho.index(nome)
            return None

        idx_cargo = indice("funcoes", "funcao")
        idx_vagas = indice("vagas")
        idx_carga = indice("carga horaria")
        idx_salario = indice("salario base", "salario")
        idx_requisitos = indice("requisitos")
        idx_taxa = indice("taxa de inscricao")

        for linha in linhas[1:]:
            celulas = [c.get_text(strip=True) for c in linha.find_all(["td", "th"])]
            if idx_cargo is None or idx_cargo >= len(celulas) or not celulas[idx_cargo]:
                continue

            vagas_qtd = None
            if idx_vagas is not None and idx_vagas < len(celulas):
                match_qtd = re.search(r"\d+", celulas[idx_vagas])
                vagas_qtd = int(match_qtd.group()) if match_qtd else None

            vagas.append(
                VagaExtraida(
                    orgao=orgao,
                    cargo=celulas[idx_cargo],
                    vagas_qtd=vagas_qtd,
                    salario=parsear_salario_brl(celulas[idx_salario]) if idx_salario is not None and idx_salario < len(celulas) else None,
                    carga_horaria=celulas[idx_carga] if idx_carga is not None and idx_carga < len(celulas) else None,
                    requisitos=celulas[idx_requisitos] if idx_requisitos is not None and idx_requisitos < len(celulas) else None,
                    taxa_inscricao=celulas[idx_taxa] if idx_taxa is not None and idx_taxa < len(celulas) else None,
                    numero_edital=numero_edital,
                    tipo_processo=tipo_processo,
                    data_publicacao=data_publicacao,
                    edicao=edicao,
                    inscricoes_inicio=inscricoes_inicio,
                    inscricoes_fim=inscricoes_fim,
                    codigo_identificador=codigo_identificador,
                    url=url,
                )
            )

    return vagas


def identificador_externo(vaga: VagaExtraida) -> str:
    """Chave de dedup de evidência: código identificador da matéria (único
    por publicação) + slug do cargo (uma matéria pode listar N cargos)."""
    slug_cargo = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", vaga.cargo).encode("ascii", "ignore").decode("ascii").lower()).strip("-")
    base = vaga.codigo_identificador or vaga.url
    return f"{base}-{slug_cargo}"
