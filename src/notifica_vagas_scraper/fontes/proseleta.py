"""Funções compartilhadas entre bancas que rodam na plataforma ProSeleta
(selecao.net.br) — SaaS multi-tenant de "Gestão de Processos Seletivos
Online" ("Desenvolvido por ProSeleta" no rodapé de cada site-cliente).

Confirmado em 2 tenants investigados de ponta a ponta (2026-09-01):
JCM Concursos (`concursosjcm.com.br`, ver `fontes/jcm.py`) e ACCESS
(`concursos.access.org.br`, ver `fontes/access.py`) — a página de detalhe
do processo (`/informacoes/<id>/`) é byte-a-byte idêntica em estrutura
entre os dois (mesmas classes CSS, mesmo `data-astv`, mesma tabela
"Vaga/Qtde"), só o subdomínio de upload dos PDFs muda
(`anexos-r2.selecao.net.br/uploads/<tenant_id>/concursos/...`). Só a
página de LISTAGEM (`/index/abertos/`) varia o layout do card entre
tenants (JCM embute tipo+número no `<h3>`, ACCESS usa `<p>` separados)
— por isso cada banca tem seu próprio `listar_processos_abertos`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup


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
    real na JCM: 1ª publicação de um processo real veio antes de leis
    municipais com a mesma data, mas a retificação mais recente veio
    depois) — por isso compara datas explicitamente em vez de pegar
    1ª/última posição. Documento sem data (`None`) fica por último no
    critério de desempate.

    Exclui título com "convocação" mesmo contendo "edital" — achado real
    na ACCESS (Contagem/MG): um concurso teve 5 "EDITAL DE CONVOCAÇÃO
    PARA ..." (verificação de indígena/quilombola/PcD, avaliação de
    títulos) publicados na MESMA data do
    "EDITAL DE ABERTURA CONSOLIDADO" de verdade — sem esse filtro,
    `max()` desempatava pela 1ª ordem de aparição e escolhia uma
    convocação (sem cargo/salário nenhum) em vez do edital com as ~40
    especialidades médicas do cargo."""
    candidatos = [d for d in documentos if "edital" in d.titulo.lower()]
    se_nao_for_convocacao = [d for d in candidatos if "convocação" not in d.titulo.lower() and "convocacao" not in d.titulo.lower()]
    if se_nao_for_convocacao:
        candidatos = se_nao_for_convocacao
    if not candidatos:
        return documentos[0] if documentos else None
    return max(candidatos, key=lambda d: d.data or date.min)


def listar_vagas_html(html: str) -> list[VagaHtml]:
    """A seção pagina os cargos em mais de uma `<table>` dentro do mesmo
    container quando há muitos (achado real na JCM: 19 cargos vieram em 2
    tabelas de "Vaga/Qtde", não numa só) — por isso pega todas as tabelas
    do container, não só a 1ª depois do `<h3>Vagas</h3>`."""
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


# ---------------------------------------------------------------------------
# Tenants do Sul (FURB, Objetiva, Univali) — investigação 2026-10-27, ver
# docs/investigacao_proseleta_sul_2026-10-27.md no repo principal. Tudo
# abaixo é novo e NÃO altera o comportamento usado por JCM/ACCESS acima.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tenant:
    chave: str
    nome_fonte: str
    base_url: str
    tenant_id: int
    formato_titulo: str  # "objetiva" | "furb" | "univali"
    uf_padrao: str | None
    banca: str


TENANTS: dict[str, Tenant] = {
    "furb": Tenant("furb", "FURB Concursos", "https://concursos.furb.br", 342, "furb", "SC", "FURB"),
    "objetiva": Tenant(
        "objetiva", "Objetiva Concursos", "https://concursos.objetivas.com.br", 361, "objetiva", None, "Objetiva Concursos"
    ),
    "univali": Tenant("univali", "Univali Seleção", "https://univali.selecao.net.br", 730, "univali", "SC", "Univali"),
}

