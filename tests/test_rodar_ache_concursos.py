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
    # limitação conhecida: slug que é prefixo de outro já gravado também é pulado
    # (o identificador guarda slug+cargo, sem separador único); slugs reais têm cidade+ano.


def test_db_item_ja_processado_prefixo_exato():
    from notifica_vagas_scraper import db

    ids = {"kingpage-123-medico", "portal2-9-clinico"}
    assert db.item_ja_processado("kingpage-123-", ids)
    assert db.item_ja_processado("portal2-9-", ids)
    assert not db.item_ja_processado("kingpage-124-", ids)
    assert not db.item_ja_processado("portal2-", set())
