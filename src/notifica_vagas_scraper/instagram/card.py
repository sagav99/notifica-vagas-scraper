"""Carrossel de 5 slides do post (HTML/CSS -> PNG via Playwright).

Layout aprovado pelo usuário em 2026-09-29, cópia de referência em
`docs/referencia_visual_instagram/` (não reabrir sem motivo novo):
slide 1 "VAGA ABERTA" (resumo da vaga), slide 2 "O QUE PESA NA DECISÃO"
(valor-hora), slide 3 "ANTES DE SE INSCREVER" (checklist fixo do edital) e
slides 3-5 fixos (2026-11-26, repaginada de conversão): "TEM DEZENAS DESTAS ABERTAS", "TUDO JUNTO. TUDO PRONTO." e oferta VIP.

Todo campo ausente vira "Não informado" — nunca inventa dado.
"""

from __future__ import annotations

import re
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

from .logo import marca

LARGURA = 1080
ALTURA = 1350
NAO_INFORMADO = "Não informado"
SITE = "medvagasapp.com.br"

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


def _brl_titulo(valor: Any) -> str:
    """9833.0 -> "9.833" (headline); com centavos reais mantém "9.833,50"."""
    v = float(valor)
    return f"{v:,.0f}".replace(",", ".") if v == int(v) else _brl(v).replace("R$ ", "")


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
_COMECA_COM_PREPOSICAO = re.compile(r"^(d[oae]s?|em|n[oa]s?)\b", re.IGNORECASE)


def especialidade_curta(cargo: str) -> str:
    """"Médico Cardiologista" -> "Cardiologista"; sem prefixo reconhecido, devolve o cargo inteiro."""
    txt = " ".join(str(cargo).split())
    resto = _PREFIXO_MEDICO.sub("", txt).strip()
    if resto.startswith("(") and resto.endswith(")") and resto.count("(") == 1:
        resto = resto[1:-1].strip()  # "Médico (Intervencionista/Regulador)" -> "Intervencionista/Regulador"
    if not resto or _COMECA_COM_PREPOSICAO.match(resto):  # "Médico do Programa..." não vira "do Programa..."
        return txt
    return resto


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
    return 128 if n <= 14 else 110 if n <= 18 else 92 if n <= 24 else 78 if n <= 30 else 64 if n <= 40 else 52


_ICONES = {
    "doc": '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M9.5 12h5M9.5 16h5"/>',
    "agenda": '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M8 3v4M16 3v4"/>',
    "escudo": '<path d="M12 3l7 3v6c0 5-3 8.5-7 9-4-.5-7-4-7-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "pasta": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "relogio": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "mapa": '<path d="M12 21s7-6.2 7-11a7 7 0 0 0-14 0c0 4.8 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>',
    "cadeado": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "seta": '<path d="M5 12h14M13 6l6 6-6 6"/>',
}


def _icone(nome: str, cor: str = "#2f6fed", tamanho: int = 26) -> str:
    return (
        f'<svg width="{tamanho}" height="{tamanho}" viewBox="0 0 24 24" fill="none" stroke="{cor}" '
        f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{_ICONES[nome]}</svg>'
    )


