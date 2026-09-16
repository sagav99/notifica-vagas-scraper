import json
import subprocess
from datetime import date

import pytest

from notifica_vagas_scraper import completude_codex, db


def test_campos_faltando_so_os_null():
    vaga = {"salario": 5000, "banca_organizadora": None, "tem_prova": None, "requisitos": "X"}
    faltando = completude_codex.campos_faltando(
        {c: vaga.get(c) for c in completude_codex.CAMPOS_PEDIVEIS}
    )
    assert "banca_organizadora" in faltando
    assert "tem_prova" in faltando
    assert "salario" not in faltando
    assert "requisitos" not in faltando


def test_montar_schema_so_inclui_campos_pedidos():
    schema = completude_codex.montar_schema(["salario", "tem_prova"])
    assert set(schema["properties"]) == {"salario", "tem_prova", "confianca", "fonte_usada"}
    assert schema["required"] == ["salario", "tem_prova", "confianca", "fonte_usada"]
    assert schema["additionalProperties"] is False


def test_montar_schema_tipo_aceita_null():
    schema = completude_codex.montar_schema(["tem_prova"])
    assert schema["properties"]["tem_prova"]["type"] == ["boolean", "null"]


def test_montar_prompt_inclui_link_quando_existe():
    vaga = {"cargo": "Médico ESF", "orgao": "Prefeitura de X", "nome": "X", "uf": "MG", "numero_edital": "01/2026"}
    prompt = completude_codex.montar_prompt(vaga, ["tem_prova"], link="https://exemplo.com/edital.pdf")
    assert "https://exemplo.com/edital.pdf" in prompt
    assert "Médico ESF" in prompt
    assert "tem_prova" in prompt


def test_montar_prompt_sem_link():
    vaga = {"cargo": "Médico ESF", "orgao": None, "nome": "X", "uf": "MG", "numero_edital": None}
    prompt = completude_codex.montar_prompt(vaga, ["salario"], link=None)
    assert "Não temos nenhum link salvo" in prompt


def test_filtrar_campos_aceitos_rejeita_confianca_baixa():
    resultado = {"salario": 5000, "confianca": "baixa", "fonte_usada": "x"}
    assert completude_codex.filtrar_campos_aceitos(resultado, ["salario"]) == {}


def test_filtrar_campos_aceitos_aceita_alta_e_media():
    resultado = {"salario": 5000, "confianca": "alta", "fonte_usada": "x"}
    assert completude_codex.filtrar_campos_aceitos(resultado, ["salario"]) == {"salario": 5000}

    resultado2 = {"salario": 5000, "confianca": "media", "fonte_usada": "x"}
    assert completude_codex.filtrar_campos_aceitos(resultado2, ["salario"]) == {"salario": 5000}


def test_filtrar_campos_aceitos_ignora_null_e_campo_fora_do_pedido():
    resultado = {"salario": None, "banca_organizadora": "IBFC", "confianca": "alta", "fonte_usada": "x"}
    # só "salario" foi pedido -- "banca_organizadora" não deveria vazar mesmo vindo na resposta
    assert completude_codex.filtrar_campos_aceitos(resultado, ["salario"]) == {}


def test_converter_tipos_data_iso_vira_date():
    convertido = completude_codex._converter_tipos({"data_prova": "2026-11-15", "salario": 5000})
    assert convertido["data_prova"] == date(2026, 11, 15)
    assert convertido["salario"] == 5000


def test_converter_tipos_data_mal_formada_e_descartada_sem_quebrar():
    convertido = completude_codex._converter_tipos({"data_prova": "não é uma data", "salario": 5000})
    assert "data_prova" not in convertido
    assert convertido["salario"] == 5000


