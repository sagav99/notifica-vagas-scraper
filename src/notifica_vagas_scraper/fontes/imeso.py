"""Parser da IMESO (Instituto Mineiro Educar & Sorrir), banca organizadora
que atende ~111 entidades de MG (prefeituras, câmaras municipais e
consórcios intermunicipais).

Estrutura investigada (ver docs/fixtures/imeso/ no repo principal):
- GET /edital lista todos os editais numa única página, sem paginação nem
  AJAX (todas as abas de status — futuros/abertos/andamento/finalizados —
  já vêm no HTML inicial). Cada item tem entidade, tipo+número do processo,
  período de inscrição, id do edital (via link) e status (texto do botão).
- GET /edital/ver/<id> tem 3 seções: Informações (entidade, período,
  "cidade - UF" numa linha própria — funciona igual pra prefeitura, câmara
  e consórcio), Arquivos disponíveis (links de download em S3 público, sem
  bloqueio) e Vagas (`<div id="vagas">`, cargo/requisitos/remuneração já em
  HTML estruturado — não depende de abrir o PDF pra dado básico).
Sem bloqueio anti-bot em nenhuma etapa; sem API/RSS encontrado.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from bs4 import BeautifulSoup

from ..formatacao import parsear_salario_brl

BASE_URL = "https://portal.imeso.com.br"


@dataclass
class ItemListagem:
    edital_id: int
    url: str
    entidade: str
    tipo_processo: str | None
    numero_edital: str | None
    inscricoes_inicio: date | None
    inscricoes_fim: date | None
    status: str | None  # texto do botão: "Inscrições Abertas", "Finalizado" etc.


@dataclass
class VagaImeso:
    orgao: str
    cargo: str
    salario: Decimal | None
    requisitos: str | None
    numero_edital: str | None
    tipo_processo: str | None
    municipio: str | None
    uf: str | None
    inscricoes_inicio: date | None
    inscricoes_fim: date | None
    url: str


def _parsear_data(texto: str) -> date | None:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _extrair_tipo_e_numero(texto: str) -> tuple[str | None, str | None]:
    match = re.search(
        r"(Processo Seletivo (?:P[úu]blico|Simplificado)|Concurso P[úu]blico)\s*[-–]?\s*n?[ºo°]?\s*([\d./]+)",
        texto,
        re.IGNORECASE,
    )
    if not match:
        return None, None
    return match.group(1).strip(), match.group(2).strip()


#: id do <div class="tab-pane"> -> rótulo de status. O texto/title do botão
#: "detalhes-edital" NÃO é confiável (quase todo item mostra "Mais
#: detalhes" genérico) — a aba em que o card está é o sinal real, achado
#: ao inspecionar o HTML real (99/117 itens estavam em "encerrados" com
#: botão idêntico ao de "futuros"/"andamento").
_TAB_PARA_STATUS = {
    "abertos": "Inscrições Abertas",
    "futuros": "Futuro (inscrições ainda não abertas)",
    "andamento": "Em andamento (inscrições encerradas)",
    "encerrados": "Encerrado",
    "suspensos": "Suspenso/cancelado",
}


def listar_editais(html: str) -> list[ItemListagem]:
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []

    for tab in soup.find_all("div", class_="tab-pane"):
        status = _TAB_PARA_STATUS.get(tab.get("id"), tab.get("id"))

        for link in tab.find_all("a", class_="detalhes-edital"):
            match_id = re.search(r"/edital/ver/(\d+)", link.get("href", ""))
            if not match_id:
                continue

            card = link.find_parent("div", class_="row")
            if card is None:
                continue
            textos = [p.get_text(" ", strip=True) for p in card.find_all("p")]
            titulo_processo = textos[0] if textos else ""
            entidade = textos[1] if len(textos) > 1 else ""

            tipo_processo, numero_edital = _extrair_tipo_e_numero(titulo_processo)
            # datas ficam num <p> à parte, depois do rótulo "Período de
            # Inscrição" — busca no texto do card inteiro em vez de indexar
            # por posição fixa.
            datas = re.findall(r"\d{2}/\d{2}/\d{4}", card.get_text(" ", strip=True))

            itens.append(
                ItemListagem(
                    edital_id=int(match_id.group(1)),
                    url=link["href"],
                    entidade=entidade,
                    tipo_processo=tipo_processo,
                    numero_edital=numero_edital,
                    inscricoes_inicio=_parsear_data(datas[0]) if datas else None,
                    inscricoes_fim=_parsear_data(datas[1]) if len(datas) > 1 else None,
                    status=status,
                )
            )

    return itens


def parsear_edital(html: str, url: str) -> list[VagaImeso]:
    soup = BeautifulSoup(html, "html.parser")

    info = soup.find("div", class_="col-sm-10")
    entidade = ""
    tipo_processo = numero_edital = None
    inscricoes_inicio = inscricoes_fim = None
    municipio = uf = None

    if info is not None:
        paragrafos = info.find_all("p")
        if paragrafos:
            titulo_texto = paragrafos[0].get_text(" ", strip=True)
            tipo_processo, numero_edital = _extrair_tipo_e_numero(titulo_texto)
            partes = list(paragrafos[0].stripped_strings)
            entidade = partes[-1] if partes else ""
        if len(paragrafos) > 1:
            datas = re.findall(r"\d{2}/\d{2}/\d{4}", paragrafos[1].get_text())
            inscricoes_inicio = _parsear_data(datas[0]) if datas else None
            inscricoes_fim = _parsear_data(datas[1]) if len(datas) > 1 else None
        if len(paragrafos) > 2:
            match_cidade = re.match(r"^(.*)-\s*([A-Za-z]{2})$", paragrafos[2].get_text(" ", strip=True))
            if match_cidade:
                municipio, uf = match_cidade.group(1).strip(), match_cidade.group(2).strip().upper()

    vagas_tab = soup.find("div", id="vagas")
    if vagas_tab is None:
        return []

    vagas: list[VagaImeso] = []
    for item in vagas_tab.find_all("div", class_="list-group-item"):
        span_cargo = item.find("span")
        if span_cargo is None:
            continue
        cargo = re.sub(r"^\d+\.\s*", "", span_cargo.get_text(strip=True))

        requisitos = None
        salario = None
        for bloco in item.find_all("div", class_="alert-observacao"):
            rotulo_tag = bloco.find("div")
            rotulo = rotulo_tag.get_text(strip=True) if rotulo_tag else ""
            valor = bloco.get_text(" ", strip=True)
            if rotulo:
                valor = valor.replace(rotulo, "", 1).strip()
            if "Requisito" in rotulo:
                requisitos = valor
            elif "Remunera" in rotulo:
                salario = parsear_salario_brl(valor)

        vagas.append(
            VagaImeso(
                orgao=entidade,
                cargo=cargo,
                salario=salario,
                requisitos=requisitos,
                numero_edital=numero_edital,
                tipo_processo=tipo_processo,
                municipio=municipio,
                uf=uf,
                inscricoes_inicio=inscricoes_inicio,
                inscricoes_fim=inscricoes_fim,
                url=url,
            )
        )

    return vagas


def identificador_externo(vaga: VagaImeso, edital_id: int) -> str:
    """Chave de dedup: id do edital (único por processo na IMESO) + slug do
    cargo (um edital lista N cargos)."""
    slug_cargo = re.sub(
        r"[^a-z0-9]+",
        "-",
        unicodedata.normalize("NFKD", vaga.cargo).encode("ascii", "ignore").decode("ascii").lower(),
    ).strip("-")
    return f"imeso-{edital_id}-{slug_cargo}"
