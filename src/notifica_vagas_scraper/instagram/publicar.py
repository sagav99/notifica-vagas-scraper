"""Hospedagem da imagem + publicação via Instagram Graph API (login do Instagram).

A API exige URL pública da imagem: o PNG vai para a branch `instagram-cards`
deste repo público (API de conteúdo do GitHub, com o GITHUB_TOKEN do
próprio workflow) e é servido por raw.githubusercontent.com, sem redirect.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import requests

GRAPH = "https://graph.instagram.com/v21.0"
BRANCH_IMAGENS = "instagram-cards"
TIMEOUT = 60


class ErroPublicacao(RuntimeError):
    pass


def _gh_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }


def _garantir_branch(repo: str) -> None:
    base = f"https://api.github.com/repos/{repo}"
    if requests.get(f"{base}/git/ref/heads/{BRANCH_IMAGENS}", headers=_gh_headers(), timeout=TIMEOUT).status_code == 200:
        return
    # Branch órfã: commit sem pai, só com um README.
    blob = requests.post(
        f"{base}/git/blobs",
        headers=_gh_headers(),
        json={"content": "Imagens dos posts diários do Instagram (geradas automaticamente).\n", "encoding": "utf-8"},
        timeout=TIMEOUT,
    )
    blob.raise_for_status()
    tree = requests.post(
        f"{base}/git/trees",
        headers=_gh_headers(),
        json={"tree": [{"path": "README.md", "mode": "100644", "type": "blob", "sha": blob.json()["sha"]}]},
        timeout=TIMEOUT,
    )
    tree.raise_for_status()
    commit = requests.post(
        f"{base}/git/commits",
        headers=_gh_headers(),
        json={"message": "chore: cria branch de imagens do Instagram", "tree": tree.json()["sha"], "parents": []},
        timeout=TIMEOUT,
    )
    commit.raise_for_status()
    ref = requests.post(
        f"{base}/git/refs",
        headers=_gh_headers(),
        json={"ref": f"refs/heads/{BRANCH_IMAGENS}", "sha": commit.json()["sha"]},
        timeout=TIMEOUT,
    )
    ref.raise_for_status()


def hospedar_imagem(png: Path, nome: str) -> str:
    """Sobe o PNG para a branch de imagens e devolve a URL pública."""
    repo = os.environ["GITHUB_REPOSITORY"]
    _garantir_branch(repo)
    url = f"https://api.github.com/repos/{repo}/contents/{nome}"
    corpo = {
        "message": f"chore: card do post {nome}",
        "content": base64.b64encode(png.read_bytes()).decode(),
        "branch": BRANCH_IMAGENS,
    }
    existente = requests.get(url, headers=_gh_headers(), params={"ref": BRANCH_IMAGENS}, timeout=TIMEOUT)
    if existente.status_code == 200:
        corpo["sha"] = existente.json()["sha"]
    resposta = requests.put(url, headers=_gh_headers(), json=corpo, timeout=TIMEOUT)
    if resposta.status_code not in (200, 201):
        raise ErroPublicacao(f"Falha ao hospedar imagem ({resposta.status_code}): {resposta.text[:200]}")
    return f"https://raw.githubusercontent.com/{repo}/{BRANCH_IMAGENS}/{nome}"


def _aguardar_pronto(container: str, token: str) -> None:
    for _ in range(20):
        status = requests.get(
            f"{GRAPH}/{container}", params={"fields": "status_code", "access_token": token}, timeout=TIMEOUT
        ).json().get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise ErroPublicacao(f"Container em estado {status}")
        time.sleep(3)
    raise ErroPublicacao("Container não ficou pronto a tempo")


def publicar_carrossel_no_instagram(imagens_url: list[str], legenda: str) -> str:
    """Cria 1 container por imagem (item do carrossel), depois o container CAROUSEL e publica.
    Devolve o id da mídia publicada."""
    if not 2 <= len(imagens_url) <= 10:
        raise ErroPublicacao(f"Carrossel precisa de 2 a 10 imagens, recebeu {len(imagens_url)}")

    ig_user = os.environ["IG_USER_ID"]
    token = os.environ["IG_ACCESS_TOKEN"]

    filhos: list[str] = []
    for url in imagens_url:
        criar = requests.post(
            f"{GRAPH}/{ig_user}/media",
            data={"image_url": url, "is_carousel_item": "true", "access_token": token},
            timeout=TIMEOUT,
        )
        if criar.status_code != 200:
            raise ErroPublicacao(f"Falha ao criar item do carrossel ({criar.status_code}): {criar.text[:300]}")
        item_id = criar.json()["id"]
        _aguardar_pronto(item_id, token)
        filhos.append(item_id)

    criar_carrossel = requests.post(
        f"{GRAPH}/{ig_user}/media",
        data={
            "media_type": "CAROUSEL",
            "children": ",".join(filhos),
            "caption": legenda,
            "access_token": token,
        },
        timeout=TIMEOUT,
    )
    if criar_carrossel.status_code != 200:
        raise ErroPublicacao(f"Falha ao criar container do carrossel ({criar_carrossel.status_code}): {criar_carrossel.text[:300]}")
    container = criar_carrossel.json()["id"]
    _aguardar_pronto(container, token)

    publicar = requests.post(
        f"{GRAPH}/{ig_user}/media_publish",
        data={"creation_id": container, "access_token": token},
        timeout=TIMEOUT,
    )
    if publicar.status_code != 200:
        raise ErroPublicacao(f"Falha ao publicar ({publicar.status_code}): {publicar.text[:300]}")
    return publicar.json()["id"]
