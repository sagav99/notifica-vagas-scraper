"""Card 1080x1350 do post (HTML/CSS -> PNG via Playwright).

Layout inspirado no post de referência do @medvagasapp: logo, cidade/UF,
selo de status das inscrições, cargo, órgão, grade 2 colunas de dados e
requisitos. Todo campo ausente vira "Não informado" — nunca inventa dado.
"""

from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path
from typing import Any

LARGURA = 1080
ALTURA = 1350
NAO_INFORMADO = "Não informado"

ROTULOS_TIPO = {
    "concurso_efetivo": "Concurso público (efetivo)",
    "processo_seletivo_temporario": "Processo seletivo temporário",
    "credenciamento": "Credenciamento",
    "contratacao_emergencial": "Contratação emergencial",
    "selecao_plantao": "Seleção-plantão",
}
ROTULOS_SALARIO = {"mensal": "mensal", "plantao": "plantão", "hora": "hora"}


def formatar_data(valor: date | None) -> str:
    return valor.strftime("%d/%m/%Y") if valor else NAO_INFORMADO


def _brl(valor: Any) -> str:
    return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_salario(vaga: dict[str, Any]) -> str:
    if vaga.get("salario") is None:
        return NAO_INFORMADO
    sufixo = ROTULOS_SALARIO.get(vaga.get("salario_tipo") or "", "")
    return f"{_brl(vaga['salario'])}/{sufixo}" if sufixo else _brl(vaga["salario"])


def rotulo_prazo(fim: date | None, hoje: date) -> str:
    if fim is None:
        return "inscrições abertas"
    dias = (fim - hoje).days
    if dias == 0:
        return "encerra hoje"
    if dias == 1:
        return "encerra amanhã"
    if 0 < dias <= 3:
        return f"últimos dias — até {fim.strftime('%d/%m')}"
    return "inscrições abertas"


def _taxa(valor: Any) -> str:
    if valor is None:
        return NAO_INFORMADO
    return "Sem taxa" if float(valor) == 0 else _brl(valor)


def _sim_nao(valor: bool | None, sim: str, nao: str) -> str:
    return NAO_INFORMADO if valor is None else (sim if valor else nao)


def _data_prova(vaga: dict[str, Any]) -> str:
    if vaga.get("data_prova"):
        return formatar_data(vaga["data_prova"])
    return "Sem prova" if vaga.get("tem_prova") is False else NAO_INFORMADO


def _resumir(texto: str | None, limite: int = 190) -> str:
    if not texto:
        return NAO_INFORMADO
    limpo = " ".join(texto.split())
    return limpo if len(limpo) <= limite else limpo[: limite - 1].rstrip() + "…"


def campos(vaga: dict[str, Any], destaque_fim: bool) -> list[tuple[str, str, str, bool]]:
    """(icone, rotulo, valor, destaque) na ordem da grade 2 colunas."""
    return [
        ("moeda", "REMUNERAÇÃO", formatar_salario(vaga), False),
        ("doc", "TIPO DE OPORTUNIDADE", ROTULOS_TIPO.get(vaga.get("tipo_oportunidade") or "", NAO_INFORMADO), False),
        ("doc", "EDITAL", vaga.get("numero_edital") or NAO_INFORMADO, False),
        ("agenda", "INÍCIO DAS INSCRIÇÕES", formatar_data(vaga.get("inscricoes_inicio")), False),
        ("agenda", "FIM DAS INSCRIÇÕES", formatar_data(vaga.get("inscricoes_fim")), destaque_fim),
        ("pessoas", "NÚMERO DE VAGAS", str(vaga["numero_vagas"]) if vaga.get("numero_vagas") else NAO_INFORMADO, False),
        ("moeda", "TAXA DE INSCRIÇÃO", _taxa(vaga.get("taxa_inscricao")), False),
        ("relogio", "CARGA HORÁRIA", vaga.get("carga_horaria") or NAO_INFORMADO, False),
        ("agenda", "DATA DA PROVA", _data_prova(vaga), False),
        ("predio", "BANCA ORGANIZADORA", vaga.get("banca_organizadora") or NAO_INFORMADO, False),
        ("doc", "PROVA", _sim_nao(vaga.get("tem_prova"), "Há prova", "Não há prova"), False),
        ("doc", "CURRÍCULO/TÍTULOS", _sim_nao(vaga.get("exige_curriculo"), "Exige currículo/títulos", "Não exige"), False),
    ]


