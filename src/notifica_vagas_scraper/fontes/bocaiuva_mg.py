"""Parser da Prefeitura de Bocaiúva/MG (site próprio,
`bocaiuva.mg.gov.br` — sem banca organizadora terceirizada). Roda no
mesmo CMS "XFind.inc" de João Monlevade/MG (`fontes/pmjm_mg.py`) —
parsing genérico compartilhado vive em `fontes/xfind_cms.py` (ver
docstring de lá pro detalhe de template/coluna por cliente); este módulo
só declara o que é específico de Bocaiúva e delega.

Investigado pelo `pesquisador-fonte` (fixtures reais em
`docs/fixtures/bocaiuva_mg/` no repo principal: listagem completa de
`/concursos-publicos` e 2 páginas de detalhe — Edital 03/2026 e Edital
06/2025, ambos com "Convocação de médico(s)"). Achados específicos deste
cliente (achados genéricos do CMS foram pra `xfind_cms.py`):

- Mesmo padrão de URL de João Monlevade (`concursos-publicos` /
  `concursos_view/<id>`), mesmo CMS, sem bloqueio anti-bot — mas **2
  diferenças reais de template que exigiram generalizar o parser**
  (documentadas em `xfind_cms.py`, não repetidas aqui): a listagem tem
  coluna "Área" extra (secretaria responsável) que João Monlevade não
  tem, e a página de detalhe usa um layout em cards em vez da tabela
  `#tb_concursos` de João Monlevade.
- **A listagem desta fixture (79 linhas) é bem menor que a de João
  Monlevade (1078 linhas)** — não dá pra saber se é o histórico completo
  de Bocaiúva ou só uma janela mais recente sem investigar de novo
  (fora de escopo aqui); o parser não assume nada sobre o tamanho, só
  processa o que vier.
- **Os 2 processos de saúde/médico confirmados na fixture (Edital
  03/2026 e Edital 06/2025) só têm anexo de "Convocação" recente (até
  24-26/08/2026) — não há evidência, nestas 2 fixtures, de que a
  inscrição do edital de ABERTURA ainda esteja aberta** (o PDF de
  abertura do 03/2026 é de 16/04/2026, o do 06/2025 é de 04/02/2026,
  ambos anteriores à data de "hoje" assumida na investigação). Decisão de
  escopo já registrada em TAREFAS.md (opção a: só edital de abertura com
  inscrição aberta) — este parser continua descobrindo qualquer edital de
  abertura NOVO que apareça daqui pra frente (é o comportamento normal de
  monitoramento contínuo de qualquer fonte já coberta), mas não gera vaga
  aberta retroativa a partir de uma convocação de quem já passou.
- **O PDF do edital de Bocaiúva também é escaneado/sem camada de texto**
  (confirmado via `pypdf` na investigação, mesmo achado de João
  Monlevade) — não exige nenhum código novo, `gemini_pdf.
  extrair_vagas_de_pdf` já lê PDF escaneado visualmente por padrão (ver
  docstring de `pmjm_mg.py`).
"""

from __future__ import annotations

from . import xfind_cms as _cms

BASE_URL = "https://www.bocaiuva.mg.gov.br"
URL_LISTAGEM = f"{BASE_URL}/concursos-publicos"

#: fonte é dedicada a 1 único município — sem necessidade de casar título
#: contra catálogo (diferente de fontes multi-município tipo FGV/Instituto
#: Mais). Nome oficial IBGE tem acento (ver
#: `dados/entidades_amm_mg.csv`), diferente do domínio sem acento.
MUNICIPIO = "Bocaiúva"
UF = "MG"

#: mesmo esquema de `pmjm_mg.ID_PREFIX` — ver docstring de
#: `xfind_cms.extrair_ids_processados`.
ID_PREFIX = "bocaiuva-mg"

__all__ = [
    "BASE_URL",
    "URL_LISTAGEM",
    "MUNICIPIO",
    "UF",
    "ID_PREFIX",
    "ItemListagem",
    "Anexo",
    "DetalheProcesso",
    "listar_processos",
    "eh_cargo_saude",
    "filtrar_processos_saude",
    "extrair_processo_id",
    "extrair_detalhe",
    "escolher_pdf_edital",
    "extrair_ids_processados",
]

ItemListagem = _cms.ItemListagem
Anexo = _cms.Anexo
DetalheProcesso = _cms.DetalheProcesso

extrair_processo_id = _cms.extrair_processo_id
eh_cargo_saude = _cms.eh_cargo_saude
filtrar_processos_saude = _cms.filtrar_processos_saude
escolher_pdf_edital = _cms.escolher_pdf_edital


def listar_processos(html: str) -> list[_cms.ItemListagem]:
    return _cms.listar_processos(html, base_url=BASE_URL)


def extrair_detalhe(html: str) -> _cms.DetalheProcesso:
    return _cms.extrair_detalhe(html, base_url=BASE_URL)


def extrair_ids_processados(identificadores_processados: set[str], *, prefixo: str = ID_PREFIX) -> set[int]:
    return _cms.extrair_ids_processados(identificadores_processados, prefixo=prefixo)
