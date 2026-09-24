"""Busca avançada por texto no Diário Oficial hospedado pela plataforma
SIGPub (diariomunicipal.com.br) — mesmo mecanismo usado tanto por
`/amm-mg/` (Diário Oficial dos Municípios Mineiros) quanto `/apm/`
(Associação Paulista de Municípios, SP). Investigação completa do
mecanismo em `docs/fixtures/dom_amm_mg/busca_resultado_*.html` no repo
principal (achado 2026-09-01): GET simples com `requests.Session`, sem
JS, sem navegador headless.

**Achado real 2026-09-24**: o site removeu o form-token CSRF e renomeou
2 campos do formulário de busca, quebrando o canário há 3 dias seguidos
(21-23/09) sem erro HTTP visível — `obter_token` sempre devolvia `None`
(campo `busca_avancada[_token]` não existe mais na página), e mesmo
ignorando isso, os nomes `busca_avancada[entidadeUsuaria]`/
`busca_avancada[page]` mudaram pra `busca_avancada[entidade]`/
`busca_avancada[pagina]`, e o formato de data de `dataInicio`/`dataFim`
mudou de `dd/mm/aaaa` pra ISO `aaaa-mm-dd`. Confirmado manualmente
contra o site real (`busca_avancada[entidade]=435` = Perdões/MG) antes
de corrigir — a busca por "processo seletivo" nesse município achou 24
matérias reais, incluindo o Anexo II (vagas) do Edital 011/2026 que
tinha ficado de fora. `obter_token`/parâmetro `token` de `buscar`
mantidos só por compatibilidade de assinatura (sempre `None`/ignorado
agora) — não custa nada manter caso o site volte a exigir token no
futuro.

Fluxo: `buscar` (GET com os parâmetros `busca_avancada[...]`) →
`parsear_resultados` (extrai as linhas de `table#datatable`) →
`resolver_url_materia` (segue o redirect de `/<base>/load/<codigo>`
até a URL canônica da matéria).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.diariomunicipal.com.br"
USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"


@dataclass
class ResultadoBusca:
    entidade: str
    titulo: str
    orgao: str
    data_circulacao: date | None
    codigo: str
    url_load: str


def obter_token(session: requests.Session, caminho_pesquisar: str) -> str | None:
    """GET na página de busca (ex: `/amm-mg/pesquisar`) — mantido só por
    compatibilidade de assinatura das chamadas existentes. O site parou
    de exigir token CSRF nesse form (achado 2026-09-24, ver docstring do
    módulo); devolve `None` sempre que o campo não existir mais, sem
    levantar erro — `buscar` não depende mais deste retorno."""
    resposta = session.get(
        f"{BASE_URL}{caminho_pesquisar}", headers={"User-Agent": USER_AGENT}, timeout=30
    )
    resposta.raise_for_status()
    soup = BeautifulSoup(resposta.text, "html.parser")
    campo = soup.find("input", {"name": "busca_avancada[_token]"})
    return campo.get("value") if campo else None


def _parsear_data_circulacao(texto: str) -> date | None:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def parsear_resultados(html: str) -> list[ResultadoBusca]:
    """Extrai os cartões de `ul.lista-materias > li.materia-card` —
    **reescrito 2026-09-24**: o site trocou a tabela antiga
    (`table#datatable`, biblioteca DataTables) por uma lista de cartões
    (achado ao investigar por que o canário do AMM-MG/APM-SP passou 3
    dias falhando — não era só nome de campo/token, o HTML de resultado
    também mudou de estrutura). Devolve lista vazia tanto pra "nenhuma
    matéria encontrada" quanto pra qualquer HTML sem essa lista (ex.:
    página de erro) — mesmo efeito prático pro chamador: nada
    aproveitável desta busca.

    O link do título aponta DIRETO pra `/<base>/materia/<codigo>` agora
    (sem precisar mais do redirect `/<base>/load/<codigo>` da estrutura
    antiga) — `resolver_url_materia` continua funcionando sobre esse
    link (um GET numa URL sem redirect simplesmente devolve a mesma
    URL), não precisou mudar os chamadores."""
    soup = BeautifulSoup(html, "html.parser")
    cartoes = soup.select("ul.lista-materias > li.materia-card")

    resultados = []
    for cartao in cartoes:
        link_titulo = cartao.select_one("h2.materia-card__titulo a")
        span_entidade = cartao.select_one("span.materia-card__entidade")
        span_orgao = cartao.select_one("span.materia-card__orgao")
        dt_circulacao = cartao.find("dt", string="Circulação")
        if not (link_titulo and span_entidade and span_orgao and dt_circulacao):
            continue
        dd_circulacao = dt_circulacao.find_next_sibling("dd")
        href = link_titulo.get("href", "")
        codigo = href.rsplit("/", 1)[-1]
        resultados.append(
            ResultadoBusca(
                entidade=span_entidade.get_text(strip=True),
                titulo=link_titulo.get_text(strip=True),
                orgao=span_orgao.get_text(strip=True),
                data_circulacao=_parsear_data_circulacao(dd_circulacao.get_text(strip=True)) if dd_circulacao else None,
                codigo=codigo,
                url_load=f"{BASE_URL}{href}" if href.startswith("/") else href,
            )
        )
    return resultados


def buscar(
    session: requests.Session,
    *,
    caminho_pesquisar: str,
    token: str | None = None,
    entidade_id: str,
    termo: str,
    data_inicio: date,
    data_fim: date,
) -> str:
    """GET de busca avançada. Devolve o HTML bruto da resposta — usar
    `parsear_resultados` pra extrair. `data_inicio`/`data_fim` são
    obrigatórios pro formulário (achado real: sem eles a busca é
    rejeitada). `token` não é mais usado (ver docstring do módulo,
    achado 2026-09-24) — parâmetro mantido só pra não quebrar chamada
    existente, nunca enviado no request.

    Nomes de campo (`entidade`/`pagina`) e formato de data (`aaaa-mm-dd`,
    ISO) confirmados contra o form real do site em 2026-09-24 — os
    nomes antigos (`entidadeUsuaria`/`page`) e o formato `dd/mm/aaaa`
    faziam a busca devolver a página vazia sem erro (mesmo sintoma do
    token: rejeição silenciosa, sem HTTP de erro)."""
    parametros = {
        "busca_avancada[pagina]": "1",
        "busca_avancada[entidade]": entidade_id,
        "busca_avancada[orgao]": "",
        "busca_avancada[titulo]": "",
        "busca_avancada[texto]": termo,
        "busca_avancada[dataInicio]": data_inicio.strftime("%Y-%m-%d"),
        "busca_avancada[dataFim]": data_fim.strftime("%Y-%m-%d"),
        "busca_avancada[ordenacao]": "recente",
        "busca_avancada[Enviar]": "",
    }
    resposta = session.get(
        f"{BASE_URL}{caminho_pesquisar}",
        params=parametros,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resposta.raise_for_status()
    return resposta.text


def resolver_url_materia(session: requests.Session, url_load: str) -> str:
    """`/<base>/load/<codigo>` redireciona pra URL canônica da matéria
    (`/<base>/materia/<codigo>/<hash>`) — `requests` já segue o redirect
    por padrão, só devolve a URL final."""
    resposta = session.get(url_load, headers={"User-Agent": USER_AGENT}, timeout=30)
    resposta.raise_for_status()
    return resposta.url