_ICONES = {
    "moeda": '<circle cx="12" cy="12" r="9"/><path d="M14.5 9.2c-.6-.7-1.5-1-2.5-1-1.4 0-2.4.7-2.4 1.8 0 2.4 5 1.2 5 3.7 0 1.1-1.1 1.9-2.6 1.9-1.1 0-2-.4-2.7-1.1M12 6.5v11"/>',
    "doc": '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M9.5 12h5M9.5 16h5"/>',
    "agenda": '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M8 3v4M16 3v4"/>',
    "pessoas": '<circle cx="9" cy="8.5" r="3"/><path d="M3.5 19c.4-3 2.6-4.8 5.5-4.8s5.1 1.8 5.5 4.8"/><circle cx="17" cy="9.5" r="2.3"/><path d="M16.5 14.4c2.3.1 3.7 1.5 4 4"/>',
    "relogio": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    "predio": '<path d="M4 20h16M6 20V9l6-4 6 4v11M9.5 20v-5h5v5M10 11h4"/>',
    "capelo": '<path d="M2.5 9.5 12 5l9.5 4.5L12 14zM6.5 12v4.5c1.5 1.5 3.4 2 5.5 2s4-.5 5.5-2V12"/>',
    "pin": '<path d="M12 21s6.5-5.6 6.5-11a6.5 6.5 0 0 0-13 0c0 5.4 6.5 11 6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
}


def _icone(nome: str, cor: str = "#2c4fb3", tamanho: int = 30) -> str:
    return (
        f'<svg width="{tamanho}" height="{tamanho}" viewBox="0 0 24 24" fill="none" stroke="{cor}" '
        f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{_ICONES[nome]}</svg>'
    )


_LOGO_M = (
    '<svg width="74" height="60" viewBox="0 0 74 60"><path d="M4 56V8l18-4 15 26L52 4l18 4v48H56V26L41 52H33L18 26v30z" fill="#1f3fa8"/>'
    '<path d="M4 8l18-4 15 26-4 8L18 26z" fill="#2f7cf6"/></svg>'
)


def _tamanho_titulo(cargo: str) -> int:
    n = len(cargo)
    return 78 if n <= 26 else 64 if n <= 46 else 52


