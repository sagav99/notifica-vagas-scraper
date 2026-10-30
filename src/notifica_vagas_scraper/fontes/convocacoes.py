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
import ipaddress
from urllib.parse import urlparse
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
    url_convocacoes: str | None = None


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
    prazo: str | None = None
    confianca: str | None = None  # 'alta' = classificação informada confere; 'baixa' = publicação indica outra


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
    partes = alvo.split()
    # "SILVA, Maria da" (sobrenome primeiro, comum em listas): também vale para o mesmo nome
    invertido = rf"{re.escape(partes[-1])}\s*,\s*{re.escape(' '.join(partes[:-1]))}"
    if len(sem_acento) == len(plano):
        achado_inv = re.search(rf"(?<![a-z0-9]){invertido}(?![a-z0-9])", sem_acento)
        if achado_inv and not re.search(rf"(?<![a-z0-9]){re.escape(alvo)}(?![a-z0-9])", sem_acento):
            return achado_inv.start()
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
            if re.search(r"(efetivo|lota[cç]|unidade|\bdata\b|segue|abaixo|conforme|tabela|anexo)", cargo, re.I) or len(cargo) < 4:
                cargo = None
                continue
            break
    return concurso, cargo


_NUMEROS_EXTENSO = {"um": 1, "dois": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "dez": 10, "quinze": 15, "vinte": 20, "trinta": 30}
_PRAZO_DIAS = re.compile(r"prazo\s+(?:m[aá]ximo\s+)?de\s+(\d{1,3}|[^\W\d_]+)\s*(?:\([^)]{1,30}\)\s*)?(dias?(?:\s+[uú]teis)?|horas?)", re.I)
_PRAZO_ATE = re.compile(r"(?:at[eé]|no dia|em)\s+(?:o\s+dia\s+)?(\d{1,2}/\d{1,2}/\d{2,4})(?:\s*(?:,|[àa]s)?\s*(?:at[eé]\s+)?(?:[àa]s\s+)?(\d{1,2}[h:]\d{0,2}))?", re.I)


def detectar_prazo(texto: str, pos: int = -1) -> str | None:
    """Prazo para comparecer/apresentar documentos ('5 dias úteis', 'até 05/11/2026'), perto do nome ou no texto."""
    plano = re.sub(r"\s+", " ", texto)
    regioes = ([plano[max(0, pos - 1200) : pos + 1500]] if pos >= 0 else []) + [plano[:4000]]
    for regiao in regioes:
        achado = _PRAZO_DIAS.search(regiao)
        if achado:
            bruto = normalizar(achado[1])
            numero = int(bruto) if bruto.isdigit() else _NUMEROS_EXTENSO.get(bruto)
            if numero:
                return f"{numero} {re.sub(r'\s+', ' ', normalizar(achado[2]))}".replace("uteis", "úteis")
        achado = _PRAZO_ATE.search(regiao)
        if achado:
            hora = f" às {achado[2]}" if achado[2] else ""
            return f"até {achado[1]}{hora}"
    return None


_ORDINAL = re.compile(r"(\d{1,5})\s*(?:[ºo°ª]|\u00ba)(?!\w)")


def avaliar_classificacao(classificacao: int | None, texto: str, pos: int) -> str | None:
    """'alta' se o número informado aparece junto do nome; 'baixa' se só aparecem outros; None sem dado."""
    if not classificacao or pos < 0:
        return None
    plano = re.sub(r"\s+", " ", texto)
    janela = plano[max(0, pos - 90) : pos + 160]
    numeros = {int(n) for n in _ORDINAL.findall(janela)} | {int(n) for n in re.findall(r"(?:classifica[cç][aã]o|coloca[cç][aã]o|lugar)\D{0,12}(\d{1,5})", janela, re.I)}
    if not numeros:
        return None
    return "alta" if classificacao in numeros else "baixa"


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
        return Achado(
            ac.id, "nome", chave, fonte, uf, municipio, titulo, data_publicacao, url, trecho_em_torno(texto, ac.nome_edital),
            concurso, cargo, detectar_prazo(texto, pos), avaliar_classificacao(ac.classificacao, texto, pos),
        )
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
        return Achado(ac.id, "concurso", chave, fonte, uf, ac.municipio, titulo, data_publicacao, url, _sem_cabecalho(texto)[:420], concurso, cargo, detectar_prazo(texto))
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
) -> tuple[list[Achado], int, int]:
    """Lê as publicações novas dos `dias` e cruza com os acompanhamentos. Devolve (achados, lidas, cartões vistos);
    cartões = 0 em dia útil indica que o diário mudou de HTML ou saiu do ar (o chamador deve falhar alto)."""
    alvo = [a for a in acompanhamentos if a.uf in (None, uf)]
    if not alvo:
        return [], 0, -1
    achados: list[Achado] = []
    lidas = 0
    cartoes_total = 0
    for dia in dias:
        cartoes_dia = varrer_dia_sigpub(sessao, uf, dia, pausa=pausa)
        cartoes_total += len(cartoes_dia)
        for c in cartoes_dia:
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
    return achados, lidas, cartoes_total


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