#: listagens a ler por tenant: "abertos" + "em andamento" (esta última traz
#: também os que abrem em breve, com datas futuras e sem selo).
CAMINHOS_LISTAGEM = ("/index/abertos/", "/index/1/")


@dataclass
class ItemListagemTenant:
    tenant: str
    processo_id: int
    url: str
    titulo: str
    tipo: str | None
    numero_edital: str | None
    inscricoes_inicio: date | None
    inscricoes_fim: date | None
    qtde_total: int | None
    municipio: str | None
    uf: str | None
    orgao: str | None
    em_abertos: bool = False


@dataclass
class VagaTabela:
    codigo: str | None
    cargo: str
    escolaridade: str | None
    salario: Decimal | None
    salario_texto: str | None
    carga_horaria: str | None
    quantidade: int | None
    cadastro_reserva: bool
    taxa_inscricao: Decimal | None


def decodificar(conteudo: bytes) -> str:
    """Páginas dos tenants vêm em utf-8 (home Univali) ou ISO-8859-1
    (demais) — tenta utf-8 e cai para latin-1."""
    try:
        return conteudo.decode("utf-8")
    except UnicodeDecodeError:
        return conteudo.decode("latin-1")


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _minusculo_sem_acento(texto: str) -> str:
    return _sem_acento(texto).lower()


_PARTICULAS = {"de", "da", "do", "das", "dos", "e"}


def _title_case_municipio(nome: str) -> str:
    palavras = nome.strip().lower().split()
    return " ".join(p if (i > 0 and p in _PARTICULAS) else p[:1].upper() + p[1:] for i, p in enumerate(palavras))


def _extrair_numero_edital_titulo(titulo: str) -> str | None:
    match = re.search(r"(\d+(?:-[A-Za-z])?/\d{4})", titulo)
    return match.group(1) if match else None


def extrair_municipio_uf_orgao(titulo: str, tenant: Tenant) -> tuple[str | None, str | None, str | None]:
    """`(municipio, uf, orgao)` a partir do título livre; qualquer parte
    pode vir `None` quando o título não permite determinar."""
    titulo = re.sub(r"\s+", " ", titulo).strip()

    if tenant.formato_titulo == "objetiva":
        match = re.match(r"^(.+?)/([A-Za-z]{2})\s+-\s+(.*?)(?:\s+-\s+|$)", titulo)
        if not match:
            return None, None, None
        bruto, uf, segmento = match.group(1), match.group(2).upper(), match.group(3)
        # "EPTC - PORTO ALEGRE/RS": o município é o último trecho
        municipio = _title_case_municipio(bruto.split(" - ")[-1])
        seg = _minusculo_sem_acento(segmento)
        if seg.startswith("camara"):
            orgao = f"Câmara Municipal de {municipio}"
        elif seg.startswith("municipio") or seg.startswith("prefeitura"):
            orgao = f"Prefeitura Municipal de {municipio}"
        else:
            orgao = f"{segmento} - {municipio}" if " - " in bruto else segmento
        return municipio, uf, orgao

    if tenant.formato_titulo == "univali":
        match = re.search(r"Munic[ií]pio de (.+?)\s*$", titulo)
        if not match:
            return None, tenant.uf_padrao, None
        municipio = match.group(1).strip()
        return municipio, tenant.uf_padrao, f"Prefeitura Municipal de {municipio}"

    # furb: "<Órgão> - <Tipo> - Edital n.º NNN/AAAA"
    orgao = titulo.split(" - ")[0].strip()
    match = re.match(r"^(?:Prefeitura Municipal|C[âa]mara Municipal|Munic[ií]pio) de (.+)$", orgao)
    if match:
        municipio: str | None = match.group(1).strip()
    else:
        match = re.match(
            r"^(?:Prefeitura|C[âa]mara|Funda[çc][ãa]o|Instituto|Servi[çc]o|Autarquia|Cons[óo]rcio)\b.* de (\S.*)$", orgao
        )
        municipio = match.group(1).strip() if match else None
    return municipio, tenant.uf_padrao, orgao


