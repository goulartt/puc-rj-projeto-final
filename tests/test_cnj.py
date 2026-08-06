"""Testes do extrator de número CNJ.

O dígito verificador é o que separa "sequência de dígitos parecida com um
processo" de "número de processo real". Se estes testes passarem, o Estágio 2
pode confiar no campo o suficiente para consultar o DataJud com ele.

    pytest tests/test_cnj.py -q
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services"))

from extractors import cnj  # noqa: E402


# Números com DV correto. O caso de Alagoas veio de um edital público e é a
# âncora independente do teste: ele não foi gerado pela nossa própria fórmula,
# então passar aqui é evidência de que o cálculo está certo, e não tautologia.
VALIDOS = [
    ("0710802-55.2018.8.02.0001", "AL", "api_publica_tjal"),
    ("1234567-14.2024.8.19.0001", "RJ", "api_publica_tjrj"),
]


@pytest.mark.parametrize("numero,uf,alias", VALIDOS)
def test_numero_valido(numero: str, uf: str, alias: str) -> None:
    parsed = cnj.parse(numero)
    assert parsed is not None
    assert parsed.valido
    assert parsed.uf == uf
    assert parsed.datajud_alias == alias


@pytest.mark.parametrize("numero,_uf,_alias", VALIDOS)
def test_dv_adulterado_reprova(numero: str, _uf: str, _alias: str) -> None:
    """Trocar o DV por qualquer outro valor tem de invalidar o número."""
    seq, resto = numero.split("-", 1)
    dv_certo, cauda = resto.split(".", 1)

    for delta in range(1, 97):
        dv_errado = f"{(int(dv_certo) + delta) % 100:02d}"
        if dv_errado == dv_certo:
            continue
        parsed = cnj.parse(f"{seq}-{dv_errado}.{cauda}")
        assert parsed is not None
        assert not parsed.valido, f"DV {dv_errado} passou indevidamente"


def test_dv_calculado_bate_com_o_do_numero() -> None:
    for numero, _uf, _alias in VALIDOS:
        p = cnj.parse(numero)
        assert cnj.digito_verificador(p.sequencial, p.ano, p.segmento, p.tribunal, p.origem) == p.digito


def test_aceita_formato_sem_pontuacao() -> None:
    """Alguns editais colam o número cru, com 20 dígitos e sem separadores."""
    p = cnj.parse("07108025520188020001")
    assert p is not None and p.valido
    assert p.numero == "0710802-55.2018.8.02.0001"


def test_extrai_de_texto_corrido_sem_repetir() -> None:
    texto = """
    EDITAL DE LEILAO. Nos autos do processo 0710802-55.2018.8.02.0001, que
    tramita perante a Justica Estadual, e no apenso 0710802-55.2018.8.02.0001,
    designa-se hasta publica.
    """
    achados = cnj.extrair(texto)
    assert [a.numero for a in achados] == ["0710802-55.2018.8.02.0001"]


def test_ignora_sequencias_que_apenas_parecem_processo() -> None:
    """Sem a validação, qualquer 20 dígitos viraria um número de processo."""
    texto = "Codigo de barras 11111111111111111111 e conta 99999999999999999999."
    assert cnj.extrair(texto) == []


def test_segmento_nao_estadual_nao_tem_alias() -> None:
    """A cobertura do DataJud aqui é só a Justiça Estadual.

    Devolver None em vez de chutar um índice deixa o fluxo responder
    "fora de cobertura" em vez de falhar numa URL inexistente.
    """
    p = cnj.parse("0000000-00.2020.5.01.0001")
    assert p is not None
    assert p.segmento_nome == "Justiça do Trabalho"
    assert p.datajud_alias is None


def test_texto_sem_processo_devolve_none() -> None:
    assert cnj.parse("edital de leilao extrajudicial, Lei 9.514/97") is None


# ─── Separar o processo do leilão da jurisprudência citada ──────────────────
#
# Trecho reduzido do edital real em data/editais/. O edital cita quatro agravos
# como precedente; consultar qualquer um deles no DataJud traria a situação de
# uma causa alheia ao imóvel — pior do que não consultar nada.

EDITAL_COM_PRECEDENTES = """
Edital de leilao do CONDOMINIO EDIFICIO CHARMANT em face de ANTONIO JOSE DE
ALMEIDA e outro - processo n 1002465-53.2023.8.26.0100. A venda observa o
entendimento firmado pelo Egregio Tribunal de Justica do Estado de Sao Paulo,
conforme precedentes nos Agravos de Instrumento n 2132770-30.2017.8.26.0000,
2132317-30.2020.8.26.0000, 2028406-02.2020.8.26.0000 e
2143178-41.2021.8.26.0000, admitindo-se a necessaria ponderacao.
"""


def test_identifica_o_processo_do_leilao_entre_precedentes() -> None:
    principal = cnj.processo_principal(EDITAL_COM_PRECEDENTES)
    assert principal is not None
    assert principal.numero == "1002465-53.2023.8.26.0100"
    assert principal.datajud_alias == "api_publica_tjsp"


def test_precedentes_sao_marcados_como_citados() -> None:
    achados = cnj.extrair(EDITAL_COM_PRECEDENTES)
    principais = [n.numero for n in achados if n.papel != "citado"]
    citados = [n.numero for n in achados if n.papel == "citado"]

    assert principais == ["1002465-53.2023.8.26.0100"]
    assert len(citados) == 4
    assert all(n.startswith("2") for n in citados)


def test_origem_zerada_indica_segundo_grau() -> None:
    """Origem 0000 = processo no proprio tribunal.

    A execucao que leva um imovel a leilao corre em primeiro grau, entao um
    numero de segundo grau no edital e quase sempre jurisprudencia.
    """
    segundo = cnj.parse("2132770-30.2017.8.26.0000")
    primeiro = cnj.parse("1002465-53.2023.8.26.0100")

    assert segundo.instancia == "segundo_grau"
    assert segundo.papel == "citado"
    assert primeiro.instancia == "primeiro_grau"


def test_so_precedentes_nao_produz_processo_principal() -> None:
    """Sem processo identificavel, o fluxo tem de saber disso.

    Devolver None deixa a resposta ser "nao identifiquei o processo", em vez de
    escolher um numero no chute e consultar a causa errada.
    """
    texto = (
        "Conforme precedentes nos Agravos de Instrumento n "
        "2132770-30.2017.8.26.0000 e 2143178-41.2021.8.26.0000."
    )
    assert cnj.processo_principal(texto) is None


def test_numero_partido_por_quebra_de_linha_no_markdown() -> None:
    """O Docling quebra linha no meio da frase; o contexto nao pode se perder."""
    texto = "em face de FULANO e outro -\nprocesso n\n1002465-53.2023.8.26.0100 ."
    principal = cnj.processo_principal(texto)
    assert principal is not None
    assert principal.papel == "principal"