# ----------------------------------------------- Querido Diário (milhares de municípios)

QD_API = "https://queridodiario.ok.org.br/api"  # api.queridodiario... falha no TLS de alguns clientes; este caminho é o mesmo serviço
UFS_QD = {"MG", "SP", "PR", "SC", "RS"}
MAX_PAGINAS_QD = 3


def consulta_querido_diario(nome: str) -> str:
    """Frase exata do nome (a API do Querido Diário respeita aspas, ao contrário do SIGPub)."""
    return '"' + re.sub(r"\s+", " ", nome.replace('"', " ")).strip() + '"'


def achados_querido_diario(sessao: requests.Session, ac: Acompanhamento, desde: date, *, baixar=None) -> list[Achado]:
    """Busca o NOME do assinante (frase exata) em todos os diários que o Querido Diário indexa e confirma localmente.

    Custo: 1 requisição por nome (+ o download dos diários que a busca devolveu), independente de quantos
    municípios existam. Cobre o que o SIGPub/CIGA não cobrem (ex.: SP). `baixar(url) -> str` é injetável nos testes."""
    if len(normalizar(ac.nome_edital).split()) < 2:
        return []
    baixar = baixar or (lambda u: sessao.get(u, headers={"User-Agent": USER_AGENT}, timeout=120).text[:12_000_000])
    ufs = {ac.uf} if ac.uf else UFS_QD
    achados: list[Achado] = []
    vistas: set[str] = set()
    for pagina in range(MAX_PAGINAS_QD):
        r = sessao.get(
            f"{QD_API}/gazettes",
            params={"querystring": consulta_querido_diario(ac.nome_edital), "published_since": desde.isoformat(), "size": 50, "offset": pagina * 50, "sort_by": "descending_date"},
            headers={"User-Agent": USER_AGENT}, timeout=90,
        )
        r.raise_for_status()
        dados = r.json()
        for g in dados.get("gazettes", []):
            if g.get("state_code") not in ufs or g["txt_url"] in vistas:
                continue
            vistas.add(g["txt_url"])
            texto = re.sub(r"\s+", " ", baixar(g["txt_url"]))
            pos = nome_no_texto(ac.nome_edital, texto)
            if pos < 0:
                continue
            # o diário inteiro é enorme: avalia só a janela em torno do nome (o contexto e o concurso são locais)
            janela = texto[max(0, pos - 2500) : pos + 1500]
            achado = avaliar_publicacao(
                ac, fonte="querido-diario", uf=g["state_code"], chave=f"qd:{g['territory_id']}:{g['date']}:{g.get('edition') or ''}:{g['is_extra_edition']}",
                titulo="Diário Oficial", entidade=g["territory_name"], data_publicacao=date.fromisoformat(g["date"]), url=g.get("url") or g["txt_url"], texto=janela,
            )
            if achado and achado.tipo == "nome":
                achado.municipio = g["territory_name"]
                achados.append(achado)
        if len(dados.get("gazettes", [])) < 50:
            break
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


# ------------------------------------------------------------------- cobertura


def municipios_cobertos_sigpub(sessao: requests.Session, uf: str) -> list[str]:
    """Municípios que o diário agregador do estado cobre (opções do seletor de entidade)."""
    from bs4 import BeautifulSoup

    resposta = sessao.get(
        f"https://www.diariomunicipal.com.br/{SLUG_POR_UF[uf]}/pesquisar", headers={"User-Agent": USER_AGENT}, timeout=45
    )
    resposta.raise_for_status()
    soup = BeautifulSoup(resposta.text, "html.parser")
    nomes: list[str] = []
    for sel in soup.find_all("select", attrs={"name": "busca_avancada[entidade]"}):
        for opcao in sel.find_all("option"):
            entidade = opcao.get_text(strip=True)
            if re.match(r"(prefeitura|munic[ií]pio)", entidade, re.I):
                municipio = municipio_da_entidade(entidade)
                if municipio:
                    nomes.append(municipio)
    return sorted(set(nomes))


# ---------------------------------------------- página de convocações (link do usuário)