def listar_processos_abertos(html: str, tenant: Tenant | str, *, em_abertos: bool = False) -> list[ItemListagemTenant]:
    """Lê uma página de listagem (`/index/abertos/` ou `/index/1/`) de um
    tenant do Sul. Cada `<tr>` tem `h3 > a[href=/informacoes/<id>/]`."""
    if isinstance(tenant, str):
        tenant = TENANTS[tenant]
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagemTenant] = []
    for link in soup.select('h3 > a[href^="/informacoes/"]'):
        match_id = re.match(r"^/informacoes/(\d+)/?$", link.get("href", ""))
        if not match_id:
            continue
        linha = link.find_parent("tr")
        titulo = re.sub(r"\s+", " ", link.get_text(" ", strip=True))

        tipo_el = linha.select_one("p.tipo") if linha else None
        edital_el = linha.select_one("p.edital") if linha else None
        numero = None
        if edital_el is not None:
            span = edital_el.find("span")
            if span is not None:
                span.extract()
            numero = edital_el.get_text(" ", strip=True) or None
        if not numero:
            numero = _extrair_numero_edital_titulo(titulo)

        datas = [_parsear_data(b.get_text()) for b in linha.select("p.inscricoes-info-mobile-only b")] if linha else []
        inicio = datas[0] if len(datas) >= 1 else None
        fim = datas[1] if len(datas) >= 2 else None

        qtde_el = linha.select_one(".vagas a.qtde b") if linha else None
        qtde_txt = qtde_el.get_text(strip=True) if qtde_el else ""

        municipio, uf, orgao = extrair_municipio_uf_orgao(titulo, tenant)
        itens.append(
            ItemListagemTenant(
                tenant=tenant.chave,
                processo_id=int(match_id.group(1)),
                url=f"{tenant.base_url}/informacoes/{match_id.group(1)}/",
                titulo=titulo,
                tipo=tipo_el.get_text(strip=True) if tipo_el else None,
                numero_edital=numero,
                inscricoes_inicio=inicio,
                inscricoes_fim=fim,
                qtde_total=int(qtde_txt) if qtde_txt.isdigit() else None,
                municipio=municipio,
                uf=uf,
                orgao=orgao,
                em_abertos=em_abertos,
            )
        )
    return itens


def mesclar_listagens(*listas: list[ItemListagemTenant]) -> list[ItemListagemTenant]:
    """Dedup por `processo_id` (mantém a 1ª ocorrência; `em_abertos` vira
    True se qualquer listagem o marcou)."""
    vistos: dict[int, ItemListagemTenant] = {}
    for lista in listas:
        for item in lista:
            existente = vistos.get(item.processo_id)
            if existente is None:
                vistos[item.processo_id] = item
            elif item.em_abertos:
                existente.em_abertos = True
    return list(vistos.values())


def _parsear_moeda(texto: str | None) -> Decimal | None:
    if not texto:
        return None
    match = re.search(r"R\$\s*([\d.]+,\d{2})", texto)
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def _coluna(nome_cabecalho: str) -> str | None:
    n = _minusculo_sem_acento(nome_cabecalho).replace("*", "").strip()
    if n.startswith("cod"):
        return "codigo"
    if n == "vaga" or n == "cargo":
        return "cargo"
    if n.startswith("escolaridade"):
        return "escolaridade"
    if n.startswith(("salario", "vencimento", "bolsa", "remuneracao")):
        return "salario"
    if n.startswith("carga"):
        return "carga"
    if n.startswith("qtde") or n.startswith("quant"):
        return "qtde"
    if n.startswith("taxa"):
        return "taxa"
    return None


