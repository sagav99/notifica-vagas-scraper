"""Legenda do post — template determinístico (sem IA)."""

from __future__ import annotations

from datetime import date
from typing import Any

from .card import formatar_data, formatar_salario, rotulo_prazo

SITE = "medvagasapp.com.br"


def _hashtags(vaga: dict[str, Any]) -> str:
    cidade = "".join(ch for ch in (vaga.get("municipio") or "").title() if ch.isalnum())
    uf = (vaga.get("uf") or "").lower()
    tags = ["#concursopublico", "#medico", "#vagasmedicas", "#concursomedico", "#medicina", "#saudepublica"]
    if uf:
        tags.append(f"#concursos{uf}")
        tags.append(f"#medico{uf}")
    if cidade:
        tags.append(f"#{cidade.lower()}")
    return " ".join(tags)


def montar_legenda(vaga: dict[str, Any], tipo: str, hoje: date) -> str:
    local = f"{vaga['municipio']}/{vaga['uf']}"
    if tipo == "fim_prazo":
        abertura = f"⏰ {rotulo_prazo(vaga.get('inscricoes_fim'), hoje).capitalize()}: {vaga['cargo']} — {local}"
    else:
        abertura = f"🩺 Vaga aberta: {vaga['cargo']} — {local}"

    linhas = [abertura, "", f"🏛 {vaga['orgao']}"]
    linhas.append(f"💰 Remuneração: {formatar_salario(vaga)}")
    if vaga.get("numero_vagas"):
        linhas.append(f"👥 Vagas: {vaga['numero_vagas']}")
    linhas.append(f"📅 Início das inscrições: {formatar_data(vaga.get('inscricoes_inicio'))}")
    linhas.append(f"⏳ Fim das inscrições: {formatar_data(vaga.get('inscricoes_fim'))}")
    if vaga.get("data_prova"):
        linhas.append(f"📝 Prova: {formatar_data(vaga['data_prova'])}")
    linhas += [
        "",
        "No Med Vagas toda vaga traz o link da fonte oficial, com todas as informações, sem você precisar ler o PDF inteiro.",
        "Confira sempre o edital oficial antes de se inscrever.",
        f"Conheça o Med Vagas e receba alertas de vagas médicas por e-mail: link na bio ({SITE}).",
        "",
        _hashtags(vaga),
    ]
    return "\n".join(linhas)
