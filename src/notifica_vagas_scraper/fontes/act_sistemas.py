"""Parser da plataforma ACT Sistemas / "Gestor Editais" (2 tenants: Instituto
Legalle e Instituto Tupy, bancas do Sul, SC/RS/PR principalmente).
Investigação em `docs/investigacao_act_sistemas_sul_2026-10-27.md` (repo
principal), fixtures em `docs/fixtures/legalle_tupy/`.

HTML estático, sem Cloudflare. `/edital/index/abertos` devolve a página
inteira (abas abertos/andamento/futuros/encerrados no mesmo HTML); só a aba
`id="abertos"` interessa. A listagem não traz UF nem cargos: isso vem do
detalhe `/edital/ver/<id>` ("Município - UF", PDFs em "ARQUIVOS
DISPONÍVEIS", cargos em "VAGAS"). Legalle traz "Remuneração:" por cargo;
Tupy não (só no PDF).

Nenhum cargo é descartado aqui: `parse_detalhe` devolve TODOS os cargos; o
filtro de médico é do runner (`classificar_medico.eh_cargo_medico`, que já
exclui Médico Veterinário).
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

TENANTS: dict[str, dict[str, str]] = {
    "legalle": {"base": "https://portal.institutolegalle.org.br", "banca": "Instituto Legalle"},
    "tupy": {"base": "https://portal.institutotupy.com.br", "banca": "Instituto Tupy"},
}


@dataclass(frozen=True)
class ItemListagem:
    edital_id: int
    tipo_numero: str  # ex.: "Concurso Público - 001/2026"
    orgao: str  # ex.: "Município de Rio Negrinho"
    inscricoes_inicio: date | None
    inscricoes_fim: date | None
    url_detalhe: str


@dataclass(frozen=True)
class Cargo:
    nome: str
    grupo: str | None = None  # nível (Tupy) ou turno/tabela (Legalle)
    requisitos: str | None = None
    salario: Decimal | None = None


@dataclass(frozen=True)
class Arquivo:
    rotulo: str
    url: str
    data: date | None = None


@dataclass(frozen=True)
class Detalhe:
    titulo: str
    orgao: str
    municipio: str | None
    uf: str | None
    inscricoes_inicio: date | None
    inscricoes_fim: date | None
    arquivos: list[Arquivo] = field(default_factory=list)
    cargos: list[Cargo] = field(default_factory=list)


def url_listagem(tenant: str) -> str:
    return f"{TENANTS[tenant]['base']}/edital/index/abertos"


def url_detalhe(tenant: str, edital_id: int) -> str:
    return f"{TENANTS[tenant]['base']}/edital/ver/{edital_id}"


def identificador_externo(tenant: str, edital_id: int, cargo: str) -> str:
    return f"act-{tenant}-{edital_id}-{_slug(cargo)}"


def prefixo_edital(tenant: str, edital_id: int) -> str:
    return f"act-{tenant}-{edital_id}-"


def _slug(texto: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFD", texto)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _limpar(fragmento: str) -> str:
    sem_tags = re.sub(r"<style.*?</style>", " ", fragmento, flags=re.S)
    sem_tags = re.sub(r"<[^>]+>", " ", sem_tags)
    return re.sub(r"\s+", " ", _html.unescape(sem_tags)).strip()


def _data(texto: str | None) -> date | None:
    if not texto:
        return None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", texto)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def parse_salario(texto: str | None) -> Decimal | None:
    """'1.835,25' -> Decimal('1835.25'); aceita 'R$' e texto em volta."""
    if not texto:
        return None
    m = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})", texto)
    if not m:
        return None
    return Decimal(m.group(1).replace(".", "").replace(",", "."))


def parse_listagem(html: str, tenant: str) -> list[ItemListagem]:
    """Cartões da aba `abertos` (inscrição aberta)."""
    base = TENANTS[tenant]["base"]
    ini = re.search(r'<div[^>]*class="tab-pane[^"]*"[^>]*id="abertos"', html)
    if not ini:
        ini = re.search(r'<div[^>]*id="abertos"', html)
    if not ini:
        return []
    resto = html[ini.end():]
    fim = re.search(r'<div[^>]*class="tab-pane[^"]*"[^>]*id="(?!abertos)', resto)
    aba = resto[: fim.start()] if fim else resto

    itens: list[ItemListagem] = []
    vistos: set[int] = set()
    for cartao in re.split(r'<div class="card rounded', aba)[1:]:
        m_id = re.search(r"/edital/ver/(\d+)", cartao)
        if not m_id:
            continue
        edital_id = int(m_id.group(1))
        if edital_id in vistos:
            continue
        vistos.add(edital_id)
        m_tipo = re.search(r'<p class="[^"]*text-uppercase[^"]*"[^>]*>(.*?)</p>', cartao, re.S)
        m_orgao = re.search(r'<p class="[^"]*text-18[^"]*"[^>]*>(.*?)</p>', cartao, re.S)
        m_de = re.search(r"<b>De:</b>\s*(\d{2}/\d{2}/\d{4})", cartao)
        m_ate = re.search(r"<b>At[ée]:</b>\s*(\d{2}/\d{2}/\d{4})", cartao)
        itens.append(
            ItemListagem(
                edital_id=edital_id,
                tipo_numero=_limpar(m_tipo.group(1)) if m_tipo else "",
                orgao=_limpar(m_orgao.group(1)) if m_orgao else "",
                inscricoes_inicio=_data(m_de.group(1)) if m_de else None,
                inscricoes_fim=_data(m_ate.group(1)) if m_ate else None,
                url_detalhe=f"{base}/edital/ver/{edital_id}",
            )
        )
    return itens


def _parse_arquivos(html: str) -> list[Arquivo]:
    ini = html.find('id="arquivos"')
    if ini < 0:
        return []
    fim = html.find('id="vagas"', ini)
    bloco = html[ini : fim if fim > 0 else len(html)]
    arquivos: list[Arquivo] = []
    for item in re.split(r'<div class="list-group-item', bloco)[1:]:
        m_url = re.search(r'href="([^"]*arquivo\.php\?file=[^"]+)"', item)
        if not m_url:
            continue
        m_rot = re.search(r'<p class="mb-0 text-12 text-700[^"]*"[^>]*>(.*?)</p>', item, re.S)
        m_data = re.search(r"<small[^>]*>\s*(\d{2}/\d{2}/\d{4})", item)
        arquivos.append(
            Arquivo(
                rotulo=_limpar(m_rot.group(1)) if m_rot else "",
                url=_html.unescape(m_url.group(1)),
                data=_data(m_data.group(1)) if m_data else None,
            )
        )
    return arquivos


def _parse_cargos(html: str) -> list[Cargo]:
    ini = html.find('id="vagas"')
    if ini < 0:
        return []
    fim = html.find('id="extrato-legislacao"', ini)
    bloco = html[ini : fim if fim > 0 else len(html)]
    cargos: list[Cargo] = []
    # cada grupo (nível/turno/tabela) é um <div class="card mb-4">
    for grupo_html in re.split(r'<div class="card mb-4">', bloco)[1:]:
        m_g = re.search(r'<a[^>]*data-toggle="collapse"[^>]*aria-controls="collapse\d+"[^>]*>(.*?)</a>', grupo_html, re.S)
        grupo = _limpar(m_g.group(1)) if m_g else None
        for item in re.split(r'<div class="list-group-item text-dark">', grupo_html)[1:]:
            m_n = re.search(r'<span[^>]*aria-controls="cargo\d*"[^>]*>(.*?)</span>', item, re.S)
            if not m_n:
                continue
            nome = _limpar(m_n.group(1))
            if not nome:
                continue
            requisitos = salario = None
            for rotulo, corpo in re.findall(
                r'<div class="text-14 text-500">\s*([^<]*?)\s*</div>(.*?)</div>', item, re.S
            ):
                if rotulo.startswith("Requisitos"):
                    requisitos = _limpar(corpo) or None
                elif rotulo.startswith("Remunera"):
                    salario = parse_salario(_limpar(corpo))
            cargos.append(Cargo(nome=nome, grupo=grupo, requisitos=requisitos, salario=salario))
    return cargos


def parse_detalhe(html: str) -> Detalhe:
    m_info = re.search(
        r'<p tabindex="10" class="text-500 text-uppercase text-16 m-0"><u[^>]*>(.*?)</u>\s*<br>(.*?)</p>', html, re.S
    )
    titulo = _limpar(m_info.group(1)) if m_info else ""
    orgao = _limpar(m_info.group(2)) if m_info else ""
    m_insc = re.search(
        r"Inscri[çc][õo]es de</b>\s*(\d{2}/\d{2}/\d{4}).*?<b>at[ée]</b>\s*(\d{2}/\d{2}/\d{4})", html, re.S | re.I
    )
    municipio = uf = None
    m_loc = re.search(r'<p tabindex="11" class="m-0">\s*<i[^>]*></i>\s*([^<]+?)\s*-\s*([A-Z]{2})\s*</p>', html)
    if m_loc:
        municipio, uf = _limpar(m_loc.group(1)), m_loc.group(2)
    return Detalhe(
        titulo=titulo,
        orgao=orgao,
        municipio=municipio,
        uf=uf,
        inscricoes_inicio=_data(m_insc.group(1)) if m_insc else None,
        inscricoes_fim=_data(m_insc.group(2)) if m_insc else None,
        arquivos=_parse_arquivos(html),
        cargos=_parse_cargos(html),
    )


_RUIM = ("resultado", "gabarito", "homologa", "convoca", "classifica", "recurso")


def escolher_edital(arquivos: list[Arquivo]) -> Arquivo | None:
    """Edital de abertura/anexos; ignora resultado etc. Sem candidato
    ideal, o primeiro arquivo não-ruim; sem nenhum, None."""
    if not arquivos:
        return None
    for a in arquivos:
        r = a.rotulo.lower()
        if "edital" in r and "retifica" not in r and not any(x in r for x in _RUIM):
            return a
    for a in arquivos:
        if not any(x in a.rotulo.lower() for x in _RUIM):
            return a
    return None


def tipo_oportunidade(titulo: str) -> str | None:
    """'Concurso Público ...' -> concurso_efetivo; processo seletivo /
    habilitação -> processo_seletivo_temporario; senão None (não chuta)."""
    t = titulo.lower()
    if "concurso" in t:
        return "concurso_efetivo"
    if "seletivo" in t or "habilita" in t:
        return "processo_seletivo_temporario"
    return None


def numero_edital(titulo: str) -> str | None:
    m = re.search(r"(\d{1,4}(?:\s*,\s*\d{1,4})*\s*/\s*\d{4})", titulo)
    return re.sub(r"\s+", "", m.group(1)) if m else None
