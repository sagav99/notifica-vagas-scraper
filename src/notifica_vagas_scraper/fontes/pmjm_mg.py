"""Parser da Prefeitura de João Monlevade/MG (site próprio,
`pmjm.mg.gov.br` — sem banca organizadora, seleção conduzida direto pela
Secretaria Municipal de Saúde). Roda no CMS "XFind.inc" — parsing
genérico compartilhado com outros clientes desse CMS vive em
`fontes/xfind_cms.py` (ver docstring de lá pra achado de template/coluna
por cliente); este módulo só declara o que é específico de João
Monlevade (domínio, prefixo de identificador) e delega.

Investigado pelo `pesquisador-fonte` (fixtures reais em
`docs/fixtures/pmjm_mg/` no repo principal: listagem completa de
`/concursos-publicos`, a página de detalhe do Edital 12/2026 — Médico
Plantonista Ortopedista —, o PDF escaneado do edital e o PDF de texto real
do "Resultado Preliminar" do mesmo processo, usado só pra conferência
cruzada). Achados:

- `GET /concursos-publicos` devolve, numa única página SEM paginação real
  (DataTables client-side, não AJAX), o histórico INTEIRO de processos
  seletivos/concursos desde 2011 — mais de 1000 linhas confirmadas na
  fixture. Cada linha tem: número/ano do edital (coluna "Nº/ Ano"),
  categoria ("Processos Seletivos" ou "Concurso"), súmula/título, data de
  publicação, e um link `<a href="https://www.pmjm.mg.gov.br/concursos_view/
  <id>">` pra página de detalhe — `<id>` é sequencial e estável, é a chave
  de descoberta de processo novo. Sem bloqueio anti-bot em nenhuma parte
  do site, `requests` simples resolve tudo. Diferente de outros clientes
  do mesmo CMS (ex. Bocaiúva), esta listagem não tem coluna "Área".
- Como a listagem cobre TODO o histórico (não só os vigentes), reprocessar
  cega todo item de saúde a cada cron seria caro à toa (PDF + chamada
  Gemini pra ~130 processos de saúde acumulados, repetido a cada 3 dias,
  disputando a mesma cota diária compartilhada por todas as fontes) —
  `db.listar_identificadores_processados` traz os `identificador_externo`
  já gravados pra esta fonte, e `extrair_ids_processados` decodifica o
  `<id>` de processo embutido em cada um (prefixo `pmjm-mg-<id>-`), pra
  `scripts/rodar_pmjm_mg.py` só baixar detalhe/PDF/chamar Gemini pros IDs
  genuinamente novos. Nunca é o `on conflict do nothing` de
  `inserir_vaga_com_evidencia` que faz esse corte (esse só evita duplicar
  linha, não evita o custo de rede/IA de reprocessar um item antigo).
- Cada processo normalmente tem 1 único cargo (diferente de fontes tipo
  FGV/Instituto Mais, que multiplexam dezenas de cargos no mesmo edital) —
  o risco aqui não é "descartar especialidade dentro do mesmo edital", é
  "não descobrir processo novo". `eh_cargo_saude` por isso é
  deliberadamente permissivo (ver `xfind_cms._RE_CARGO_SAUDE`).
- A página de detalhe (`concursos_view/<id>`) repete número/categoria/
  súmula/data da listagem numa tabela própria (`#tb_concursos`) e lista os
  documentos anexos numa segunda tabela (`#tb_anexos_concursos` — colunas
  Tipo/Descrição/Data/Arquivo). Achado real do Edital 12/2026: 3 anexos,
  todos com `Tipo="Edital"` (o campo "Tipo" NÃO distingue edital de
  abertura de resultado) — "Edital 12-2026 Médico Plantonista -
  ORTOPEDISTA" (09/07, o documento de abertura), "Resultado Preliminar PS
  12-2026..." (21/07) e "Resultado Final PS 12-2026..." (27/07). Só a
  DESCRIÇÃO diferencia — `escolher_pdf_edital` exclui qualquer anexo cujo
  Tipo OU Descrição batam com resultado/homologação/classificação/
  convocação/recurso (documento que sai depois e não é fonte confiável de
  cargo de ABERTURA) e, entre os candidatos restantes, pega o de data mais
  recente (cobre o caso de uma retificação publicada depois do edital
  original).
- **O PDF do Edital 12/2026 é escaneado/imagem, sem camada de texto
  extraível** (`pypdf`/`pdfplumber` devolvem string vazia pra sua única
  página) — diferente de Mauá/SP, onde o PDF tinha texto real só com
  colunas embaralhadas. Isso NÃO exige nenhum modo especial de
  `gemini_pdf`: a API do Gemini pra documento (`inline_data` com
  `mime_type="application/pdf"`, já usada por `gemini_pdf.
  extrair_vagas_de_pdf`) processa o PDF visualmente por padrão
  (renderiza cada página como imagem internamente), então já lê PDF
  escaneado sem qualquer mudança de código — só não dá pra extrair
  `taxa_inscricao`/tabela por regex determinístico como fallback (não tem
  texto pra regexar), então este parser depende 100% do Gemini pro
  conteúdo do PDF, sem nenhuma extração determinística paralela (diferente
  de `maua.extrair_salario_por_hora_uniforme`, que só existe porque o PDF
  de lá tem texto real).
"""

from __future__ import annotations

from . import xfind_cms as _cms

BASE_URL = "https://www.pmjm.mg.gov.br"
URL_LISTAGEM = f"{BASE_URL}/concursos-publicos"

#: fonte é dedicada a 1 único município — sem necessidade de casar título
#: contra catálogo (diferente de fontes multi-município tipo FGV/Instituto
#: Mais).
MUNICIPIO = "João Monlevade"
UF = "MG"

#: usado tanto por `scripts/rodar_pmjm_mg.py` (como `id_prefix` de
#: `processamento_pdf_gemini.processar_pdf_e_gravar_vagas`, que monta
#: `identificador_externo=f"{id_prefix}-{processo_id}-{slug_cargo}"`)
#: quanto por `extrair_ids_processados` (decodifica o `<id>` de volta) —
#: 1 constante só, pra nunca dessincronizar os dois lados.
ID_PREFIX = "pmjm-mg"

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