def montar_html(vaga: dict[str, Any], tipo: str, hoje: date) -> str:
    fim = vaga.get("inscricoes_fim")
    urgente = tipo == "fim_prazo"
    selo = rotulo_prazo(fim, hoje).upper() if urgente else "INSCRIÇÕES ABERTAS"
    cor_selo, fundo_selo = ("#b3261e", "#fde8e6") if urgente else ("#1d7a46", "#e2f4e8")
    cargo = escolher_cargo(vaga)
    tiles = "".join(
        f'<div class="tile{" destaque" if dest else ""}"><div class="ico">{_icone(ic, "#b3261e" if dest else "#2c4fb3")}</div>'
        f'<div><div class="rot">{escape(rot)}</div><div class="val">{escape(val)}</div></div></div>'
        for ic, rot, val, dest in campos(vaga, urgente)
    )
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;600;700;800&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box}}body{{margin:0;width:{LARGURA}px;height:{ALTURA}px;overflow:hidden;background:#f4f6ff;
font-family:'Montserrat','Helvetica Neue',Arial,sans-serif;color:#0f1f4d;position:relative}}
.canto1{{position:absolute;top:0;right:0;width:330px;height:150px;background:linear-gradient(200deg,#2f7cf6 0 38%,transparent 38%),linear-gradient(200deg,#1f3fa8 0 22%,transparent 22%)}}
.canto2{{position:absolute;bottom:0;right:0;width:250px;height:110px;background:linear-gradient(20deg,#1f3fa8 0 40%,#2f7cf6 40% 62%,transparent 62%);clip-path:polygon(100% 0,100% 100%,0 100%,0 62%)}}
.wrap{{position:relative;padding:54px 72px 0}}
.logo{{display:flex;align-items:center;gap:14px;font-weight:800;font-size:58px;letter-spacing:-1px}}
.logo .a{{color:#0f1f4d}}.logo .b{{color:#2f7cf6}}
.linha{{display:flex;justify-content:space-between;align-items:center;margin-top:34px}}
.local{{display:flex;align-items:center;gap:10px;font-size:29px;font-weight:700}}
.selo{{padding:12px 26px;border-radius:999px;font-size:22px;font-weight:800;letter-spacing:.3px;color:{cor_selo};background:{fundo_selo};border:2px solid {cor_selo}33}}
h1{{margin:26px 0 0;font-size:{_tamanho_titulo(cargo)}px;line-height:1.06;font-weight:800;letter-spacing:-1.5px;text-transform:uppercase;color:#0f1f4d}}
.orgao{{margin:16px 0 26px;font-size:27px;font-weight:500;color:#2a3a72}}
.grade{{display:grid;grid-template-columns:1fr 1fr;gap:14px 16px}}
.tile{{display:flex;align-items:center;gap:16px;padding:16px 20px;min-height:98px;border-radius:16px;background:#e3e9fb;border:1px solid #d1daf5}}
.tile.destaque{{background:#fde8e6;border-color:#f3b8b3}}
.ico{{flex:none;width:56px;height:56px;border-radius:50%;background:#fff;display:flex;align-items:center;justify-content:center}}
.rot{{font-size:15px;font-weight:600;letter-spacing:.4px;color:#41507f}}
.val{{margin-top:3px;font-size:24px;font-weight:800;line-height:1.15;color:#0f1f4d}}
.tile.destaque .val{{color:#b3261e}}
.req{{margin-top:14px;display:flex;align-items:center;gap:16px;padding:16px 20px;border-radius:16px;background:#e3e9fb;border:1px solid #d1daf5}}
.req .val{{font-size:21px;font-weight:700;line-height:1.3}}
.rodape{{position:absolute;left:72px;bottom:30px;font-size:16px;font-weight:600;letter-spacing:1.4px;color:#41507f;line-height:1.5}}
.site{{position:absolute;right:280px;bottom:34px;font-size:19px;font-weight:700;color:#1f3fa8}}
</style></head><body>
<div class="canto1"></div><div class="canto2"></div>
<div class="wrap">
<div class="logo">{_LOGO_M}<span><span class="a">Med</span><span class="b">Vagas</span></span></div>
<div class="linha"><div class="local">{_icone("pin", "#1f3fa8", 34)}{escape(vaga["municipio"])}/{escape(vaga["uf"])}</div><div class="selo">{escape(selo)}</div></div>
<h1>{escape(cargo)}</h1>
<div class="orgao">{escape(vaga["orgao"])}</div>
<div class="grade">{tiles}</div>
<div class="req"><div class="ico">{_icone("capelo", "#2c4fb3")}</div><div><div class="rot">REQUISITOS</div><div class="val">{escape(_resumir(vaga.get("requisitos")))}</div></div></div>
</div>
<div class="rodape">MAIS MÉDICOS<br>PARA UM BRASIL MAIS FORTE</div>
<div class="site">med-vagas.vercel.app</div>
</body></html>"""


def escolher_cargo(vaga: dict[str, Any]) -> str:
    return " ".join(str(vaga["cargo"]).split())


def renderizar_png(html: str, destino: Path) -> Path:
    """Renderiza o HTML em PNG (precisa de `playwright` instalado)."""
    from playwright.sync_api import sync_playwright

    destino.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        try:
            navegador = pw.chromium.launch()
        except Exception:  # Chromium do Playwright ausente (uso local): tenta o Chrome instalado.
            navegador = pw.chromium.launch(channel="chrome")
        pagina = navegador.new_page(viewport={"width": LARGURA, "height": ALTURA})
        pagina.set_content(html, wait_until="networkidle")
        pagina.evaluate("document.fonts.ready")
        pagina.screenshot(path=str(destino), type="png")
        navegador.close()
    return destino
