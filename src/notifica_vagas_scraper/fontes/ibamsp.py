"""Parser da banca terceirizada IBAM-SP (`ibamsp-concursos.org.br`) —
investigado pelo `pesquisador-fonte` em 2026-09-06 (ver
`docs/investigacao_fonte_ibamsp_sp_2026-09-06.md` e fixtures reais em
`docs/fixtures/ibamsp_sp/`, ambos no repo principal). Mesma plataforma
**ProSeleta / selecao.net.br** já vista na JCM, ACCESS e Avança SP
(`fontes/jcm.py`, `fontes/access.py`, `fontes/avancasp.py`) — tenant/
domínio próprio, não compartilha listagem com elas, mas
`Documento`/`escolher_edital`/`listar_documentos` de `fontes/proseleta.py`
funcionam sem alteração (mesmo `<li class="pdf"><a data-astv=... href=...>`
byte-a-byte).

**Duas partes exigiram parser próprio**:

1. Listagem (`/index/abertos/`): o card usa `<h3><a>` com o TÍTULO
   completo do processo já pronto ("MOGI MIRIM - CONCURSO PÚBLICO -
   02/2026 - EDITAL 01"), diferente da Avança SP ("Tipo - NNN/AAAA -
   Órgão", sempre 3 partes) — aqui o nome do MUNICÍPIO já vem como
   primeiro segmento cru (sem prefixo "PREFEITURA MUNICIPAL DE"), então
   não dá pra usar whitelist de prefixo de órgão como a Avança SP.
   `listar_processos_abertos` NÃO tenta extrair município aqui —
   devolve o título cru inteiro (`titulo`) e deixa o casamento contra a
   lista de municípios de MG/SP pro chamador, via
   `fgv.encontrar_municipio` (mesmo padrão de `scripts/
   rodar_nosso_rumo.py`, escolhido porque, diferente da Avança SP, não
   existe aqui um prefixo fixo de órgão pra recortar). Caso real que
   confirma a escolha: o card do Vestibular da Faculdade de Ciências
   Médicas da Santa Casa de São Paulo (id 186) não é vaga de concurso
   nenhuma — `fgv.encontrar_municipio` simplesmente não bate nada
   reconhecível como prefeitura/câmara e o item é descartado no script,
   sem lógica especial aqui.

2. Detalhe do processo (`/informacoes/<id>/`): a tabela "Vagas" tem
   **número de colunas variável** — achado real comparando as fixtures:
   Mogi Mirim 01/2026 edital 03 e o processo ativo Mogi Mirim 02/2026
   edital 01 têm 7 colunas (Cód./Vaga/Escolaridade/Salário ou
   Vencimentos/Carga Horária/Qtde./Valor de Inscrição, igual Avança SP),
   mas **Franca 01/2026 "MÉDICOS" tem só 5 colunas** (sem Salário nem
   Carga Horária). Um parser com índice de coluna fixo (`celulas[3]` pra
   salário, como em `avancasp.listar_vagas_html`) teria **descartado
   inteiro o cargo médico de Franca** por não bater `len(celulas) == 7` —
   exatamente o tipo de bug que a prioridade #1 do produto proíbe.
   `listar_vagas_html` por isso monta o índice de cada coluna a partir do
   TEXTO do cabeçalho (`_indices_colunas`), não de uma posição fixa, e
   aceita qualquer contagem de colunas desde que exista uma coluna
   "Vaga" (cargo) — sem lista de cargos conhecida, sem filtro por nome,
   mesma regra da Avança SP.

   O rótulo da coluna de salário também varia entre tenants/editais do
   mesmo site: "Salário (R$)" (Mogi Mirim 01/2026) vs. "Vencimentos (R$)"
   (Mogi Mirim 02/2026) — `_indices_colunas` reconhece os dois.

   Salário por hora (comum em plantão médico) não é convertido pra
   mensal — mesma regra já usada em `avancasp.py`/`gemini_pdf.py`:
   `VagaIbamSp.salario` fica `None` e o texto original vai em
   `salario_texto`.

**Prioridade #1 do produto (saúde/médicos, ver CLAUDE.md)**: as 39 linhas
de Mogi Mirim 01/2026 edital 03 (28 especialidades médicas + 6
odontológicas + Médico Regulador/Neurologista/Proctologista + 2
Enfermeiro) e as 30 linhas de Franca 01/2026 "MÉDICOS" (30 especialidades
médicas, tabela de 5 colunas) são exemplos HISTÓRICOS/encerrados — nenhum
dos 10 processos com inscrição aberta hoje (2026-09-06) tem cargo médico
(ver investigação) — mas servem de prova de que `listar_vagas_html` não
perde nenhuma especialidade em nenhum dos dois formatos de tabela vistos.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from bs4 import BeautifulSoup

from ..formatacao import parsear_salario_brl
from .proseleta import Documento, escolher_edital, listar_documentos

__all__ = [
    "ItemListagem",
    "VagaIbamSp",
    "Documento",
    "BASE_URL",
    "listar_processos_abertos",
    "listar_documentos",
    "escolher_edital",
    "listar_vagas_html",
    "extrair_periodo_inscricoes",
    "identificador_externo",
]

BASE_URL = "https://www.ibamsp-concursos.org.br"

_RE_EDITAL_PREFIXO = re.compile(r"^edital\s*n[ºo°]?\s*", re.IGNORECASE)
_RE_PERIODO_INSCRICAO = re.compile(r"(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})")


@dataclass
class ItemListagem:
    processo_id: int
    url: str
    #: título cru do card, ex: "MOGI MIRIM - CONCURSO PÚBLICO - 02/2026 -
    #: EDITAL 01" — o nome do município (quando existe) é sempre o
    #: primeiro segmento, mas o casamento contra a lista de municípios de
    #: MG/SP é responsabilidade do chamador (`fgv.encontrar_municipio`,
    #: ver docstring do módulo), não deste parser.
    titulo: str
    tipo_processo: str
    numero_edital: str | None


@dataclass
class VagaIbamSp:
    cargo: str
    escolaridade: str | None
    salario: Decimal | None
    salario_texto: str | None
    carga_horaria: str | None
    quantidade: int | None
    cadastro_reserva: bool
    taxa_inscricao: Decimal | None


def _normalizar_texto(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.strip().lower()


def _extrair_numero_edital(texto: str | None) -> str | None:
    """"Edital nº 12/2026" -> "12/2026"; "Edital nº 02/2026 - Edital 01" ->
    "02/2026 - Edital 01" (mantém o sufixo "- Edital NN" quando existe,
    achado real de Mogi Mirim: 3 editais distintos do MESMO concurso
    02/2026 só se diferenciam por esse sufixo, perder ele colidiria os 3
    processos na dedup de `db.inserir_vaga_com_evidencia`, que usa
    `numero_edital` na chave)."""
    if not texto:
        return None
    limpo = _RE_EDITAL_PREFIXO.sub("", texto.strip())
    return limpo or None


def listar_processos_abertos(html: str) -> list[ItemListagem]:
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []

    for card in soup.select("ul.lista > li.item"):
        link = card.select_one("h3 > a[href]")
        if link is None:
            continue
        match_id = re.match(r"^/informacoes/(\d+)/$", link.get("href", ""))
        if not match_id:
            continue

        titulo = re.sub(r"\s+", " ", link.get_text(strip=True))
        if not titulo:
            continue

        tipo_elemento = card.select_one("p.tipo")
        tipo_processo = tipo_elemento.get_text(strip=True) if tipo_elemento else ""

        edital_elemento = card.select_one("p.edital")
        numero_edital = _extrair_numero_edital(
            edital_elemento.get_text(" ", strip=True) if edital_elemento else None
        )

        itens.append(
            ItemListagem(
                processo_id=int(match_id.group(1)),
                url=f"{BASE_URL}{match_id.group(0)}",
                titulo=titulo,
                tipo_processo=tipo_processo,
                numero_edital=numero_edital,
            )
        )

    return itens


def _texto_celula(celula) -> str:
    """Mesma normalização de `avancasp._texto_celula`: o HTML real vem com
    quebra de linha/indentação dentro do texto de uma célula só."""
    return re.sub(r"\s+", " ", celula.get_text()).strip()


def _parsear_quantidade(texto: str) -> tuple[int | None, bool]:
    match = re.match(r"^(\d+)", texto)
    quantidade = int(match.group(1)) if match else None
    cadastro_reserva = "cadastro de reserva" in texto.lower()
    return quantidade, cadastro_reserva


def _parsear_salario_estruturado(texto: str) -> tuple[Decimal | None, str | None]:
    """`None` quando a remuneração é por hora/aula — mesma regra de
    `avancasp._parsear_salario_estruturado`."""
    if not texto:
        return None, None
    if re.search(r"por\s+hora|por\s+aula", texto, re.IGNORECASE):
        return None, texto
    return parsear_salario_brl(texto), texto


def _indices_colunas(cabecalhos: list[str]) -> dict[str, int]:
    """Mapeia nome de coluna -> índice a partir do TEXTO do cabeçalho, não
    de posição fixa — a contagem/ordem de colunas varia entre tenants do
    mesmo site (ver docstring do módulo: Franca tem 5 colunas sem
    Salário/Carga Horária, Mogi Mirim tem 7 com "Salário (R$)" ou
    "Vencimentos (R$)"). Coluna não encontrada simplesmente não entra no
    dict — o chamador trata ausência como `None`, nunca como erro."""
    indices: dict[str, int] = {}
    for posicao, bruto in enumerate(cabecalhos):
        texto = _normalizar_texto(bruto)
        if texto == "vaga":
            indices["cargo"] = posicao
        elif "escolaridade" in texto:
            indices["escolaridade"] = posicao
        elif "salario" in texto or "vencimento" in texto:
            indices["salario"] = posicao
        elif "carga horaria" in texto:
            indices["carga_horaria"] = posicao
        elif "qtde" in texto or "quantidade" in texto:
            indices["quantidade"] = posicao
        elif "inscricao" in texto:
            indices["taxa_inscricao"] = posicao
    return indices


def listar_vagas_html(html: str) -> list[VagaIbamSp]:
    """Lê a tabela "Vagas" direto do HTML — sem filtrar por nome de cargo,
    sem lista de cargos conhecida (prioridade #1 do produto: nenhuma
    especialidade pode ser descartada silenciosamente, ver docstring do
    módulo). Aceita qualquer contagem de colunas, desde que exista uma
    coluna "Vaga" reconhecível no cabeçalho."""
    soup = BeautifulSoup(html, "html.parser")
    vagas: list[VagaIbamSp] = []

    for tabela in soup.find_all("table"):
        cabecalho = tabela.find("thead")
        if cabecalho is None:
            continue
        celulas_cabecalho = [th.get_text(strip=True) for th in cabecalho.find_all("th")]
        indices = _indices_colunas(celulas_cabecalho)
        if "cargo" not in indices:
            continue

        corpo = tabela.find("tbody")
        if corpo is None:
            continue

        for linha in corpo.find_all("tr"):
            celulas = linha.find_all("td")
            if len(celulas) != len(celulas_cabecalho):
                continue

            cargo = _texto_celula(celulas[indices["cargo"]])
            if not cargo:
                continue

            escolaridade = (
                _texto_celula(celulas[indices["escolaridade"]]) or None
                if "escolaridade" in indices
                else None
            )
            if "salario" in indices:
                salario, salario_texto = _parsear_salario_estruturado(
                    _texto_celula(celulas[indices["salario"]])
                )
            else:
                salario, salario_texto = None, None
            carga_horaria = (
                _texto_celula(celulas[indices["carga_horaria"]]) or None
                if "carga_horaria" in indices
                else None
            )
            if "quantidade" in indices:
                quantidade, cadastro_reserva = _parsear_quantidade(
                    _texto_celula(celulas[indices["quantidade"]])
                )
            else:
                quantidade, cadastro_reserva = None, False
            taxa_inscricao = (
                parsear_salario_brl(_texto_celula(celulas[indices["taxa_inscricao"]]))
                if "taxa_inscricao" in indices
                else None
            )

            vagas.append(
                VagaIbamSp(
                    cargo=cargo,
                    escolaridade=escolaridade,
                    salario=salario,
                    salario_texto=salario_texto,
                    carga_horaria=carga_horaria,
                    quantidade=quantidade,
                    cadastro_reserva=cadastro_reserva,
                    taxa_inscricao=taxa_inscricao,
                )
            )

    return vagas


def extrair_periodo_inscricoes(html: str) -> tuple[date | None, date | None]:
    """Lê "Inscrições de DD/MM/AAAA a DD/MM/AAAA" de `<p
    class="periodoInscricoes">` na página de detalhe. `(None, None)` se o
    parágrafo não existir ou o formato não bater — mesmo padrão de
    `nosso_rumo.parsear_periodo_inscricao`."""
    soup = BeautifulSoup(html, "html.parser")
    paragrafo = soup.find("p", class_="periodoInscricoes")
    if paragrafo is None:
        return None, None
    match = _RE_PERIODO_INSCRICAO.search(paragrafo.get_text(" ", strip=True))
    if not match:
        return None, None

    def _parse(data_texto: str) -> date:
        dia, mes, ano = data_texto.split("/")
        return date(int(ano), int(mes), int(dia))

    return _parse(match.group(1)), _parse(match.group(2))


def identificador_externo(processo_id: int, vaga: VagaIbamSp) -> str:
    """Chave de dedup: id do processo (único no IBAM-SP) + slug do cargo —
    mesmo padrão de `avancasp.identificador_externo`."""
    slug_cargo = re.sub(
        r"[^a-z0-9]+",
        "-",
        unicodedata.normalize("NFKD", vaga.cargo).encode("ascii", "ignore").decode("ascii").lower(),
    ).strip("-")
    return f"ibamsp-{processo_id}-{slug_cargo}"
