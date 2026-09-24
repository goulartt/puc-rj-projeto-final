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


# ─── Escolha do imóvel ──────────────────────────────────────────────────────
#
# Um edital pode cobrir vários imóveis — dois dos cinco reais cobrem. Escolher
# o lote errado faria a ficha descrever o imóvel errado com toda a aparência de
# estar certa, que é o pior desfecho possível para este produto.

LOTES = [{"registry": "7.195"}, {"registry": "81.909"}, {"registry": "9.937"}]


def test_numero_da_lista() -> None:
    assert scope.parse_lot_choice("2", LOTES)["index"] == 2
    assert scope.parse_lot_choice("quero o 3", LOTES)["index"] == 3


def test_matricula_vence_o_numero_da_lista() -> None:
    """`quero a 81.909` traz números que não são índice."""
    resultado = scope.parse_lot_choice("quero a 81.909", LOTES)
    assert resultado["index"] == 2
    assert resultado["reason"] == "matricula"


def test_matricula_sem_pontuacao() -> None:
    assert scope.parse_lot_choice("matricula 9937", LOTES)["index"] == 3


def test_ordinal_por_extenso() -> None:
    assert scope.parse_lot_choice("o segundo", LOTES)["index"] == 2
    assert scope.parse_lot_choice("a terceira", LOTES)["index"] == 3


def test_sim_so_resolve_quando_ha_um_imovel() -> None:
    """Com vários, "sim" confirma sem dizer qual — e adivinhar seria o erro."""
    assert scope.parse_lot_choice("sim", LOTES)["understood"] is False
    assert scope.parse_lot_choice("sim", [{"registry": "106.233"}])["index"] == 1


def test_resposta_ambigua_nao_e_adivinhada() -> None:
    """Perguntar de novo custa uma mensagem; errar custa a ficha inteira."""
    for texto in ("1 ou 2", "sei la", "", "42"):
        assert scope.parse_lot_choice(texto, LOTES)["understood"] is False


def test_recusa_e_reconhecida_como_recusa() -> None:
    resultado = scope.parse_lot_choice("nenhum", LOTES)
    assert resultado["understood"] is False
    assert resultado["reason"] == "recusou"


def test_sim_sem_lote_algum_e_aceito() -> None:
    """Sem matrícula legível o fluxo pergunta "analiso assim mesmo?".

    Recusar o "sim" aqui prendia a conversa: o bot repetia a pergunta a cada
    confirmação, para sempre, porque pedia algo que não sabia aceitar.
    """
    for texto in ("sim", "SIM", "pode ser", "ok"):
        resultado = scope.parse_lot_choice(texto, [])
        assert resultado["understood"] is True, texto
        assert resultado["index"] is None


def test_sem_lote_algum_ainda_recusa_o_que_nao_e_confirmacao() -> None:
    assert scope.parse_lot_choice("nao", [])["reason"] == "recusou"
    assert scope.parse_lot_choice("sei la", [])["understood"] is False


# ─── Escolha num catálogo ───────────────────────────────────────────────────

CATALOGO = [
    {"item": "461", "registry": "41437", "asset_id": "1444420714491", "kind": "Apartamento",
     "development": "ED DINAMARCA", "address": "ALAMEDA CASA BRANCA N. 438 Apto. 111",
     "district": "Jardim Paulista", "city": "Sao Paulo/SP"},
    {"item": "12", "registry": "1437", "asset_id": "8787700000001", "kind": "Casa",
     "development": None, "address": "RUA AUGUSTA N. 10", "district": "Consolacao",
     "city": "Sao Paulo/SP"},
    {"item": "483", "registry": "4372", "asset_id": "8787700000002", "kind": "Casa",
     "development": None, "address": "RUA PRINCESA ISABEL N. SN", "district": "Jardim Paulista",
     "city": "Paraiso Do Tocantins/TO"},
]


def test_matricula_casa_por_igualdade_e_nao_por_trecho() -> None:
    """"41437" contém "1437". Comparar por trecho escolheria o imóvel errado
    num catálogo em que a matrícula menor viesse antes na lista."""
    assert scope.parse_lot_choice("41437", CATALOGO)["index"] == 1
    assert scope.parse_lot_choice("matrícula 1437", CATALOGO)["index"] == 2


def test_item_numero_do_bem_e_endereco() -> None:
    assert scope.parse_lot_choice("item 461", CATALOGO)["index"] == 1
    assert scope.parse_lot_choice("1444420714491", CATALOGO)["index"] == 1
    assert scope.parse_lot_choice("Casa Branca 438", CATALOGO)["index"] == 1
    assert scope.parse_lot_choice("Ed Dinamarca", CATALOGO)["index"] == 1


def test_endereco_ambiguo_devolve_candidatos() -> None:
    """"Jardim Paulista" existe em São Paulo e em Paraíso do Tocantins."""
    resultado = scope.parse_lot_choice("Jardim Paulista", CATALOGO)
    assert resultado["understood"] is False
    assert resultado["reason"] == "varios"
    assert resultado["candidates"] == [1, 3]


def test_sim_num_catalogo_nao_escolhe_nada() -> None:
    assert scope.parse_lot_choice("sim", CATALOGO)["understood"] is False


def test_imovel_inexistente_nao_e_adivinhado() -> None:
    assert scope.parse_lot_choice("matrícula 99999", CATALOGO)["reason"] == "nao encontrado"
