"""Carrossel de 4 slides do post (HTML/CSS -> PNG via Playwright).

Layout aprovado pelo usuário em 2026-09-29, cópia de referência em
`docs/referencia_visual_instagram/` (não reabrir sem motivo novo):
slide 1 "VAGA DA SEMANA" (resumo da vaga), slide 2 "O QUE PESA NA DECISÃO"
(valor-hora), slide 3 "ANTES DE SE INSCREVER" (checklist fixo do edital) e
slide 4 "ESSA É SÓ UMA DAS VAGAS" (CTA + preview de outras 2 vagas).

Todo campo ausente vira "Não informado" — nunca inventa dado.
"""

from __future__ import annotations

import re
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

LARGURA = 1080
ALTURA = 1350
NAO_INFORMADO = "Não informado"
PRECO_INICIAL = "A partir de R$29,99/mês · Sem fidelidade"  # lib/planos.ts (repo do site) — basic_mensal, conferir antes de mudar
SITE = "med-vagas.vercel.app"

ROTULOS_TIPO = {
    "concurso_efetivo": "Concurso efetivo",
    "processo_seletivo_temporario": "Processo seletivo temporário",
    "credenciamento": "Credenciamento",
    "contratacao_emergencial": "Contratação emergencial",
    "selecao_plantao": "Seleção-plantão",
}
ROTULOS_SALARIO = {"mensal": "MÊS", "plantao": "PLANTÃO", "hora": "HORA"}


def formatar_data(valor: date | None) -> str:
    return valor.strftime("%d/%m/%Y") if valor else NAO_INFORMADO


def _brl(valor: Any) -> str:
    return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_salario(vaga: dict[str, Any]) -> str:
    if vaga.get("salario") is None:
        return NAO_INFORMADO
    sufixo = {"mensal": "mensal", "plantao": "plantão", "hora": "hora"}.get(vaga.get("salario_tipo") or "", "")
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


def _data_prova(vaga: dict[str, Any]) -> str:
    if vaga.get("data_prova"):
        return formatar_data(vaga["data_prova"])
    return "Sem prova" if vaga.get("tem_prova") is False else NAO_INFORMADO


def _resumir(texto: str | None, limite: int = 150) -> str:
    if not texto:
        return NAO_INFORMADO
    limpo = " ".join(texto.split())
    return limpo if len(limpo) <= limite else limpo[: limite - 1].rstrip() + "…"


_PREFIXO_MEDICO = re.compile(r"^m[eé]dic[oa]s?\b[\s\-:]*", re.IGNORECASE)


def especialidade_curta(cargo: str) -> str:
    """"Médico Cardiologista" -> "Cardiologista"; sem prefixo reconhecido, devolve o cargo inteiro."""
    txt = " ".join(str(cargo).split())
    resto = _PREFIXO_MEDICO.sub("", txt).strip()
    return resto if resto else txt


_NUM_HORAS = re.compile(r"(\d+(?:[.,]\d+)?)")


def horas_semanais(carga_horaria: str | None) -> float | None:
    if not carga_horaria:
        return None
    m = _NUM_HORAS.search(carga_horaria)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def valor_hora(vaga: dict[str, Any]) -> float | None:
    """Salário mensal / horas no mês (horas semanais * 52/12). None se faltar dado ou tipo != mensal."""
    if vaga.get("salario") is None or (vaga.get("salario_tipo") or "mensal") != "mensal":
        return None
    horas = horas_semanais(vaga.get("carga_horaria"))
    if not horas:
        return None
    horas_mes = horas * 52 / 12
    if horas_mes <= 0:
        return None
    return float(vaga["salario"]) / horas_mes


def formatar_valor_hora(vaga: dict[str, Any]) -> str:
    vh = valor_hora(vaga)
    return f"{_brl(vh)}" if vh is not None else NAO_INFORMADO


def _tamanho_titulo(texto: str) -> int:
    n = len(texto)
    return 76 if n <= 18 else 62 if n <= 28 else 50 if n <= 40 else 40


_LOGO_M = (
    '<svg width="46" height="38" viewBox="0 0 74 60"><path d="M4 56V8l18-4 15 26L52 4l18 4v48H56V26L41 52H33L18 26v30z" fill="#0f1e3d"/>'
    '<path d="M4 8l18-4 15 26-4 8L18 26z" fill="#2f6fed"/></svg>'
)

