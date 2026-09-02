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
plataforma SaaS multi-tenant, não exclusiva da JCM. **Confirmado
2026-09-01**: a ACCESS (`fontes/access.py`) roda na mesma plataforma,
com a página de detalhe do processo idêntica byte-a-byte em estrutura —
por isso `listar_documentos`/`escolher_edital`/`listar_vagas_html` vêm
de `fontes/proseleta.py` (compartilhado), só `listar_processos_abertos`
é específico daqui (o layout do card de listagem varia por tenant).

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

from bs4 import BeautifulSoup

from .proseleta import Documento, VagaHtml, escolher_edital, listar_documentos, listar_vagas_html

__all__ = [
    "ItemListagem",
    "Documento",
    "VagaHtml",
    "BASE_URL",
    "listar_processos_abertos",
    "listar_documentos",
    "escolher_edital",
    "listar_vagas_html",
]

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