def listar_vagas_tabela(html: str) -> list[VagaTabela]:
    """Tabela de vagas dos tenants do Sul: colunas mapeadas pelo TEXTO do
    cabeçalho (variam por edital), todas as tabelas do bloco "Vagas".
    Nenhuma linha com cargo é descartada."""
    soup = BeautifulSoup(html, "html.parser")
    secao = next((h for h in soup.find_all("h3") if h.get_text(strip=True) == "Vagas"), None)
    if secao is None:
        return []
    container = secao.find_parent("div")
    if container is None:
        return []

    vagas: list[VagaTabela] = []
    for tabela in container.find_all("table"):
        cabecalho = [th.get_text(" ", strip=True) for th in tabela.find_all("th")]
        mapa = {i: _coluna(c) for i, c in enumerate(cabecalho)}
        if "cargo" not in mapa.values():
            continue
        for linha in tabela.find_all("tr"):
            celulas = linha.find_all("td")
            if not celulas:
                continue
            campos: dict[str, str] = {}
            for i, td in enumerate(celulas):
                chave = mapa.get(i)
                if chave:
                    campos[chave] = re.sub(r"\s+", " ", td.get_text(" ", strip=True))
            cargo = campos.get("cargo", "").strip()
            if not cargo:
                continue
            qtde_txt = campos.get("qtde", "")
            digitos = re.match(r"^(\d+)", qtde_txt)
            salario_txt = campos.get("salario") or None
            vagas.append(
                VagaTabela(
                    codigo=campos.get("codigo") or None,
                    cargo=cargo,
                    escolaridade=campos.get("escolaridade") or None,
                    salario=_parsear_moeda(salario_txt),
                    salario_texto=salario_txt,
                    carga_horaria=campos.get("carga") or None,
                    quantidade=int(digitos.group(1)) if digitos else None,
                    cadastro_reserva="reserva" in _minusculo_sem_acento(qtde_txt),
                    taxa_inscricao=_parsear_moeda(campos.get("taxa")),
                )
            )
    return vagas


_EXCLUIR_TITULO = ("errata", "retifica", "impugna", "resposta", "isencao", "resultado", "convocacao")
_EXCLUIR_TITULO_FRACO = ("impugna", "resposta", "isencao", "resultado", "convocacao")


def escolher_edital_sul(documentos: list[Documento]) -> Documento | None:
    """Escolhe o edital de abertura nos tenants do Sul. Ordem de preferência
    (desempate sempre pelo MAIS ANTIGO, que é o edital original):
    1. título começando com "Edital n"/"Edital de Abertura" sem errata/
       retificação/impugnação/resposta/isenção/resultado/convocação;
    2. idem, mas tolerando "errata/retificação" no título (edital
       consolidado, ex: "Edital n.º 010/2026 ... COM ERRATA");
    3. qualquer título com "edital" sem os termos fracos;
    4. `escolher_edital` genérico."""

    def norm(d: Documento) -> str:
        return _minusculo_sem_acento(d.titulo).strip()

    def comeca_edital(t: str) -> bool:
        return bool(re.match(r"^edital\s*(n|de abertura)", t))

    def mais_antigo(docs: list[Documento]) -> Documento:
        return min(docs, key=lambda d: d.data or date.max)

    t1 = [d for d in documentos if comeca_edital(norm(d)) and not any(x in norm(d) for x in _EXCLUIR_TITULO)]
    if t1:
        return mais_antigo(t1)
    t2 = [d for d in documentos if comeca_edital(norm(d)) and not any(x in norm(d) for x in _EXCLUIR_TITULO_FRACO)]
    if t2:
        return mais_antigo(t2)
    t3 = [d for d in documentos if "edital" in norm(d) and not any(x in norm(d) for x in _EXCLUIR_TITULO)]
    if t3:
        return mais_antigo(t3)
    return escolher_edital(documentos)
