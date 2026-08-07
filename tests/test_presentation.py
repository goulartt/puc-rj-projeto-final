"""Testes da camada de apresentação.

Todos os casos vieram da primeira ficha entregue de verdade pelo Telegram, que
mostrou `debts.enforced_claim` na tela, gastou duas das três linhas de risco
com avisos que valem para qualquer leilão, e não disse nada de favorável sobre
um imóvel que tinha deságio na segunda praça.

    pytest tests/test_presentation.py -q
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services"))

from extractors import presentation  # noqa: E402


# ─── Rótulos ────────────────────────────────────────────────────────────────

def test_o_campo_que_vazou_para_a_tela() -> None:
    assert presentation.label("debts.enforced_claim") == "Valor cobrado na execução"


def test_caminhos_do_schema_viram_portugues() -> None:
    for campo, esperado in [
        ("occupancy.status", "Se o imóvel está ocupado"),
        ("appraisal.value", "Valor da avaliação"),
        ("auction.second_round.minimum_bid", "Lance mínimo da segunda praça"),
        ("property.registry_number", "Matrícula"),
        ("encumbrances", "Ônus na matrícula (hipoteca, penhora, usufruto)"),
    ]:
        assert presentation.label(campo) == esperado


def test_campo_composto_que_o_modelo_inventou() -> None:
    """O modelo escreveu `appraisal.value / auction minimum bid amount`.

    Não é caminho de schema, é prosa. Tem de sair legível mesmo assim.
    """
    resultado = presentation.label("appraisal.value / auction minimum bid amount")
    assert "Valor da avaliação" in resultado
    assert "/" not in resultado
    assert "appraisal" not in resultado


def test_campo_sem_o_prefixo_do_caminho() -> None:
    """Vistos na tela de um usuário: "condo fees" e "enforced credito valor".

    O modelo escreve o nome do campo sem o prefixo `debts.`, ou com uma palavra
    a mais. Os dois caíam no tradutor palavra a palavra e saíam em inglês.
    """
    assert presentation.label("condo fees") == "Dívida de condomínio"
    assert presentation.label("enforced claim value") == "Valor cobrado na execução"
    assert presentation.label("condo_fees amount") == "Dívida de condomínio"
    assert presentation.label("debts.enforced_claim value") == "Valor cobrado na execução"


def test_apelido_ambiguo_nao_e_indexado() -> None:
    """`type` é de `procedure` e de `property`; adivinhar erraria em silêncio."""
    assert "type" not in presentation._ALIASES
    assert "value" not in presentation._ALIASES
    # Mas o caminho completo continua resolvendo.
    assert presentation.label("property.type") == "Tipo do imóvel"
    assert presentation.label("procedure.type") == "Tipo de leilão (judicial ou extrajudicial)"


def test_nenhum_rotulo_devolve_ingles_dos_campos_conhecidos() -> None:
    """Varredura: nenhum caminho do schema pode sair com nome de variável."""
    for path in presentation.LABELS:
        for forma in (path, path.replace("_", " "), path.rsplit(".", 1)[-1]):
            saida = presentation.label(forma)
            assert "_" not in saida, (forma, saida)
            assert "." not in saida.replace("…", ""), (forma, saida)


def test_campo_desconhecido_degrada_em_vez_de_quebrar() -> None:
    """Nunca mostrar caminho cru, mesmo sem tradução exata."""
    resultado = presentation.label("property.some_new_field")
    assert "_" not in resultado
    assert resultado == "Dados do imóvel"

    solto = presentation.label("random_unknown_thing")
    assert "_" not in solto
    assert solto[0].isupper()


def test_campo_vazio_nao_quebra() -> None:
    assert presentation.label("") == "Campo não identificado"
    assert presentation.label(None) == "Campo não identificado"  # type: ignore[arg-type]


# ─── Riscos genéricos ───────────────────────────────────────────────────────

RISCO_GENERICO = ("Prazo de pagamento integral de 24 horas após arrematação; "
                  "inadimplência gera perda de caução de 20% e cobrança da "
                  "comissão do leiloeiro.")
RISCO_ESPECIFICO = ("O edital não informa se o imóvel está ocupado; a venda é "
                    "'no estado' e a desocupação fica com o arrematante.")
RISCO_FIDUCIARIO = ("O objeto são direitos fiduciários; a propriedade só é "
                    "transmitida mediante quitação contratual.")


def test_reconhece_o_risco_que_todo_leilao_tem() -> None:
    assert presentation.is_generic_risk(RISCO_GENERICO) is True
    assert presentation.is_generic_risk(RISCO_FIDUCIARIO) is False


def test_as_duas_ordens_do_prazo_de_pagamento() -> None:
    """Edital escreve das duas formas, e casar so uma deixava metade passar."""
    for texto in (
        "Prazo de pagamento integral de 24 horas apos a arrematacao.",
        "Prazo de 24 horas para pagamento integral do lance a vista.",
        "O arrematante tem vinte e quatro horas para efetuar o pagamento.",
    ):
        assert presentation.is_generic_risk(texto) is True, texto


def test_prazo_que_nao_e_de_pagamento_nao_e_generico() -> None:
    """Nem todo prazo curto e o aviso padrao — este e especifico do lote."""
    assert presentation.is_generic_risk(
        "O usufrutuario tem 24 horas para exercer o direito de preferencia."
    ) is False


def test_generico_vai_para_o_fim_mas_nao_some() -> None:
    """Esconder um risco verdadeiro seria pior que mostrá-lo em segundo plano."""
    riscos = [
        {"description": RISCO_GENERICO, "severity": "high"},
        {"description": RISCO_FIDUCIARIO, "severity": "high"},
    ]
    ordenados = presentation.rank_risks(riscos)
    assert ordenados[0]["description"] == RISCO_FIDUCIARIO
    assert len(ordenados) == 2


def test_o_resumo_prefere_o_especifico() -> None:
    ficha = {"risks": [
        {"description": RISCO_GENERICO, "severity": "high"},
        {"description": RISCO_FIDUCIARIO, "severity": "medium"},
    ]}
    saida = presentation.present(ficha)
    assert [r["description"] for r in saida["risks"]] == [RISCO_FIDUCIARIO]
    assert saida["risks_generic_hidden"] == 1


def test_so_ha_generico_entao_mostra_o_generico() -> None:
    """Melhor um aviso banal do que uma seção vazia sugerindo que não há risco."""
    ficha = {"risks": [{"description": RISCO_GENERICO, "severity": "high"}]}
    assert len(presentation.present(ficha)["risks"]) == 1


# ─── Fatos favoráveis ───────────────────────────────────────────────────────

def test_desagio_na_segunda_praca() -> None:
    ficha = {
        "appraisal": {"updated_value": {"amount_brl": 800000.0}},
        "auction": {"second_round": {"minimum_bid": {"amount_brl": 480000.0}}},
    }
    fatos = presentation.highlights(ficha)
    texto = fatos[0]["text"]
    assert fatos[0]["kind"] == "second_round_discount"
    assert "40%" in texto
    assert "R$ 480.000,00" in texto


def test_desagio_irrelevante_nao_vira_destaque() -> None:
    ficha = {
        "appraisal": {"value": {"amount_brl": 100000.0}},
        "auction": {"second_round": {"minimum_bid": {"amount_brl": 98000.0}}},
    }
    assert presentation.highlights(ficha) == []


def test_ausencia_de_onus_e_fato_favoravel() -> None:
    fatos = presentation.highlights({"encumbrances": []})
    assert fatos[0]["kind"] == "no_encumbrances"


def test_onus_nao_apurado_nao_vira_ausencia_de_onus() -> None:
    """Lista vazia com lacuna declarada significa 'não sei', não 'não há'.

    Afirmar que não há ônus quando ninguém apurou é o erro mais caro que esta
    seção poderia cometer — ela existe para tranquilizar, e tranquilizaria
    errado.
    """
    ficha = {"encumbrances": [], "gaps": [{"field": "encumbrances",
                                           "why_it_matters": "não apurado"}]}
    assert presentation.highlights(ficha) == []


def test_processo_limpo_exige_consulta_feita() -> None:
    ficha = {"court_case": {"number": "1002465-53.2023.8.26.0100"}}
    # Sem consulta, silêncio.
    assert presentation.highlights(ficha) == []
    # Com consulta e sem sinal ativo, afirmação.
    fatos = presentation.highlights(
        ficha, {"found": True, "active_signals": [], "movement_count": 197}
    )
    assert fatos[0]["kind"] == "clean_case"
    assert "197" in fatos[0]["text"]


def test_processo_com_sinal_ativo_nao_vira_destaque() -> None:
    fatos = presentation.highlights(
        {}, {"found": True, "active_signals": [{"kind": "embargos"}], "movement_count": 12}
    )
    assert fatos == []


def test_imovel_desocupado_declarado() -> None:
    fatos = presentation.highlights({"occupancy": {"status": {"value": "vacant"}}})
    assert fatos[0]["kind"] == "vacant"


def test_ocupacao_nao_informada_nao_vira_destaque() -> None:
    assert presentation.highlights({"occupancy": {"status": {"value": "not_informed"}}}) == []


# ─── Composição ─────────────────────────────────────────────────────────────

def test_present_traduz_as_lacunas() -> None:
    ficha = {"gaps": [
        {"field": "occupancy.status", "why_it_matters": "afeta custo e prazo"},
        {"field": "debts.enforced_claim", "why_it_matters": "valor não explicitado"},
    ]}
    rotulos = [g["label"] for g in presentation.present(ficha)["gaps"]]
    assert rotulos == ["Se o imóvel está ocupado", "Valor cobrado na execução"]


def test_ficha_vazia_nao_quebra() -> None:
    saida = presentation.present({})
    assert saida == {"warning": None, "risks": [], "risks_generic_hidden": 0,
                     "gaps": [], "highlights": []}


def test_aviso_de_edital_com_varios_imoveis() -> None:
    """A ficha descreve um lote; sem o aviso ela e lida como se fosse do edital."""
    saida = presentation.present(
        {}, deterministic={"multi_lot": {"multi": True, "properties": 7}}
    )
    assert "7 imóveis" in saida["warning"]
    assert "confira no PDF" in saida["warning"]


def test_edital_de_um_imovel_nao_gera_aviso() -> None:
    saida = presentation.present(
        {}, deterministic={"multi_lot": {"multi": False, "properties": 1}}
    )
    assert saida["warning"] is None
