"""Diários oficiais municipais agregadores do Sul (investigação 2026-10-28,
`docs/investigacao_diarios_oficiais_sul_2026-10-28.md` no repo principal).

- **SC** (CIGA/DOMSC): dados abertos em CKAN (`dados.ciga.sc.gov.br`), um dataset
  por mês e um ZIP por bloco de horário; cada ZIP tem JSON `{"autopublicacoes": [...]}`
  com texto completo (HTML do Tika). A busca do site é SPA, não serve.
- **PR** (AMP) e **RS** (FAMURS): mesmo motor SIGPub do DOM/AMM-MG
  (`sigpub_busca`), busca global pelo estado (sem filtrar entidade).

Tudo aqui é determinístico e sem custo de Serper/Gemini: o filtro de candidato
(`eh_candidata`) roda antes de qualquer chamada de IA.
"""

from __future__ import annotations

import io
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

CKAN_BASE = "https://dados.ciga.sc.gov.br/api/3/action"
USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"

#: Slug do diário no SIGPub por UF (`/{slug}/pesquisar`).
SLUG_SIGPUB = {"PR": "amp", "RS": "famurs"}

#: Termos da busca global PR/RS (cada termo = 1+ páginas de 25 cartões).
TERMOS_SIGPUB = ('"processo seletivo" médico', '"concurso público" médico', "edital médico", "médico")

#: Categorias do CIGA que nunca trazem abertura (atas de preço, licitação etc.).
CATEGORIAS_DESCARTADAS = {  # já normalizadas (sem acento, minúsculas), comparar com `normalizar`
    "ata de registro de precos",
    "licitacoes",
    "contratos",
    "leis",
    "decretos",
    "portarias",
}

_ABERTURA = re.compile(
    r"(processo seletivo|concurso p[uú]blico|\bpss\b|edital de abertura|abertura de inscri|inscri[cç][oõ]es|chamamento p[uú]blico)",
    re.I,
)
_EXCLUIR_TITULO = re.compile(
    r"(c\s?o\s?n\s?v\s?o\s?c|homologa|resultado|classifica|gabarito|errata|prorroga|nomea|exonera|"
    r"cancel|suspen|recurso|comiss[aã]o|conselho tutelar|extrato de contrato|dispensa|inexigib|"
    r"credenciamento|\blista\b|rela[cç][aã]o d|ensalamento|locais de prova|isen[cç]|deferid|"
    r"termo de|edital de chamada\b|chamamento\s*n\S*\s*\d+/\d+\s*do\s*(concurso|processo)|"
    r"chamada p[uú]blica suplementar|posse|decreto|portaria|\blei\b|resolu[cç]|s[uú]mula|aditivo|"
    r"plano de contrata|eliminad|candidatos|comunicado)",
    re.I,
)
# Fase posterior à abertura anunciada no CORPO quando o título é genérico ("EDITAL Nº 182/2026"): achado 2026-10-30 (Guaíra, Campo Bom, Erechim).
_FASE_POSTERIOR = re.compile(
    r"(a\s+convoca[cç][aã]o dos candidatos|ser[aá] convocad|fica(m)? convocad|classifica[cç][aã]o final|homologa[cç][aã]o d|resultado (final|preliminar|d[oa])|"
    r"avalia[cç][aã]o dos t[ií]tulos|sorteio p[uú]blico|resultado d[oa] sorteio|convoca[cç][aã]o d[eo]s? candidat|candidatos? (aprovad|classificad|convocad))",
    re.I,
)
_ABERTURA_FORTE = re.compile(r"(edital de abertura|abertura (do|de) (processo|concurso)|per[ií]odo de inscri|inscri[cç][oõ]es (abertas|ser[aã]o|poder[aã]o)|as inscri[cç][oõ]es)", re.I)
_EXCLUIR_TEXTO = re.compile(r"(m[eé]dico[- ]veterin|exame m[eé]dico|laudo m[eé]dico|atestado m[eé]dico|junta m[eé]dica|perito m[eé]dico)", re.I)
_CARGO_MEDICO = re.compile(
    r"\bm[eé]dic[oa]s?\b(?![- ]veterin)|\b(cl[ií]nic[oa] geral|ginecolog|obstetr|pediatr|psiquiatr|ortoped|cardiolog|anestesi)",
    re.I,
)