_ICONES = {
    "doc": '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M9.5 12h5M9.5 16h5"/>',
    "agenda": '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M8 3v4M16 3v4"/>',
    "escudo": '<path d="M12 3l7 3v6c0 5-3 8.5-7 9-4-.5-7-4-7-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "seta": '<path d="M5 12h14M13 6l6 6-6 6"/>',
}


def _icone(nome: str, cor: str = "#2f6fed", tamanho: int = 26) -> str:
    return (
        f'<svg width="{tamanho}" height="{tamanho}" viewBox="0 0 24 24" fill="none" stroke="{cor}" '
        f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{_ICONES[nome]}</svg>'
    )


def _estilo_base() -> str:
    return f"""*{{box-sizing:border-box}}body{{margin:0;width:{LARGURA}px;height:{ALTURA}px;overflow:hidden;
background:#eef2fb;font-family:'Montserrat','Helvetica Neue',Arial,sans-serif;color:#0f1e3d;position:relative}}
.canto1{{position:absolute;top:0;right:0;width:340px;height:160px;
background:linear-gradient(200deg,#8fb6fb 0 38%,transparent 38%),linear-gradient(200deg,#2f6fed 0 22%,transparent 22%)}}
.canto2{{position:absolute;bottom:0;right:0;width:280px;height:130px;
background:linear-gradient(20deg,#0f1e3d 0 42%,#2f6fed 42% 64%,transparent 64%);clip-path:polygon(100% 0,100% 100%,0 100%,0 64%)}}
.wrap{{position:relative;padding:56px 70px 60px;height:100%;display:flex;flex-direction:column}}
.empurra{{margin-top:auto}}
.topo{{display:flex;justify-content:space-between;align-items:center}}
.logo{{display:flex;align-items:center;gap:12px;font-weight:800;font-size:36px;letter-spacing:-.5px}}
.logo .a{{color:#0f1e3d}}.logo .b{{color:#2f6fed}}
.badge-n{{padding:10px 22px;border-radius:999px;font-size:22px;font-weight:700;color:#fff;background:rgba(15,30,61,.82)}}
.eyebrow{{margin-top:40px;font-size:24px;font-weight:800;letter-spacing:1.5px;color:#2f6fed;text-transform:uppercase}}
h1{{font-family:'Archivo Black','Montserrat',sans-serif;margin:14px 0 0;line-height:1.04;font-weight:400;
letter-spacing:-1px;text-transform:uppercase}}
h1 .navy{{color:#0f1e3d}}h1 .azul{{color:#2f6fed}}
.divisor{{margin-top:22px;width:190px;height:5px;border-radius:3px;background:#8fb6fb}}
.rodape-num{{display:flex;align-items:center;gap:16px}}
.rodape-num .n{{font-size:26px;font-weight:800;color:#2f6fed}}
.rodape-num .barra{{width:210px;height:5px;border-radius:3px;background:#c9d8fb}}
.site{{position:absolute;right:70px;bottom:50px;font-size:18px;font-weight:700;color:#41507f}}
"""


def _cabecalho(numero: int) -> str:
    return (
        f'<div class="topo"><div class="logo">{_LOGO_M}<span><span class="a">Med</span>'
        f'<span class="b">Vagas</span></span></div><div class="badge-n">{numero}/4</div></div>'
    )


def _html(corpo_estilo: str, corpo_html: str) -> str:
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;600;700;800&family=Archivo+Black&display=swap" rel="stylesheet">
<style>{_estilo_base()}{corpo_estilo}</style></head><body>
<div class="canto1"></div><div class="canto2"></div>
<div class="wrap">{corpo_html}</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Slide 1 — resumo da vaga


