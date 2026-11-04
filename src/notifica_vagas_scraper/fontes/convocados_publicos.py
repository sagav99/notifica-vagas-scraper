"""Convocados PÚBLICOS: nomes que os diários oficiais publicam ao convocar aprovados de concursos que
temos cadastrados como vaga médica (alimenta o PDF coletivo do grupo; nada a ver com assinante).

Entrada: texto de uma publicação já lida pelo monitor (`ao_texto`). Saída: `Convocado` com nome, CPF
exatamente como publicado (o diário costuma mascarar; nunca buscamos CPF em outro lugar), classificação,
concurso, cargo e a vaga cadastrada a que o concurso corresponde. Sem IA, sem Serper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .convocacoes import (
    _ASSINATURA,
    _RUIDO_TITULO,
    _TITULO_CONVOCACAO,
    cargo_no_texto,
    detectar_concurso,
    entidade_eh_do_municipio,
    municipio_da_entidade,
    normalizar,
    numero_edital_no_texto,
)

_CONECTORES = {"da", "de", "do", "das", "dos", "e"}
#: palavra que nunca faz parte de nome de pessoa; basta uma para rejeitar o candidato a nome
_NAO_NOME = {
    "prefeitura", "municipal", "municipio", "estado", "concurso", "publico", "edital", "convocacao", "secretaria",
    "saude", "medico", "medica", "cargo", "diario", "oficial", "classificacao", "nome", "candidato", "candidata",
    "candidatos", "inscricao", "resultado", "homologacao", "nomeacao", "posse", "chamada", "chamamento", "prazo",
    "documentos", "anexo", "clinico", "geral", "plantonista", "esf", "psf", "familia", "estrategia", "jornada",
    "horas", "vaga", "vagas", "lista", "ordem", "ampla", "concorrencia", "deficiencia", "cadastro", "reserva",
    "pagina", "numero", "data", "decreto", "portaria", "lei", "processo", "seletivo", "simplificado", "temporario",
    "ministerio", "federal", "governo", "unidade", "hospital", "central", "recursos", "humanos", "departamento",
    "gabinete", "prefeito", "prefeita", "presidente", "secretario", "secretaria", "diretor", "diretora", "comissao",
    "avenida", "rua", "praca", "centro", "cep", "cnpj", "cpf", "rg", "brasil", "minas", "gerais", "paulo", "parana",
    "catarina", "grande", "sul", "total", "pontos", "nota", "titulos", "prova", "objetiva", "escrita", "pratica",
    "especialidade", "residencia", "ginecologia", "pediatria", "cardiologia", "ortopedia", "psiquiatria", "obstetricia",
    "anestesiologia", "cirurgia", "urgencia", "emergencia", "emergência", "saude", "publica", "atencao", "basica",
    "regulacao", "vigilancia", "epidemiologica", "sanitaria", "assistencia", "social", "educacao", "obras", "fazenda",
    "administracao", "finanzas", "financas", "planejamento", "gestao", "contrato", "convenio", "empresa", "ltda",
    "associacao", "fundacao", "instituto", "consorcio", "camara", "vereadores", "tribunal", "justica", "janeiro",
    "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
}
_PALAVRA_CAPS = r"[A-ZÁÉÍÓÚÂÊÔÃÕÇÜ]{2,}"
_NOME_CAPS = re.compile(rf"\b{_PALAVRA_CAPS}(?:\s+(?:(?:DA|DE|DO|DAS|DOS|E)\s+)?{_PALAVRA_CAPS}){{1,6}}\b")
_PALAVRA_TITULO = r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõçü]{1,}"
_NOME_TITULO = re.compile(rf"\b{_PALAVRA_TITULO}(?:\s+(?:(?:da|de|do|das|dos|e)\s+)?{_PALAVRA_TITULO}){{1,6}}\b")
_ROTULO = re.compile(r"(?:nome|candidato|candidata|convocado|convocada|sr\.?|sra\.?)\s*[:\-–]\s*$", re.I)
_CPF = re.compile(r"(?<![\d*])(?:\d{3}|\*{3})[.\s]?(?:\d{3}|\*{3})[.\s]?(?:\d{3}|\*{3})[-\s]?(?:\d{2}|\*{2})(?![\d*])")
_ORDINAL_ANTES = re.compile(r"(\d{1,4})\s*[ºo°ª]\s*[\-–.:)]?\s*$")
_NUMERO_ANTES = re.compile(r"(?:^|\s)(\d{1,4})\s*[\-–.)]?\s*$")
_SECAO_CARGO = re.compile(r"(?:cargo|fun[cç][aã]o|emprego)\s*(?:de|:)?\s*([^\n:;]{3,70})", re.I)


@dataclass
class Convocado:
    nome: str
    nome_norm: str
    cpf_publicado: str | None
    classificacao: int | None
    cargo: str | None


@dataclass
class VagaAlvo:
    id: str
    uf: str
    municipio: str
    cargo: str
    numero_edital: str | None
    banca: str | None = None


def nome_valido(candidato: str) -> bool:
    partes = normalizar(candidato).split()
    miolo = [p for p in partes if p not in _CONECTORES]
    if len(miolo) < 2 or len(candidato) > 80:
        return False
    if any(p in _NAO_NOME for p in partes):
        return False
    if any(len(p) < 2 for p in miolo) or partes[0] in _CONECTORES or partes[-1] in _CONECTORES:
        return False
    return True


def _cpf_depois(texto: str, fim: int) -> str | None:
    achado = _CPF.search(texto[fim : fim + 90])
    return achado[0].strip() if achado else None


def _classificacao_antes(texto: str, inicio: int) -> int | None:
    antes = texto[max(0, inicio - 14) : inicio]
    achado = _ORDINAL_ANTES.search(antes)
    if achado:
        return int(achado[1])
    achado = _NUMERO_ANTES.search(antes)  # lista "12 NOME": o número é a posição da lista (classificação) na maioria dos diários
    return int(achado[1]) if achado and int(achado[1]) <= 3000 else None


def _cargo_do_trecho(texto: str, pos: int) -> str | None:
    """Último 'cargo de X' citado nos 1500 caracteres anteriores ao nome."""
    ultimo = None
    for achado in _SECAO_CARGO.finditer(texto[max(0, pos - 1500) : pos]):
        ultimo = achado[1].strip(" -–:.,")
    return ultimo[:70] if ultimo else None


def extrair_convocados(texto: str, *, exigir_medico: bool = True) -> list[Convocado]:
    """Nomes de pessoas convocadas numa publicação. Conservador: prefere perder um nome a inventar um.

    Aceita um nome quando (a) vem depois de rótulo "Nome:/Candidato:" ou número de classificação, ou
    (b) é sequência em CAIXA ALTA de 2+ palavras de lista. Descarta assinatura do ato (Prefeito etc.).
    Com `exigir_medico`, o último cargo citado antes do nome precisa ser de médico (se houver cargo citado)."""
    plano = re.sub(r"[ \t]+", " ", texto)
    plano = re.sub(r"\s*\n\s*", " \n ", plano)
    vistos: set[str] = set()
    saida: list[Convocado] = []
    candidatos: list[tuple[int, int, str, bool]] = []  # (inicio, fim, nome, confiavel)
    for m in _NOME_CAPS.finditer(plano):
        antes = plano[max(0, m.start() - 24) : m.start()]
        confiavel = bool(_ROTULO.search(antes) or _ORDINAL_ANTES.search(antes) or _NUMERO_ANTES.search(antes[-10:]))
        candidatos.append((m.start(), m.end(), m[0], confiavel))
    for m in _NOME_TITULO.finditer(plano):
        antes = plano[max(0, m.start() - 24) : m.start()]
        if _ROTULO.search(antes) or _ORDINAL_ANTES.search(antes):
            candidatos.append((m.start(), m.end(), m[0], True))
    segmentos: list[tuple[int, int, str, bool]] = []
    for inicio, fim, nome, confiavel in sorted(candidatos):
        # "MARIA DA SILVA CPF" / "JOSÉ SOUZA MÉDICO": palavra que não é de nome corta o candidato em pedaços
        atual: list[tuple[str, int]] = []
        for achado in re.finditer(r"\S+", nome):
            if normalizar(achado[0]) in _NAO_NOME:
                if atual:
                    segmentos.append((inicio + atual[0][1], inicio + atual[-1][1] + len(atual[-1][0]), " ".join(p for p, _ in atual), confiavel and atual[0][1] == 0))
                atual = []
            else:
                atual.append((achado[0], achado.start()))
        if atual:
            segmentos.append((inicio + atual[0][1], inicio + atual[-1][1] + len(atual[-1][0]), " ".join(p for p, _ in atual), confiavel and atual[0][1] == 0))
    for inicio, fim, nome, confiavel in segmentos:
        if not confiavel and not _maiuscula_lista(plano, inicio):
            continue
        if not nome_valido(nome):
            continue
        norm = normalizar(nome)
        if norm in vistos:
            continue
        if _ASSINATURA.match(normalizar(plano[fim : fim + 40])):
            continue
        cargo = _cargo_do_trecho(plano, inicio)
        if exigir_medico and cargo and "medic" not in normalizar(cargo):
            continue
        vistos.add(norm)
        saida.append(Convocado(_capitalizar(nome), norm, _cpf_depois(plano, fim), _classificacao_antes(plano, inicio), cargo))
    return saida


def _capitalizar(nome: str) -> str:
    if not nome.isupper():
        return nome
    return " ".join(p.lower() if p.lower() in _CONECTORES else p.capitalize() for p in nome.split())


def _maiuscula_lista(plano: str, inicio: int) -> bool:
    """Nome em CAIXA ALTA solto só vale se parece linha de lista: há outro nome em CAIXA ALTA ou CPF a ≤160 caracteres."""
    janela = plano[max(0, inicio - 160) : inicio + 200]
    return bool(_CPF.search(janela)) or len(_NOME_CAPS.findall(janela)) >= 3


def casar_vaga(vagas: list[VagaAlvo], *, uf: str, entidade: str | None, titulo: str, texto: str) -> VagaAlvo | None:
    """Vaga cadastrada a que a publicação se refere: mesmo município e UF, título/ato de convocação, e o
    número do concurso (ou, sem número na vaga, o cargo de médico) aparece no texto."""
    if not entidade or _RUIDO_TITULO.search(normalizar(titulo)):
        return None
    if not _TITULO_CONVOCACAO.search(normalizar(titulo)) and not re.search(r"convoc|chamamento|nomea", normalizar(texto[:600])):
        return None
    municipio = municipio_da_entidade(entidade) or entidade
    do_lugar = [v for v in vagas if v.uf == uf and (entidade_eh_do_municipio(entidade, v.municipio) or normalizar(municipio) == normalizar(v.municipio))]
    if not do_lugar:
        return None
    com_numero = [v for v in do_lugar if v.numero_edital and numero_edital_no_texto(v.numero_edital, texto)]
    if com_numero:
        return com_numero[0]
    if re.search(r"m[eé]dic", texto, re.I):
        sem_numero = [v for v in do_lugar if not v.numero_edital and cargo_no_texto(v.cargo, texto)]
        if sem_numero:
            return sem_numero[0]
    return None


def convocados_da_publicacao(
    vagas: list[VagaAlvo], *, uf: str, entidade: str | None, titulo: str, texto: str, data_publicacao: date | None = None,
) -> tuple[VagaAlvo, str | None, list[Convocado]] | None:
    """(vaga, rótulo do concurso, convocados) ou None se a publicação não é de convocação de concurso nosso."""
    vaga = casar_vaga(vagas, uf=uf, entidade=entidade, titulo=titulo, texto=texto)
    if vaga is None:
        return None
    convocados = extrair_convocados(texto)
    if not convocados:
        return None
    concurso, _ = detectar_concurso(titulo, texto)
    return vaga, concurso or (f"Concurso {vaga.numero_edital}" if vaga.numero_edital else None), convocados


def processar_publicacao(vagas: list[VagaAlvo], pub: dict) -> tuple[VagaAlvo, str | None, list[Convocado]] | None:
    """Ponto de entrada do monitor. Publicação avulsa: `convocados_da_publicacao`. Edição inteira de diário (DOSP):
    só as janelas ao redor do número do concurso de uma vaga do município, com sinal de convocação por perto."""
    if not pub.get("edicao_inteira"):
        return convocados_da_publicacao(
            vagas, uf=pub["uf"], entidade=pub.get("entidade"), titulo=pub["titulo"], texto=pub["texto"], data_publicacao=pub.get("data_publicacao"),
        )
    texto = pub["texto"]
    for vaga in vagas:
        if vaga.uf != pub["uf"] or not vaga.numero_edital or not entidade_eh_do_municipio(pub.get("entidade") or "", vaga.municipio):
            continue
        for m in re.finditer(r"(\d+)\s*/\s*(\d{2,4})", vaga.numero_edital):
            for achado in re.finditer(rf"(?<!\d)0*{int(m[1])}\s*/\s*(?:20)?{m[2][-2:]}(?!\d)", texto):
                janela = texto[max(0, achado.start() - 1500) : achado.end() + 4000]
                if not re.search(r"convoc|chamamento|nomea", normalizar(janela[1000:2200])):
                    continue
                r = convocados_da_publicacao([vaga], uf=pub["uf"], entidade=pub["entidade"], titulo="Convocação", texto=janela)
                if r:
                    return r
    return None