_SENTENCA_CONVOCACAO = re.compile(r"(convoc|nomea|chamad|posse|classificad|aprovad)", re.I)


def url_de_pagina_valida(url: str | None) -> bool:
    """https, host com domínio (nunca IP/localhost/porta), sem credenciais: o scraper só busca páginas públicas."""
    if not url or len(url) > 300:
        return False
    try:
        partes = urlparse(url)
        host = partes.hostname or ""
        if partes.scheme != "https" or partes.username or partes.password or partes.port not in (None, 443):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            pass
        return "." in host and not host.endswith((".local", ".internal", ".localhost"))
    except ValueError:
        return False


def _sentencas(texto: str) -> list[str]:
    plano = re.sub(r"\s+", " ", texto)
    return [p.strip() for p in re.split(r"(?<=[.;!?])\s+|\s{2,}| \| ", plano) if len(p.strip()) >= 25]


def sentencas_novas(texto: str, anteriores: set[str]) -> list[str]:
    """Frases de convocação que não estavam na leitura anterior da página (hash de cada frase)."""
    novas = []
    for frase in _sentencas(texto):
        if hashlib.sha1(normalizar(frase).encode()).hexdigest()[:16] not in anteriores and _SENTENCA_CONVOCACAO.search(frase):
            novas.append(frase)
    return novas


def hashes_das_sentencas(texto: str) -> set[str]:
    return {hashlib.sha1(normalizar(f).encode()).hexdigest()[:16] for f in _sentencas(texto)}


def achados_da_pagina(ac: Acompanhamento, url: str, texto: str, anteriores: set[str] | None) -> list[Achado]:
    """Compara a página com a leitura anterior. Primeira leitura (`anteriores` None) só avisa se o NOME já está lá."""
    chave_base = "pagina:" + hashlib.sha1(f"{url}|{ac.id}".encode()).hexdigest()[:12]
    achados: list[Achado] = []
    pos = nome_no_texto(ac.nome_edital, texto)
    novas = sentencas_novas(texto, anteriores or set()) if anteriores is not None else []
    if pos >= 0 and (anteriores is None or any(nome_no_texto(ac.nome_edital, f) >= 0 for f in novas)):
        trecho = trecho_em_torno(texto, ac.nome_edital)
        chave = f"{chave_base}:{hashlib.sha1(normalizar(trecho).encode()).hexdigest()[:10]}"
        achados.append(Achado(ac.id, "nome", chave, "pagina", ac.uf or "", ac.municipio, "Página de convocações do concurso", None, url, trecho, None, None, detectar_prazo(texto, pos), avaliar_classificacao(ac.classificacao, texto, pos)))
    elif novas:
        resumo = " … ".join(novas[:3])[:420]
        chave = f"{chave_base}:{hashlib.sha1(normalizar(resumo).encode()).hexdigest()[:10]}"
        achados.append(Achado(ac.id, "concurso", chave, "pagina", ac.uf or "", ac.municipio, "Página de convocações atualizada", None, url, resumo))
    return achados


# ------------------------------------------------ Google (só para município sem diário agregador)

_DOMINIOS_RUIDO = ("instagram.com", "facebook.com", "linkedin.com", "glassdoor", "indeed.com", "jusbrasil.com.br", "youtube.com", "tiktok.com", "x.com", "twitter.com")


def consulta_google(ac: Acompanhamento) -> str:
    """Busca do município + concurso (nunca do nome: o Google não indexa os diários por nome, backtest 2026-10-29)."""
    return f'"{ac.municipio}" convocação {ac.concurso or ac.cargo or "concurso"}'


def achados_google(ac: Acompanhamento, resposta: dict) -> list[Achado]:
    """Resultados orgânicos da Serper que falam de convocação NO município (título ou resumo)."""
    achados: list[Achado] = []
    for item in resposta.get("organic", []) or []:
        link, titulo = item.get("link") or "", item.get("title") or ""
        if not link or any(d in link for d in _DOMINIOS_RUIDO):
            continue
        base = normalizar(f"{titulo} {item.get('snippet') or ''}")
        if not ac.municipio or normalizar(ac.municipio) not in base or not _TITULO_CONVOCACAO.search(base):
            continue
        if not numero_edital_no_texto(ac.concurso, f"{titulo} {item.get('snippet') or ''}") and ac.concurso:
            continue
        chave = "google:" + hashlib.sha1(link.encode()).hexdigest()[:16]
        achados.append(Achado(ac.id, "concurso", chave, "google", ac.uf or "", ac.municipio, titulo, None, link, (item.get("snippet") or titulo)[:420]))
    return achados
