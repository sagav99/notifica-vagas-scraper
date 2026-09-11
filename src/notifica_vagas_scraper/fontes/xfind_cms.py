"""Parsing genérico do CMS "XFind.inc" (`concursos-publicos` /
`concursos_view/<id>`), compartilhado por município-cliente que roda esse
mesmo CMS pra publicar concurso/processo seletivo. Extraído de
`fontes/pmjm_mg.py` (João Monlevade/MG, implementado primeiro) quando
Bocaiúva/MG entrou como 2º cliente confirmado (`fontes/bocaiuva_mg.py`) —
ver docstring de cada um dos dois módulos finos pra detalhe específico do
município (domínio, órgão padrão, etc.).

Cada `fontes/<municipio>.py` só declara `BASE_URL`/`MUNICIPIO`/`UF`/
`ID_PREFIX`/`ORGAO_PADRAO` e delega toda a lógica de parsing pra cá — API
pública idêntica em espírito à do `pmjm_mg.py` original (mesmos nomes de
função, só que agora recebendo `base_url` como parâmetro em vez de
constante fixa de módulo).

**Achado real ao investigar Bocaiúva (2º cliente, fixtures em
`docs/fixtures/bocaiuva_mg/` no repo principal): o mesmo CMS tem (pelo
menos) 2 templates de página de detalhe diferentes entre clientes** —
João Monlevade usa uma tabela `#tb_concursos` (rótulo/valor por `<tr>`),
Bocaiúva usa um layout em cards com `<label>` + elemento-irmão de valor,
sem tabela de cabeçalho nenhuma. `extrair_detalhe` tenta a tabela primeiro
e cai pro layout de card se não achar — nenhum dos dois é "o formato
errado", são só versões de tema diferentes do mesmo produto CMS. A tabela
de anexos (`#tb_anexos_concursos`, 4 colunas Tipo/Descrição/Data/Download)
é idêntica nos dois.

A listagem (`#view_paginas_concursos`) também varia: Bocaiúva tem uma
coluna "Área" (secretaria responsável, ex. "Saúde") que João Monlevade
não tem — `listar_processos` por isso NUNCA assume posição fixa de coluna
(diferente da 1ª versão do parser, que hardcodeava índice de `<td>`);
lê o `<thead>` e casa cada rótulo (normalizado sem acento/maiúscula) pra
achar dinamicamente onde está Nº/Ano, Categoria, Súmula, Data e Área (se
existir) — resolve as duas listagens com o mesmo código, e continua
resolvendo se um 3º cliente inserir ainda outra coluna no meio.

**Achado crítico de prioridade #1 (cargo de saúde nunca pode ser
descartado silenciosamente)**: pelo menos 5 dos 10 processos da fixture
de Bocaiúva marcados com Área="Saúde" têm súmula que NÃO cita nenhuma
palavra de saúde (ex. "EDITAL DE PROCESSO SELETIVO SIMPLIFICADO N
03/2026 MODALIDADE: ANÁLISE DE TÍTULOS..." — só a coluna Área revela que
é da Secretaria de Saúde). Se `eh_cargo_saude` checasse só o título (como
a versão original de `pmjm_mg`, onde a listagem não tem coluna Área),
esses 5 processos seriam descartados antes mesmo de baixar o PDF —
por isso `eh_cargo_saude`/`filtrar_processos_saude` agora também casam
a mesma regex contra o campo `area` do item (quando presente; João
Monlevade não tem essa coluna, então `area` fica `None` e o
comportamento pra esse cliente não muda em nada).

**Achado sobre `escolher_pdf_edital`**: em João Monlevade todo anexo tem
`Tipo="Edital"` (o campo Tipo nunca ajuda a diferenciar abertura de
resultado/convocação, só a Descrição) — mas em Bocaiúva o campo Tipo
realmente varia (`"Convocação"`, `"Homologação"`, `"Edital"`) **e pelo
menos 1 anexo real da fixture tem Tipo="Convocação" com Descrição que NÃO
cita a palavra "convocação"** (`"recepcionista zona urbana (processo
06/2025) SECRETARIA DE SAÚDE"`, datado depois do edital de abertura
verdadeiro) — se `_RE_EXCLUIR_ANEXO` checasse só a Descrição (como a
versão original), esse anexo seria escolhido por engano no lugar do
edital de abertura real (por ser o mais recente entre os não-excluídos).
Por isso a checagem agora roda contra Tipo E Descrição — qualquer um dos
dois batendo já exclui o anexo. Não muda nada pra João Monlevade (onde
Tipo é sempre "Edital", nunca bate a regex de exclusão).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup

__all__ = [
    "ItemListagem",
    "Anexo",
    "DetalheProcesso",
    "url_absoluta",
    "extrair_processo_id",
    "listar_processos",
    "eh_cargo_saude",
    "filtrar_processos_saude",
    "extrair_detalhe",
    "escolher_pdf_edital",
    "extrair_ids_processados",
]


@dataclass
class ItemListagem:
    processo_id: int
    numero_edital: str | None
    categoria: str
    titulo: str
    data_publicacao: date | None
    url: str
    #: só populado pros clientes cuja listagem tem coluna "Área"
    #: (secretaria responsável) — ver docstring do módulo. `None` quando
    #: a coluna não existe nessa listagem.
    area: str | None = None


@dataclass
class Anexo:
    tipo: str
    descricao: str
    data: date | None
    url: str


@dataclass
class DetalheProcesso:
    numero_edital: str | None
    titulo: str
    categoria: str
    data_publicacao: date | None
    anexos: list[Anexo]


def _parsear_data(texto: str | None) -> date | None:
    try:
        return datetime.strptime((texto or "").strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


def _limpar_texto(no) -> str:
    return re.sub(r"\s+", " ", no.get_text(" ", strip=True)).strip()


def _normalizar_rotulo(texto: str) -> str:
    """Normaliza rótulo de cabeçalho de tabela/label pra comparação
    tolerante a acento/maiúscula/pontuação (ex. "Nº/ Ano" e "Nº/Ano" viram
    ambos "noano") — usado só pra CASAR rótulo com campo conhecido, nunca
    pra exibir."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", texto.lower())


