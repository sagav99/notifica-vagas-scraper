"""Parser da FUNDEP (Fundação de Desenvolvimento da Pesquisa/UFMG,
"Gestão de Concursos" — `fundep.selecao.net.br`), banca organizadora
mineira — achado 2026-09-01 (link externo da PCI), investigado de ponta a
ponta pelo `pesquisador-fonte` em 2026-09-02. Fixtures reais em
`docs/fixtures/fundep/` (repo principal).

Mesma plataforma **ProSeleta / selecao.net.br** já vista na JCM
(`fontes/jcm.py`), ACCESS (`fontes/access.py`) e Avança SP
(`fontes/avancasp.py`) — `Documento`/`escolher_edital`/`listar_documentos`
de `fontes/proseleta.py` funcionam sem alteração nenhuma aqui: confirmado
pelo `pesquisador-fonte` direto contra o HTML real do DMAE Uberlândia (6
documentos, "EDITAL CONSOLIDADO DO CONCURSO PÚBLICO Nº 01/2026 do
Departamento Municipal de Água e Esgoto  DMAE Uberlândia" escolhido
corretamente por data, empatado em 10/07/2026 com uma retificação que
aparece DEPOIS na ordem do HTML — mesmo achado de desempate por posição já
documentado em `proseleta.escolher_edital`).

**3 diferenças exigiram código próprio, não herdado:**

1. Listagem (`/index/abertos/`): o card usa `<p class="tipo">` +
   `<a href="/informacoes/<id>/"><p><strong>{título}</strong></p></a>` +
   `<p class="edital">` — layout HTML mais parecido com o da ACCESS (campos
   separados) que com o da JCM (string única), mas o TEXTO do título não
   segue o padrão "Tipo - NNN/AAAA - Órgão" da Avança SP nem "Município-UF
   - Tipo NNN/AAAA - Órgão" da JCM: aqui é um título livre por processo
   ("Concurso Público DMAE - 01/2026", "Concurso Público da Câmara
   Municipal de Passos - 01/2026", "Vestibular de Medicina - EMESCAM
   01/2027") — **acheio real de peso**: o card do DMAE/Uberlândia (o
   achado que motivou reabrir esta fonte) não cita "Uberlândia" em lugar
   nenhum do título, só a sigla do órgão ("DMAE"). `extrair_municipio_de_
   titulo` só reconhece o padrão "Prefeitura (Municipal) de X"/"Câmara
   (Municipal) de X" (cobre o caso da Câmara de Passos); pra autarquias
   como o DMAE, o município fica `None` na listagem e só é resolvido
   depois, no script (`rodar_fundep.py`), tentando sufixos do título do
   edital ESCOLHIDO via `candidatos_municipio_por_sufixo` + validação
   contra o IBGE (função pura aqui, sem rede — a validação em si é
   responsabilidade do script, igual todo o resto do projeto).

   Vestibular (entrada em curso de graduação, ex: "Vestibular para
   provimento de vagas no curso de Medicina - FAME/FUNJOB", "Vestibular de
   Medicina - EMESCAM") é filtrado com segurança pelo campo `tipo`
   estruturado (`<p class="tipo">Vestibular</p>`, rótulo exato, não
   substring de título) — fora do escopo do produto (concurso
   público/processo seletivo de EMPREGO). "Processo Seletivo de
   Especialização Lato Sensu ... Hospital Felício Rocho" (id 68) NÃO é
   filtrado: o rótulo de tipo é só "Processo Seletivo", o mesmo usado por
   seleções de emprego legítimas nesta mesma plataforma (ex: ACS de
   Itapecerica da Serra na Avança SP) — sem uma lista de exclusão
   agressiva por título (proibido, ver CLAUDE.md), deixa passar e a
   revisão automática do Gemini decide.

2. Tabela "Vagas" do detalhe (`/informacoes/<id>/`): só 2 colunas
   (Cargo/Qtde., igual JCM/ACCESS), MAS a célula de quantidade vem como
   texto solto tipo `"10\\n + Cadastro de Reserva"` em vez de dígito puro
   (achado real, confirmado nas 28 linhas reais do DMAE/Uberlândia) — o
   `quantidade_texto.isdigit()` estrito de `proseleta.listar_vagas_html`
   devolveria 0 vagas aqui. `listar_vagas_html` desta fonte reaproveita a
   busca pelo container "Vagas" (mesma lógica de mais de uma `<table>` no
   mesmo bloco, achado original da JCM), mas parseia a quantidade com
   `_parsear_quantidade` (regex `r"\\s*(\\d+)"` pra pegar só o número
   inicial, mais detecção do sufixo "+ Cadastro de Reserva" — mesmo padrão
   de `avancasp.VagaAvancaSp.cadastro_reserva`). Sem lista de cargos
   conhecida, sem filtro por nome — nenhuma especialidade é descartada.

3. Salário só existe no PDF do edital (achado confirmado no trecho real do
   PDF consolidado do DMAE/Uberlândia: tabela "Cargo / Requisitos / Vagas /
   Jornada / Remuneração Inicial" — texto pesquisável, não escaneado),
   diferente da Avança SP (salário estruturado em HTML). Precisa do mesmo
   fluxo de `gemini_pdf.extrair_vagas_de_pdf` já usado por JCM/ACCESS — ver
   `scripts/rodar_fundep.py`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .proseleta import Documento, escolher_edital, listar_documentos

__all__ = [
    "ItemListagem",
    "VagaFundep",
    "Documento",
    "BASE_URL",
    "listar_processos_abertos",
    "extrair_municipio_de_titulo",
    "candidatos_municipio_por_sufixo",
    "listar_documentos",
    "escolher_edital",
    "listar_vagas_html",
    "identificador_externo",
]

BASE_URL = "https://fundep.selecao.net.br"

#: rótulo exato de `<p class="tipo">` (não substring de título) pra
#: processo que é vestibular/entrada em curso de graduação — fora do
#: escopo do produto (concurso público/processo seletivo de EMPREGO). Só
#: "vestibular" entra aqui de propósito — ver docstring do módulo pro
#: motivo de "Processo Seletivo" (mesmo quando o TÍTULO sugere algo
#: acadêmico) não ser filtrado.
_TIPOS_FORA_DE_ESCOPO = {"vestibular"}

#: prefixo de título de órgão -> reconhecido com segurança como
#: prefeitura/câmara municipal (mesmo padrão de `access.py`/`avancasp.py`),
#: mas aplicado como BUSCA dentro do título inteiro (não só prefixo),
#: porque o título da FUNDEP é uma frase livre ("Concurso Público da
#: Câmara Municipal de Passos - 01/2026"), não "Tipo - Órgão" limpo.
_PREFIXOS_ORGAO: tuple[str, ...] = (
    "Prefeitura Municipal de ",
    "Prefeitura de ",
    "Câmara Municipal de ",
    "Câmara de ",
)


@dataclass
class ItemListagem:
    processo_id: int
    url: str
    tipo_processo: str
    numero_edital: str | None
    titulo: str
    #: `None` quando o título não cita município reconhecível (ex: sigla
    #: de autarquia sem o nome da cidade, caso real do DMAE/Uberlândia) —
    #: resolvido depois em `rodar_fundep.py` via `candidatos_municipio_
    #: por_sufixo` do título do edital escolhido, não aqui.
    municipio: str | None


@dataclass
class VagaFundep:
    cargo: str
    quantidade: int | None
    cadastro_reserva: bool


def extrair_municipio_de_titulo(titulo: str) -> str | None:
    """Devolve o nome do município se o título citar "Prefeitura (Municipal)
    de X"/"Câmara (Municipal) de X" em algum ponto da frase, `None` caso
    contrário (autarquia/sigla sem nome de cidade no título, vestibular de
    faculdade privada etc. — não adivinha, mesmo padrão de
    `access.extrair_municipio_uf`/`avancasp.extrair_municipio`)."""
    for prefixo in _PREFIXOS_ORGAO:
        indice = titulo.find(prefixo)
        if indice == -1:
            continue
        resto = titulo[indice + len(prefixo):]
        # corta no primeiro separador de edital ("... - 01/2026") ou dígito
        # solto, pra não incluir número de edital no nome do município.
        municipio = re.split(r"\s+-\s+|\s+\d", resto, maxsplit=1)[0].strip()
        if municipio:
            return municipio
    return None


def candidatos_municipio_por_sufixo(texto: str, max_palavras: int = 6) -> list[str]:
    """Gera candidatos a nome de município a partir do SUFIXO de `texto`
    (pensado pro título do edital ESCOLHIDO, ver `escolher_edital`), do
    mais longo pro mais curto — achado real: convenção comum de autarquia
    municipal mineira é "ÓRGÃO POR EXTENSO — SIGLA MUNICÍPIO" (ex:
    "EDITAL CONSOLIDADO DO CONCURSO PÚBLICO Nº 01/2026 do Departamento
    Municipal de Água e Esgoto  DMAE Uberlândia" — o sufixo de 1 palavra
    "Uberlândia" é o município real, mas não dá pra saber de antemão
    quantas palavras o nome do órgão/sigla ocupam antes dele).

    Função PURA (sem rede, sem IBGE) — só gera candidatos; quem chama
    decide a UF e valida contra o IBGE de verdade (ver
    `scripts/rodar_fundep.py`), porque `buscar_codigo_ibge` faz chamada de
    rede e não pode entrar num parser testado só com fixture."""
    palavras = texto.split()
    tamanho_maximo = min(max_palavras, len(palavras))
    return [" ".join(palavras[-tamanho:]) for tamanho in range(tamanho_maximo, 0, -1)]


def listar_processos_abertos(html: str) -> list[ItemListagem]:
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []

    for container in soup.select("td.col-2 div.dados"):
        link = container.find("a", href=True)
        if link is None:
            continue
        match_id = re.match(r"^/informacoes/(\d+)/$", link.get("href", ""))
        if not match_id:
            continue

        tipo_tag = container.find("p", class_="tipo")
        tipo_processo = tipo_tag.get_text(strip=True) if tipo_tag else ""
        if tipo_processo.strip().lower() in _TIPOS_FORA_DE_ESCOPO:
            continue

        titulo = link.get_text(" ", strip=True)

        edital_tag = container.find("p", class_="edital")
        numero_edital = None
        if edital_tag is not None:
            texto_edital = edital_tag.get_text(" ", strip=True)
            numero_edital = re.sub(r"^Edital\s*n[ºo°]?\s*", "", texto_edital, flags=re.IGNORECASE).strip() or None

        itens.append(
            ItemListagem(
                processo_id=int(match_id.group(1)),
                url=f"{BASE_URL}{match_id.group(0)}",
                tipo_processo=tipo_processo,
                numero_edital=numero_edital,
                titulo=titulo,
                municipio=extrair_municipio_de_titulo(titulo),
            )
        )

    return itens


def _parsear_quantidade(texto: str) -> tuple[int | None, bool]:
    """A célula de quantidade vem como `"10\\n + Cadastro de Reserva"` —
    texto solto com quebra de linha, não dígito puro — achado real
    confirmado nas 28 linhas reais do DMAE/Uberlândia. `quantidade_texto
    .isdigit()` estrito (usado em `proseleta.listar_vagas_html`, que serve
    JCM/ACCESS) devolveria 0 vagas nesse formato; por isso a FUNDEP usa
    esta função própria com regex em vez do helper compartilhado."""
    match = re.match(r"\s*(\d+)", texto)
    quantidade = int(match.group(1)) if match else None
    cadastro_reserva = "cadastro de reserva" in texto.lower()
    return quantidade, cadastro_reserva


def listar_vagas_html(html: str) -> list[VagaFundep]:
    """Lê a(s) tabela(s) "Vagas" (Cargo/Qtde., igual JCM/ACCESS) do
    container "Vagas" — mesmo achado da JCM de que os cargos podem vir
    paginados em mais de uma `<table>` dentro do mesmo bloco (confirmado
    aqui também: 28 cargos reais do DMAE/Uberlândia vêm em 2 tabelas).
    Sem lista de cargos conhecida, sem filtro por nome — nenhuma
    especialidade (médica ou não) é descartada silenciosamente."""
    soup = BeautifulSoup(html, "html.parser")
    secao = soup.find("h3", string=re.compile(r"^\s*Vagas\s*$"))
    if secao is None:
        return []
    container = secao.find_parent("div")
    if container is None:
        return []

    vagas: list[VagaFundep] = []
    for tabela in container.find_all("table"):
        for linha in tabela.find_all("tr"):
            celulas = linha.find_all("td")
            if len(celulas) != 2:
                continue
            cargo = celulas[0].get_text(strip=True)
            if not cargo:
                continue
            quantidade, cadastro_reserva = _parsear_quantidade(celulas[1].get_text(strip=True))
            vagas.append(VagaFundep(cargo=cargo, quantidade=quantidade, cadastro_reserva=cadastro_reserva))
    return vagas


def identificador_externo(processo_id: int, vaga: VagaFundep) -> str:
    """Chave de dedup: id do processo (único na FUNDEP) + slug do cargo. O
    cargo inclui o código numérico do quadro de vagas (ex: "101 - Auxiliar
    Técnico Operacional"), então nunca colide mesmo entre cargos com nome
    parecido."""
    slug_cargo = re.sub(
        r"[^a-z0-9]+",
        "-",
        unicodedata.normalize("NFKD", vaga.cargo).encode("ascii", "ignore").decode("ascii").lower(),
    ).strip("-")
    return f"fundep-{processo_id}-{slug_cargo}"
