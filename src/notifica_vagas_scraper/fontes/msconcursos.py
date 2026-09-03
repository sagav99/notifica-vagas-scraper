"""Parser da MSConcursos (msconcursos.com.br), banca organizadora nacional
do Grupo Sarmento que atende clientes espalhados pelo Brasil, incluindo
municípios de MG/SP — achado 2026-09-03 (link real: Prefeitura de Santana
de Parnaíba/SP, Concurso Público para Médico e Médico Plantonista, edital
05/2026, 4 vagas de "Médico Plantonista 24h - Urgência e Emergência",
inscrições até 13/09/2026) e investigado de ponta a ponta pelo
`pesquisador-fonte` na mesma data. Fixtures reais em
`docs/fixtures/msconcursos/` (repo principal, copiadas pra
`tests/fixtures/msconcursos/` neste repo).

Sem bloqueio anti-bot (`www.msconcursos.com.br` só redireciona 308 pro
domínio sem `www`, resto responde 200 direto).

Estrutura:

1. A homepage (`msconcursos.com.br/`) lista TODOS os concursos numa página
   só, sem paginação, dividida em 3 seções por `<section class="secao
   secao-concursos ...">`: `abertas` ("INSCRIÇÕES ABERTAS"), `em-andamento`
   ("CONCURSOS EM ANDAMENTO" — inscrição já fechada, mas processo ainda
   rodando) e `autorizados` ("CONCURSOS REALIZADOS" — histórico). Só a
   seção `abertas` interessa pro cron (mesmo papel do `/index/abertos/` da
   Avança SP/JCM/ACCESS — só concurso com inscrição de verdade aberta gera
   notificação) — `listar_concursos_abertos` lê só essa seção.

   A página de detalhe de um concurso individual (`concurso/<id>/<slug>`)
   repete a MESMA estrutura de card (`div.concurso` com `h3.titulo`,
   `div.datas`, link "Saiba mais") dentro da sua própria seção `abertas`
   quando o concurso está com inscrição aberta — por isso
   `listar_concursos_abertos` funciona igual nas duas páginas, embora o
   cron só precise rodar na homepage.

2. **Não existe campo de município/UF estruturado em lugar nenhum** (nem
   na listagem, nem na página de detalhe do concurso) — o nome do
   município só aparece embutido no título completo do concurso, quando o
   órgão é uma prefeitura: `"... - PREFEITURA (MUNICIPAL )?DE <MUNICÍPIO>,
   <UF> - EDITAL ..."` ou, variação real vista na mesma amostra, com
   hífen em vez de vírgula antes da UF: `"... PREFEITURA MUNICIPAL DE
   SANTANA DE PARNAÍBA-SP - ..."`. `extrair_municipio_uf` reconhece só
   esse padrão ancorado na palavra "PREFEITURA" (não tenta achar município
   em texto livre, nem usa lista curada de nomes — diferente de
   `fgv.encontrar_municipio`, mais simples porque aqui o padrão textual é
   consistente o bastante). Título sem "PREFEITURA" (ex: "CONSÓRCIO
   REGIONAL INTERMUNICIPAL DE SAÚDE (CRIS)" — atende vários municípios ao
   mesmo tempo, não tem 1 município só pra mapear; ou "EDITAL
   003-2026/DP/CBMSC" — Corpo de Bombeiros de SC, nem prefeitura é) não
   bate o padrão e `extrair_municipio_uf` devolve `None` —
   `listar_concursos_abertos` descarta esses itens, mesmo padrão de
   `avancasp.extrair_municipio`/`access.extrair_municipio_uf` (nunca
   "chuta" município a partir de texto ambíguo).

   MSConcursos atende BANCAS de vários estados (Santana de
   Parnaíba/Ibiúna-SP, Itaperuçu-PR, Manhuaçu/Divinópolis/Patrocínio/
   Uberlândia-MG vistos historicamente, ver
   `docs/fixtures/msconcursos/api_busca_concursos_exemplo.json`) — por
   isso, diferente da Avança SP (UF fixa), aqui a UF é extraída
   dinamicamente do próprio título e validada contra a lista de siglas
   reais de UF (`_UFS_BRASIL`), pra não confundir com um número de 2
   dígitos qualquer que apareça perto de uma vírgula/hífen por acaso. O
   filtro MG/SP do produto (ver CLAUDE.md) é aplicado pelo script
   `scripts/rodar_msconcursos.py`, não aqui — mesmo padrão de
   `access.py`/`ibgp.py`.

3. A página de detalhe do concurso (`concurso/<id>/<slug>`) tem
   `<section class="secao secao-cargos-concurso">` com cargo/salário/
   quantidade de vagas/carga horária/taxa de inscrição JÁ estruturados em
   HTML — **não precisa abrir PDF nem chamar Gemini pro caso comum**
   (confirmado no concurso real: "MÉDICO PLANTONISTA 24H - URGÊNCIA E
   EMERGÊNCIA", R$ 14.975,66/mês, 4 vagas, 24h). Cada `div.bloco` agrupa
   cargos por nível de escolaridade (`h5.titulo-categoria`, ex: "NÍVEL
   SUPERIOR"); cada cargo é um `div.item` com o nome do cargo + taxa de
   inscrição no próprio link, e remuneração/quantidade/carga
   horária/etapas num parágrafo dentro do `div.drop` que abre ao clicar.
   `listar_vagas_html` lê TODOS os `div.item` de TODOS os `div.bloco`
   dentro da seção, sem lista de cargos conhecida e sem filtrar por nome
   — os 16 cargos médicos do concurso real (Angiologista, Clínica Médica,
   Colposcopista, Endocrinologista, Endocrinologista Infantil,
   Gastroenterologista, Ginecologista/Obstetra, Hematologista,
   Mastologista, Neuropediatra, Ortopedista, Pediatra, Plantonista 24h,
   Psiquiatra, Psiquiatra da Infância e Adolescência, Ultrassonografista,
   Urologista) estão todos na fixture salva (ela é o HTML completo da
   seção de cargos, não um trecho) e todos aparecem no resultado.

   Cargo com "Quantidade de vagas: 0" (ex: "MÉDICO CLÍNICA MÉDICA" e
   outras 6 especialidades no concurso real) **não é descartado** — mesmo
   precedente de `ibgp.py` (`cargo.total_vagas` 0/None ainda vira linha no
   banco, só muda o texto do resumo). Filtrar por quantidade é decisão de
   negócio arriscada demais pra tomar sem confirmação explícita (poderia
   esconder um cargo médico que passou a ter vaga numa retificação
   futura, sem o scraper perceber a mudança porque nunca gravou a vaga
   original) — fica pra `scripts/rodar_msconcursos.py` decidir o texto do
   resumo, nunca pra este módulo decidir o que existe ou não.

   Remuneração já vem em valor mensal fixo no HTML (sem sufixo "por
   hora"/"por plantão" em nenhum cargo da amostra, mesmo nos plantonistas
   — valores na faixa de R$ 13-15 mil/mês, incompatíveis com taxa
   horária/por plantão) — mas `_parsear_salario_estruturado` mantém a
   mesma checagem defensiva de `avancasp.py` (nunca inventar mensal a
   partir de texto "por hora"/"por plantão"), caso uma fonte futura da
   MSConcursos venha com essa unidade.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..formatacao import parsear_salario_brl

__all__ = [
    "ItemListagem",
    "VagaMSConcursos",
    "BASE_URL",
    "extrair_municipio_uf",
    "extrair_numero_edital",
    "listar_concursos_abertos",
    "listar_vagas_html",
    "identificador_externo",
]

BASE_URL = "https://msconcursos.com.br"

#: siglas reais de UF — usadas só pra validar o que `extrair_municipio_uf`
#: capturou depois de "PREFEITURA (MUNICIPAL )?DE <município>,/-", pra não
#: aceitar por acaso um par de letras que não seja UF de verdade.
_UFS_BRASIL = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
}

#: aceita "PREFEITURA DE X, UF" e "PREFEITURA MUNICIPAL DE X-UF" (achados
#: reais na mesma amostra, ver docstring do módulo). Município é lazy até
#: a primeira vírgula ou hífen seguidos da sigla de UF.
_RE_PREFEITURA = re.compile(
    r"PREFEITURA(?:\s+MUNICIPAL)?\s+DE\s+([^,\-]+?)\s*[,\-]\s*([A-Za-z]{2})\b",
    re.IGNORECASE,
)

#: "EDITAL N.º 05/2026" / "EDITAL Nº 05/2026" / "EDITAL N° 05/2023" /
#: "EDITAL N.º 01-2026" / "EDITAL 003-2026/DP/CBMSC" (sem marcador "Nº"
#: nenhum) — todos achados reais na mesma amostra da homepage.
_RE_NUMERO_EDITAL = re.compile(r"EDITAL[^\d]{0,10}([0-9]{1,4}[./\-][0-9]{2,4})", re.IGNORECASE)

_RE_INSCRICOES = re.compile(r"(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})")

#: conectivos que ficam em minúsculo em nome de município — mesmo padrão
#: de `avancasp._titularizar_municipio` (compartilhado só por convenção,
#: não por import, ver docstring do módulo lá).
_CONECTIVOS_MINUSCULOS = {"de", "da", "do", "das", "dos", "e"}


def _titularizar_municipio(nome: str) -> str:
    palavras = nome.strip().split()
    resultado = []
    for indice, palavra in enumerate(palavras):
        minuscula = palavra.lower()
        if indice > 0 and minuscula in _CONECTIVOS_MINUSCULOS:
            resultado.append(minuscula)
        else:
            resultado.append(minuscula.capitalize())
    return " ".join(resultado)


def _parsear_data(texto: str) -> date | None:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _texto_normalizado(tag) -> str:
    """Colapsa espaço em branco/quebra de linha do texto de uma tag —
    o HTML real vem com indentação profunda dentro do próprio texto."""
    return re.sub(r"\s+", " ", tag.get_text(" ")).strip()


@dataclass
class ItemListagem:
    concurso_id: int
    url: str
    titulo: str
    tipo_processo: str
    numero_edital: str | None
    municipio: str
    uf: str
    inscricoes_inicio: date | None
    inscricoes_fim: date | None


@dataclass
class VagaMSConcursos:
    cargo: str
    escolaridade: str | None
    salario: Decimal | None
    salario_texto: str | None
    carga_horaria: str | None
    quantidade: int | None
    taxa_inscricao: Decimal | None
    etapas: str | None


def extrair_municipio_uf(titulo: str) -> tuple[str, str] | None:
    """Devolve `(município, UF)` se o título citar "PREFEITURA (MUNICIPAL
    )?DE <município>" seguido de UF válida, `None` caso contrário (ex:
    consórcio intermunicipal, corpo de bombeiros estadual, programa de
    ensino do SESI — nenhum mapeia pra 1 único município, ver docstring
    do módulo)."""
    match = _RE_PREFEITURA.search(titulo)
    if not match:
        return None
    municipio_bruto = match.group(1).strip(" .")
    uf = match.group(2).upper()
    if not municipio_bruto or uf not in _UFS_BRASIL:
        return None
    return _titularizar_municipio(municipio_bruto), uf


def extrair_numero_edital(titulo: str) -> str | None:
    match = _RE_NUMERO_EDITAL.search(titulo)
    return match.group(1) if match else None


def _parsear_item_concurso(div) -> ItemListagem | None:
    h3 = div.find("h3", class_="titulo")
    link = div.find("a", class_="saiba-mais", href=True)
    if h3 is None or link is None:
        return None

    match_id = re.search(r"concurso/(\d+)/", link["href"])
    if not match_id:
        return None

    tipo_tag = h3.find("b")
    tipo_processo = tipo_tag.get_text(strip=True) if tipo_tag else ""

    titulo_completo = _texto_normalizado(h3)
    # remove o prefixo "Tipo | " (já capturado em tipo_processo acima) pra
    # sobrar só a parte descritiva do título, onde mora município/edital.
    partes = titulo_completo.split("|", 1)
    titulo_descritivo = partes[1].strip() if len(partes) == 2 else titulo_completo

    separado = extrair_municipio_uf(titulo_descritivo)
    if separado is None:
        return None
    municipio, uf = separado

    inscricoes_inicio = inscricoes_fim = None
    data_tag = div.find("div", class_="data-inscricao")
    if data_tag is not None:
        match_datas = _RE_INSCRICOES.search(_texto_normalizado(data_tag))
        if match_datas:
            inscricoes_inicio = _parsear_data(match_datas.group(1))
            inscricoes_fim = _parsear_data(match_datas.group(2))

    return ItemListagem(
        concurso_id=int(match_id.group(1)),
        url=urljoin(f"{BASE_URL}/", link["href"]),
        titulo=titulo_descritivo,
        tipo_processo=tipo_processo,
        numero_edital=extrair_numero_edital(titulo_descritivo),
        municipio=municipio,
        uf=uf,
        inscricoes_inicio=inscricoes_inicio,
        inscricoes_fim=inscricoes_fim,
    )


def listar_concursos_abertos(html: str) -> list[ItemListagem]:
    """Lê só a seção "INSCRIÇÕES ABERTAS" (`section.secao-concursos.
    abertas`) — a mesma estrutura de card existe nas seções "em-andamento"
    (inscrição já fechada) e "autorizados" (concurso encerrado), mas essas
    não interessam pro cron (nada novo pra notificar). Itens sem município
    reconhecível (ver `extrair_municipio_uf`) são descartados aqui, mesmo
    padrão de `avancasp.listar_processos_abertos`."""
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []

    for secao in soup.select("section.secao-concursos.abertas"):
        for div in secao.select("div.concurso"):
            item = _parsear_item_concurso(div)
            if item is not None:
                itens.append(item)

    return itens


def _campo_paragrafo(linhas: list[str], rotulo: str) -> str | None:
    padrao = re.compile(rf"^{re.escape(rotulo)}\s*:?\s*(.*)$", re.IGNORECASE)
    for linha in linhas:
        match = padrao.match(linha)
        if match:
            return match.group(1).strip() or None
    return None


def _parsear_quantidade(texto: str | None) -> int | None:
    if not texto:
        return None
    match = re.match(r"^(\d+)", texto.strip())
    return int(match.group(1)) if match else None


def _parsear_salario_estruturado(texto: str | None) -> tuple[Decimal | None, str | None]:
    """Mesma regra defensiva de `avancasp._parsear_salario_estruturado`:
    nunca inventar valor mensal a partir de remuneração "por hora"/"por
    plantão" — nenhum cargo da amostra investigada usa essa unidade (ver
    docstring do módulo), mas a checagem fica pronta pra quando aparecer."""
    if not texto:
        return None, None
    if re.search(r"por\s+hora|por\s+plant[ãa]o", texto, re.IGNORECASE):
        return None, texto
    return parsear_salario_brl(texto), texto


def listar_vagas_html(html: str) -> list[VagaMSConcursos]:
    """Lê todo `div.item` de todo `div.bloco` dentro de `section.
    secao-cargos-concurso` — sem lista de cargos conhecida, sem filtrar
    por nome nem por quantidade de vagas (ver docstring do módulo,
    prioridade #1 do produto: nenhuma especialidade pode ser descartada
    silenciosamente)."""
    soup = BeautifulSoup(html, "html.parser")
    vagas: list[VagaMSConcursos] = []

    for secao in soup.select("section.secao-cargos-concurso"):
        for bloco in secao.select("div.bloco"):
            categoria_tag = bloco.find("h5", class_="titulo-categoria")
            escolaridade = categoria_tag.get_text(strip=True) if categoria_tag else None

            for item in bloco.find_all("div", class_="item", recursive=False):
                link = item.find("a", class_="categoria")
                if link is None:
                    continue

                taxa_div = link.find("div", class_="taxa")
                taxa_texto = _texto_normalizado(taxa_div) if taxa_div else ""
                match_taxa = re.search(r"R\$\s*[\d.,]+", taxa_texto)
                taxa_inscricao = parsear_salario_brl(match_taxa.group(0)) if match_taxa else None

                # texto direto da âncora, sem descer no `div.taxa` aninhado
                # (`recursive=False` isola só os nós de texto filhos
                # diretos, que são o nome do cargo).
                cargo = " ".join(
                    " ".join(str(no).split())
                    for no in link.find_all(string=True, recursive=False)
                ).strip()
                if not cargo:
                    continue

                paragrafo = item.find("div", class_="drop")
                linhas: list[str] = []
                if paragrafo is not None:
                    texto_paragrafo = paragrafo.get_text("\n")
                    linhas = [linha.strip() for linha in texto_paragrafo.split("\n") if linha.strip()]

                remuneracao_texto = _campo_paragrafo(linhas, "Remuneração")
                salario, salario_texto = _parsear_salario_estruturado(remuneracao_texto)
                quantidade = _parsear_quantidade(_campo_paragrafo(linhas, "Quantidade de vagas"))
                carga_horaria = _campo_paragrafo(linhas, "Carga Horária")
                etapas = _campo_paragrafo(linhas, "Etapas")

                vagas.append(
                    VagaMSConcursos(
                        cargo=cargo,
                        escolaridade=escolaridade,
                        salario=salario,
                        salario_texto=salario_texto,
                        carga_horaria=carga_horaria,
                        quantidade=quantidade,
                        taxa_inscricao=taxa_inscricao,
                        etapas=etapas,
                    )
                )

    return vagas


def identificador_externo(concurso_id: int, vaga: VagaMSConcursos) -> str:
    """Chave de dedup: id do concurso (único na MSConcursos) + slug do
    cargo — mesmo padrão de `avancasp.identificador_externo`."""
    slug_cargo = re.sub(
        r"[^a-z0-9]+",
        "-",
        unicodedata.normalize("NFKD", vaga.cargo).encode("ascii", "ignore").decode("ascii").lower(),
    ).strip("-")
    return f"msconcursos-{concurso_id}-{slug_cargo}"
