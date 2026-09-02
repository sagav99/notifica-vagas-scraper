"""Funções compartilhadas entre bancas que rodam na plataforma ProSeleta
(selecao.net.br) — SaaS multi-tenant de "Gestão de Processos Seletivos
Online" ("Desenvolvido por ProSeleta" no rodapé de cada site-cliente).

Confirmado em 2 tenants investigados de ponta a ponta (2026-09-01):
JCM Concursos (`concursosjcm.com.br`, ver `fontes/jcm.py`) e ACCESS
(`concursos.access.org.br`, ver `fontes/access.py`) — a página de detalhe
do processo (`/informacoes/<id>/`) é byte-a-byte idêntica em estrutura
entre os dois (mesmas classes CSS, mesmo `data-astv`, mesma tabela
"Vaga/Qtde"), só o subdomínio de upload dos PDFs muda
(`anexos-r2.selecao.net.br/uploads/<tenant_id>/concursos/...`). Só a
página de LISTAGEM (`/index/abertos/`) varia o layout do card entre
tenants (JCM embute tipo+número no `<h3>`, ACCESS usa `<p>` separados)
— por isso cada banca tem seu próprio `listar_processos_abertos`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup


@dataclass
class Documento:
    titulo: str
    data: date | None
    url_pdf: str


@dataclass
class VagaHtml:
    cargo: str
    quantidade: int


def _parsear_data(texto: str) -> date | None:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def listar_documentos(html: str) -> list[Documento]:
    soup = BeautifulSoup(html, "html.parser")
    documentos: list[Documento] = []
    for item in soup.select("li.pdf a[data-astv][href]"):
        span = item.find("span")
        documentos.append(
            Documento(
                titulo=item["data-astv"].strip(),
                data=_parsear_data(span.get_text()) if span else None,
                url_pdf=item["href"],
            )
        )
    return documentos


def escolher_edital(documentos: list[Documento]) -> Documento | None:
    """A ordem de `listar_documentos` não é cronológica confiável (achado
    real na JCM: 1ª publicação de um processo real veio antes de leis
    municipais com a mesma data, mas a retificação mais recente veio
    depois) — por isso compara datas explicitamente em vez de pegar
    1ª/última posição. Documento sem data (`None`) fica por último no
    critério de desempate."""
    candidatos = [d for d in documentos if "edital" in d.titulo.lower()]
    if not candidatos:
        return documentos[0] if documentos else None
    return max(candidatos, key=lambda d: d.data or date.min)


def listar_vagas_html(html: str) -> list[VagaHtml]:
    """A seção pagina os cargos em mais de uma `<table>` dentro do mesmo
    container quando há muitos (achado real na JCM: 19 cargos vieram em 2
    tabelas de "Vaga/Qtde", não numa só) — por isso pega todas as tabelas
    do container, não só a 1ª depois do `<h3>Vagas</h3>`."""
    soup = BeautifulSoup(html, "html.parser")
    secao = soup.find("h3", string=re.compile(r"^\s*Vagas\s*$"))
    if secao is None:
        return []
    container = secao.find_parent("div")
    if container is None:
        return []

    vagas: list[VagaHtml] = []
    for tabela in container.find_all("table"):
        for linha in tabela.find_all("tr"):
            celulas = linha.find_all("td")
            if len(celulas) != 2:
                continue
            cargo = celulas[0].get_text(strip=True)
            quantidade_texto = celulas[1].get_text(strip=True)
            if cargo == "Vaga" or not quantidade_texto.isdigit():
                continue
            vagas.append(VagaHtml(cargo=cargo, quantidade=int(quantidade_texto)))
    return vagas
