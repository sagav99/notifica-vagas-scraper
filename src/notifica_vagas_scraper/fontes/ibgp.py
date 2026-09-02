"""Parser da IBGP (Instituto Brasileiro de Gestão Pública,
`ibgpconcursos.com.br`), banca organizadora que atende prefeituras/
câmaras/autarquias de MG (achada via link externo da PCI, ver
TAREFAS.md — última de 3 bancas do mesmo lote; FUNDEP e Avança SP já
resolvidas antes dela). Investigada de ponta a ponta pelo
`pesquisador-fonte` em 2026-09-02 **via navegador só pra descobrir a
API** (o site é SPA client-side puro — `www.ibgpconcursos.com.br` só
redireciona via `window.location.href` pra `novo.ibgpconcursos.com.br`,
HTML raso nos dois domínios, `curl`/`requests` puro não alcança conteúdo
nenhum na página). Uma vez descoberta a API, a COLETA em si é 100%
`requests`/`curl` puro contra `novo.ibgpconcursos.com.br/rest/...`,
JSON aberto, sem autenticação. Fixtures reais em `docs/fixtures/ibgp/`
(repo principal).

Estrutura investigada:
- GET `/rest/concurso/inscricaoAberta` — lista de concursos com
  inscrição aberta agora (15 itens na amostra real). Cada item tem `id`,
  `nome` (título livre do concurso, ex: "CONCURSO PÚBLICO DO MUNICÍPIO
  DE PARACATU/MG - EDITAL Nº 02/2026"), `empresa.nome` (nome do órgão,
  geralmente mais limpo pra extrair município, ex: "MUNICÍPIO DE
  PARACATU/MG"), `edital` (número curto), `tipo.nome`, `totalVagas`,
  `totalCargos`, datas formatadas de início/fim de inscrição
  (`"DD/MM/AA HH:mm"`).
- GET `/rest/concurso/proximasInscricoes` — mesmo formato, concursos "em
  breve" (inscrição ainda não abriu, 23 itens na amostra). **Decisão de
  projeto, não investigação pendente**: não processado por
  `rodar_ibgp.py`. Sem inscrição aberta não existe "vaga pra se
  candidatar" de verdade ainda — datas/vagas podem mudar até lá. Quando
  a inscrição de fato abrir, o mesmo concurso passa a aparecer em
  `inscricaoAberta` e é pego normalmente no próximo ciclo do cron
  (cadência de 3 dias, ver CLAUDE.md) — sem risco de perder cobertura,
  só uma pequena defasagem entre abertura real e detecção.
- GET `/rest/concurso/cargos/{id}` — objeto com campo `cargos` (array),
  **fonte de verdade estruturada** pra nome/código/total de vagas de
  cada cargo (69 cargos reais em Paracatu/MG na fixture, incluindo 17
  especialidades médicas — nenhum campo de salário aqui).
- GET `/rest/concurso/editais/{id}` — array de documentos (`nome`,
  `nomeReal`, `dataCriacaoFormatada`). O documento com "ANEXO I" +
  "VENCIMENTO" no `nome` tem a tabela cargo/vagas/salário (achado real:
  pode vir com sufixo de retificação, ex: "ANEXO I - CARGO,
  ESCOLARIDADE... E VENCIMENTO INICIAL - RETIFICAÇÃO Nº 01" —
  `escolher_edital_vencimento` compara por data quando há mais de 1
  candidato, mesmo padrão de `proseleta.escolher_edital`).
- Download do PDF via `/rest/concurso/download/edital/{editalId}/?file=
  site/anexos/{concursoId}/{nomeArquivo}` — **achado real confirmado na
  fixture**: o campo `nomeReal` do documento JÁ inclui a extensão
  `.pdf` (ex: `"01 - ANEXO I - CARGOS ESC. JORNADAS VAGAS E VENCIMENTOS
  - RETIFICAÇÃO Nº 01.pdf"`) — `montar_url_download` usa `nomeReal`
  verbatim no `file=`, sem acrescentar outra `.pdf` (evita URL com
  `....pdf.pdf`). `{concursoId}` no caminho é o id do CONCURSO (não do
  documento/edital-item, que só entra na URL base como `{editalId}`).

**Cargo/vagas vêm 100% estruturados via `/rest/concurso/cargos/{id}`** —
diferente de JCM/FUNDEP (só PDF), aqui não é preciso confiar no Gemini
pra saber quantas especialidades existem nem seus nomes/quantidades: o
Gemini só entra pra extrair o SALÁRIO de cada cargo a partir do PDF do
Anexo I (camada de auditoria sobre JSON/estrutura já determinística,
mesmo padrão do resto do projeto — ver CLAUDE.md). Confirmado no texto
extraído do PDF real de Paracatu (`paracatu_anexo1_cargos_vencimentos.
pdf`): cada linha da tabela começa com "<código> - <NOME DO CARGO>"
(ex: "601 - MÉDICO - CIRURGIA GERAL"), o MESMO código de
`cargos[].codigo` — por isso `parear_salario_por_codigo` casa salário
por código extraído do início do texto de cargo devolvido pelo Gemini,
não por nome livre (mais confiável contra pequena variação de
formatação/travessão que o Gemini possa introduzir ao ler o PDF). Cai
pra nome normalizado (sem acento, espaços colapsados, maiúsculo) só
quando o texto do Gemini não tem código reconhecível no início. Cargo
sem casamento nenhum ainda é gravado pelo chamador (a fonte de verdade é
sempre `listar_cargos`, nunca a lista do Gemini) só que com
`salario=None` — **nenhum cargo é descartado por falha de casamento com
o PDF.**
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import quote

BASE_URL = "https://novo.ibgpconcursos.com.br"


@dataclass
class ItemListagem:
    concurso_id: int
    nome: str
    empresa_nome: str
    numero_edital: str
    tipo: str
    total_vagas: int
    total_cargos: int
    inicio_inscricao: datetime | None
    fim_inscricao: datetime | None


@dataclass
class Cargo:
    id: int
    codigo: str
    nome: str
    total_vagas: int | None


@dataclass
class Documento:
    id: int
    nome: str
    nome_real: str
    data: date | None


def url_cargos(concurso_id: int) -> str:
    return f"{BASE_URL}/rest/concurso/cargos/{concurso_id}"


def url_editais(concurso_id: int) -> str:
    return f"{BASE_URL}/rest/concurso/editais/{concurso_id}"


def montar_url_download(concurso_id: int, edital_id: int, nome_real: str) -> str:
    """`nome_real` (campo `nomeReal` do documento) já inclui a extensão
    `.pdf` — ver docstring do módulo. Caminho e nome do arquivo são
    URL-encoded (espaço, acento, "º" etc. no `nomeReal` real), mantendo
    só `/` como separador de path."""
    caminho = f"site/anexos/{concurso_id}/{nome_real}"
    return f"{BASE_URL}/rest/concurso/download/edital/{edital_id}/?file={quote(caminho, safe='/')}"


_PADRAO_PREFIXO_EDITAL = re.compile(r"^\s*edital\s*n[ºo°]?\s*", re.IGNORECASE)


def _limpar_numero_edital(texto: str) -> str:
    """`"EDITAL Nº 01/2026"` -> `"01/2026"` — mesmo padrão de
    `fundep.listar_processos_abertos` (remove só o rótulo, mantém o
    número/ano como veio, sem adivinhar formato)."""
    return _PADRAO_PREFIXO_EDITAL.sub("", texto or "").strip()


def _parsear_data_hora_listagem(texto: str) -> datetime | None:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%y %H:%M")
    except (ValueError, AttributeError):
        return None


def _parsear_data_documento(texto: str) -> date | None:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


def listar_concursos(dados: list[dict]) -> list[ItemListagem]:
    """Funciona tanto pra `/rest/concurso/inscricaoAberta` quanto pra
    `/rest/concurso/proximasInscricoes` — mesmo formato de item nos dois
    endpoints (só quem chama decide qual endpoint processar, ver
    docstring do módulo)."""
    itens: list[ItemListagem] = []
    for item in dados:
        empresa = item.get("empresa") or {}
        tipo = item.get("tipo") or {}
        itens.append(
            ItemListagem(
                concurso_id=item["id"],
                nome=(item.get("nome") or "").strip(),
                empresa_nome=(empresa.get("nome") or "").strip(),
                numero_edital=_limpar_numero_edital(item.get("edital") or ""),
                tipo=(tipo.get("nome") or "").strip(),
                total_vagas=item.get("totalVagas") or 0,
                total_cargos=item.get("totalCargos") or 0,
                inicio_inscricao=_parsear_data_hora_listagem(item.get("inicioInscricaoFormatado", "")),
                fim_inscricao=_parsear_data_hora_listagem(item.get("fimInscricaoFormatado", "")),
            )
        )
    return itens


def listar_cargos(dados: dict) -> list[Cargo]:
    """Lê o campo `cargos` do objeto devolvido por
    `/rest/concurso/cargos/{id}` — sem lista de cargos conhecida, sem
    filtro por nome, nenhuma especialidade (médica ou não) é descartada."""
    cargos = dados.get("cargos") or []
    resultado: list[Cargo] = []
    for c in cargos:
        nome = (c.get("nome") or "").strip()
        if not nome:
            continue
        resultado.append(
            Cargo(
                id=c["id"],
                codigo=str(c.get("codigo") or "").strip(),
                nome=nome,
                total_vagas=c.get("totalVagas"),
            )
        )
    return resultado


def listar_editais(dados: list[dict]) -> list[Documento]:
    return [
        Documento(
            id=d["id"],
            nome=(d.get("nome") or "").strip(),
            nome_real=d.get("nomeReal") or "",
            data=_parsear_data_documento(d.get("dataCriacaoFormatada", "")),
        )
        for d in dados
    ]


_PADRAO_ANEXO_I = re.compile(r"anexo\s+i\b", re.IGNORECASE)


def escolher_edital_vencimento(documentos: list[Documento]) -> Documento | None:
    """Escolhe o documento "ANEXO I" que também cita "VENCIMENTO" no
    nome (case-insensitive) — achado real: pode vir com sufixo de
    retificação (ex: "... E VENCIMENTO INICIAL - RETIFICAÇÃO Nº 01"), e
    pode haver mais de 1 candidato (edital original + retificações); usa
    o mais recente por `dataCriacaoFormatada`, mesmo padrão de
    `proseleta.escolher_edital`. `None` se não achar nenhum (edital sem
    Anexo I de vencimento publicado ainda, ou nome fora do padrão
    esperado — quem chama decide pular, sem adivinhar)."""
    candidatos = [d for d in documentos if _PADRAO_ANEXO_I.search(d.nome) and "vencimento" in d.nome.lower()]
    if not candidatos:
        return None
    return max(candidatos, key=lambda d: d.data or date.min)


_PADRAO_MUNICIPIO_UF = re.compile(r"([A-ZÀ-Ü][A-ZÀ-Ü\s.\-]*)/([A-Z]{2})(?![A-Za-zÀ-ü])")


def extrair_candidatos_municipio_uf(*textos: str, max_palavras: int = 4) -> list[tuple[str, str]]:
    """Gera candidatos a `(município, uf)` a partir do padrão
    `"<PALAVRAS>/<UF>"` encontrado em qualquer um dos `textos` passados
    (chamador decide a ordem — normalmente `empresa.nome` primeiro,
    depois `nome` do concurso, já que `empresa.nome` costuma ser mais
    limpo). Pra cada ocorrência do padrão, gera candidatos com as
    últimas 1 a `max_palavras` palavras antes da barra, do mais longo
    pro mais curto (mesma convenção de
    `fundep.candidatos_municipio_por_sufixo`) — nome de município quase
    sempre é só a última palavra (ex: "MUNICÍPIO DE DELFINÓPOLIS/MG" →
    "Delfinópolis"), mas pode ter até 4 (ex: "MUNICÍPIO DE SANTO
    ANTÔNIO DO ITAMBÉ/MG" → "Santo Antônio do Itambé") ou aparecer no
    MEIO do texto, não no fim (achado real: "... INSTITUTO DE
    PREVIDÊNCIA DE ITABIRA/MG - ITABIRAPREV" — o `/MG` não está no fim
    da frase, mas o candidato de 1 palavra "Itabira" ainda é gerado
    corretamente porque a busca é pelo padrão `/UF`, não pelo sufixo do
    texto inteiro).

    Função PURA (sem rede, sem IBGE) — só gera candidatos; quem chama
    valida contra o IBGE de verdade e para no primeiro que bater (ver
    `scripts/rodar_ibgp.py`), porque `buscar_codigo_ibge` faz chamada de
    rede e não pode entrar num parser testado só com fixture."""
    candidatos: list[tuple[str, str]] = []
    vistos: set[tuple[str, str]] = set()
    for texto in textos:
        if not texto:
            continue
        for match in _PADRAO_MUNICIPIO_UF.finditer(texto.strip()):
            prefixo, uf = match.group(1), match.group(2)
            palavras = prefixo.split()
            tamanho_maximo = min(max_palavras, len(palavras))
            for tamanho in range(tamanho_maximo, 0, -1):
                candidato = " ".join(palavras[-tamanho:]).strip(" .-")
                if not candidato:
                    continue
                chave = (candidato.title(), uf)
                if chave not in vistos:
                    vistos.add(chave)
                    candidatos.append(chave)
    return candidatos


def _normalizar_nome_cargo(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento).strip().upper()


_PADRAO_CODIGO_CARGO = re.compile(r"^\s*(\d+)\s*[-–—]\s*(.+)$")


def parear_salario_por_codigo(cargos: list[Cargo], vagas_gemini: list[dict]) -> dict[str, float | None]:
    """Casa cada cargo ESTRUTURADO (`listar_cargos`, fonte de verdade —
    nenhum é descartado) com o salário extraído pelo Gemini do PDF do
    Anexo I. Casamento por CÓDIGO (ex: "601" em "601 - MÉDICO - CIRURGIA
    GERAL") — ver docstring do módulo pro achado real que motivou isso.
    Cai pra nome normalizado (sem acento, maiúsculo, espaços colapsados)
    só quando o texto do Gemini não tem código reconhecível no início.

    Devolve `{cargo.codigo: salario|None}` com uma entrada pra CADA
    cargo de `cargos`, mesmo sem casamento nenhum (`None` nesse caso) —
    quem chama sempre grava todos os cargos, nunca só os que bateram com
    o PDF."""
    por_codigo: dict[str, float | None] = {}
    por_nome: dict[str, float | None] = {}
    for vaga in vagas_gemini:
        texto_cargo = (vaga.get("cargo") or "").strip()
        if not texto_cargo:
            continue
        salario = vaga.get("salario")
        match = _PADRAO_CODIGO_CARGO.match(texto_cargo)
        if match:
            codigo, nome = match.group(1), match.group(2)
            por_codigo[codigo.strip()] = salario
            por_nome[_normalizar_nome_cargo(nome)] = salario
        else:
            por_nome[_normalizar_nome_cargo(texto_cargo)] = salario

    resultado: dict[str, float | None] = {}
    for cargo in cargos:
        if cargo.codigo and cargo.codigo in por_codigo:
            resultado[cargo.codigo] = por_codigo[cargo.codigo]
        else:
            resultado[cargo.codigo] = por_nome.get(_normalizar_nome_cargo(cargo.nome))
    return resultado


def identificador_externo(concurso_id: int, cargo: Cargo) -> str:
    """Chave de dedup: id do concurso (único na IBGP) + código do cargo
    (único dentro do concurso — confirmado nas 69 linhas reais de
    Paracatu). Cai pro id numérico do cargo (sempre presente, PK da
    IBGP) no caso raro de `codigo` vir vazio, pra nunca colidir."""
    chave = cargo.codigo or str(cargo.id)
    return f"ibgp-{concurso_id}-{chave}"
