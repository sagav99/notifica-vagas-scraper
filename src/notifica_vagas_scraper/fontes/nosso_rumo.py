"""Parser da banca terceirizada Instituto Nosso Rumo (`nossorumo.org.br`) —
investigado pelo `pesquisador-fonte` em 2026-09-06 (ver
`docs/investigacao_fonte_nosso_rumo_sp_2026-09-06.md` e fixtures reais em
`docs/fixtures/nosso_rumo_sp/`, ambos no repo principal). Achado que motivou
esta implementação: Olímpia/SP 01/2026 tem 16 vagas médicas especialistas
(R$ 7.727,13, "Inscrições Abertas", prazo 22/09/2026) hospedadas só no
domínio da banca — o parser de município (padrão Instar) não enxerga esse
edital porque ele não está no site oficial da prefeitura. Mesmo padrão de
banca terceirizada tratada como fonte própria (independente do município)
de `access.py`/`instituto_mais.py`/`inepam.py`.

**17 municípios de SP + 2 secretarias estaduais de SP, ZERO município de
MG** confirmados no histórico completo de 77 certames (ver investigação) —
mesmo assim o filtro de UF do script cobre MG também, por segurança e
consistência com o padrão de `rodar_instituto_mais.py`/`rodar_access.py`
(se a banca abrir concurso em MG no futuro, é coberto automaticamente sem
precisar tocar no parser).

## Estrutura técnica

Site ASP.NET MVC (IIS), server-rendered (não SPA). A homepage
(`nossorumo.org.br/`) já traz, em atributos `data-*` de elementos `<a
class="btn-certame2">`, o histórico completo dos certames — sem paginação
nem JS necessário pra descobrir quais concursos existem
(`listar_certames`). Cada certame aparece com 3 `<a>` irmãos (mesmo pai
direto no HTML: "Publicações" com `data-periodo-inscricao`, "Cronograma" e
"Vagas" com `data-vagas=<id_projeto>` — o id usado nas APIs abaixo).
**Achado de parsing**: o card de cada certame aparece 2x na página em
~24 dos 77 casos (seção "destaque"/recentes no topo + histórico completo
mais abaixo) — `listar_certames` deduplica por `id_projeto`, mantendo a
1ª ocorrência.

Existe uma API JSON interna, sem autenticação, sem bloqueio de CORS/anti-
bot (`curl` simples resolve):

- `GET /Cargo/Pesquisar?filtros={"mobile":false,"id":<id_projeto>,
  "mostrar_finalizado":true}` → array de cargos com `cargo`, `vagas`,
  `salario`, `escolaridade`, `requisitos`, `status` ("Inscrições
  Abertas"/"Em Andamento"/"Finalizado"), `codigo_cargo`, cidade de
  trabalho/prova e o edital (nome + nome do arquivo PDF) já embutido por
  cargo — **cargo/vagas/salário/status vêm prontos aqui, não precisa abrir
  PDF pro dado essencial** (`listar_cargos`, `montar_url_cargos`).
- `GET /Home/GetPublicacoes?idProjeto=<id>` existe (lista de editais e
  retificações) mas não é usada por este módulo: os campos que
  importariam de lá (nome do edital vigente, arquivo PDF) já vêm dentro de
  cada cargo em `/Cargo/Pesquisar` (campo `edital`).

**PDF do edital bloqueado pra download direto** (HTTP 401 sem cookie;
HTTP 200 com `Content-Length: 0` mesmo com cookie de antiforgery — ver
investigação, 3 tentativas diferentes). Não é um problema pro parser:
como a API já cobre cargo/vagas/salário/status estruturados, `url_evidencia`
aponta pra própria URL da API JSON (auditável por qualquer humano, sem
precisar de sessão de navegador) em vez do PDF em si.

**Filtro de status**: só cargos com `status == "Inscrições Abertas"`
interessam pro cron (mesma decisão de escopo de `instituto_mais`/`inepam`:
inscrição fechada = nada novo pra notificar agora). `listar_cargos` NÃO
filtra por status — devolve todos os cargos do certame sem exceção
(PRIORIDADE #1 do produto, ver CLAUDE.md: nenhuma especialidade
médica/saúde pode ser descartada silenciosamente já dentro do parser; o
filtro de status é responsabilidade de quem chama, `scripts/
rodar_nosso_rumo.py`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from bs4 import BeautifulSoup

__all__ = [
    "BASE_URL",
    "STATUS_INSCRICOES_ABERTAS",
    "ItemListagem",
    "Cargo",
    "listar_certames",
    "montar_url_cargos",
    "listar_cargos",
    "extrair_nome_concurso",
    "parsear_salario",
    "parsear_vagas",
    "parsear_periodo_inscricao",
    "extrair_numero_edital",
    "classificar_tipo_oportunidade",
    "identificador_externo",
]

_RE_PERIODO_INSCRICAO = re.compile(r"(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})")
_RE_NUMERO_EDITAL = re.compile(r"(\d{1,4}/\d{2,4})")

BASE_URL = "https://nossorumo.org.br"

#: valor exato do campo `status` da API pra inscrição aberta agora — os
#: outros valores vistos na investigação são "Em Andamento" (inscrição já
#: fechada, processo ainda rodando) e "Finalizado".
STATUS_INSCRICOES_ABERTAS = "Inscrições Abertas"


@dataclass
class ItemListagem:
    """Um certame descoberto na homepage — `id_projeto` é o número usado
    tanto no atributo `data-vagas` quanto no parâmetro `id` da API de
    cargos."""

    id_projeto: int
    nome_certame: str
    #: "DD/MM/AAAA a DD/MM/AAAA" cru, como vem do atributo
    #: `data-periodo-inscricao` — `None` se a homepage não trouxe esse dado
    #: pro certame (achado não confirmado na investigação, mas o parser não
    #: assume que sempre existe).
    periodo_inscricao: str | None


@dataclass
class Cargo:
    """Uma linha da API `/Cargo/Pesquisar` — 1 por especialidade/cargo do
    certame, nunca agrupado (ver docstring do módulo, PRIORIDADE #1)."""

    id_cargo: int
    id_projeto: int
    status: str
    #: nome do certame já sem as tags `<br>` (ver `extrair_nome_concurso`).
    concurso: str
    cargo: str
    vagas: int | None
    salario: Decimal | None
    escolaridade: str | None
    requisitos: str | None
    codigo_cargo: str | None
    cidade_trabalho: str | None
    cidade_prova: str | None
    edital_nome: str | None
    edital_arquivo: str | None


def listar_certames(html: str) -> list[ItemListagem]:
    """Lê a homepage e devolve 1 item por certame distinto (deduplicado por
    `id_projeto` — ver achado do "card duplicado" na docstring do módulo).

    Cada certame tem 3 `<a class="btn-certame2">` irmãos (mesmo elemento
    pai): só o que tem `data-vagas` é obrigatório (é o id usado na API);
    sem ele, o certame é ignorado (não há como buscar cargos sem esse id).
    """
    soup = BeautifulSoup(html, "html.parser")

    grupos_por_pai: dict[int, list] = {}
    ordem_pais: list[int] = []
    for anchor in soup.find_all("a", class_="btn-certame2"):
        pai = anchor.parent
        if pai is None:
            continue
        chave = id(pai)
        if chave not in grupos_por_pai:
            grupos_por_pai[chave] = []
            ordem_pais.append(chave)
        grupos_por_pai[chave].append(anchor)

    vistos: set[int] = set()
    itens: list[ItemListagem] = []
    for chave in ordem_pais:
        grupo = grupos_por_pai[chave]
        anchor_vagas = next((a for a in grupo if a.has_attr("data-vagas")), None)
        if anchor_vagas is None:
            continue
        try:
            id_projeto = int(str(anchor_vagas["data-vagas"]).strip())
        except (TypeError, ValueError):
            continue
        if id_projeto in vistos:
            continue
        vistos.add(id_projeto)

        nome_certame = re.sub(r"\s+", " ", anchor_vagas.get("data-nome-certame", "")).strip()
        anchor_periodo = next((a for a in grupo if a.has_attr("data-periodo-inscricao")), None)
        periodo = anchor_periodo["data-periodo-inscricao"].strip() if anchor_periodo else None

        itens.append(ItemListagem(id_projeto=id_projeto, nome_certame=nome_certame, periodo_inscricao=periodo))

    return itens


def montar_url_cargos(id_projeto: int) -> str:
    """Monta a URL de `GET /Cargo/Pesquisar` pro `id_projeto` dado — mesmo
    formato de filtro usado pelo próprio front-end do site (ver docstring
    do módulo)."""
    filtros = json.dumps(
        {"mobile": False, "id": id_projeto, "mostrar_finalizado": True},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{BASE_URL}/Cargo/Pesquisar?filtros={quote(filtros)}"


def extrair_nome_concurso(bruto: str) -> str:
    """O campo `concurso` da API vem com `<br>` separando "tipo",
    "entidade" e "número do edital" (ex: "Concurso Público<br>PREFEITURA
    ...<br>01/2026") — troca por " - " e normaliza espaço."""
    texto = re.sub(r"<br\s*/?>", " - ", bruto)
    return re.sub(r"\s+", " ", texto).strip()


def parsear_salario(texto: str | None) -> Decimal | None:
    """"R$ 7.727,13" -> `Decimal("7727.13")`. `None` pra vazio/não numérico
    — nunca lança exceção (dado de fonte externa, sempre tratado como
    potencialmente sujo)."""
    if not texto:
        return None
    limpo = re.sub(r"[^\d,.]", "", texto)
    if not limpo:
        return None
    limpo = limpo.replace(".", "").replace(",", ".")
    try:
        return Decimal(limpo)
    except InvalidOperation:
        return None


def parsear_vagas(texto: str | None) -> int | None:
    """"1"/"142" -> `int`. `None` pra vazio/não numérico. **Não filtra
    vaga com 0** (cadastro de reserva visto na investigação, ex: Porangaba
    Médico Generalista) — mesma decisão de `instituto_mais.VagaQuadro`,
    PRIORIDADE #1 do produto: 0 vaga ainda é uma linha, não é descartada."""
    if texto is None:
        return None
    texto = str(texto).strip()
    if not texto:
        return None
    try:
        return int(texto)
    except ValueError:
        match = re.search(r"\d+", texto)
        return int(match.group(0)) if match else None


def listar_cargos(dados: list[dict]) -> list[Cargo]:
    """Lê o array devolvido por `/Cargo/Pesquisar` (já decodificado de
    JSON pelo chamador) — 1 `Cargo` por item, **sem filtrar por status nem
    por nome** (PRIORIDADE #1 do produto: nenhum cargo médico/saúde pode
    ser descartado já aqui; o filtro de "Inscrições Abertas" é
    responsabilidade de `scripts/rodar_nosso_rumo.py`). Item sem `cargo`
    ou sem `id_cargo`/`id_projeto` é ignorado por ser malformado, não por
    conteúdo."""
    cargos: list[Cargo] = []
    for item in dados:
        nome_cargo = (item.get("cargo") or "").strip()
        if not nome_cargo:
            continue
        if item.get("id_cargo") is None or item.get("id_projeto") is None:
            continue

        edital = item.get("edital") or {}
        inscricao = item.get("inscricao") or {}

        cargos.append(
            Cargo(
                id_cargo=int(item["id_cargo"]),
                id_projeto=int(item["id_projeto"]),
                status=(item.get("status") or "").strip(),
                concurso=extrair_nome_concurso(item.get("concurso") or ""),
                cargo=nome_cargo,
                vagas=parsear_vagas(item.get("vagas")),
                salario=parsear_salario(item.get("salario")),
                escolaridade=(item.get("escolaridade") or "").strip() or None,
                requisitos=(item.get("requisitos") or "").strip() or None,
                codigo_cargo=(item.get("codigo_cargo") or "").strip() or None,
                cidade_trabalho=(inscricao.get("cidade_trabalho") or "").strip() or None,
                cidade_prova=(inscricao.get("cidade_prova") or "").strip() or None,
                edital_nome=(edital.get("nome") or "").strip() or None,
                edital_arquivo=(edital.get("arquivo") or "").strip() or None,
            )
        )
    return cargos


def parsear_periodo_inscricao(texto: str | None) -> tuple[date | None, date | None]:
    """"03/09/2026 a 22/09/2026" -> `(date(2026,9,3), date(2026,9,22))`.
    `(None, None)` pra vazio/formato inesperado — mesmo padrão de
    `inepam.extrair_periodo_inscricao`."""
    if not texto:
        return None, None
    match = _RE_PERIODO_INSCRICAO.search(texto)
    if not match:
        return None, None

    def _parse(data_texto: str) -> date:
        dia, mes, ano = data_texto.split("/")
        return date(int(ano), int(mes), int(dia))

    return _parse(match.group(1)), _parse(match.group(2))


def extrair_numero_edital(concurso: str) -> str | None:
    """Extrai "NN/AAAA" do nome do certame — no formato observado na
    investigação, é sempre o último segmento (ex: "Concurso Público -
    PREFEITURA ... - 01/2026"). `None` se não achar o padrão."""
    match = _RE_NUMERO_EDITAL.search(concurso)
    return match.group(1) if match else None


def classificar_tipo_oportunidade(concurso: str) -> str | None:
    """Deriva `tipo_oportunidade` a partir do prefixo do campo `concurso`
    (ex: "Concurso Público<br>..." -> `"concurso_efetivo"`) — taxonomia
    fechada de `check_tipos_oportunidade_validos` (migration 009 do repo
    principal). `None` quando o texto não bate com nenhum padrão
    conhecido (nunca inventa valor fora da lista fechada)."""
    texto = concurso.lower()
    if "processo seletivo" in texto:
        return "processo_seletivo_temporario"
    if "credenciamento" in texto:
        return "credenciamento"
    if "concurso" in texto:
        return "concurso_efetivo"
    return None


def identificador_externo(id_cargo: int) -> str:
    """Chave de dedup: `id_cargo` já é único globalmente na plataforma
    (confirmado na investigação: faixas distintas por certame, ex:
    9956-10002 em Olímpia, 10082+ em Itanhaém) — não precisa compor com
    `id_projeto` nem com slug do nome do cargo."""
    return f"nosso-rumo-{id_cargo}"
