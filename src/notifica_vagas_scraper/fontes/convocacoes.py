"""Monitor de convocações em diários oficiais (plano Max do Med Vagas).

Duas perguntas, ambas respondidas SEM IA e sem Serper:

1. **nome** — o nome do assinante (como no edital) aparece numa publicação que fala de
   convocação/nomeação/posse, em QUALQUER concurso? Sem cadastro de concurso.
2. **concurso** (opcional) — saiu convocação do município/concurso que o assinante acompanha?

Arquitetura (backtest 2026-10-29): a busca por texto do SIGPub ignora aspas e ordem das palavras e
ordena por data, então NÃO serve para achar um nome (nome comum estoura as 2 páginas). Em vez
disso lê-se, uma vez, todas as publicações novas de convocação do dia (varredura por termos, dia
a dia para não bater o teto de 12 páginas) e cruza-se o texto com os nomes de TODOS os assinantes
em memória: custo independente do número de assinantes. Publicações já lidas ficam em
`convocacao_vistos`. SC: ZIPs do CIGA, mesma ideia.

Mapa de cobertura (investigação 2026-10-29, `docs/convocacoes_monitoramento.md` no repo principal):
MG/PR/RS/SC bem cobertos pelos agregadores; SP fraco (APM tem ~8 entidades) -> complemento com
Google News RSS (grátis), só como "possível convocação, confira".

Tudo aqui é função pura ou HTTP simples; o gravar no banco fica em `scripts/monitorar_convocacoes.py`.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

from . import sigpub_busca
from .diarios_sul import SLUG_SIGPUB, USER_AGENT, _EXCLUIR_TEXTO, normalizar, texto_limpo, texto_materia_sigpub

#: Diário SIGPub por UF (`/{slug}/pesquisar`). SC não usa SIGPub (CIGA, dados abertos).
SLUG_POR_UF = {"MG": "amm-mg", "SP": "apm", "PR": SLUG_SIGPUB["PR"], "RS": SLUG_SIGPUB["RS"]}

#: Termos da varredura global de convocação por UF (cada termo = até MAX_PAGINAS_VARREDURA de 25 cartões).
TERMOS_VARREDURA = ("convocação", "nomeação", "chamada", "chamamento", "aprovados", "posse")
MAX_PAGINAS_VARREDURA = 12

_SINAL_CONVOCACAO = re.compile(r"(convoc|nomea|chamad|posse|admiss|apresent|aprovad|classificad)")
_TITULO_CONVOCACAO = re.compile(r"(convoc|nomea|chamad|posse)")
_RUIDO_TITULO = re.compile(r"(exonera|aposenta|licenca|ferias|ata de registro|pregao|licitac|assinatura|aditivo|prorrogacao de contrato|distrato|contrato de admissao|chamada publica n)")


@dataclass
class Acompanhamento:
    """`uf`/`municipio` vazios = "meu nome em qualquer diário" (modo padrão, sem cadastrar concurso)."""

    id: str
    usuario_id: str
    nome_edital: str
    uf: str | None
    municipio: str | None
    concurso: str | None = None
    cargo: str | None = None
    classificacao: int | None = None


@dataclass
class Achado:
    """Publicação que merece aviso para um acompanhamento."""

    acompanhamento_id: str
    tipo: str  # 'nome' | 'concurso'
    chave: str  # chave única da publicação (fonte:codigo)
    fonte: str
    uf: str
    municipio: str | None
    titulo: str
    data_publicacao: date | None
    url: str
    trecho: str
    concurso: str | None = None
    cargo: str | None = None


# --------------------------------------------------------------------- texto


#: Palavras que podem vir antes do nome sem que ele seja parte de um nome maior.
_ANTES_DO_NOME = {
    "candidato", "candidata", "sr", "sra", "srta", "senhor", "senhora", "dr", "dra", "nome", "servidor", "servidora",
    "aprovado", "aprovada", "classificado", "classificada", "convocado", "convocada", "nomear", "nomeado", "nomeada",
    "a", "o", "e", "sob", "pelo", "pela", "para", "que", "como", "inscrito", "inscrita",
}


_ASSINATURA = re.compile(r"[\s.,;:\-–]*(prefeit|president|secretari|diretor|gestor|vice|coordenador|chefe)", re.I)


def _maiuscula_predominante(trecho: str) -> bool:
    letras = [c for c in trecho if c.isalpha()]
    return bool(letras) and sum(c.isupper() for c in letras) / len(letras) >= 0.6


def nome_no_texto(nome: str, texto: str) -> int:
    """Posição do nome completo (sem acento, sem caixa, palavras inteiras) no texto, ou -1.

    Nome com menos de duas palavras nunca casa (evita avisar por um sobrenome solto). Também
    não casa quando o nome é só a ponta de um nome maior colado à esquerda ('Bianca Maria da
    Silva' para 'Maria da Silva'), exceto em texto de listas em CAIXA ALTA, onde as linhas vêm
    coladas e perder a convocação é pior que um aviso a mais."""
    alvo = normalizar(nome)
    if len(alvo.split()) < 2:
        return -1
    plano = re.sub(r"\s+", " ", texto)
    sem_acento = normalizar(plano)
    if len(sem_acento) != len(plano):  # normalizar mexeu no tamanho (símbolos exóticos): sem checagem de borda
        achado = re.search(rf"(?<![a-z0-9]){re.escape(alvo)}(?![a-z0-9])", sem_acento)
        return achado.start() if achado else -1
    for achado in re.finditer(rf"(?<![a-z0-9]){re.escape(alvo)}(?![a-z0-9])", sem_acento):
        if _ASSINATURA.match(sem_acento[achado.end() : achado.end() + 40]):
            continue  # assinatura do ato ("... Prefeito Municipal"), não candidato
        antes = re.search(r"([^\W\d_]+)\s+$", plano[: achado.start()])
        if antes and antes[1].lower() not in _ANTES_DO_NOME and antes[1][:1].isupper():
            if not _maiuscula_predominante(plano[max(0, achado.start() - 200) : achado.end() + 200]):
                continue
        return achado.start()
    return -1


def trecho_em_torno(texto: str, nome: str, raio: int = 220) -> str:
    """Trecho do texto original em torno do nome (para mostrar ao assinante)."""
    plano = re.sub(r"\s+", " ", texto)
    pos = nome_no_texto(nome, plano)
    if pos < 0:
        return _sem_cabecalho(plano)[: raio * 2]
    ini = max(0, pos - raio)
    return ("…" if ini else "") + plano[ini : pos + len(nome) + raio] + "…"


def _sem_cabecalho(texto: str) -> str:
    """Tira o cabeçalho de navegação do SIGPub ('Imprimir a matéria Baixar esta matéria ...')."""
    plano = re.sub(r"\s+", " ", texto)
    achado = re.search(r"Baixar a edi[cç][aã]o completa \(PDF\)\s*", plano)
    return plano[achado.end() :] if achado else plano


def contexto_de_convocacao(titulo: str, texto: str, nome: str) -> bool:
    """O nome aparece numa publicação de convocação/nomeação? Título com o sinal OU sinal perto do nome."""
    if _RUIDO_TITULO.search(normalizar(titulo)):
        return False
    if _TITULO_CONVOCACAO.search(normalizar(titulo)):
        return True
    plano = normalizar(texto)
    pos = nome_no_texto(nome, texto)
    if pos < 0:
        return False
    return bool(_SINAL_CONVOCACAO.search(plano[max(0, pos - 500) : pos + 400]))


def numero_edital_no_texto(concurso: str | None, texto: str) -> bool:
    """Confere o número do concurso/edital ('001/2023', 'Concurso 1/2023') ignorando zeros à esquerda."""
    if not concurso:
        return True
    numeros = re.findall(r"(\d+)\s*/\s*(\d{2,4})", concurso)
    if not numeros:
        return normalizar(concurso) in normalizar(texto)
    for numero, ano in numeros:
        padrao = rf"(?<!\d)0*{int(numero)}\s*/\s*(?:20)?{ano[-2:]}(?!\d)"
        if re.search(padrao, texto):
            return True
    return False


def cargo_no_texto(cargo: str | None, texto: str) -> bool:
    """Alguma palavra significativa (≥5 letras) do cargo aparece no texto? Sem cargo, aceita."""
    if not cargo:
        return True
    plano = normalizar(_EXCLUIR_TEXTO.sub(" ", texto))
    palavras = [p for p in normalizar(cargo).split() if len(p) >= 5]
    return not palavras or any(p in plano for p in palavras)


def entidade_eh_do_municipio(entidade: str, municipio: str) -> bool:
    return normalizar(municipio) in normalizar(entidade)


# ------------------------------------------------------------------- SIGPub


def _buscar_sigpub(sessao: requests.Session, uf: str, termo: str, inicio: date, fim: date, pagina: int) -> str:
    resposta = sessao.get(
        f"https://www.diariomunicipal.com.br/{SLUG_POR_UF[uf]}/pesquisar",
        params={
            "busca_avancada[texto]": termo,
            "busca_avancada[dataInicio]": inicio.strftime("%Y-%m-%d"),
            "busca_avancada[dataFim]": fim.strftime("%Y-%m-%d"),
            "busca_avancada[pagina]": str(pagina),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=45,
    )
    resposta.raise_for_status()
    return resposta.text


_CACHE_MATERIAS: dict[tuple[str, str], str] = {}


def texto_da_materia(sessao: requests.Session, uf: str, codigo: str) -> str:
    if (uf, codigo) in _CACHE_MATERIAS:
        return _CACHE_MATERIAS[(uf, codigo)]
    resposta = sessao.get(
        f"https://www.diariomunicipal.com.br/{SLUG_POR_UF[uf]}/materia/{codigo}",
        headers={"User-Agent": USER_AGENT},
        timeout=45,
    )
    resposta.raise_for_status()
    _CACHE_MATERIAS[(uf, codigo)] = texto_materia_sigpub(resposta.text)
    return _CACHE_MATERIAS[(uf, codigo)]


def url_materia(uf: str, codigo: str) -> str:
    return f"https://www.diariomunicipal.com.br/{SLUG_POR_UF[uf]}/materia/{codigo}"


_NUM_CONCURSO = re.compile(
    r"(concurso p[uú]blico|processo seletivo(?: simplificado)?|\bpss\b|edital)\s*(?:de [a-zç ]{0,20})?(?:n[ºo°.]*\s*)?(\d{1,4}\s*/\s*\d{2,4})", re.I
)
_CARGO = re.compile(r"cargo(?:s)?\s*(?:de|:)?\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,;.\n(]{3,60})", re.I)
_ENTIDADE_MUNICIPAL = re.compile(r"^(?:prefeitura|munic[ií]pio)(?: municipal)?(?: de| do| da)?\s+", re.I)


def municipio_da_entidade(entidade: str | None) -> str | None:
    if not entidade:
        return None
    nome = _ENTIDADE_MUNICIPAL.sub("", entidade.strip()).strip()
    return nome or None


def detectar_concurso(titulo: str, texto: str, pos: int = -1) -> tuple[str | None, str | None]:
    """(concurso, cargo) que a publicação cita, para dizer ao assinante de qual concurso é a convocação.

    Procura o número no título, depois no começo do texto; o cargo, perto do nome (ou no começo)."""
    plano = re.sub(r"\s+", " ", texto)
    concurso = None
    for base in (titulo, plano[:3000]):
        achado = _NUM_CONCURSO.search(base)
        if achado:
            tipo = achado[1].lower().replace("público", "público")
            numero = re.sub(r"\s+", "", achado[2])
            concurso = f"{tipo.capitalize()} {numero}"
            break
    cargo = None
    regioes = ([plano[max(0, pos - 700) : pos + 200]] if pos >= 0 else []) + [plano[:3000]]
    for regiao in regioes:
        achado = _CARGO.search(regiao)
        if achado:
            cargo = re.split(r"\s+(?:o|a|os|as)\s+(?:candidat|aprovad|classificad)|\s+(?:para|que|do|no|da|conforme|aprovad)\b", achado[1], maxsplit=1, flags=re.I)[0]
            cargo = cargo.strip(" -–:")[:60]
            break
    return concurso, cargo


def avaliar_publicacao(
    ac: Acompanhamento, *, fonte: str, uf: str, chave: str, titulo: str, entidade: str | None,
    data_publicacao: date | None, url: str, texto: str,
) -> Achado | None:
    """Esta publicação merece aviso para este acompanhamento? Nome vence concurso."""
    if _RUIDO_TITULO.search(normalizar(titulo)):
        return None
    municipio = municipio_da_entidade(entidade) or entidade
    pos = nome_no_texto(ac.nome_edital, texto)
    if pos >= 0 and contexto_de_convocacao(titulo, texto, ac.nome_edital):
        concurso, cargo = detectar_concurso(titulo, texto, pos)
        return Achado(ac.id, "nome", chave, fonte, uf, municipio, titulo, data_publicacao, url, trecho_em_torno(texto, ac.nome_edital), concurso, cargo)
    if (
        ac.uf == uf
        and ac.municipio
        and entidade
        and entidade_eh_do_municipio(entidade, ac.municipio)
        and _TITULO_CONVOCACAO.search(normalizar(titulo))
        and numero_edital_no_texto(ac.concurso, texto)
        and cargo_no_texto(ac.cargo, texto)
    ):
        concurso, cargo = detectar_concurso(titulo, texto)
        return Achado(ac.id, "concurso", chave, fonte, uf, ac.municipio, titulo, data_publicacao, url, _sem_cabecalho(texto)[:420], concurso, cargo)
    return None


def varrer_dia_sigpub(sessao: requests.Session, uf: str, dia: date, *, pausa=lambda: None) -> list:
    """Cartões (título, entidade, código, data) de convocação/nomeação/chamada de UM dia do diário do estado.

    Um dia por vez para nunca bater o teto de MAX_PAGINAS_VARREDURA páginas por termo."""
    vistos: dict[str, object] = {}
    for termo in TERMOS_VARREDURA:
        for pagina in range(1, MAX_PAGINAS_VARREDURA + 1):
            cartoes = sigpub_busca.parsear_resultados(_buscar_sigpub(sessao, uf, termo, dia, dia, pagina))
            pausa()
            for c in cartoes:
                vistos.setdefault(c.codigo, c)
            if len(cartoes) < 25:
                break
    return list(vistos.values())


def processar_sigpub(
    sessao: requests.Session, uf: str, dias: list[date], acompanhamentos: list[Acompanhamento],
    ja_vistos: set[str], *, pausa=lambda: None, ao_ler=lambda chave: None,
) -> tuple[list[Achado], int]:
    """Lê as publicações novas dos `dias` e cruza com os acompanhamentos. Devolve (achados, lidas)."""
    alvo = [a for a in acompanhamentos if a.uf in (None, uf)]
    if not alvo:
        return [], 0
    achados: list[Achado] = []
    lidas = 0
    for dia in dias:
        for c in varrer_dia_sigpub(sessao, uf, dia, pausa=pausa):
            chave = f"sigpub-{uf.lower()}:{c.codigo}"
            if chave in ja_vistos:
                continue
            texto = texto_da_materia(sessao, uf, c.codigo)
            pausa()
            lidas += 1
            for ac in alvo:
                achado = avaliar_publicacao(
                    ac, fonte=f"sigpub-{uf.lower()}", uf=uf, chave=chave, titulo=c.titulo, entidade=c.entidade,
                    data_publicacao=c.data_circulacao, url=url_materia(uf, c.codigo), texto=texto,
                )
                if achado:
                    achados.append(achado)
            ao_ler(chave)
    return achados, lidas


# ----------------------------------------------------------- SC (CIGA aberto)


def _data_iso(texto: str) -> date | None:
    achado = re.match(r"(\d{4})-(\d{2})-(\d{2})", texto or "")
    if achado:
        return date(int(achado[1]), int(achado[2]), int(achado[3]))
    achado = re.match(r"(\d{2})/(\d{2})/(\d{4})", texto or "")
    return date(int(achado[3]), int(achado[2]), int(achado[1])) if achado else None


def achados_sc(publicacoes: list, acompanhamentos: list[Acompanhamento]) -> list[Achado]:
    """Cruza as publicações do CIGA (já lidas de um ZIP) com os acompanhamentos de SC (ou sem UF)."""
    alvo = [a for a in acompanhamentos if a.uf in (None, "SC")]
    achados: list[Achado] = []
    if not alvo:
        return achados
    for pub in publicacoes:
        base = normalizar(f"{pub.titulo} {pub.categoria}")
        if not re.search(r"(convoc|nomea|chamad|posse|admiss|concurs|processo seletivo|resultado|homolog|edital)", base):
            continue
        texto = texto_limpo(pub.texto)
        for ac in alvo:
            achado = avaliar_publicacao(
                ac, fonte="ciga-sc", uf="SC", chave=f"ciga-sc:{pub.codigo}", titulo=pub.titulo, entidade=pub.municipio,
                data_publicacao=_data_iso(pub.data), url=pub.link or pub.url, texto=texto,
            )
            if achado:
                achado.municipio = pub.municipio
                achados.append(achado)
    return achados


# ----------------------------------------------------------- Google News (SP)


def noticias_convocacao(sessao: requests.Session, ac: Acompanhamento, desde: date) -> list[Achado]:
    if not ac.municipio:
        return []
    """Complemento para onde não há diário agregador (SP): RSS do Google Notícias, grátis.

    Só vale como pista ('possível convocação, confira'): o título precisa citar o município e
    falar de convocação/nomeação."""
    termos = f'convocação concurso "{ac.municipio}" {ac.cargo or "médico"}'
    resposta = sessao.get(
        f"https://news.google.com/rss/search?q={quote(termos)}+when:3d&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resposta.raise_for_status()
    achados: list[Achado] = []
    for item in ET.fromstring(resposta.content).iter("item"):
        titulo = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not titulo or not link:
            continue
        base = normalizar(titulo)
        if normalizar(ac.municipio) not in base or not _TITULO_CONVOCACAO.search(base):
            continue
        publicado = None
        if item.findtext("pubDate"):
            try:
                publicado = parsedate_to_datetime(item.findtext("pubDate")).date()
            except (TypeError, ValueError):
                publicado = None
        if publicado and publicado < desde:
            continue
        chave = "gnews:" + hashlib.sha1(link.encode()).hexdigest()[:16]
        achados.append(Achado(ac.id, "concurso", chave, "google-news", ac.uf, ac.municipio, titulo, publicado, link, titulo))
    return achados
