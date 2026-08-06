"""Testes da conferência de citação.

Esta é a verificação que sustenta a promessa central do projeto: se o
assistente cita uma cláusula, a cláusula está no documento. Ela precisa ser
tolerante com artefato de conversão e intolerante com paráfrase — e a fronteira
entre as duas coisas é justamente o que estes testes fixam.

    pytest tests/test_text.py -q
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services"))

from extractors.text import contains_citation, normalize  # noqa: E402

EDITAL = (
    "O Leilão será realizado por MEIO ELETRÔNICO, tendo o 1º Leilão início no "
    "dia 20/07/2026 às 14h00 , onde somente serão aceitos lances iguais ou "
    "superiores ao valor da avaliação. Valor da Avaliação atualizado até junho "
    "de 2026: R$ 800.933,79. A venda é feita em caráter \"AD CORPUS '."
)


# ─── Tolerância a artefato de conversão ─────────────────────────────────────

def test_espaco_antes_de_pontuacao_nao_reprova() -> None:
    """O Docling insere espaço antes da vírgula; o edital não tem."""
    assert contains_citation(EDITAL, "às 14h00, onde somente serão aceitos lances")


def test_aspas_trocadas_nao_reprovam() -> None:
    """A conversão abre com aspa dupla e fecha com simples no mesmo trecho."""
    assert contains_citation(EDITAL, 'em caráter "AD CORPUS"')


# ─── Citação composta ───────────────────────────────────────────────────────

def test_partes_distantes_unidas_por_reticencias() -> None:
    """O prompt pede `[...]`, e o modelo escreve `...` — as duas valem.

    Exigir a forma exata mediria obediência ao formato do prompt, e não se a
    citação corresponde ao documento.
    """
    for separador in ("[...]", " ... ", "…"):
        citacao = f"lances iguais ou superiores ao valor da avaliação{separador}R$ 800.933,79"
        assert contains_citation(EDITAL, citacao), separador


def test_metade_inventada_reprova_mesmo_com_reticencias() -> None:
    """Aceitar reticências não pode virar porta para paráfrase.

    A primeira metade existe, a segunda não. Tem de reprovar.
    """
    assert not contains_citation(
        EDITAL,
        "lances iguais ou superiores ao valor da avaliação ... R$ 1.500.000,00",
    )


# ─── Intolerância a paráfrase ───────────────────────────────────────────────

def test_parafrase_reprova() -> None:
    """Mesmo sentido, outras palavras. É exatamente o que o projeto não aceita."""
    assert not contains_citation(
        EDITAL, "o leilão acontecerá de forma eletrônica a partir de 20 de julho"
    )


def test_fragmento_curto_demais_e_ignorado() -> None:
    """`R$` casaria em qualquer edital e não provaria nada."""
    assert contains_citation(EDITAL, "R$")


# ─── Normalização ───────────────────────────────────────────────────────────

def test_normalize_preserva_acento() -> None:
    """`não` e `nao` são coisas diferentes: apagar isso esconderia erro de conversão."""
    assert normalize("não") != normalize("nao")


def test_normalize_colapsa_quebra_de_linha() -> None:
    assert normalize("valor da\n  avaliação") == "valor da avaliação"