def _slide1(vaga: dict[str, Any], tipo: str, hoje: date) -> str:
    urgente = tipo == "fim_prazo"
    eyebrow = "VAGA COM PRAZO ACABANDO" if urgente else "VAGA DA SEMANA"
    cor, fundo = ("#b3261e", "#fde8e6") if urgente else ("#1d7a46", "#e2f4e8")
    selo = rotulo_prazo(vaga.get("inscricoes_fim"), hoje).upper() if urgente else "INSCRIÇÕES ABERTAS"

    titulo = especialidade_curta(vaga["cargo"]).upper()
    if vaga.get("salario") is not None:
        sufixo = ROTULOS_SALARIO.get(vaga.get("salario_tipo") or "mensal", "MÊS")
        linha1, linha2 = f"{titulo} COM", f"R$ {_brl(vaga['salario']).replace('R$ ', '')}/{sufixo}"
    else:
        linha1, linha2 = titulo, "INSCRIÇÕES ABERTAS"
    tam = _tamanho_titulo(max(linha1, linha2, key=len))

    tipo_rotulo = ROTULOS_TIPO.get(vaga.get("tipo_oportunidade") or "", NAO_INFORMADO)
    edital = f" · Edital {vaga['numero_edital']}" if vaga.get("numero_edital") else ""

    campos = [
        ("REMUNERAÇÃO", formatar_salario(vaga)),
        ("INSCRIÇÕES ATÉ", formatar_data(vaga.get("inscricoes_fim"))),
        ("CARGA HORÁRIA", vaga.get("carga_horaria") or NAO_INFORMADO),
        ("VALOR-HORA", formatar_valor_hora(vaga)),
        ("DATA DA PROVA", _data_prova(vaga)),
        ("TAXA", _taxa(vaga.get("taxa_inscricao"))),
    ]
    grade = "".join(
        f'<div class="c"><div class="r">{escape(r)}</div><div class="v">{escape(v)}</div></div>' for r, v in campos
    )

    requisitos = _resumir(vaga.get("requisitos"), 100)
    rodape_card = f"Banca {escape(vaga.get('banca_organizadora') or NAO_INFORMADO)}"
    if requisitos != NAO_INFORMADO:
        rodape_card += f" · {escape(requisitos)}"

    estilo = """
.card{margin-top:32px;background:#fff;border-radius:18px;border:1px solid #e2e8f8;
border-left:8px solid var(--cor);padding:30px 34px;box-shadow:0 14px 30px rgba(15,30,61,.06)}
.card .linha1{display:flex;justify-content:space-between;align-items:center}
.card .local{font-size:22px;font-weight:800;color:#2f6fed}
.card .selo{padding:9px 20px;border-radius:999px;font-size:17px;font-weight:800;letter-spacing:.3px;color:var(--cor);background:var(--fundo)}
.card h2{margin:14px 0 2px;font-size:36px;font-weight:800;color:#0f1e3d}
.card .sub{font-size:19px;font-weight:500;color:#5a6789}
.gradec{margin-top:22px;display:grid;grid-template-columns:1fr 1fr;gap:12px 16px}
.gradec .c{background:#f3f6fd;border-radius:12px;padding:14px 18px}
.gradec .r{font-size:13px;font-weight:700;letter-spacing:.4px;color:#5a6789}
.gradec .v{margin-top:3px;font-size:21px;font-weight:800;color:#0f1e3d}
.rodapec{margin-top:16px;font-size:16px;font-weight:600;color:#5a6789}
.deslize{margin-top:30px;font-size:23px;font-weight:800;color:#2f6fed;display:flex;align-items:center;gap:10px}
.deslize .barra{margin-top:6px;width:210px;height:5px;border-radius:3px;background:#8fb6fb}
"""
    corpo = f"""{_cabecalho(1)}
<div class="eyebrow">{escape(eyebrow)}</div>
<h1 style="font-size:{tam}px"><span class="navy">{escape(linha1)}</span><br><span class="azul">{escape(linha2)}</span></h1>
<div class="divisor"></div>
<div class="card" style="--cor:{cor};--fundo:{fundo}">
  <div class="linha1"><div class="local">{escape(vaga['municipio'])}/{escape(vaga['uf'])}</div><div class="selo">{escape(selo)}</div></div>
  <h2>{escape(vaga['cargo'])}</h2>
  <div class="sub">{escape(tipo_rotulo)}{escape(edital)}</div>
  <div class="gradec">{grade}</div>
  <div class="rodapec">{rodape_card}</div>
</div>
<div class="empurra"><div class="deslize">DESLIZE PARA O LADO →</div><div class="barra"></div></div>"""
    return _html(estilo, corpo)


# ---------------------------------------------------------------------------
# Slide 2 — valor-hora