class _ProcessoFalso:
    def __init__(self, returncode=0, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def test_rodar_codex_usa_sandbox_workspace_write(monkeypatch):
    chamados = {}

    def _run_falso(cmd, **kwargs):
        chamados["cmd"] = cmd
        # escreve no arquivo -o real que o comando receberia
        indice_saida = cmd.index("-o") + 1
        with open(cmd[indice_saida], "w") as f:
            json.dump({"salario": 5000, "confianca": "alta", "fonte_usada": "x"}, f)
        return _ProcessoFalso(returncode=0)

    monkeypatch.setattr(subprocess, "run", _run_falso)

    resultado = completude_codex.rodar_codex("prompt de teste", {"type": "object"})

    assert resultado == {"salario": 5000, "confianca": "alta", "fonte_usada": "x"}
    assert "--sandbox" in chamados["cmd"]
    assert chamados["cmd"][chamados["cmd"].index("--sandbox") + 1] == "workspace-write"
    assert "danger-full-access" not in chamados["cmd"]


def test_rodar_codex_levanta_erro_em_exit_code_diferente_de_zero(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kwargs: _ProcessoFalso(returncode=1, stderr="deu ruim")
    )
    with pytest.raises(completude_codex.ErroCompletudeCodex, match="deu ruim"):
        completude_codex.rodar_codex("prompt", {"type": "object"})


def test_rodar_codex_levanta_erro_em_timeout(monkeypatch):
    def _run_timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    monkeypatch.setattr(subprocess, "run", _run_timeout)
    with pytest.raises(completude_codex.ErroCompletudeCodex, match="timeout"):
        completude_codex.rodar_codex("prompt", {"type": "object"}, timeout=1)


def test_rodar_codex_levanta_erro_em_json_invalido(monkeypatch):
    def _run_json_ruim(cmd, **kwargs):
        indice_saida = cmd.index("-o") + 1
        with open(cmd[indice_saida], "w") as f:
            f.write("isso não é json")
        return _ProcessoFalso(returncode=0)

    monkeypatch.setattr(subprocess, "run", _run_json_ruim)
    with pytest.raises(completude_codex.ErroCompletudeCodex, match="JSON válido"):
        completude_codex.rodar_codex("prompt", {"type": "object"})


# --- processar_vaga: pipeline completo, com db mockado ---


class _ConexaoFalsa:
    def __init__(self):
        self.atualizacoes: list[dict] = []
        self.conferencias: list[dict] = []


def _instalar_db_falso(monkeypatch, *, atualizar=None, registrar=None):
    if atualizar is not None:
        monkeypatch.setattr(db, "atualizar_campos_vaga", atualizar)
    if registrar is not None:
        monkeypatch.setattr(db, "registrar_conferencia", registrar)


def test_processar_vaga_sem_campo_faltando_e_no_op(monkeypatch):
    chamadas = []
    _instalar_db_falso(monkeypatch, registrar=lambda *a, **k: chamadas.append(k))
    vaga = {c: "algo" for c in completude_codex.CAMPOS_PEDIVEIS}
    vaga["id"] = "v1"

    resultado = completude_codex.processar_vaga(conn=None, vaga=vaga)

    assert resultado == "sem_alteracao"
    assert chamadas == []  # nem chega a registrar conferência -- não gastou Codex à toa


def test_processar_vaga_grava_campo_aceito(monkeypatch):
    atualizacoes = []
    conferencias = []
    _instalar_db_falso(
        monkeypatch,
        atualizar=lambda conn, *, vaga_id, campos: atualizacoes.append((vaga_id, campos)),
        registrar=lambda conn, **k: conferencias.append(k),
    )
    monkeypatch.setattr(
        completude_codex, "rodar_codex",
        lambda prompt, schema, **k: {"salario": 5000, "confianca": "alta", "fonte_usada": "edital.pdf"},
    )
    vaga = {c: "algo" for c in completude_codex.CAMPOS_PEDIVEIS}
    vaga["salario"] = None
    vaga["id"] = "v1"
    vaga["url"] = "https://exemplo.com/edital.pdf"
    vaga["cargo"] = "Médico"
    vaga["nome"] = "X"
    vaga["uf"] = "MG"

    resultado = completude_codex.processar_vaga(conn=None, vaga=vaga)

    assert resultado == "campo_preenchido"
    assert atualizacoes == [("v1", {"salario": 5000})]
    assert conferencias[0]["conferido_por"] == "completude_codex"
    assert conferencias[0]["resultado"] == "campo_preenchido"


def test_processar_vaga_sem_alteracao_quando_confianca_baixa(monkeypatch):
    conferencias = []
    _instalar_db_falso(monkeypatch, registrar=lambda conn, **k: conferencias.append(k))
    monkeypatch.setattr(
        completude_codex, "rodar_codex",
        lambda prompt, schema, **k: {"salario": 5000, "confianca": "baixa", "fonte_usada": "chute"},
    )
    vaga = {c: "algo" for c in completude_codex.CAMPOS_PEDIVEIS}
    vaga["salario"] = None
    vaga["id"] = "v1"
    vaga["cargo"] = "Médico"
    vaga["nome"] = "X"
    vaga["uf"] = "MG"

    resultado = completude_codex.processar_vaga(conn=None, vaga=vaga)

    assert resultado == "sem_alteracao"
    assert conferencias[0]["resultado"] == "sem_alteracao"


def test_eh_erro_de_cota_reconhece_sinais_comuns():
    assert completude_codex.eh_erro_de_cota("HTTP 429 Too Many Requests")
    assert completude_codex.eh_erro_de_cota("Usage limit reached, try again later")
    assert completude_codex.eh_erro_de_cota("Rate limit exceeded")
    assert completude_codex.eh_erro_de_cota("cota esgotada pra hoje")


def test_eh_erro_de_cota_nao_confunde_erro_pontual():
    assert not completude_codex.eh_erro_de_cota("codex exec estourou timeout de 480s")
    assert not completude_codex.eh_erro_de_cota("connection reset by peer")
    assert not completude_codex.eh_erro_de_cota("saída do codex exec não é JSON válido: ...")


def test_processar_vaga_erro_codex_registra_e_nao_quebra(monkeypatch):
    conferencias = []
    _instalar_db_falso(monkeypatch, registrar=lambda conn, **k: conferencias.append(k))

    def _rodar_codex_com_erro(prompt, schema, **k):
        raise completude_codex.ErroCompletudeCodex("falhou")

    monkeypatch.setattr(completude_codex, "rodar_codex", _rodar_codex_com_erro)
    vaga = {c: "algo" for c in completude_codex.CAMPOS_PEDIVEIS}
    vaga["salario"] = None
    vaga["id"] = "v1"
    vaga["cargo"] = "Médico"
    vaga["nome"] = "X"
    vaga["uf"] = "MG"

    resultado = completude_codex.processar_vaga(conn=None, vaga=vaga)

    assert resultado == "erro_codex"
    assert conferencias[0]["resultado"] == "erro_codex"
