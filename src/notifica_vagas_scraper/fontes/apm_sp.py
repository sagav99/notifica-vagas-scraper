"""Entidades reais confirmadas do Diário Oficial dos Municípios do Estado
de São Paulo (SIGPub, `diariomunicipal.com.br/apm/`) — mesmo motor do
DOM/AMM-MG de MG (ver `fontes/dom_amm_mg.py` e `fontes/sigpub_busca.py`),
plataforma/vendor diferente por trás (Associação Paulista de Municípios).

Achado 2026-09-01: o `<select>` de `/apm/pesquisar` genuinamente só tem 7
municípios reais (o resto é "Selecione" + a própria associação) — não é
limitação de JS/AJAX, é a base de clientes real dessa plataforma em SP
(confirmado também que o PDF de "edição diária" do mesmo motor cobre só
essa mesma base pequena, não todos os municípios de SP — ver
TAREFAS.md). Por isso não precisa de CSV com centenas de linhas nem de
processamento em lote feito o AMM-MG: a lista inteira cabe aqui mesmo.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dom_amm_mg import EntidadeAmmMg

CAMINHO_PESQUISAR = "/apm/pesquisar"

ENTIDADES_APM_SP: list[EntidadeAmmMg] = [
    EntidadeAmmMg(codigo_ibge=3505401, nome="Barra do Turvo", uf="SP", entidade_id="17868"),
    EntidadeAmmMg(codigo_ibge=3506805, nome="Bocaina", uf="SP", entidade_id="1578"),
    EntidadeAmmMg(codigo_ibge=3521507, nome="Irapuã", uf="SP", entidade_id="1287"),
    EntidadeAmmMg(codigo_ibge=3523909, nome="Itu", uf="SP", entidade_id="1369"),
    EntidadeAmmMg(codigo_ibge=3524006, nome="Itupeva", uf="SP", entidade_id="2179"),
    EntidadeAmmMg(codigo_ibge=3530508, nome="Mococa", uf="SP", entidade_id="742"),
    EntidadeAmmMg(codigo_ibge=3553807, nome="Taquarituba", uf="SP", entidade_id="860"),
]


def listar_entidades_apm_sp() -> list[EntidadeAmmMg]:
    return list(ENTIDADES_APM_SP)