def url_absoluta(href: str, *, base_url: str) -> str:
    if href.startswith("http"):
        return href
    return base_url + "/" + href.lstrip("/")


_RE_PROCESSO_ID = re.compile(r"concursos_view/(\d+)")


def extrair_processo_id(url: str) -> int | None:
    match = _RE_PROCESSO_ID.search(url)
    return int(match.group(1)) if match else None


def listar_processos(html: str, *, base_url: str) -> list[ItemListagem]:
    """Lê `/concursos-publicos`. Nunca assume posição fixa de coluna —
    casa cada rótulo do `<thead>` (normalizado) contra os campos
    conhecidos (Nº/Ano, Categoria, Súmula, Data, e opcionalmente Área) —
    ver docstring do módulo pro motivo (clientes diferentes têm colunas
    extras em posições diferentes). Linha sem link reconhecível de
    `concursos_view/<id>`, ou tabela sem cabeçalho reconhecível o
    suficiente pra achar Nº/Ano+Categoria+Súmula+Data, é ignorada (nunca
    quebra o parsing das demais)."""
    soup = BeautifulSoup(html, "html.parser")
    tabela = soup.find("table")
    if tabela is None or tabela.find("tbody") is None:
        return []

    thead = tabela.find("thead")
    if thead is None:
        return []

    indice_numero = indice_categoria = indice_titulo = indice_data = indice_area = None
    for i, th in enumerate(thead.find_all("th")):
        rotulo = _normalizar_rotulo(_limpar_texto(th))
        if rotulo == "noano":
            indice_numero = i
        elif rotulo == "categoria":
            indice_categoria = i
        elif rotulo == "sumula":
            indice_titulo = i
        elif rotulo == "data":
            indice_data = i
        elif rotulo == "area":
            indice_area = i

    if None in (indice_numero, indice_categoria, indice_titulo, indice_data):
        return []

    itens: list[ItemListagem] = []
    for tr in tabela.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        indice_maximo = max(i for i in (indice_numero, indice_categoria, indice_titulo, indice_data, indice_area) if i is not None)
        if len(tds) <= indice_maximo:
            continue

        link = tr.find("a", href=True)
        if link is None:
            continue
        processo_id = extrair_processo_id(link["href"])
        if processo_id is None:
            continue

        numero_edital = _limpar_texto(tds[indice_numero]) or None
        categoria = _limpar_texto(tds[indice_categoria])
        # achado real (pmjm_mg): 1 linha antiga tem súmula vazia de
        # verdade na fonte — mantém a linha (título="") em vez de
        # descartar; `eh_cargo_saude("")` já é False sozinho, o corte
        # acontece em `filtrar_processos_saude`, nunca aqui.
        titulo = _limpar_texto(tds[indice_titulo])
        data_publicacao = _parsear_data(tds[indice_data].get_text(strip=True))
        area = _limpar_texto(tds[indice_area]) or None if indice_area is not None else None

        itens.append(
            ItemListagem(
                processo_id=processo_id,
                numero_edital=numero_edital,
                categoria=categoria,
                titulo=titulo,
                area=area,
                data_publicacao=data_publicacao,
                url=url_absoluta(link["href"], base_url=base_url),
            )
        )

    return itens


