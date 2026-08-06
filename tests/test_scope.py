"""Testes do classificador de escopo.

O caso que originou o módulo está aqui: perguntado "vale a pena comprar esse
imóvel?", o modelo local respondeu com análise de investimento em vez de
recusar. O limite prometido pelo produto virou regra por causa disso.

A metade mais importante destes testes é a de baixo — as perguntas que
**precisam passar**. Um filtro que recusa demais quebra o produto de um jeito
mais silencioso do que um que recusa de menos, porque a segunda camada (a
instrução do prompt) ainda existe para o que escapa, mas nada resgata uma
pergunta legítima que foi barrada.

    pytest tests/test_scope.py -q
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services"))

from extractors import scope  # noqa: E402


# ─── O caso real ────────────────────────────────────────────────────────────

def test_a_pergunta_que_o_modelo_respondeu_quando_nao_devia() -> None:
    resultado = scope.classify("Vale a pena comprar esse imóvel?")
    assert resultado["in_scope"] is False
    assert resultado["kind"] == "investment_advice"
    # A recusa oferece alternativa: porta fechada sem saída não ajuda ninguém.
    assert "custos que o edital menciona" in resultado["reply"]


# ─── Conselho de investimento ───────────────────────────────────────────────

def test_variacoes_de_pedido_de_conselho() -> None:
    for pergunta in (
        "vale a pena?",
        "Devo arrematar esse apartamento?",
        "você recomenda esse leilão?",
        "isso é um bom negócio?",
        "compensa investir nesse lote?",
        "me aconselha a dar lance?",
    ):
        assert scope.classify(pergunta)["in_scope"] is False, pergunta


def test_falta_de_acento_nao_burla_o_filtro() -> None:
    """Ninguém acentua com pressa no Telegram, e o filtro não pode depender disso."""
    assert scope.classify("voce recomenda esse imovel?")["in_scope"] is False
    assert scope.classify("e um bom negocio?")["in_scope"] is False


# ─── Valor de mercado ───────────────────────────────────────────────────────

def test_estimativa_de_mercado_e_recusada() -> None:
    for pergunta in (
        "qual o valor de mercado desse apartamento?",
        "esse imóvel vai valorizar?",
        "quanto posso revender depois?",
        "qual o retorno do investimento?",
    ):
        assert scope.classify(pergunta)["in_scope"] is False, pergunta


# ─── Orientação jurídica ────────────────────────────────────────────────────

def test_pedido_de_orientacao_juridica_e_recusado() -> None:
    for pergunta in (
        "posso processar o antigo dono?",
        "tenho direito a desconto se o imóvel estiver ocupado?",
        "vale a pena entrar com uma ação?",
    ):
        assert scope.classify(pergunta)["in_scope"] is False, pergunta

    assert scope.classify("posso processar o antigo dono?")["kind"] == "legal_advice"


# ─── O que NÃO pode ser barrado ─────────────────────────────────────────────
#
# Estas têm resposta na ficha ou no glossário. Barrar qualquer uma é um defeito
# pior do que deixar passar uma pergunta duvidosa.

def test_perguntas_sobre_o_edital_passam() -> None:
    for pergunta in (
        "esse imóvel está ocupado?",
        "quanto vale a comissão do leiloeiro?",
        "qual o valor da avaliação?",
        "quando é a segunda praça?",
        "a dívida de condomínio vem comigo?",
        "qual o lance mínimo?",
        "o que acontece se eu não pagar?",
        "existe algum ônus na matrícula?",
        "quanto vou pagar de ITBI?",
    ):
        assert scope.classify(pergunta)["in_scope"] is True, pergunta


def test_quanto_vale_sozinho_nao_e_recusa() -> None:
    """`quanto vale a comissão` pergunta um campo do edital.

    Foi por isso que `quanto vale` ficou fora do padrão de avaliação de
    mercado: sozinho, ele é ambíguo demais para recusar.
    """
    assert scope.classify("quanto vale a comissão?")["in_scope"] is True
    assert scope.classify("quanto vale esse imóvel hoje?")["in_scope"] is False


def test_perguntas_conceituais_passam() -> None:
    for pergunta in (
        "o que é praça?",
        "o que significa imissão na posse?",
        "o que é arrematação?",
        "como funciona leilão judicial?",
    ):
        assert scope.classify(pergunta)["in_scope"] is True, pergunta


# ─── Robustez ───────────────────────────────────────────────────────────────

def test_texto_vazio_passa() -> None:
    assert scope.classify("")["in_scope"] is True
    assert scope.classify(None)["in_scope"] is True  # type: ignore[arg-type]


def test_classify_all_preserva_ordem() -> None:
    resultados = scope.classify_all(
        ["esse imóvel está ocupado?", "vale a pena?", "o que é praça?"]
    )
    assert [r["in_scope"] for r in resultados] == [True, False, True]
