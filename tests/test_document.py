"""Testes da trava de entrada: isto é mesmo um edital de leilão?

Roda antes de qualquer chamada de modelo. Se o PDF for um contrato, uma
matrícula avulsa ou um boleto, a pessoa descobre em segundos em vez de esperar
dois minutos por uma ficha inventada sobre um documento que não é edital.

O equilíbrio aqui é o **oposto** do de `test_scope.py`. Lá, recusar demais
quebra o produto. Aqui, recusar um edital legítimo de redação incomum impede a
pessoa de usar o sistema, enquanto aceitar um documento errado custa meio
centavo e produz uma ficha visivelmente sem sentido. Por isso os testes de
baixo — os que precisam ser aceitos — mandam mais que os de cima.

    pytest tests/test_document.py -q
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services"))

from extractors import document  # noqa: E402


def encher(texto: str) -> str:
    """Completa até passar do mínimo de caracteres, sem acrescentar sinal.

    Os testes de conteúdo não devem passar ou falhar por tamanho — para isso há
    um teste próprio.
    """
    recheio = (" O presente instrumento observa as disposições aplicáveis e "
               "produz efeitos entre as partes na forma da legislação. ")
    while len(texto) < document.MIN_CHARS + 100:
        texto += recheio
    return texto


# ─── O que precisa ser recusado ─────────────────────────────────────────────

def test_pdf_digitalizado_sem_camada_de_texto() -> None:
    """O Docling devolve resíduo quando o PDF é imagem de página."""
    veredito = document.inspect("Página 1\n\n\n2\n")
    assert veredito["is_notice"] is False
    assert veredito["reason"] == "no_text"
    assert "digitalizado" in document.rejection_message(veredito)


def test_contrato_de_locacao() -> None:
    """Cita imóvel, matrícula e valor à exaustão, e nunca fala em leilão."""
    veredito = document.inspect(encher(
        "CONTRATO DE LOCAÇÃO RESIDENCIAL. O LOCADOR cede ao LOCATÁRIO o imóvel "
        "situado na Rua das Flores, 100, objeto da matrícula nº 12.345 do "
        "Registro de Imóveis, pelo aluguel mensal de R$ 2.500,00, com reajuste "
        "anual. O apartamento será entregue em perfeitas condições."
    ))
    assert veredito["is_notice"] is False
    assert veredito["reason"] == "no_auction_terms"


def test_matricula_avulsa() -> None:
    """Marca imóvel, matrícula, valor e avaliação — e não é edital nenhum.

    É o caso que justifica exigir um termo de leilão além da contagem: só
    contar sinais deixaria este documento passar.
    """
    veredito = document.inspect(encher(
        "CERTIDÃO DE MATRÍCULA Nº 106.233 do 4º Oficial de Registro de Imóveis. "
        "R.1 - Compra e venda. Imóvel: apartamento nº 112. Avaliação para fins "
        "de ITBI: R$ 500.000,00. Av.2 - Averbação de construção."
    ))
    assert veredito["is_notice"] is False
    assert veredito["reason"] == "no_auction_terms"


def test_documento_que_so_cita_leilao_de_passagem() -> None:
    veredito = document.inspect(encher(
        "PARECER JURÍDICO. Consulta sobre a possibilidade de participação em "
        "leilão. Respondemos que a matéria comporta análise casuística."
    ))
    assert veredito["is_notice"] is False
    assert veredito["reason"] == "too_few_signals"


# ─── O que NÃO pode ser recusado ────────────────────────────────────────────
#
# Um edital legítimo barrado deixa a pessoa sem o produto. Estes valem mais que
# os testes acima.

def test_edital_curto_e_seco_passa() -> None:
    """Sem juridiquês, sem processo, sem cartório — e ainda é edital."""
    veredito = document.inspect(encher(
        "EDITAL DE LEILÃO. O leiloeiro oficial levará a leilão o imóvel "
        "residencial situado na Rua A, 10, matrícula 555, avaliado em "
        "R$ 300.000,00. Primeira praça em 10/09/2026; não havendo lance igual "
        "ou superior à avaliação, segue-se a segunda praça. A arrematação "
        "obedecerá ao CPC."
    ))
    assert veredito["is_notice"] is True


def test_edital_extrajudicial_sem_numero_de_processo() -> None:
    """Alienação fiduciária não tem processo, e nem por isso deixa de ser edital."""
    veredito = document.inspect(encher(
        "EDITAL DE LEILÃO EXTRAJUDICIAL. Nos termos da Lei nº 9.514/97, o "
        "leiloeiro levará a público leilão o imóvel objeto da matrícula 81.909, "
        "avaliado em R$ 600.000,00. Serão aceitos lances a partir do valor da "
        "dívida. O arrematante arcará com as despesas de transferência."
    ))
    assert veredito["is_notice"] is True


def test_edital_de_hasta_publica_com_vocabulario_antigo() -> None:
    veredito = document.inspect(encher(
        "EDITAL DE HASTA PÚBLICA. Faz saber que será levado a público pregão o "
        "bem imóvel penhorado nos autos, matrícula 9.937, avaliado em "
        "R$ 75.000,00, a quem maior lanço oferecer. A arrematação far-se-á à "
        "vista."
    ))
    assert veredito["is_notice"] is True


def test_os_cinco_editais_reais_passam() -> None:
    """Regressão contra o corpus, quando o Markdown estiver disponível.

    Pulado num clone sem os editais convertidos — a suíte não pode depender de
    o Docker estar de pé.
    """
    import glob

    encontrados = sorted(glob.glob(str(
        pathlib.Path(__file__).resolve().parents[1] / "data" / "editais" / "*.md")))
    if not encontrados:
        return
    for caminho in encontrados:
        veredito = document.inspect(pathlib.Path(caminho).read_text())
        assert veredito["is_notice"] is True, caminho


# ─── Forma da resposta ──────────────────────────────────────────────────────

def test_a_recusa_diz_o_que_fazer() -> None:
    """Recusa que não oferece saída só transfere o problema."""
    for motivo in ("no_text", "no_auction_terms", "too_few_signals"):
        mensagem = document.MESSAGES[motivo]
        assert len(mensagem) > 80
        assert "\n\n" in mensagem  # explicação e depois o que fazer


def test_o_veredito_lista_o_que_faltou() -> None:
    """A mensagem precisa poder dizer o que o sistema procurou."""
    veredito = document.inspect(encher("CONTRATO DE PRESTAÇÃO DE SERVIÇOS."))
    assert "menciona leilão" in veredito["missing"]