#: deliberadamente permissivo — ver docstring de `fontes/pmjm_mg.py` pro
#: motivo original ("não descobrir processo novo" é o risco real aqui,
#: não "processar cargo não-saúde a mais"). Inclui "saude"/"saúde" cru
#: porque vários títulos citam só "Secretaria Municipal de Saúde" sem
#: nomear o cargo — e é o mesmo motivo que faz "Saúde" bater quando
#: casada contra o campo `area` da listagem de Bocaiúva (ver docstring do
#: módulo).
_RE_CARGO_SAUDE = re.compile(
    r"m[eé]dic[oa]?s?\b"
    r"|enfermeir"
    r"|fisioterapeut"
    r"|psic[oó]log"
    r"|nutricionist"
    r"|fonoaudi[oó]log"
    r"|odontolog"
    r"|dentist"
    r"|farmac[eê]utic"
    r"|assistente\s+social"
    r"|biom[eé]dic"
    r"|veterin[aá]ri"
    r"|terapeuta\s+ocupacional"
    r"|agente\s+comunit[aá]rio\s+de\s+sa[uú]de"
    r"|sa[uú]de",
    re.IGNORECASE,
)


def eh_cargo_saude(titulo: str, area: str | None = None) -> bool:
    """`area` é opcional — casa a MESMA regex contra o campo "Área"
    da listagem quando o cliente tem essa coluna (ex. Bocaiúva: vários
    processos ali marcados Área="Saúde" têm título sem nenhuma palavra de
    saúde — ver docstring do módulo). Cliente sem essa coluna (ex. João
    Monlevade) sempre chama com `area=None`, comportamento idêntico ao
    original (só título)."""
    return bool(_RE_CARGO_SAUDE.search(titulo)) or bool(area and _RE_CARGO_SAUDE.search(area))


def filtrar_processos_saude(itens: list[ItemListagem]) -> list[ItemListagem]:
    return [item for item in itens if eh_cargo_saude(item.titulo, item.area)]


def _extrair_cabecalho_tabela(soup: BeautifulSoup) -> dict[str, str] | None:
    """Template João Monlevade: `<table id="tb_concursos">` com 1 `<tr>`
    de 2 `<td>` (rótulo, valor) por campo."""
    tabela = soup.find("table", id="tb_concursos")
    if tabela is None:
        return None

    campos: dict[str, str] = {}
    for tr in tabela.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 2:
            continue
        rotulo = tds[0].get_text(strip=True).rstrip(":")
        campos[rotulo] = _limpar_texto(tds[1])
    return campos


_RE_BADGE_NUMERO_EDITAL = re.compile(r"(.*?)\s*n[ºo]\s*(\d+/\d{4})", re.IGNORECASE)


def _extrair_cabecalho_cards(soup: BeautifulSoup) -> dict[str, str] | None:
    """Template Bocaiúva: sem tabela de cabeçalho — número/categoria vêm
    do badge `.badge-primary` no topo (ex. "Edital nº 3/2026" ou
    "Processos Seletivos nº 6/2025"), súmula/data vêm de `<label>` +
    elemento-irmão de valor dentro de `#print-view` (ex. label "Súmula /
    Descrição" seguido de um `<div>` com o texto, label "DATA DE
    PUBLICAÇÃO" seguido de um `<p>`). `None` se nem o badge existir (nem
    esse template bate)."""
    badge = soup.select_one(".badge-primary")
    if badge is None:
        return None

    texto_badge = _limpar_texto(badge)
    match = _RE_BADGE_NUMERO_EDITAL.match(texto_badge)
    numero_edital = match.group(2) if match else None
    categoria = match.group(1).strip() if match else ""

    campos: dict[str, str] = {"Nº/Ano": numero_edital or "", "Tipo": categoria}
    escopo = soup.find(id="print-view") or soup
    for label in escopo.find_all("label"):
        rotulo = label.get_text(strip=True).rstrip(":")
        valor_no = label.find_next_sibling()
        if valor_no is None:
            continue
        campos[rotulo] = _limpar_texto(valor_no)
    return campos