def _estilo_base() -> str:
    return f"""*{{box-sizing:border-box}}body{{margin:0;width:{LARGURA}px;height:{ALTURA}px;overflow:hidden;
background:linear-gradient(180deg,#f7faff,#e9f0fc);font-family:'Montserrat','Helvetica Neue',Arial,sans-serif;color:#0f1e3d;position:relative}}
.canto1{{position:absolute;top:0;right:0;width:460px;height:330px;
background:linear-gradient(135deg,#9ccbff,#58b6fd 60%,#4a90f0);clip-path:polygon(38% 0,100% 0,100% 62%);opacity:.9}}
.canto1b{{position:absolute;top:0;right:0;width:250px;height:190px;
background:linear-gradient(135deg,#215dca,#012566);clip-path:polygon(55% 0,100% 0,100% 74%)}}
.canto1c{{position:absolute;top:150px;right:0;width:230px;height:520px;
background:linear-gradient(180deg,rgba(88,182,253,.22),rgba(88,182,253,.05));clip-path:polygon(100% 0,100% 100%,0 50%)}}
.canto2{{position:absolute;bottom:0;right:0;width:400px;height:170px;
background:linear-gradient(90deg,#215dca,#58b6fd);clip-path:polygon(100% 0,100% 100%,20% 100%)}}
.canto2b{{position:absolute;bottom:0;right:0;width:330px;height:120px;
background:linear-gradient(90deg,#011951,#012a6e);clip-path:polygon(100% 45%,100% 100%,0 100%)}}
.wrap{{position:relative;padding:56px 70px 60px;height:100%;display:flex;flex-direction:column}}
.empurra{{margin-top:auto}}
.topo{{display:flex;justify-content:space-between;align-items:center}}
.logo{{display:flex;align-items:center;gap:12px;font-weight:800;font-size:36px;letter-spacing:-.5px}}
.logo .a{{color:#0f1e3d}}.logo .b{{color:#2f6fed}}
.badge-n{{padding:10px 22px;border-radius:999px;font-size:22px;font-weight:700;color:#fff;background:rgba(1,25,81,.88);position:relative}}
.eyebrow{{margin-top:46px;font-size:24px;font-weight:800;letter-spacing:1.5px;color:#4a9ae8;text-transform:uppercase}}
h1{{font-family:'Barlow Condensed','Arial Narrow',sans-serif;margin:10px 0 0;line-height:.96;font-weight:800;
letter-spacing:-1px;text-transform:uppercase}}
h1 .navy{{color:#0f1e3d}}h1 .azul{{color:#2f6fed}}h1{{margin-bottom:0}}
.divisor{{margin-top:22px;width:190px;height:5px;border-radius:3px;background:#8fb6fb}}
.rodape-num{{display:flex;align-items:center;gap:16px}}
.rodape-num .n{{font-size:26px;font-weight:800;color:#2f6fed}}
.rodape-num .barra{{width:210px;height:5px;border-radius:3px;background:#c9d8fb}}
.site{{position:absolute;right:70px;bottom:50px;font-size:18px;font-weight:700;color:#41507f}}
"""


def _cabecalho(numero: int, total: int = 4) -> str:
    return (
        f'<div class="topo">{marca()}<div class="badge-n">{numero}/{total}</div></div>'
    )


def _html(corpo_estilo: str, corpo_html: str) -> str:
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;600;700;800&family=Barlow+Condensed:wght@700;800&display=swap" rel="stylesheet">
<style>{_estilo_base()}{corpo_estilo}</style></head><body>
<div class="canto1c"></div><div class="canto1"></div><div class="canto1b"></div><div class="canto2"></div><div class="canto2b"></div>
<div class="wrap">{corpo_html}</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Slide 1 — resumo da vaga


