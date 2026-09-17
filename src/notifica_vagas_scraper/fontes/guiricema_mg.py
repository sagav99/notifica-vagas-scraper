"""Parser da Prefeitura de Guiricema/MG (WordPress + Elementor/JetEngine,
`guiricema.mg.gov.br` — sem banca organizadora terceirizada, PSS direto).

Investigado pelo `pesquisador-fonte` (2026-09-16, fixtures reais em
`docs/fixtures/guiricema/` no repo principal):
`listagem_processos_seletivos.html`, `edital_pss_27_2026_medico_esf_clinico.pdf`
(alvo real: Médico ESF 40h + Médico Clínico 20h) e
`edital_convocacao_92_2026_medico.pdf`.

**Diferença de arquitetura em relação a `pmjm_mg`/`bocaiuva_mg` (CMS
"XFind.inc")**: aqui a LISTAGEM (`/legislacao-tipo/processos-seletivos/`,
grid JetEngine renderizado em HTML puro, sem JS pra montar o conteúdo)
já entrega cargo (texto livre) + data + link direto do PDF do edital —
não existe página de detalhe separada pra visitar. Por isso este módulo
não tem `extrair_detalhe`/`Anexo`: cada item da listagem já é 1 PDF
candidato, e quem decide se processa é só `eh_edital_abertura` (nunca
convocação/resultado/homologação/ata de reunião) + `eh_cargo_saude`.
`scripts/rodar_guiricema.py` chama `processamento_pdf_gemini.
processar_pdf_e_gravar_vagas` direto com a URL do PDF já na mão — sem
2º request HTTP por processo.

**PDF escaneado/sem camada de texto** (confirmado via `pypdf` na
investigação: `pdftotext`/`PdfReader.extract_text()` não extraem nada
dos 2 PDFs de fixture) — `gemini_pdf.extrair_vagas_de_pdf` já lê PDF
escaneado visualmente por padrão, nenhum código novo necessário aqui.

**Achado real que gerou este parser**: o campo de cargo da listagem
NÃO é fonte confiável do conjunto completo de cargos do edital — o
Edital 27/2026 (cadastro reserva) mostra só "MÉDICO ESF (40 HORAS)" na
listagem, mas o PDF real cobre TAMBÉM Médico Clínico (20h). O campo
`cargo` do item é usado só pra TRIAGEM (é indício suficiente de vaga de
saúde pra vale a pena baixar o PDF) — a lista definitiva de cargos
sempre vem do Gemini lendo o PDF inteiro (mesma garantia de
`processamento_pdf_gemini`, que já grava 1 vaga por cargo que o Gemini
encontrar, nunca só o 1º).

**Achado sobre o campo `cargo` em si**: o HTML tem 2 formatos pro mesmo
campo, sem padrão fixo — às vezes vem cru em caixa alta ("MÉDICO ESF (40
HORAS)", "AUXILIAR DE SERVIÇOS GERAIS") e às vezes com prefixo "Cargo: "
("Cargo: Nutricionista"). `_extrair_cargo` tira o prefixo quando
presente, mantém o texto como está quando não tem — `eh_cargo_saude`
casa a mesma regex nos dois casos, então isso não afeta o filtro de
saúde, só a exibição/resumo.

**Achado sobre `eh_edital_abertura`**: a listagem mistura vários tipos
de publicação sob a mesma categoria "Processos Seletivos" — edital de
abertura ("EDITAL DE PROCESSO SELETIVO PUBLICO CADASTRO RESERVA Nº
27/2026"), convocação ("EDITAL DE CONVOCAÇÃO Nº 98/2026", "Edital de
Convocação Especial"), ata de reunião ("Ata de Reunião Referente ao
Processo Seletivo Público nº 024/2026") e prorrogação de prazo
("Prorrogação do prazo de inscrição") — só o 1º tipo é fonte confiável
de cargo/salário/requisitos de abertura. `eh_edital_abertura` exige que
o título contenha "edital" E não bata a regex de exclusão (convocação/
resultado/homologação/classificação/julgamento/recurso/habilitação) —
isso descarta "Ata de Reunião..." (não tem "edital" no título) e
qualquer variante de convocação, sem depender de posição/coluna.
Decisão de escopo (cadastro reserva conta como "edital de abertura com
inscrição aberta") já tomada e registrada em TAREFAS.md do repo
principal — não reabrir aqui.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup

BASE_URL = "https://www.guiricema.mg.gov.br"
URL_LISTAGEM = f"{BASE_URL}/legislacao-tipo/processos-seletivos/"

#: fonte dedicada a 1 único município — sem necessidade de casar título
#: contra catálogo (diferente de fontes multi-município tipo FGV/Instituto
#: Mais).
MUNICIPIO = "Guiricema"
UF = "MG"

#: mesmo esquema de `xfind_cms.extrair_ids_processados` — ver docstring
#: de lá.
ID_PREFIX = "guiricema-mg"

__all__ = [
    "BASE_URL",
    "URL_LISTAGEM",
    "MUNICIPIO",
    "UF",
    "ID_PREFIX",
    "ItemListagem",
    "listar_processos",
    "eh_cargo_saude",
    "eh_edital_abertura",
    "filtrar_processos_saude_abertura",
    "extrair_ids_processados",
]


@dataclass
class ItemListagem:
    processo_id: int
    titulo: str
    #: cargo em texto livre da listagem — só indício pra triagem, NUNCA a
    #: lista definitiva de cargos do edital (ver docstring do módulo).
    cargo: str
    data_publicacao: date | None
    url_pdf: str


def _limpar_texto(no) -> str:
    return re.sub(r"\s+", " ", no.get_text(" ", strip=True)).strip()


def _parsear_data(texto: str | None) -> date | None:
    try:
        return datetime.strptime((texto or "").strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


_RE_PREFIXO_CARGO = re.compile(r"^\s*cargo\s*:\s*", re.IGNORECASE)


def _extrair_cargo(texto: str) -> str:
    return _RE_PREFIXO_CARGO.sub("", texto).strip()


def listar_processos(html: str) -> list[ItemListagem]:
    """Lê `/legislacao-tipo/processos-seletivos/` (grid JetEngine). Cada
    `div.jet-listing-grid__item[data-post-id]` é 1 item — título
    (`data-id="6a311d1"`), data de publicação (`<time>` dentro do
    widget post-info), cargo em texto livre (`data-id="cd54dd0"`,
    ver `_extrair_cargo`) e link do PDF (`data-id="9962211"`, âncora com
    `href`). Item sem `data-post-id`, sem título reconhecível ou sem
    link de PDF é ignorado (nunca quebra o parsing dos demais)."""
    soup = BeautifulSoup(html, "html.parser")

    itens: list[ItemListagem] = []
    for item_div in soup.select(".jet-listing-grid__item[data-post-id]"):
        post_id_texto = item_div.get("data-post-id")
        try:
            processo_id = int(post_id_texto)
        except (TypeError, ValueError):
            continue

        titulo_no = item_div.select_one('[data-id="6a311d1"] .jet-listing-dynamic-field__content')
        titulo = _limpar_texto(titulo_no) if titulo_no is not None else ""
        if not titulo:
            continue

        link_no = item_div.select_one('[data-id="9962211"] a[href]')
        if link_no is None:
            continue
        url_pdf = link_no["href"]

        cargo_no = item_div.select_one('[data-id="cd54dd0"] .jet-listing-dynamic-field__content')
        cargo = _extrair_cargo(_limpar_texto(cargo_no)) if cargo_no is not None else ""

        time_no = item_div.select_one(".elementor-post-info__item--type-date time")
        data_publicacao = _parsear_data(time_no.get_text(strip=True)) if time_no is not None else None

        itens.append(
            ItemListagem(
                processo_id=processo_id,
                titulo=titulo,
                cargo=cargo,
                data_publicacao=data_publicacao,
                url_pdf=url_pdf,
            )
        )

    return itens


#: mesma regex-base de `xfind_cms._RE_CARGO_SAUDE` (deliberadamente
#: permissiva — o risco real é "não descobrir processo novo", não
#: "processar cargo não-saúde a mais").
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


def eh_cargo_saude(titulo: str, cargo: str = "") -> bool:
    return bool(_RE_CARGO_SAUDE.search(titulo)) or bool(cargo and _RE_CARGO_SAUDE.search(cargo))


#: documento que não é fonte confiável de cargo/salário/requisitos de
#: ABERTURA (convocação/resultado/homologação/classificação/julgamento/
#: recurso/habilitação) — nunca escolhido como edital vigente pra
#: extração (ver docstring do módulo).
_RE_EXCLUIR_TITULO = re.compile(
    r"resultado|homologa|classifica|convoca|julgamento|recurso|habilita",
    re.IGNORECASE,
)

_RE_EDITAL = re.compile(r"\bedital\b", re.IGNORECASE)


def eh_edital_abertura(titulo: str) -> bool:
    """`True` só quando o título cita "edital" e não bate nenhuma
    palavra de exclusão — descarta "Ata de Reunião..." (sem "edital" no
    título), "Prorrogação do prazo..." (idem) e qualquer variante de
    convocação/resultado/homologação (ver docstring do módulo)."""
    return bool(_RE_EDITAL.search(titulo)) and not _RE_EXCLUIR_TITULO.search(titulo)


def filtrar_processos_saude_abertura(itens: list[ItemListagem]) -> list[ItemListagem]:
    return [item for item in itens if eh_edital_abertura(item.titulo) and eh_cargo_saude(item.titulo, item.cargo)]


def _regex_processo_id(prefixo: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(prefixo)}-(\d+)-")


def extrair_ids_processados(identificadores_processados: set[str], *, prefixo: str = ID_PREFIX) -> set[int]:
    """Decodifica o `<id>` de processo embutido em cada
    `identificador_externo` já gravado pra esta fonte (formato
    `<prefixo>-<id>-<slug-cargo>`, montado por
    `processamento_pdf_gemini.processar_pdf_e_gravar_vagas` com
    `id_prefix=ID_PREFIX`)."""
    padrao = _regex_processo_id(prefixo)
    ids: set[int] = set()
    for identificador in identificadores_processados:
        match = padrao.match(identificador)
        if match:
            ids.add(int(match.group(1)))
    return ids