def extrair_detalhe(html: str, *, base_url: str) -> DetalheProcesso:
    """Lê `concursos_view/<id>`. Cabeçalho: tenta o template tabela (João
    Monlevade) e cai pro template card (Bocaiúva) se não achar — ver
    docstring do módulo. Anexos: sempre `#tb_anexos_concursos` (Tipo,
    Descrição, Data, Arquivo), idêntico nos dois templates. Campo
    ausente vira string vazia/`None`, nunca derruba o parsing."""
    soup = BeautifulSoup(html, "html.parser")

    campos = _extrair_cabecalho_tabela(soup) or _extrair_cabecalho_cards(soup) or {}

    anexos: list[Anexo] = []
    tabela_anexos = soup.find("table", id="tb_anexos_concursos")
    if tabela_anexos is not None and tabela_anexos.find("tbody") is not None:
        for tr in tabela_anexos.find("tbody").find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            link = tr.find("a", href=True)
            if link is None:
                continue
            anexos.append(
                Anexo(
                    tipo=_limpar_texto(tds[0]),
                    descricao=_limpar_texto(tds[1]),
                    data=_parsear_data(tds[2].get_text(strip=True)),
                    url=url_absoluta(link["href"], base_url=base_url),
                )
            )

    return DetalheProcesso(
        numero_edital=campos.get("Nº/ Ano") or campos.get("Nº/Ano") or None,
        titulo=campos.get("Súmula") or campos.get("Súmula / Descrição") or "",
        categoria=campos.get("Tipo", ""),
        data_publicacao=_parsear_data(campos.get("Data") or campos.get("DATA DE PUBLICAÇÃO")),
        anexos=anexos,
    )


#: documento que sai DEPOIS da abertura e não é fonte confiável de
#: cargo/salário/requisitos de abertura (pode listar só aprovados, sem
#: repetir a tabela do cargo) — nunca escolhido como "edital vigente" pra
#: extração. Casado contra Tipo E Descrição (ver docstring do módulo pro
#: achado real de Bocaiúva: Tipo="Convocação" sem a palavra "convocação"
#: na Descrição — só o Tipo denuncia).
_RE_EXCLUIR_ANEXO = re.compile(
    r"resultado|homologa|classifica|convoca|julgamento|recurso|habilita",
    re.IGNORECASE,
)


def escolher_pdf_edital(anexos: list[Anexo]) -> Anexo | None:
    """Escolhe o anexo PDF que representa a versão vigente do edital de
    ABERTURA (original ou retificação) — nunca resultado/homologação/
    classificação/convocação (ver `_RE_EXCLUIR_ANEXO` e docstring do
    módulo). Entre os candidatos restantes, pega o de data mais recente
    (cobre retificação publicada depois do edital original). `None` se só
    houver documentos de resultado/convocação (edital de abertura ainda
    não listado, ou removido) — quem chama não insere nada nesse ciclo e
    tenta de novo no próximo cron, nunca inventa dado a partir de um
    documento de resultado."""
    candidatos = [
        anexo
        for anexo in anexos
        if not _RE_EXCLUIR_ANEXO.search(anexo.tipo)
        and not _RE_EXCLUIR_ANEXO.search(anexo.descricao)
        and anexo.url.lower().endswith(".pdf")
    ]
    if not candidatos:
        return None

    com_data = [anexo for anexo in candidatos if anexo.data is not None]
    if com_data:
        return max(com_data, key=lambda anexo: anexo.data)
    return candidatos[0]


def _regex_processo_id(prefixo: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(prefixo)}-(\d+)-")


def extrair_ids_processados(identificadores_processados: set[str], *, prefixo: str) -> set[int]:
    """Decodifica o `<id>` de processo embutido em cada `identificador_externo`
    já gravado pra esta fonte (formato `<prefixo>-<id>-<slug-cargo>`,
    montado por `processamento_pdf_gemini.processar_pdf_e_gravar_vagas` em
    `scripts/rodar_<cliente>.py` com `id_prefix=<ID_PREFIX do cliente>`) —
    usado pra pular processo já visto ANTES de baixar detalhe/PDF/chamar
    Gemini de novo. Não depende de como o cargo foi transformado em slug —
    só do `<id>` numérico entre os dois `-` fixos."""
    padrao = _regex_processo_id(prefixo)
    ids: set[int] = set()
    for identificador in identificadores_processados:
        match = padrao.match(identificador)
        if match:
            ids.add(int(match.group(1)))
    return ids