def _slide1(vaga: dict[str, Any], tipo: str, hoje: date) -> str:
    urgente = tipo == "fim_prazo"
    eyebrow = "VAGA COM PRAZO ACABANDO" if urgente else "VAGA ABERTA"
    cor, fundo = ("#b3261e", "#fde8e6") if urgente else ("#1d7a46", "#e2f4e8")
    selo = rotulo_prazo(vaga.get("inscricoes_fim"), hoje).upper() if urgente else "INSCRIÇÕES ABERTAS"
    fim = vaga.get("inscricoes_fim")
    dias_fim = (fim - hoje).days if fim else None
    alerta = None
    if urgente and dias_fim is not None and 0 <= dias_fim <= 3:
        alerta = {0: "ENCERRA HOJE!", 1: "ENCERRA AMANHÃ!"}.get(dias_fim, f"ENCERRA EM {dias_fim} DIAS!")

    titulo = especialidade_curta(vaga["cargo"]).upper()
    if vaga.get("salario") is not None:
        sufixo = ROTULOS_SALARIO.get(vaga.get("salario_tipo") or "mensal", "MÊS")
        linha1, linha2 = f"{titulo} COM", f"R$ {_brl_titulo(vaga['salario'])}/{sufixo}"
    else:
        linha1, linha2 = titulo, "INSCRIÇÕES ABERTAS"
    tam = _tamanho_titulo(max(linha1, linha2, key=len))

    tipo_rotulo = ROTULOS_TIPO.get(vaga.get("tipo_oportunidade") or "", NAO_INFORMADO)
    edital = f" · Edital {vaga['numero_edital']}" if vaga.get("numero_edital") else ""

    campos = [
        ("REMUNERAÇÃO", formatar_salario(vaga).replace("/mensal", "/mês")),
        ("INSCRIÇÕES ATÉ", formatar_data(vaga.get("inscricoes_fim"))),
        ("CARGA HORÁRIA", vaga.get("carga_horaria") or NAO_INFORMADO),
        ("VALOR-HORA", formatar_valor_hora(vaga)),
        ("DATA DA PROVA", _data_prova(vaga)),
        ("TAXA", _taxa(vaga.get("taxa_inscricao"))),
    ]
    grade = "".join(
        f'<div class="c"><div class="r">{escape(r)}</div><div class="v">{escape(v)}</div></div>' for r, v in campos
    )

    requisitos_texto = " ".join((vaga.get("requisitos") or "").split())
    if len(requisitos_texto) > 100 and ";" in requisitos_texto:
        requisitos_texto = requisitos_texto.split(";")[0].strip()  # corta na 1ª oração, não no meio de um parêntese
    requisitos = _resumir(requisitos_texto, 100)
    rodape_card = f"Banca {escape(vaga.get('banca_organizadora') or NAO_INFORMADO)}"
    if requisitos != NAO_INFORMADO:
        rodape_card += f" · {escape(requisitos)}"

    estilo = """
.card{margin-top:30px;flex:1;min-height:0;display:flex;flex-direction:column;margin-bottom:24px;background:#fff;border-radius:18px;border:1px solid #e2e8f8;
border-left:8px solid var(--cor);padding:30px 34px;box-shadow:0 14px 30px rgba(15,30,61,.06)}
.card .linha1{display:flex;justify-content:space-between;align-items:center}
.card .local{font-size:22px;font-weight:800;color:#2f6fed}
.card .selo{padding:9px 20px;border-radius:999px;font-size:17px;font-weight:800;letter-spacing:.3px;color:var(--cor);background:var(--fundo)}
.card h2{margin:14px 0 2px;font-family:'Barlow Condensed',sans-serif;font-size:58px;line-height:1;font-weight:800;color:#0f1e3d}
.card .sub{margin-top:8px;font-size:21px;font-weight:500;color:#5a6789}
.gradec{margin-top:24px;flex:1;display:grid;grid-template-columns:1fr 1fr;grid-auto-rows:1fr;gap:14px 16px}
.gradec .c{background:#f0f5fd;border-radius:14px;padding:16px 20px;display:flex;flex-direction:column;justify-content:center}
.gradec .r{font-size:15px;font-weight:700;letter-spacing:.5px;color:#5a6789}
.gradec .v{margin-top:4px;font-family:'Barlow Condensed',sans-serif;font-size:40px;line-height:1.05;font-weight:700;color:#0f1e3d}
.rodapec{margin-top:18px;font-size:18px;font-weight:600;color:#5a6789}
.deslize{margin-top:0;font-size:23px;font-weight:800;color:#2f6fed;display:flex;align-items:center;gap:10px}
.deslize .barra{margin-top:6px;width:210px;height:5px;border-radius:3px;background:#8fb6fb}
.alerta{margin-top:26px;width:fit-content;display:flex;align-items:center;gap:16px;padding:14px 30px;border-radius:16px;
background:linear-gradient(180deg,#e53b32,#c0261f);color:#fff;font-size:54px;line-height:1;font-weight:800;letter-spacing:.5px;box-shadow:0 10px 24px rgba(192,38,31,.35)}
.selos{margin-top:20px;display:flex;gap:14px;align-items:center;flex-wrap:nowrap}
.pilula{width:fit-content;border-radius:999px;padding:10px 22px;font-size:27px;font-weight:800;white-space:nowrap}
.zap{margin-top:18px;width:fit-content;background:#0f1e3d;color:#fff;border-radius:999px;padding:10px 24px;font-size:21px;font-weight:700}
"""
    cabecalho_titulo = f'<div class="eyebrow">{escape(eyebrow)}</div>'
    pilula_residencia = (
        '<div class="pilula" style="background:#e2f4e8;color:#1d7a46;border:3px solid #1d7a46">✓ NÃO PRECISA DE RESIDÊNCIA</div>'
        if vaga.get("sem_residencia") else ""
    )
    pilula_alerta = (
        '<div class="pilula" style="background:linear-gradient(180deg,#e53b32,#c0261f);color:#fff;border:3px solid #c0261f;'
        f'box-shadow:0 6px 16px rgba(192,38,31,.3)">⚠ {escape(alerta)}</div>'
        if alerta else ""
    )
    faixa_selos = f'<div class="selos">{pilula_alerta}{pilula_residencia}</div>' if (alerta or pilula_residencia) else ""
    corpo = f"""{_cabecalho(1, 5)}
{cabecalho_titulo}
<h1 style="font-size:{tam}px"><span class="navy">{escape(linha1)}</span><br><span class="azul">{escape(linha2)}</span></h1>
{faixa_selos}<div class="divisor"></div>
<div class="card" style="--cor:{cor};--fundo:{fundo}">
  <div class="linha1"><div class="local">{escape(vaga['municipio'])}/{escape(vaga['uf'])}</div><div class="selo">{escape(selo)}</div></div>
  <h2>{escape(vaga['cargo'])}</h2>
  <div class="sub">{escape(tipo_rotulo)}{escape(edital)}</div>
  <div class="gradec">{grade}</div>
  <div class="rodapec">{rodape_card}</div>
</div>
<div class="empurra"><div class="deslize">DESLIZE PARA O LADO →</div><div class="barra"></div><div class="zap">↓ Tem mais: assine pelo link da bio</div></div>"""
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
.gradec{margin-top:38px;flex:1;display:grid;grid-template-columns:1fr 1fr;grid-auto-rows:1fr;gap:18px 20px}
.gradec .c{background:#fff;border:1px solid #dfe8f8;border-radius:16px;padding:22px 26px;display:flex;flex-direction:column;justify-content:center;box-shadow:0 10px 24px rgba(1,25,81,.05)}
.gradec .c:first-child{border-left:8px solid #2f6fed}
.gradec .r{font-size:17px;font-weight:700;letter-spacing:.5px;color:#5a6789}
.gradec .v{margin-top:6px;font-family:'Barlow Condensed',sans-serif;font-size:56px;line-height:1;font-weight:800;color:#0f1e3d}
.box{margin-top:26px;margin-bottom:30px;background:#e3ecfd;border-radius:16px;padding:26px 30px}
.box h3{margin:0 0 10px;font-size:23px;font-weight:800;color:#0f1e3d}
.box p{margin:0;font-size:21px;line-height:1.5;color:#33436f;font-weight:500}
"""
    corpo = f"""{_cabecalho(2, 5)}
<div class="eyebrow">INFORMAÇÕES</div>
<h1 style="font-size:116px"><span class="navy">DETALHES</span><br><span class="azul">DA VAGA</span></h1>
<div class="divisor"></div>
<div class="gradec">{grade}</div>
<div class="box"><h3>POR QUE REPARAR NO VALOR-HORA</h3>
<p>Duas vagas com o mesmo salário mensal podem ter cargas horárias bem diferentes. O valor-hora mostra o que realmente compensa.</p></div>
<div class="rodape-num empurra"><div class="n">02</div><div class="barra"></div></div>"""
    return _html(estilo, corpo)


# ---------------------------------------------------------------------------
# Slides 3, 4 e 5 — fixos (não mudam por vaga): desejo -> prova do produto -> oferta.
# Gatilhos: curiosidade (cards borrados), perda/urgência (prazo), conveniência, ancoragem (R$/dia),
# escassez (preço de lançamento), CTA único (link da bio). Nunca a palavra "PDF" nem número inventado.

PRECO_VIP = "29,99"
_ASSETS = Path(__file__).parent / "assets"


def _img64(nome: str) -> str:
    import base64

    return "data:image/png;base64," + base64.b64encode((_ASSETS / nome).read_bytes()).decode()


_CORES_ESP = ["#d92d20", "#d63384", "#f5a623", "#2f6fed", "#16a34a", "#8d6e63"]


def _slide3(vagas_extra: list[dict[str, Any]]) -> str:
    # Outras vagas abertas de verdade (as mesmas do banco), escritas normalmente: prova de que há mais.
    cards = "".join(
        f"""<div class="lk" style="border-left-color:{_CORES_ESP[i % len(_CORES_ESP)]}">
  <div class="lk-t"><span class="lk-esp">{escape(especialidade_curta(v["cargo"]).upper())}</span><span class="lk-l">{escape(v["municipio"])}/{escape(v["uf"])}</span></div>
  <div class="lk-s">{escape(formatar_salario(v).replace("/mensal", "/mês"))} · inscrições até {escape(formatar_data(v.get("inscricoes_fim")))}</div>
  <div class="lk-ab">ABERTA</div>
</div>"""
        for i, v in enumerate(vagas_extra[:4])
    )
    estilo = """
.lista3{margin-top:34px;display:flex;flex-direction:column;gap:16px}
.lk{position:relative;background:#fff;border:1px solid #dfe8f8;border-left:10px solid #2f6fed;border-radius:16px;padding:22px 170px 22px 28px;box-shadow:0 10px 24px rgba(1,25,81,.06)}
.lk-t{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.lk-esp{font-family:'Barlow Condensed',sans-serif;font-size:46px;font-weight:800;line-height:1;color:#0f1e3d}
.lk-l{font-size:24px;font-weight:800;color:#2f6fed}
.lk-s{margin-top:8px;font-size:24px;font-weight:700;color:#41507f}
.lk-ab{position:absolute;right:26px;top:50%;transform:translateY(-50%);background:#e2f4e8;color:#1d7a46;border-radius:999px;padding:8px 18px;font-size:18px;font-weight:800;letter-spacing:.4px}
.mais{margin-top:22px;text-align:center;font-family:'Barlow Condensed',sans-serif;font-size:60px;line-height:1;font-weight:800;color:#2f6fed;text-transform:uppercase}
.sub3{margin-top:14px;text-align:center;font-size:27px;line-height:1.4;font-weight:600;color:#33436f}
.sub3 b{color:#0f1e3d;font-weight:800}
"""
    corpo = f"""{_cabecalho(3, 5)}
<div class="eyebrow">VOCÊ VIU UMA. E AS OUTRAS?</div>
<h1 style="font-size:124px"><span class="navy">TEM DEZENAS</span><br><span class="azul">DESTAS ABERTAS</span></h1>
<div class="divisor"></div>
<div class="lista3">{cards}</div>
<div class="mais">+ dezenas de vagas novas toda semana</div>
<div class="sub3">Médico em <b>MG, SP, PR, SC e RS</b>: a que você procura<br>pode fechar antes de você achar.</div>
<div class="rodape-num empurra"><div class="n">03</div><div class="barra"></div></div>"""
    return _html(estilo, corpo)


def _slide4() -> str:
    itens = [
        ("pasta", "<b>Por especialidade</b>, cada uma com a sua cor"),
        ("relogio", "<b>Salário, prazo e requisitos</b> conferidos no edital"),
        ("mapa", "<b>Rota no Waze</b> e no Google Maps em 1 toque"),
        ("escudo", "<b>Link do edital oficial</b> em cada vaga"),
    ]
    lista = "".join(
        f'<div class="b4"><div class="b4i">{_icone(ic, "#2f6fed", 34)}</div><div class="b4t">{tx}</div></div>' for ic, tx in itens
    )
    estilo = f"""
.mocks{{margin-top:30px;position:relative;height:520px;flex:none}}
.m1{{position:absolute;left:0;top:0;width:740px;border-radius:12px;border:1px solid #cfdaf3;box-shadow:0 22px 44px rgba(1,25,81,.22);transform:rotate(-1.5deg);z-index:2}}
.m2{{position:absolute;right:0;top:296px;width:740px;border-radius:12px;border:1px solid #cfdaf3;box-shadow:0 22px 44px rgba(1,25,81,.28);transform:rotate(1.5deg)}}
.g4{{margin-top:26px;display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.b4{{display:flex;align-items:center;gap:16px;background:#fff;border:1px solid #dfe8f8;border-radius:16px;padding:18px 20px;box-shadow:0 8px 20px rgba(1,25,81,.05)}}
.b4i{{flex:none;width:58px;height:58px;border-radius:16px;background:#e3ecfd;display:flex;align-items:center;justify-content:center}}
.b4t{{font-size:22px;line-height:1.3;font-weight:500;color:#0f1e3d}}.b4t b{{font-weight:800}}
.fim4{{margin-top:22px;text-align:center;font-size:27px;font-weight:800;color:#2f6fed}}
"""
    corpo = f"""{_cabecalho(4, 5)}
<div class="eyebrow">SEM CAÇAR VAGA EM SITE DE PREFEITURA</div>
<h1 style="font-size:116px"><span class="navy">TUDO JUNTO.</span><br><span class="azul">TUDO PRONTO.</span></h1>
<div class="divisor"></div>
<div class="mocks"><img class="m1" src="{_img64('mock_vaga.png')}"><img class="m2" src="{_img64('mock_destaques.png')}"></div>
<div class="g4">{lista}</div>
<div class="fim4">Cada vaga de médico, num só lugar →</div>"""
    return _html(estilo, corpo)


def _slide5() -> str:
    estilo = """
.oferta{margin-top:34px;background:#fff;border:2px solid #dfe8f8;border-radius:24px;padding:28px 30px 26px;text-align:center;box-shadow:0 12px 28px rgba(1,25,81,.08)}
.selo5{display:inline-block;background:#f5c542;color:#3a2a00;border-radius:999px;padding:8px 20px;font-size:21px;font-weight:800;letter-spacing:.6px}
.preco{margin-top:10px;display:flex;align-items:flex-start;justify-content:center;gap:8px;font-family:'Barlow Condensed',sans-serif;font-weight:800;line-height:.9;color:#0f1e3d}
.preco .rs{margin-top:18px;font-size:44px}.preco .v{font-size:140px;letter-spacing:-2px}.preco .m{align-self:flex-end;margin-bottom:14px;font-size:42px;color:#2f6fed}
.dia{margin-top:14px;font-size:25px;font-weight:700;color:#41507f}.dia b{color:#0f1e3d}
.cmd{margin-top:36px;text-align:center}
.cmd .seta{font-size:110px;line-height:1;color:#2f6fed;font-weight:800}
.cmd .t{margin-top:4px;font-family:'Barlow Condensed',sans-serif;font-size:104px;line-height:.95;font-weight:800;text-transform:uppercase;color:#0f1e3d}
.cmd .t span{color:#2f6fed}
.cmd .p{margin-top:18px;font-size:30px;font-weight:700;color:#33436f}
"""
    corpo = f"""{_cabecalho(5, 5)}
<div class="eyebrow">QUER VER TODAS AS VAGAS?</div>
<h1 style="font-size:112px"><span class="navy">SEJA ASSINANTE</span><br><span class="azul">MED VAGAS</span></h1>
<div class="oferta">
  <div class="selo5">PREÇO DE LANÇAMENTO</div>
  <div class="preco"><span class="rs">R$</span><span class="v">{PRECO_VIP}</span><span class="m">/mês</span></div>
  <div class="dia">menos de <b>R$ 1 por dia</b></div>
</div>
<div class="cmd">
  <div class="seta">↑</div>
  <div class="t">CLIQUE NO <span>LINK<br>DA BIO</span></div>
  <div class="p">e assine agora</div>
</div>"""
    return _html(estilo, corpo)


# ---------------------------------------------------------------------------


def montar_htmls(vaga: dict[str, Any], tipo: str, hoje: date, vagas_extra: list[dict[str, Any]]) -> list[str]:
    """5 HTMLs: 1-2 variam por vaga; 3-5 são fixos (desejo, produto, oferta). `vagas_extra` (até 4 outras vagas abertas) alimenta o slide 3."""
    return [_slide1(vaga, tipo, hoje), _slide2(vaga), _slide3(vagas_extra), _slide4(), _slide5()]


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
