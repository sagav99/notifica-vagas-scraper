"""Detecta página/PDF servido por proteção anti-bot (Cloudflare, WAF
genérico, captcha) em vez do conteúdo real — pra distinguir "a fonte
bloqueou o robô" de "a fonte genuinamente não tinha esse dado", que hoje
resultam na mesma coisa (evidência gravada com campo crítico null, sem
nenhum registro do motivo).

Achado real (2026-09-13): `pousoalegre.mg.gov.br` bloqueou o fetch do
Vigia (Cloudflare, HTTP 403 + página "Just a moment...") — o PDF do
edital tinha o salário, mas o pipeline nunca chegou a lê-lo e gravou a
vaga como se a página não tivesse mais informação nenhuma, sem sinalizar
a causa. Ver `TAREFAS.md` do repo principal.
"""

from __future__ import annotations

_MARCADORES_HTML = (
    "just a moment",
    "attention required! | cloudflare",
    "cf-chl",
    "cf_chl_opt",
    "checking your browser before accessing",
    "ddos protection by",
    "sucuri website firewall",
    "incapsula incident id",
    "datadome",
    "perimeterx",
    "please verify you are a human",
    "verifying you are human",
    "enable javascript and cookies to continue",
    "pardon our interruption",
)

#: Status HTTP que, combinados com corpo pequeno (resposta "vazia" de
#: verdade, não uma página de erro custom com conteúdo real), reforçam a
#: suspeita de bloqueio mesmo sem bater nenhum marcador de texto conhecido.
_STATUS_SUSPEITOS = {403, 429, 503}
_TAMANHO_CORPO_SUSPEITO = 2000


class FetchSuspeitoError(Exception):
    """Levantada quando a resposta obtida não parece o conteúdo real
    esperado (página de desafio anti-bot, ou PDF esperado que veio como
    outra coisa) — distinta de `requests.RequestException` (aqui a fonte
    respondeu, só que não com o que pedimos). `motivo` é uma descrição
    curta pra registrar junto do sinal de descoberta."""

    def __init__(self, motivo: str):
        self.motivo = motivo
        super().__init__(motivo)


def detectar_bloqueio_html(*, status_code: int, corpo: str) -> str | None:
    """Retorna uma descrição curta do motivo se `corpo`/`status_code`
    parecem uma página de desafio anti-bot; `None` se não achar nenhum
    indício conhecido (não é garantia de que a página é o conteúdo real —
    só que não bateu nenhuma assinatura catalogada)."""
    corpo_lower = (corpo or "").lower()
    for marcador in _MARCADORES_HTML:
        if marcador in corpo_lower:
            return f'marcador anti-bot encontrado: "{marcador}" (HTTP {status_code})'
    if status_code in _STATUS_SUSPEITOS and len(corpo_lower.strip()) < _TAMANHO_CORPO_SUSPEITO:
        return f"HTTP {status_code} com corpo curto ({len(corpo_lower)} chars) — possível bloqueio"
    return None


def parece_pdf_valido(conteudo: bytes) -> bool:
    """PDF de verdade começa com a assinatura ``%PDF-``. WAF que intercepta
    o download de um PDF costuma devolver HTML (a página de desafio) com
    HTTP 200 — sem checar a assinatura, isso seguiria pro Gemini como se
    fosse um PDF legítimo."""
    return conteudo[:5] == b"%PDF-"


def detectar_bloqueio_pdf(*, status_code: int, conteudo: bytes) -> str | None:
    """Mesma ideia de `detectar_bloqueio_html`, mas pra download de PDF:
    além dos marcadores conhecidos, considera bloqueio quando o conteúdo
    baixado simplesmente não é um PDF válido (`parece_pdf_valido`)."""
    if parece_pdf_valido(conteudo):
        return None
    motivo = detectar_bloqueio_html(
        status_code=status_code, corpo=conteudo.decode("utf-8", errors="ignore")
    )
    if motivo:
        return motivo
    return f"conteúdo baixado não é um PDF válido (HTTP {status_code}, {len(conteudo)} bytes)"