def _slide2(vaga: dict[str, Any]) -> str:
    campos = [
        ("VALOR-HORA REAL", formatar_valor_hora(vaga)),
        ("CARGA HORÁRIA", vaga.get("carga_horaria") or NAO_INFORMADO),
        ("TIPO", ROTULOS_TIPO.get(vaga.get("tipo_oportunidade") or "", NAO_INFORMADO)),
        ("BANCA", vaga.get("banca_organizadora") or NAO_INFORMADO),
    ]
    grade = "".join(
        f'<div class="c"><div class="r">{escape(r)}</div><div class="v">{escape(v)}</div></div>' for r, v in campos
    )
    estilo = """
.gradec{margin-top:38px;display:grid;grid-template-columns:1fr 1fr;gap:16px 20px}
.gradec .c{background:#fff;border:1px solid #e2e8f8;border-radius:14px;padding:20px 24px}
.gradec .r{font-size:15px;font-weight:700;letter-spacing:.4px;color:#5a6789}
.gradec .v{margin-top:5px;font-size:28px;font-weight:800;color:#0f1e3d}
.box{margin-top:26px;background:#e3ecfd;border-radius:16px;padding:26px 30px}
.box h3{margin:0 0 10px;font-size:23px;font-weight:800;color:#0f1e3d}
.box p{margin:0;font-size:19px;line-height:1.5;color:#33436f;font-weight:500}
"""
    corpo = f"""{_cabecalho(2)}
<div class="eyebrow">O QUE PESA NA DECISÃO</div>
<h1 style="font-size:56px"><span class="navy">VALE MAIS QUE</span><br><span class="navy">PARECE </span><span class="azul">À PRIMEIRA VISTA</span></h1>
<div class="divisor"></div>
<div class="gradec">{grade}</div>
<div class="box"><h3>POR QUE REPARAR NO VALOR-HORA</h3>
<p>Duas vagas com o mesmo salário mensal podem ter cargas horárias bem diferentes. O valor-hora mostra o que realmente compensa.</p></div>
<div class="rodape-num empurra"><div class="n">02</div><div class="barra"></div></div>"""
    return _html(estilo, corpo)


# ---------------------------------------------------------------------------
# Slide 3 — checklist fixo (não muda por vaga)


def _slide3() -> str:
    itens = [
        ("doc", "Leia os <b>requisitos</b>: especialização e registro no CRM"),
        ("agenda", "Anote <b>prazo, taxa e data da prova</b>"),
        ("escudo", "Toda vaga no MedVagas traz o <b>link da fonte oficial</b>"),
    ]
    lista = "".join(
        f'<div class="item"><div class="ic">{_icone(ic)}</div><div class="tx">{tx}</div></div>' for ic, tx in itens
    )
    estilo = """
.item{margin-top:26px;display:flex;align-items:flex-start;gap:20px}
.item .ic{flex:none;width:52px;height:52px;border-radius:14px;background:#e3ecfd;display:flex;align-items:center;justify-content:center}
.item .tx{font-size:24px;font-weight:500;line-height:1.35;color:#0f1e3d;padding-top:6px}
.item .tx b{font-weight:800}
.box{margin-top:38px;background:#e3ecfd;border-radius:16px;padding:26px 30px}
.box h3{margin:0 0 10px;font-size:23px;font-weight:800;color:#0f1e3d}
.box p{margin:0;font-size:19px;line-height:1.5;color:#33436f;font-weight:500}
"""
    corpo = f"""{_cabecalho(3)}
<div class="eyebrow">ANTES DE SE INSCREVER</div>
<h1 style="font-size:60px"><span class="navy">CONFIRA SEMPRE O</span><br><span class="azul">EDITAL OFICIAL</span></h1>
<div class="divisor"></div>
{lista}
<div class="box"><h3>INFORMAÇÃO PÚBLICA</h3>
<p>Dados do edital conferidos na data da publicação. Valores e prazos podem mudar por retificação.</p></div>
<div class="rodape-num empurra"><div class="n">03</div><div class="barra"></div></div>"""
    return _html(estilo, corpo)


# ---------------------------------------------------------------------------
# Slide 4 — CTA + preview de outras vagas


def _mini_card(vaga: dict[str, Any]) -> str:
    cargo = escape(vaga["cargo"])
    local = f"{escape(vaga['municipio'])}/{escape(vaga['uf'])}"
    orgao = escape(vaga.get("orgao") or NAO_INFORMADO)
    partes = []
    if vaga.get("numero_edital"):
        partes.append(f"Edital {escape(vaga['numero_edital'])}")
    partes.append(escape(formatar_salario(vaga)))
    if vaga.get("numero_vagas"):
        partes.append(f"{vaga['numero_vagas']} vaga(s)")
    partes.append(f"Inscrições até {escape(formatar_data(vaga.get('inscricoes_fim')))}")
    linha = " · ".join(partes)
    return f"""<div class="mini">
  <div class="mlinha1"><div class="mlocal">{local}</div><div class="mselo">INSCRIÇÕES ABERTAS</div></div>
  <div class="mcargo">{cargo}</div>
  <div class="morgao">{orgao}</div>
  <div class="mdados">{linha}</div>
</div>"""