_CONTEXTO_VAGA = re.compile(r"(r\$|\b\d{1,2}\s?(h\b|horas)|\bvagas?\b|\bcargos?\b|sal[aá]rio|vencimento|\bcbo\b|remunera)", re.I)


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFD", texto)
    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def texto_limpo(html_ou_texto: str) -> str:
    """Texto corrido de um HTML (Tika/SIGPub) — espaços colapsados."""
    if "<" in html_ou_texto:
        html_ou_texto = BeautifulSoup(html_ou_texto, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", html_ou_texto).strip()


def eh_candidata(titulo: str, texto: str, categoria: str | None = None) -> bool:
    """Publicação que parece ABERTURA de seleção com cargo médico.

    Barato e conservador (pode deixar passar ruído, que o Gemini descarta com
    `vagas: []`, mas não pode perder abertura real): precisa de sinal de
    abertura, cargo médico no texto e nenhum marcador de convocação/resultado
    no título. O texto de "exame médico admissional"/"médico veterinário" é
    removido antes de procurar o cargo médico."""
    if categoria and normalizar(categoria) in CATEGORIAS_DESCARTADAS:
        return False
    if _EXCLUIR_TITULO.search(titulo):
        return False
    base = f"{titulo} {texto}"
    if not _ABERTURA.search(base):
        return False
    # Cabeçalho do corpo (sem a navegação do SIGPub): fase posterior sem sinal forte de abertura não é abertura.
    cabeca = re.sub(r"\s+", " ", texto.split("Baixar a edição completa (PDF)")[-1])[:1200]
    if _FASE_POSTERIOR.search(cabeca) and not _ABERTURA_FORTE.search(cabeca):
        return False
    sem_ruido = _EXCLUIR_TEXTO.sub(" ", base)
    for achado in _CARGO_MEDICO.finditer(sem_ruido):
        janela = sem_ruido[max(0, achado.start() - 100) : achado.end() + 140]
        if _CONTEXTO_VAGA.search(janela):
            return True
    return False


# --------------------------------------------------------------------------- SC


@dataclass
class PublicacaoSc:
    codigo: str
    titulo: str
    data: str
    municipio: str
    categoria: str
    link: str
    url: str
    texto: str


def listar_zips_sc(dias: int, *, hoje: date, sessao: requests.Session | None = None) -> list[dict]:
    """Recursos ZIP do CKAN cujo nome traz data dentro dos últimos `dias`.

    Cobre o mês corrente e, se a janela atravessa a virada, o anterior."""
    sessao = sessao or requests.Session()
    inicio = hoje - timedelta(days=dias)
    meses = {(inicio.year, inicio.month), (hoje.year, hoje.month)}
    zips: list[dict] = []
    for ano, mes in sorted(meses):
        resposta = sessao.get(
            f"{CKAN_BASE}/package_show",
            params={"id": f"domsc-publicacoes-de-{mes:02d}-{ano}"},
            headers={"User-Agent": USER_AGENT},
            timeout=60,
        )
        if resposta.status_code == 404:
            continue
        resposta.raise_for_status()
        for recurso in resposta.json()["result"]["resources"]:
            achado = re.search(r"cigadados_publicacoes_(\d{4})_(\d{2})_(\d{2})_", recurso["url"])
            if not achado:
                continue
            dia = date(int(achado[1]), int(achado[2]), int(achado[3]))
            if inicio <= dia <= hoje:
                zips.append({"url": recurso["url"], "dia": dia, "nome": recurso.get("name", "")})
    zips.sort(key=lambda z: z["url"])
    return zips


def ler_zip_sc(conteudo: bytes) -> list[PublicacaoSc]:
    """Publicações de um ZIP do CIGA (JSON `{"autopublicacoes": [...]}`; pode ser `null`)."""
    publicacoes: list[PublicacaoSc] = []
    with zipfile.ZipFile(io.BytesIO(conteudo)) as zf:
        for nome in zf.namelist():
            if not nome.lower().endswith(".json"):
                continue
            dados = json.loads(zf.read(nome).decode("utf-8", errors="replace"))
            for item in (dados or {}).get("autopublicacoes") or []:
                publicacoes.append(
                    PublicacaoSc(
                        codigo=str(item.get("codigo", "")),
                        titulo=item.get("titulo") or "",
                        data=item.get("data") or "",
                        municipio=item.get("municipio") or "",
                        categoria=item.get("categoria") or "",
                        link=item.get("link") or "",
                        url=item.get("url") or "",
                        texto=item.get("texto") or "",
                    )
                )
    return publicacoes


def candidatas_sc(publicacoes: list[PublicacaoSc]) -> list[PublicacaoSc]:
    resultado = []
    for pub in publicacoes:
        if normalizar(pub.categoria) in CATEGORIAS_DESCARTADAS:
            continue  # evita limpar HTML de 68% do volume (atas de preço)
        if eh_candidata(pub.titulo, texto_limpo(pub.texto), pub.categoria):
            resultado.append(pub)
    return resultado


# ---------------------------------------------------------------------- PR / RS


def buscar_sigpub(
    sessao: requests.Session, uf: str, termo: str, inicio: date, fim: date, pagina: int = 1
) -> str:
    """Busca global (sem filtrar entidade) no diário do estado — HTML dos cartões."""
    resposta = sessao.get(
        f"https://www.diariomunicipal.com.br/{SLUG_SIGPUB[uf]}/pesquisar",
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


def total_resultados_sigpub(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    resumo = soup.select_one("p.busca-avancada__resumo")
    achado = re.search(r"de\s+(\d+)", resumo.get_text(" ", strip=True)) if resumo else None
    return int(achado[1]) if achado else 0


def texto_materia_sigpub(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    corpo = soup.select_one(".materia, .materia__conteudo, article, main") or soup
    return texto_limpo(str(corpo))


def nome_municipio_da_entidade(entidade: str) -> str | None:
    """'Prefeitura Municipal de Palmeira' -> 'Palmeira'. Consórcios/fundações/câmaras -> None."""
    achado = re.match(r"^(?:prefeitura|munic[ií]pio)(?: municipal)?(?: de| do| da)?\s+(.+)$", entidade.strip(), re.I)
    return achado[1].strip() if achado else None


def datetime_hoje_brasilia() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/Sao_Paulo"))
