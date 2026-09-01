"""Parser da JCM Concursos (concursosjcm.com.br), banca organizadora que
atende prefeituras/câmaras pequenas de MG (achado 2026-09-01, investigação
via `curl`/Python puro — sinal trazido pelo usuário, achado real
confirmado 2x na triagem de bancas de saúde em TAREFAS.md).

`jcmconcursos.com.br` (domínio "oficial" citado pelo usuário) dá erro de
TLS consistente — **usar sempre `concursosjcm.com.br`**, que responde 200
normalmente e é o domínio real por trás dos links internos do site.

Vendor de fundo: **ProSeleta / selecao.net.br** ("Desenvolvido por
ProSeleta - Gestão de Processos Seletivos Online" no rodapé; PDFs em
`anexos-r2.selecao.net.br`, imagens em `static-cdn.selecao.net.br`) — uma
plataforma SaaS multi-tenant, não exclusiva da JCM. Achado que cruza com
uma triagem anterior: `abcp.selecao.net.br` (Taboão da Serra/SP, ver
TAREFAS.md "Análise do documento de links reais do Codex") usa o mesmo
domínio-base — sinal de que esse parser pode generalizar pra outras
bancas na mesma plataforma no futuro, mas isso não foi confirmado ainda
(só JCM foi investigada de ponta a ponta).

Estrutura investigada:
- GET `/index/abertos/` lista só os processos com inscrição literalmente
  aberta agora (subconjunto do que aparece em `/` — a home usa o filtro
  "Em andamento", mais amplo) — cada card tem "Município-UF - Tipo
  NNN/AAAA - Órgão" como título e link `/informacoes/<id>/`.
- GET `/informacoes/<id>/` tem "Situação" (texto solto, não crucial pra
  filtragem — já filtramos na listagem), lista de "Publicações" (cada
  `<li class="pdf">` com `data-astv="<título>"`, `href` do PDF direto em
  `anexos-r2.selecao.net.br` e uma data — **sem ordem cronológica
  confiável**, por isso `escolher_edital` compara as datas em vez de
  assumir 1ª/última posição) e uma tabela "Vagas" com cargo + quantidade
  **já estruturados em HTML** (achado bônus: diferente de Actcon/FGV/
  WordPress/IMAM, aqui dá pra saber os nomes dos cargos sem abrir PDF —
  não usado ainda porque `vagas.quantidade` está deliberadamente fora de
  escopo por decisão do usuário, ver TAREFAS.md; mantido só como
  `Vaga.cargo_qtde_html` pra uso futuro). Salário não aparece em HTML em
  nenhuma parte da página — só no PDF do edital, precisa Gemini, mesmo
  padrão de sempre.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup

BASE_URL = "https://concursosjcm.com.br"


@dataclass
class ItemListagem:
    processo_id: int
    url: str
    municipio: str
    uf: str
    tipo_processo: str
    numero_edital: str | None
    orgao: str


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


def _extrair_numero_edital(tipo_e_numero: str) -> tuple[str, str | None]:
    match = re.search(r"^(.*?)\s+([\d./]+)$", tipo_e_numero.strip())
    if not match:
        return tipo_e_numero.strip(), None
    return match.group(1).strip(), match.group(2).strip()


def listar_processos_abertos(html: str) -> list[ItemListagem]:
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []

    for link in soup.select("h3 > a[href]"):
        match_id = re.match(r"^/informacoes/(\d+)/$", link.get("href", ""))
        if not match_id:
            continue

        partes = link.get_text(strip=True).split(" - ", 2)
        if len(partes) != 3:
            continue
        municipio_uf, tipo_e_numero, orgao = partes
        municipio, _, uf = municipio_uf.rpartition("-")
        if not municipio or not uf:
            continue
        tipo_processo, numero_edital = _extrair_numero_edital(tipo_e_numero)

        itens.append(
            ItemListagem(
                processo_id=int(match_id.group(1)),
                url=f"{BASE_URL}{match_id.group(0)}",
                municipio=municipio.strip(),
                uf=uf.strip().upper(),
                tipo_processo=tipo_processo,
                numero_edital=numero_edital,
                orgao=orgao.strip(),
            )
        )

    return itens


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
    real: 1ª publicação de um processo real veio antes de leis municipais
    com a mesma data, mas a retificação mais recente veio depois) — por
    isso compara datas explicitamente em vez de pegar 1ª/última posição.
    Documento sem data (`None`) fica por último no critério de
    desempate."""
    candidatos = [d for d in documentos if "edital" in d.titulo.lower()]
    if not candidatos:
        return documentos[0] if documentos else None
    return max(candidatos, key=lambda d: d.data or date.min)


def listar_vagas_html(html: str) -> list[VagaHtml]:
    """A seção pagina os cargos em mais de uma `<table>` dentro do mesmo
    `div#blocoListaVagas` quando há muitos (achado real: 19 cargos vieram
    em 2 tabelas de "Vaga/Qtde", não numa só) — por isso pega todas as
    tabelas do container, não só a 1ª depois do `<h3>Vagas</h3>`."""
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
