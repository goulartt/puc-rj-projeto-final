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
VALID_NUMBERS = [
    ("0710802-55.2018.8.02.0001", "AL", "api_publica_tjal"),
    ("1002465-53.2023.8.26.0100", "SP", "api_publica_tjsp"),
]


@pytest.mark.parametrize("number,state,alias", VALID_NUMBERS)
def test_valid_number(number: str, state: str, alias: str) -> None:
    parsed = cnj.parse(number)
    assert parsed is not None
    assert parsed.valid
    assert parsed.state == state
    assert parsed.datajud_alias == alias


@pytest.mark.parametrize("number,_state,_alias", VALID_NUMBERS)
def test_tampered_check_digits_are_rejected(number: str, _state: str, _alias: str) -> None:
    """Trocar o DV por qualquer outro valor tem de invalidar o número."""
    sequential, rest = number.split("-", 1)
    correct, tail = rest.split(".", 1)

    for delta in range(1, 97):
        tampered = f"{(int(correct) + delta) % 100:02d}"
        if tampered == correct:
            continue
        parsed = cnj.parse(f"{sequential}-{tampered}.{tail}")
        assert parsed is not None
        assert not parsed.valid, f"DV {tampered} passou indevidamente"


def test_computed_check_digits_match_the_number() -> None:
    for number, _state, _alias in VALID_NUMBERS:
        parsed = cnj.parse(number)
        computed = cnj.compute_check_digits(
            parsed.sequential, parsed.year, parsed.segment, parsed.court, parsed.origin
        )
        assert computed == parsed.check_digits


def test_accepts_unpunctuated_format() -> None:
    """Alguns editais colam o número cru, com 20 dígitos e sem separadores."""
    parsed = cnj.parse("07108025520188020001")
    assert parsed is not None and parsed.valid
    assert parsed.number == "0710802-55.2018.8.02.0001"


def test_extracts_from_prose_without_duplicates() -> None:
    text = """
    EDITAL DE LEILAO. Nos autos do processo 0710802-55.2018.8.02.0001, que
    tramita perante a Justica Estadual, e no apenso 0710802-55.2018.8.02.0001,
    designa-se hasta publica.
    """
    assert [n.number for n in cnj.extract(text)] == ["0710802-55.2018.8.02.0001"]


def test_ignores_digit_runs_that_merely_look_like_a_case() -> None:
    """Sem a validação, qualquer 20 dígitos viraria um número de processo."""
    text = "Codigo de barras 11111111111111111111 e conta 99999999999999999999."
    assert cnj.extract(text) == []


def test_non_state_segment_has_no_alias() -> None:
    """A cobertura do DataJud aqui é só a Justiça Estadual.

    Devolver None em vez de chutar um índice deixa o fluxo responder
    "fora de cobertura" em vez de falhar numa URL inexistente.
    """
    parsed = cnj.parse("0000000-00.2020.5.01.0001")
    assert parsed is not None
    assert parsed.segment_name == "Justiça do Trabalho"
    assert parsed.datajud_alias is None


def test_text_without_a_case_number_returns_none() -> None:
    assert cnj.parse("edital de leilao extrajudicial, Lei 9.514/97") is None


# ─── Separar o processo do leilão da jurisprudência citada ──────────────────
#
# Trecho reduzido do edital real em data/editais/. O edital cita quatro agravos
# como precedente; consultar qualquer um deles no DataJud traria a situação de
# uma causa alheia ao imóvel — pior do que não consultar nada.

NOTICE_WITH_PRECEDENTS = """
Edital de leilao do CONDOMINIO EDIFICIO CHARMANT em face de ANTONIO JOSE DE
ALMEIDA e outro - processo n 1002465-53.2023.8.26.0100. A venda observa o
entendimento firmado pelo Egregio Tribunal de Justica do Estado de Sao Paulo,
conforme precedentes nos Agravos de Instrumento n 2132770-30.2017.8.26.0000,
2132317-30.2020.8.26.0000, 2028406-02.2020.8.26.0000 e
2143178-41.2021.8.26.0000, admitindo-se a necessaria ponderacao.
"""


def test_finds_the_auction_case_among_precedents() -> None:
    found = cnj.main_case(NOTICE_WITH_PRECEDENTS)
    assert found is not None
    assert found.number == "1002465-53.2023.8.26.0100"
    assert found.datajud_alias == "api_publica_tjsp"


def test_precedents_are_marked_as_cited() -> None:
    found = cnj.extract(NOTICE_WITH_PRECEDENTS)
    main = [n.number for n in found if n.role != cnj.ROLE_CITED]
    cited = [n.number for n in found if n.role == cnj.ROLE_CITED]

    assert main == ["1002465-53.2023.8.26.0100"]
    assert len(cited) == 4
    assert all(number.startswith("2") for number in cited)


def test_zero_origin_means_second_instance() -> None:
    """Origem 0000 = processo no proprio tribunal.

    A execucao que leva um imovel a leilao corre em primeiro grau, entao um
    numero de segundo grau no edital e quase sempre jurisprudencia.
    """
    appeal = cnj.parse("2132770-30.2017.8.26.0000")
    trial = cnj.parse("1002465-53.2023.8.26.0100")

    assert appeal.instance == cnj.SECOND_INSTANCE
    assert appeal.role == cnj.ROLE_CITED
    assert trial.instance == cnj.FIRST_INSTANCE


def test_only_precedents_yields_no_main_case() -> None:
    """Sem processo identificavel, o fluxo tem de saber disso.

    Devolver None deixa a resposta ser "nao identifiquei o processo", em vez de
    escolher um numero no chute e consultar a causa errada.
    """
    text = (
        "Conforme precedentes nos Agravos de Instrumento n "
        "2132770-30.2017.8.26.0000 e 2143178-41.2021.8.26.0000."
    )
    assert cnj.main_case(text) is None


def test_number_split_by_markdown_line_break() -> None:
    """O Docling quebra linha no meio da frase; o contexto nao pode se perder."""
    text = "em face de FULANO e outro -\nprocesso n\n1002465-53.2023.8.26.0100 ."
    found = cnj.main_case(text)
    assert found is not None
    assert found.role == cnj.ROLE_MAIN
