"""Avisa o admin por e-mail (Brevo) qual vaga foi postada, com o link de divulgação pros stories."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import urllib.request
from typing import Any

ORIGEM = "https://medvagasapp.com.br"


def link_divulgacao(vaga_id: str, segredo: str) -> str:
    assinatura = hmac.new(segredo.encode(), f"divulgacao-vaga:{vaga_id}".encode(), hashlib.sha256).digest()
    codigo = base64.urlsafe_b64encode(assinatura).decode().rstrip("=")
    return f"{ORIGEM}/v/{vaga_id}?d={codigo}"


def montar_email(vaga: dict[str, Any], link: str) -> tuple[str, str]:
    titulo = f"{vaga['cargo']} — {vaga['municipio']}/{vaga['uf']}"
    assunto = f"Vaga do dia no Instagram: {titulo}"
    corpo = (
        f"<p>A vaga postada hoje no @medvagasapp foi:</p><p><b>{html.escape(titulo)}</b><br>{html.escape(vaga['orgao'])}</p>"
        f"<p>Link para colocar nos stories:<br><a href=\"{html.escape(link)}\">{html.escape(link)}</a></p>"
    )
    return assunto, corpo


def avisar_admin(vaga: dict[str, Any]) -> bool:
    """Retorna False (sem erro) quando falta configuração; o aviso nunca derruba o post."""
    segredo = os.environ.get("DIVULGACAO_SECRET")
    chave = os.environ.get("BREVO_API_KEY")
    remetente = os.environ.get("BREVO_REMETENTE_EMAIL")
    destino = os.environ.get("ADMIN_EMAIL")
    if not (segredo and chave and remetente and destino):
        print("Aviso ao admin ignorado: faltam DIVULGACAO_SECRET/BREVO_API_KEY/BREVO_REMETENTE_EMAIL/ADMIN_EMAIL.")
        return False
    assunto, corpo = montar_email(vaga, link_divulgacao(str(vaga["id"]), segredo))
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(
            {"sender": {"email": remetente, "name": "Med Vagas"}, "to": [{"email": destino}], "subject": assunto, "htmlContent": corpo}
        ).encode(),
        headers={"api-key": chave, "content-type": "application/json", "accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            return True
    except Exception as erro:  # noqa: BLE001
        print(f"Aviso ao admin falhou: {erro}")
        return False
