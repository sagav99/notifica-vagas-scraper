import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from notifica_vagas_scraper.fontes import ache_concursos as ache
import rodar_ache_concursos as r


def _item(slug):
    return ache.ItemListagem(titulo="X", url=f"https://www.acheconcursos.com.br/concursos-minas-gerais/{slug}", inscricoes_fim=None, quantidade_vagas=None)


def test_item_ja_processado_pelo_prefixo_do_slug():
    ids = {"ache-prefeitura-de-x-medico"}
    assert r.item_ja_processado(_item("prefeitura-de-x"), ids)
    assert not r.item_ja_processado(_item("prefeitura-de-y"), ids)
    assert not r.item_ja_processado(_item("prefeitura-de"), ids)
