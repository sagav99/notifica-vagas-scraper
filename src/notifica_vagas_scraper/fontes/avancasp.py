"""Parser da Avança SP (avancasp.org.br), banca organizadora regional que
atende prefeituras/autarquias do Estado de São Paulo — achado 2026-09-02
(subagente `triagem-fontes`, a partir de link externo da PCI: notícia real
da Autarquia Municipal de Saúde de Itapecerica da Serra/SP, salários até
R$ 11.576,25) e investigado de ponta a ponta pelo `pesquisador-fonte` na
mesma data. Fixtures reais em `docs/fixtures/avanca_sp/` (repo principal).

Mesma plataforma **ProSeleta / selecao.net.br** ("Desenvolvido por
ProSeleta" no rodapé, PDFs em
`anexos-r2.selecao.net.br/uploads/<tenant_id>/concursos/...`) já vista na
JCM Concursos (`fontes/jcm.py`) e no Instituto ACCESS (`fontes/access.py`)
— por isso `Documento`/`escolher_edital` vêm prontos de
`fontes/proseleta.py`. **Duas partes exigiram parser próprio, não
herdado**:

1. Listagem (`/index/abertos/`): o título do card aqui é
   "Tipo - NNN/AAAA - ÓRGÃO" — diferente da JCM ("Município-UF - Tipo
   NNN/AAAA - Órgão", tudo numa string) e da ACCESS (município/UF em
   elemento HTML separado). Aqui NÃO existe campo de município/UF
   explícito: o nome do município só aparece embutido no nome do órgão,
   às vezes reconhecível ("PREFEITURA MUNICIPAL DE LIMEIRA", "AUTARQUIA
   MUNICIPAL DE SAÚDE - ITAPECERICA DA SERRA"), às vezes não (ex:
   "CONSELHO REGIONAL DE CORRETORES DE IMÓVEIS DO ESTADO DE SÃO PAULO" —
   fora do escopo do produto, sem município nenhum pra mapear).
   `extrair_municipio` só reconhece uma whitelist de prefixos conhecidos
   (prefeitura/câmara/autarquia municipal de saúde); qualquer coisa fora
   disso devolve `None` e é descartada em `listar_processos_abertos`
   (mesmo padrão de `access.extrair_municipio_uf`).

   Todos os exemplos vistos na amostra são de SP (o nome "Avança SP" e o
   domínio `avancasp.org.br` sugerem escopo estadual único, diferente da
   ACCESS/JCM que atendem várias UFs) — por isso `UF` é uma constante
   fixa aqui, não extraída do card. Se um dia aparecer processo de outro
   estado, `ibge.buscar_codigo_ibge(municipio, "SP")` simplesmente não
   acha o município e a vaga é pulada com aviso no script — sem risco de
   dado errado, só de perder cobertura até reinvestigar.

2. Detalhe do processo (`/informacoes/<id>/`): a tabela "Vagas" tem 7
   colunas (Cód., Vaga, Escolaridade, Salário, Carga Horária, Qtde., Taxa
   de Inscrição) **com salário já estruturado em HTML** — diferente da
   JCM/ACCESS (só "Vaga"/"Qtde.", salário só existe no PDF do edital).
   `listar_vagas_html` lê a tabela inteira sem precisar do Gemini pro
   caso comum (Gemini continua reservado pra auditoria/verificação,
   padrão já usado no projeto).

   Salário por hora ("R$ 48,31 por hora", comum em cargo médico — regime
   de plantão) **não é convertido pra valor mensal** — mesma regra já
   usada nos prompts do Gemini (`gemini_pdf.py`/`gemini_texto.py`: "não
   invente um valor mensal a partir de uma taxa horária"). Nesse caso
   `VagaAvancaSp.salario` fica `None` e o texto original vai em
   `salario_texto`, pra não perder a informação (usado no resumo da
   vaga).

**Prioridade #1 do produto (saúde/médicos, ver CLAUDE.md)**: o concurso
266 (Autarquia Municipal de Saúde de Itapecerica da Serra/SP) tem, segundo
a investigação, 24 especialidades médicas diferentes (Cardiologista,
Clínico Geral, Pediatra, Psiquiatra etc.) mais Enfermeiro/Farmacêutico/
Fisioterapeuta/Nutricionista/Psicólogo/Auxiliar-Técnico de Enfermagem. A
fixture salva (`itapecerica_saude_concurso_266_vagas.html`) é só um
TRECHO de 6 das 53 linhas reais da tabela (a íntegra não foi salva) —
escolhido pra cobrir a variação de formato que importa pro parser
(salário mensal fixo vs. por hora, cargo de saúde e não-saúde, sufixo
"+ Cadastro de Reserva" na quantidade, célula "Cód." vazia).
`listar_vagas_html` não tem NENHUMA lógica que filtre ou reconheça cargo
por nome — lê toda `<tr>` de 7 colunas da tabela igual, sem lista de
cargos conhecida nem exceção — então o comportamento genérico coberto
aqui vale pros 53 cargos reais também. Se for necessário provar isso
linha a linha (não só por construção), é preciso pedir ao
`pesquisador-fonte` uma nova rodada salvando a tabela completa.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from bs4 import BeautifulSoup

from ..formatacao import parsear_salario_brl
from .proseleta import Documento, escolher_edital, listar_documentos

__all__ = [
    "ItemListagem",
    "VagaAvancaSp",
    "Documento",
    "BASE_URL",
    "UF",
    "extrair_municipio",
    "listar_processos_abertos",
    "listar_documentos",
    "escolher_edital",
    "listar_vagas_html",
    "identificador_externo",
]

BASE_URL = "https://www.avancasp.org.br"

#: banca atua só em SP na amostra investigada (nome "Avança SP" e domínio
#: já sugerem escopo estadual único) — ver docstring do módulo.
UF = "SP"

#: prefixo do nome do órgão -> rótulo de tipo de órgão. Ordem importa:
#: prefixos mais específicos ("Prefeitura Municipal de") antes dos mais
#: genéricos ("Prefeitura de") não é necessário aqui porque nenhum é
#: sub-string de outro, mas mantém a mesma forma de `access.py` por
#: convenção.
_PREFIXOS_ORGAO: tuple[tuple[str, str], ...] = (
    ("AUTARQUIA MUNICIPAL DE SAÚDE - ", "Autarquia Municipal de Saúde"),
    ("PREFEITURA MUNICIPAL DE ", "Prefeitura Municipal"),
    ("PREFEITURA DE ", "Prefeitura"),
    ("CÂMARA MUNICIPAL DE ", "Câmara Municipal"),
    ("CÂMARA DE ", "Câmara"),
)


@dataclass
class ItemListagem:
    processo_id: int
    url: str
    tipo_processo: str
    numero_edital: str | None
    orgao: str
    municipio: str
    uf: str


@dataclass
class VagaAvancaSp:
    cargo: str
    escolaridade: str | None
    salario: Decimal | None
    salario_texto: str | None
    carga_horaria: str | None
    quantidade: int | None
    cadastro_reserva: bool
    taxa_inscricao: Decimal | None


#: conectivos que ficam em minúsculo em nome de município (exceto na 1ª
#: palavra) — evita gravar "ITAPECERICA DA SERRA" (texto original do card,
#: tudo em maiúsculo) na tabela `municipios`, que é compartilhada com
#: outras fontes que já gravam nome em caixa própria (ex: JCM/ACCESS);
#: `upsert_municipio` faz `nome = excluded.nome` em cada rodada, então
#: gravar em maiúsculo aqui sobrescreveria o nome "bonito" de outra fonte.
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


def extrair_municipio(orgao: str) -> tuple[str, str] | None:
    """Devolve `(tipo_orgao, município)` se o nome do órgão bater com um
    prefixo conhecido de prefeitura/câmara/autarquia municipal de saúde,
    `None` caso contrário (achado real: CRECI/SP, vestibular de instituto
    de ensino etc. não mapeiam pra município nenhum e são descartados —
    mesmo padrão de `access.extrair_municipio_uf`)."""
    orgao_normalizado = orgao.strip()
    for prefixo, tipo_orgao in _PREFIXOS_ORGAO:
        if orgao_normalizado.upper().startswith(prefixo):
            municipio = orgao_normalizado[len(prefixo):].strip()
            if municipio:
                return tipo_orgao, _titularizar_municipio(municipio)
    return None


def listar_processos_abertos(html: str) -> list[ItemListagem]:
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []

    for link in soup.select("h3 > a[href]"):
        match_id = re.match(r"^/informacoes/(\d+)/$", link.get("href", ""))
        if not match_id:
            continue

        partes = link.get_text(strip=True).split(" - ", 2)
        if len(partes) != 3:
            continue
        tipo_processo, numero_edital, orgao = (p.strip() for p in partes)

        separado = extrair_municipio(orgao)
        if separado is None:
            continue
        tipo_orgao, municipio = separado

        itens.append(
            ItemListagem(
                processo_id=int(match_id.group(1)),
                url=f"{BASE_URL}{match_id.group(0)}",
                tipo_processo=tipo_processo,
                numero_edital=numero_edital or None,
                orgao=tipo_orgao,
                municipio=municipio,
                uf=UF,
            )
        )

    return itens


def _texto_celula(celula) -> str:
    """Normaliza espaço em branco de uma `<td>` — o HTML real vem com
    quebra de linha e indentação profunda DENTRO do texto de uma célula
    só (ex: "10\\n... + Cadastro de Reserva\\n..."), então `get_text(strip=True)`
    sozinho não basta (só tira ponta, não colapsa o meio)."""
    return re.sub(r"\s+", " ", celula.get_text()).strip()


def _parsear_quantidade(texto: str) -> tuple[int | None, bool]:
    match = re.match(r"^(\d+)", texto)
    quantidade = int(match.group(1)) if match else None
    cadastro_reserva = "cadastro de reserva" in texto.lower()
    return quantidade, cadastro_reserva


def _parsear_salario_estruturado(texto: str) -> tuple[Decimal | None, str | None]:
    """`None` quando a remuneração é por hora/aula — mesma regra dos
    prompts do Gemini (`gemini_pdf.py`/`gemini_texto.py`): nunca inventar
    um valor mensal a partir de uma taxa horária. O texto original é
    preservado em `salario_texto` (2º item da tupla) pra não perder a
    informação."""
    if not texto:
        return None, None
    if re.search(r"por\s+hora|por\s+aula", texto, re.IGNORECASE):
        return None, texto
    return parsear_salario_brl(texto), texto


def listar_vagas_html(html: str) -> list[VagaAvancaSp]:
    """Lê a tabela "Vagas" (Cód./Vaga/Escolaridade/Salário/Carga
    Horária/Qtde./Taxa de Inscrição) direto do HTML — sem filtrar por
    nome de cargo, sem lista de cargos conhecida (prioridade #1 do
    produto: nenhuma especialidade pode ser descartada silenciosamente,
    ver docstring do módulo)."""
    soup = BeautifulSoup(html, "html.parser")
    vagas: list[VagaAvancaSp] = []

    for tabela in soup.find_all("table"):
        cabecalho = tabela.find("thead")
        if cabecalho is None or "vaga" not in cabecalho.get_text(" ", strip=True).lower():
            continue
        corpo = tabela.find("tbody")
        if corpo is None:
            continue

        for linha in corpo.find_all("tr"):
            celulas = linha.find_all("td")
            if len(celulas) != 7:
                continue
            cargo = _texto_celula(celulas[1])
            if not cargo:
                continue

            escolaridade = _texto_celula(celulas[2]) or None
            salario, salario_texto = _parsear_salario_estruturado(_texto_celula(celulas[3]))
            carga_horaria = _texto_celula(celulas[4]) or None
            quantidade, cadastro_reserva = _parsear_quantidade(_texto_celula(celulas[5]))
            taxa_inscricao = parsear_salario_brl(_texto_celula(celulas[6]))

            vagas.append(
                VagaAvancaSp(
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


def identificador_externo(processo_id: int, vaga: VagaAvancaSp) -> str:
    """Chave de dedup: id do processo (único na Avança SP) + slug do
    cargo. O cargo já inclui a microárea quando existe (ex: "Agente
    Comunitário de Saúde - UBS JACIRA - Microárea 27"), então cargos
    repetidos com detalhamento diferente não colidem."""
    slug_cargo = re.sub(
        r"[^a-z0-9]+",
        "-",
        unicodedata.normalize("NFKD", vaga.cargo).encode("ascii", "ignore").decode("ascii").lower(),
    ).strip("-")
    return f"avancasp-{processo_id}-{slug_cargo}"
