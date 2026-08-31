"""Descoberta heurística de URL oficial de prefeitura por município.

Não é scraping de UMA fonte específica (não passa pela regra de
`pesquisador-fonte`/subagente): é uma checagem determinística e barata —
requests HTTP simples contra padrões de domínio `.gov.br`, sem raciocínio de
LLM por cidade. Desde 2021 o governo federal exige migração de sites
municipais para `<slug>.<uf>.gov.br` (registro.br só permite esse domínio
para prefeitura de fato), então tentar variações de slug + validar conteúdo
é suficiente pra maioria dos casos — sem precisar de busca externa (Google
etc.) nem de investigação manual.

Uso típico (ver scripts/descobrir_urls_prefeitura.py):
    candidatos = candidatos_url("Pedra Dourada", "MG")
    resultado = descobrir_url_prefeitura("Pedra Dourada", "MG")
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "pt-BR,pt;q=0.9",
}
# (connect, read) — servidor de prefeitura pequena costuma ser lento, mas
# uma conexão que não fecha em poucos segundos normalmente está bloqueando
# nosso IP/rede (achado real: contagem.mg.gov.br trava no connect).
TIMEOUT = (5, 10)
PALAVRAS_IGNORADAS = {"de", "da", "do", "das", "dos"}


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


def _normalizar(texto: str) -> str:
    return _sem_acento(texto).strip().lower()


def gerar_slugs(nome_municipio: str) -> list[str]:
    """Gera variações de slug — domínio de prefeitura brasileira não segue
    uma única convenção (com/sem hífen, com/sem preposição)."""
    palavras = _normalizar(nome_municipio).replace("'", "").split()
    palavras_sem_prep = [p for p in palavras if p not in PALAVRAS_IGNORADAS]

    variantes = {
        "-".join(palavras),
        "".join(palavras),
        "-".join(palavras_sem_prep),
        "".join(palavras_sem_prep),
    }
    return sorted(v for v in variantes if v)


def candidatos_url(nome_municipio: str, uf: str) -> list[str]:
    uf_slug = uf.strip().lower()
    candidatos = []
    for slug in gerar_slugs(nome_municipio):
        for prefixo in ("www.", ""):
            candidatos.append(f"https://{prefixo}{slug}.{uf_slug}.gov.br")
    return candidatos


@dataclass
class ResultadoDescoberta:
    municipio: str
    uf: str
    url: str | None
    candidatos_testados: int
    motivo: str
    certificado_invalido: bool = False


def _pagina_parece_da_prefeitura(html: str, nome_municipio: str) -> bool:
    texto = _normalizar(html)
    tem_prefeitura = "prefeitura" in texto or "municipio" in texto or "camara municipal" in texto
    tem_nome_cidade = _normalizar(nome_municipio) in texto
    return tem_prefeitura and tem_nome_cidade


def _tentar_get(url: str) -> tuple[requests.Response | None, bool]:
    """Retorna (resposta, certificado_invalido). Prefeitura pequena costuma
    deixar o certificado expirar — achado real (Conceição da Barra de Minas):
    não descartar o domínio só por isso, tentar de novo sem verificar TLS."""
    try:
        return requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True), False
    except requests.exceptions.SSLError:
        try:
            return requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True, verify=False), True
        except requests.RequestException:
            return None, True
    except requests.RequestException:
        return None, False


def descobrir_url_prefeitura(nome_municipio: str, uf: str) -> ResultadoDescoberta:
    candidatos = candidatos_url(nome_municipio, uf)
    testados = 0
    for url in candidatos:
        testados += 1
        resposta, certificado_invalido = _tentar_get(url)
        if resposta is None or resposta.status_code >= 400:
            continue

        if _pagina_parece_da_prefeitura(resposta.text, nome_municipio):
            return ResultadoDescoberta(
                municipio=nome_municipio,
                uf=uf,
                url=resposta.url,
                candidatos_testados=testados,
                motivo="200 + conteúdo confirma prefeitura/nome do município",
                certificado_invalido=certificado_invalido,
            )

    return ResultadoDescoberta(
        municipio=nome_municipio,
        uf=uf,
        url=None,
        candidatos_testados=testados,
        motivo="nenhum candidato .gov.br respondeu com conteúdo confirmado",
    )
