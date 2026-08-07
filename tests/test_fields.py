"""Testes dos extratores determinísticos de campos do edital.

Cada caso aqui saiu de um erro observado no edital real em `data/editais/`, e
não de hipótese. Dado que o modelo parafraseia — 10 das 35 citações da primeira
ficha gerada não existiam literalmente no documento — estes extratores são a
âncora factual de valor, data e matrícula.

    pytest tests/test_fields.py -q
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services"))

from extractors import fields  # noqa: E402


# ─── Dinheiro ───────────────────────────────────────────────────────────────

def test_valores_em_formato_brasileiro() -> None:
    achados = fields.money(
        "VALOR DA AVALIAÇÃO: R$ 772.545,00 (outubro/2025). Débito de R$ 293,19."
    )
    assert [a.value for a in achados] == [772545.0, 293.19]
    # O texto cru importa tanto quanto o número: e dele que a ficha cita.
    assert achados[0].raw == "R$ 772.545,00"


def test_valor_sem_centavos_nao_e_capturado_como_dinheiro() -> None:
    """`R$ 1.000` sem centavos e ambiguo com numero de processo ou lote."""
    assert fields.money("lote R$ 1000 e artigo 908") == []


# ─── Datas e praças ─────────────────────────────────────────────────────────

EDITAL_PRACAS = (
    "DATAS DO LEILÃO - O Leilão será realizado por MEIO ELETRÔNICO, tendo o "
    "1º Leilão início no dia 20/07/2026 às 14h00, e se encerrará dia 23/07/2026 "
    "às 14h00, onde somente serão aceitos lances iguais ou superiores ao valor "
    "da avaliação; não havendo lance, seguir-se-á o 2º Leilão, que terá início "
    "no dia 23/07/2026 às 14h01, e se encerrará no dia 26/08/2026 às 14h00."
)


def test_associa_datas_a_cada_praca() -> None:
    rounds = fields.auction_rounds(EDITAL_PRACAS)
    assert [d["date"] for d in rounds["first"]] == ["2026-07-20", "2026-07-23"]
    assert [d["date"] for d in rounds["second"]] == ["2026-07-23", "2026-08-26"]


def test_captura_a_hora_junto_da_data() -> None:
    rounds = fields.auction_rounds(EDITAL_PRACAS)
    assert rounds["first"][0]["time"] == "14:00"
    # 14h01 distingue o inicio da segunda praca do fim da primeira, no mesmo dia.
    assert rounds["second"][0]["time"] == "14:01"


def test_data_distante_do_rotulo_nao_entra_na_praca() -> None:
    """Sem limite de proximidade, a segunda praca engolia o resto do edital.

    No edital real ela absorvia a data de uma resolucao do CNJ de 2016 e dois
    vencimentos de IPTU, que estao milhares de caracteres adiante.
    """
    texto = EDITAL_PRACAS + " " + ("blá " * 200) + "conforme Resolução de 13/07/2016."
    rounds = fields.auction_rounds(texto)
    assert "2016-07-13" not in [d["date"] for d in rounds["second"]]


def test_sinal_de_grau_no_lugar_do_indicador_ordinal() -> None:
    """`1° leilão` com U+00B0, e não `1º` com U+00BA.

    Os dois são visualmente idênticos e os editais usam ambos sem critério. Um
    edital real escrito assim devolvia lista vazia: o extrator não achava nada
    e nada indicava que algo tinha falhado — o silêncio é o que torna esse tipo
    de defeito caro.
    """
    texto = (
        "Do início e encerramento do Leilão: Início do 1° leilão em 28/08/2026 às "
        "10:11 horas e encerramento do 1° leilão em 31/08/2026 às 10:11 horas, em "
        "não havendo lance igual ou superior à avaliação, seguir-se-á sem "
        "interrupção o 2° leilão que se encerrará em 15/09/2026 às 10:11 horas."
    )
    rounds = fields.auction_rounds(texto)
    assert [d["date"] for d in rounds["first"]] == ["2026-08-28", "2026-08-31"]
    assert [d["date"] for d in rounds["second"]] == ["2026-09-15"]


def test_hora_com_dois_pontos() -> None:
    """`10:11 horas` — o padrão de `14h00` casava o "11 h" de "10:11 horas".

    O resultado era 11:00 para um leilão que começa às 10:11: hora errada, sem
    erro nenhum. Uma hora plausível e errada é pior que hora ausente.
    """
    rounds = fields.auction_rounds("Início do 1° leilão em 28/08/2026 às 10:11 horas.")
    assert rounds["first"][0]["time"] == "10:11"

    # A grafia antiga continua valendo.
    antigo = fields.auction_rounds("1º Leilão início no dia 20/07/2026 às 14h00")
    assert antigo["first"][0]["time"] == "14:00"


def test_mes_por_extenso_no_meio_da_barra() -> None:
    """`03/setembro/2026` — um edital inteiro perdia as datas de praça."""
    rounds = fields.auction_rounds(
        "terá início o 2º leilão, que encerrar-se-á em 03/setembro/2026, às 14:00hs"
    )
    assert [d["date"] for d in rounds["second"]] == ["2026-09-03"]


def test_data_inteira_por_extenso() -> None:
    """`Dia 21 de agosto de 2026`, e o rótulo escrito `PRIMEIRO(A) LEILÃO/PRAÇA`.

    Duas variações no mesmo documento: a data sem nenhum número de mês e o
    rótulo com o `(A)` que os editais usam para servir aos dois ritos.
    """
    rounds = fields.auction_rounds(
        "PRIMEIRO(A) LEILÃO/PRAÇA:Dia 21 de agosto de 2026 às 09:30, que se "
        "realizará na Local: Hotel Thomasi. SEGUNDO(A) LEILÃO/PRAÇA:Dia 28 de "
        "agosto de 2026 às 09:30, no mesmo local."
    )
    assert [d["date"] for d in rounds["first"]] == ["2026-08-21"]
    assert [d["date"] for d in rounds["second"]] == ["2026-08-28"]


def test_data_invalida_e_descartada() -> None:
    assert fields.dates("prazo de 45/13/2026") == []


# ─── Matrícula: a armadilha da Junta Comercial ──────────────────────────────

def test_matricula_do_imovel_e_capturada() -> None:
    achados = fields.property_registry(
        "objeto da matrícula nº 106.233 do 4º Cartório de Registro de Imóveis da Capital/SP"
    )
    assert [a.value for a in achados] == ["106.233"]


def test_matricula_do_leiloeiro_na_jucesp_e_ignorada() -> None:
    """O mesmo edital traz as duas, e confundi-las poe o registro do leiloeiro
    no campo do imovel. Foi o primeiro falso positivo observado no documento."""
    texto = (
        "O Leilão será conduzido pelo Leiloeiro Oficial Sr. Fulano, matriculado "
        "na Junta Comercial do Estado de São Paulo -JUCESP sob o nº 798."
    )
    assert fields.property_registry(texto) == []


def test_matricula_sem_contexto_de_cartorio_e_ignorada() -> None:
    """Numero solto apos a palavra nao basta: exige-se o registro de imoveis."""
    assert fields.property_registry("matrícula nº 45.678 do clube recreativo") == []


def test_edital_com_as_duas_matriculas_devolve_so_a_do_imovel() -> None:
    texto = (
        "Leiloeiro matriculado na Junta Comercial sob o nº 798. "
        "O bem é objeto da matrícula nº 106.233 do 4º Cartório de Registro de Imóveis."
    )
    assert [a.value for a in fields.property_registry(texto)] == ["106.233"]


# ─── Edital com mais de um imóvel ───────────────────────────────────────────

def test_edital_de_um_imovel_nao_e_multi_lote() -> None:
    """Falso positivo aqui e pior que falso negativo: um aviso que aparece em
    todo edital treina a pessoa a ignorar a linha."""
    texto = "objeto da matrícula nº 106.233 do 4º Cartório de Registro de Imóveis"
    assert fields.multi_lot(texto)["multi"] is False


def test_varias_matriculas_disparam_o_aviso() -> None:
    """Um edital extrajudicial real trazia sete imoveis, e a ficha descrevia um.

    Nada indicava que havia outros seis — alguem podia dar lance no lote errado
    achando que tinha lido o documento.
    """
    texto = (
        "Lote 1: Matrícula nº 81.909 do 1º Serviço de Registro de Imóveis. "
        "Lote 2: Matrícula nº 82.003 do 1º Serviço de Registro de Imóveis. "
        "Lote 3: Matrícula nº 7.195 do Cartório de Registro de Imóveis."
    )
    resultado = fields.multi_lot(texto)
    assert resultado["multi"] is True
    assert resultado["properties"] == 3


def test_a_mesma_matricula_repetida_nao_e_multi_lote() -> None:
    texto = ("matrícula nº 106.233 do Registro de Imóveis; "
             "conforme a matrícula nº 106.233 do Registro de Imóveis")
    assert fields.multi_lot(texto)["properties"] == 1


# ─── CPF e CNPJ ─────────────────────────────────────────────────────────────

# CPFs sinteticos com digito verificador valido; nao pertencem a ninguem.
CPF_VALIDO = "529.982.247-25"
CNPJ_VALIDO = "11.222.333/0001-81"


def test_cpf_sai_mascarado_e_nunca_inteiro() -> None:
    """O CPF do executado consta do edital publico e nao pode ir para a ficha."""
    resultado = fields.documents(f"executado FULANO (CPF {CPF_VALIDO})")
    assert resultado["cpf"][0]["masked"] == "***.982.247-**"
    assert resultado["cpf"][0]["valid"] is True
    # A forma completa nao aparece em lugar nenhum da saida.
    assert CPF_VALIDO not in str(resultado["cpf"])


def test_cnpj_vem_completo_porque_e_pessoa_juridica() -> None:
    """O CNPJ do exequente identifica a natureza da divida e e dado util."""
    resultado = fields.documents(f"CONDOMINIO EXEMPLO (CNPJ {CNPJ_VALIDO})")
    assert resultado["cnpj"][0]["value"] == CNPJ_VALIDO
    assert resultado["cnpj"][0]["valid"] is True


def test_digito_verificador_separa_documento_de_sequencia_qualquer() -> None:
    assert fields.documents("CPF 111.111.111-11")["cpf"][0]["valid"] is False
    assert fields.documents("CNPJ 11.222.333/0001-99")["cnpj"][0]["valid"] is False


def test_redact_cpf_higieniza_o_texto_inteiro() -> None:
    texto = f"executados {CPF_VALIDO} e 529.982.247-25, conforme autos"
    limpo = fields.redact_cpf(texto)
    assert "529.982.247-25" not in limpo
    assert limpo.count("***.982.247-**") == 2
    # O resto do texto sobrevive intacto.
    assert "conforme autos" in limpo


# ─── Composição ─────────────────────────────────────────────────────────────

def test_extract_all_nao_vaza_cpf() -> None:
    """A saida composta e o que vai para o prompt e para o banco."""
    resultado = fields.extract_all(f"executado CPF {CPF_VALIDO}, imóvel avaliado em R$ 10,00")
    assert resultado["cpf_count"] == 1
    assert CPF_VALIDO not in str(resultado)


def test_extract_all_em_texto_vazio_nao_quebra() -> None:
    resultado = fields.extract_all("")
    assert resultado["money"] == []
    assert resultado["auction_rounds"] == {"first": [], "second": []}
    assert resultado["cpf_count"] == 0


# ─── Percentuais e áreas ────────────────────────────────────────────────────

def test_percentuais_e_areas() -> None:
    texto = "comissão de 5% e lance mínimo de 60% da avaliação; área de 66,02m²"
    assert [p.value for p in fields.percentages(texto)] == [5.0, 60.0]
    assert [a.value for a in fields.areas(texto)] == [66.02]