def _slide4(vagas_extra: list[dict[str, Any]]) -> str:
    minis = "".join(_mini_card(v) for v in vagas_extra[:2])
    estilo = """
.browser{margin-top:34px;background:#fff;border-radius:16px;border:1px solid #e2e8f8;overflow:hidden;box-shadow:0 14px 30px rgba(15,30,61,.06)}
.browser .barra-topo{padding:14px 18px;display:flex;gap:8px;background:#f3f6fd;border-bottom:1px solid #e2e8f8}
.browser .bolha{width:13px;height:13px;border-radius:50%}
.mini{padding:20px 26px;border-bottom:1px solid #eef1fa}
.mini:last-child{border-bottom:none}
.mlinha1{display:flex;justify-content:space-between;align-items:center}
.mlocal{font-size:15px;font-weight:800;letter-spacing:.4px;color:#2f6fed;text-transform:uppercase}
.mselo{font-size:13px;font-weight:800;color:#1d7a46;background:#e2f4e8;border-radius:999px;padding:5px 14px}
.mcargo{margin-top:6px;font-size:21px;font-weight:800;color:#0f1e3d}
.morgao{font-size:16px;font-weight:500;color:#5a6789}
.mdados{margin-top:6px;font-size:15px;font-weight:600;color:#41507f}
.chamada{margin-top:26px;font-size:19px;line-height:1.5;font-weight:500;color:#33436f}
.cta{margin-top:24px;background:#0f1e3d;color:#fff;border-radius:16px;padding:24px;text-align:center;
font-size:26px;font-weight:800;letter-spacing:.3px}
.preco{margin-top:16px;text-align:center;font-size:19px;font-weight:700;color:#41507f}
"""
    corpo = f"""{_cabecalho(4)}
<div class="eyebrow">TODA SEMANA TEM UMA NOVA</div>
<h1 style="font-size:60px"><span class="navy">ESSA É SÓ</span><br><span class="azul">UMA DAS VAGAS</span></h1>
<div class="divisor"></div>
<div class="browser">
  <div class="barra-topo"><div class="bolha" style="background:#f36457"></div><div class="bolha" style="background:#f4bd4f"></div><div class="bolha" style="background:#3fca63"></div></div>
  {minis}
</div>
<div class="chamada">Todo dia útil entram vagas novas de médico em MG e SP. Configure seus filtros e não perca a próxima.</div>
<div class="empurra">
  <div class="cta">VER TODAS AS VAGAS &nbsp;→&nbsp; LINK NA BIO</div>
  <div class="preco">{escape(PRECO_INICIAL)}</div>
</div>"""
    return _html(estilo, corpo)


# ---------------------------------------------------------------------------


def montar_htmls(vaga: dict[str, Any], tipo: str, hoje: date, vagas_extra: list[dict[str, Any]]) -> list[str]:
    """4 HTMLs, um por slide do carrossel, nessa ordem."""
    return [_slide1(vaga, tipo, hoje), _slide2(vaga), _slide3(), _slide4(vagas_extra)]


def renderizar_png(html: str, destino: Path) -> Path:
    """Renderiza 1 HTML em PNG (precisa de `playwright` instalado)."""
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


def renderizar_pngs(htmls: list[str], base: Path) -> list[Path]:
    """Renderiza os N HTMLs em `<base-sem-sufixo>-1.png`, `-2.png`, ... (mesma pasta/prefixo)."""
    from playwright.sync_api import sync_playwright

    base.parent.mkdir(parents=True, exist_ok=True)
    prefixo = base.with_suffix("")
    destinos = [Path(f"{prefixo}-{i}.png") for i in range(1, len(htmls) + 1)]
    with sync_playwright() as pw:
        try:
            navegador = pw.chromium.launch()
        except Exception:
            navegador = pw.chromium.launch(channel="chrome")
        pagina = navegador.new_page(viewport={"width": LARGURA, "height": ALTURA})
        for html, destino in zip(htmls, destinos):
            pagina.set_content(html, wait_until="networkidle")
            pagina.evaluate("document.fonts.ready")
            pagina.screenshot(path=str(destino), type="png")
        navegador.close()
    return destinos
