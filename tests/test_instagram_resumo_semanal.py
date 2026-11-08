"""Dia da semana -> tipo do post do resumo semanal (sábado = abertura, domingo = fechamento)."""

import importlib.util
from datetime import date
from pathlib import Path

_spec = importlib.util.spec_from_file_location("postar_resumo_semanal", Path(__file__).resolve().parent.parent / "scripts" / "postar_resumo_semanal.py")
postar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(postar)


def test_sabado_e_abertura():
    assert postar.tipo_do_dia(date(2026, 11, 14)) == "abertura"


def test_domingo_e_fechamento():
    assert postar.tipo_do_dia(date(2026, 11, 15)) == "fechamento"


def test_dia_util_nao_posta():
    assert postar.tipo_do_dia(date(2026, 11, 13)) is None  # sexta
    assert postar.tipo_do_dia(date(2026, 11, 9)) is None  # segunda
