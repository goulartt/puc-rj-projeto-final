"""Extratores determinísticos de campos do edital.

Rodam sobre o Markdown antes do modelo e servem a dois fins: alimentam o prompt
como dica e **conferem** a resposta dele. Divergência vira `confidence: low` na
ficha, em vez de um desempate silencioso.

O motivo de existirem é concreto. Na primeira ficha gerada por modelo, 10 das
35 citações não existiam literalmente no edital — o modelo parafraseava onde
deveria copiar. Valor, data e número de matrícula são exatamente o tipo de dado
em que paráfrase é inaceitável e regex é melhor que qualquer modelo.

Cada extração devolve o texto cru como apareceu no documento, para que a ficha
possa citar o edital em vez de reescrevê-lo.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

CONTEXT_WINDOW = 120

# Distância máxima, em caracteres, entre o rótulo da praça e uma data para que
# elas sejam consideradas relacionadas. O edital escreve as duas datas de cada
# praça logo depois do rótulo; qualquer coisa muito além é outro assunto.
ROUND_PROXIMITY = 260


@dataclass(frozen=True)
class Found:
    value: Any
    raw: str
    context: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _context(text: str, start: int, end: int) -> str:
    """Trecho ao redor da ocorrência, com CPF sempre mascarado.

    A máscara vive aqui, e não em cada extrator, porque o contexto é o caminho
    por onde o dado pessoal escapa sem ninguém notar: o valor do CPF ia
    mascarado, mas o contexto de um valor monetário vizinho carregava o número
    inteiro para a ficha e para o banco.
    """
    window = text[max(0, start - CONTEXT_WINDOW):min(len(text), end + CONTEXT_WINDOW)]
    return redact_cpf(window).strip()


def _flatten(text: str) -> str:
    """O Docling quebra linha no meio de frases; contexto sem isso vira picado."""
    return re.sub(r"\s+", " ", text)


# ─── Dinheiro ───────────────────────────────────────────────────────────────

_MONEY = re.compile(r"R\$\s*((?:\d{1,3}(?:\.\d{3})*|\d+),\d{2})")


def money(text: str) -> list[Found]:
    """Valores em reais, no formato brasileiro.

    Devolve o número já convertido e o texto original. Ambos importam: o número
    para conferir contra o modelo, o original para a citação.
    """
    text = _flatten(text)
    out: list[Found] = []
    for match in _MONEY.finditer(text):
        amount = float(match.group(1).replace(".", "").replace(",", "."))
        out.append(Found(value=amount, raw=match.group(0), context=_context(text, *match.span())))
    return out


# ─── Datas ──────────────────────────────────────────────────────────────────

_DATE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
# Duas grafias, e a ordem importa: `10:11 horas` precisa ser tentada antes de
# `14h00`, senão o segundo padrão casa o "11 h" de "10:11 h*oras*" e devolve
# 11:00 para um leilão que começa às 10:11.
_TIME_NEARBY = re.compile(r"(\d{1,2})\s*:\s*(\d{2})|(\d{1,2})\s*h\s*(\d{2})?")


def dates(text: str) -> list[Found]:
    """Datas em DD/MM/AAAA, normalizadas para ISO."""
    text = _flatten(text)
    out: list[Found] = []
    for match in _DATE.finditer(text):
        day, month, year = match.groups()
        if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
            continue
        out.append(
            Found(
                value=f"{year}-{month}-{day}",
                raw=match.group(0),
                context=_context(text, *match.span()),
            )
        )
    return out


# Rótulo da praça e as formas que aparecem em edital. `1º Leilão`, `1ª praça` e
# `primeira praça` significam a mesma coisa.
#
# `°` (sinal de grau, U+00B0) entra junto de `º` (indicador ordinal, U+00BA)
# porque são visualmente idênticos e os editais usam os dois sem critério. Um
# edital real escrito com `1° leilão` fez a extração de praças devolver lista
# vazia — o extrator não achava nada e nada indicava que algo tinha falhado.
_ORDINAL = "[ºª°o]?"

_ROUND_LABELS = [
    ("first", re.compile(rf"(1{_ORDINAL}|primeir[ao])\s*(leil[ãa]o|pra[çc]a|hasta)",
                         re.IGNORECASE)),
    ("second", re.compile(rf"(2{_ORDINAL}|segund[ao])\s*(leil[ãa]o|pra[çc]a|hasta)",
                          re.IGNORECASE)),
]


def auction_rounds(text: str) -> dict[str, list[dict]]:
    """Associa datas ao rótulo de praça que as antecede.

    O edital diz "tendo o 1º Leilão início no dia 20/07/2026 às 14h00, e se
    encerrará dia 23/07/2026". Sem associar a data ao rótulo, sobra uma lista de
    datas soltas e o modelo tem de adivinhar qual é qual.

    A regra: uma data pertence ao último rótulo de praça anterior a ela, **desde
    que esteja perto dele**. Sem o limite de distância, tudo o que vem depois do
    último rótulo é absorvido — no edital de exemplo, a segunda praça engolia a
    data de uma resolução do CNJ de 2016 e dois vencimentos de IPTU, que estão
    milhares de caracteres adiante e não têm relação nenhuma.
    """
    text = _flatten(text)

    marks: list[tuple[int, str]] = []
    for label, regex in _ROUND_LABELS:
        marks.extend((m.start(), label) for m in regex.finditer(text))
    marks.sort()

    rounds: dict[str, list[dict]] = {"first": [], "second": []}
    for match in _DATE.finditer(text):
        previous = [(position, label) for position, label in marks if position < match.start()]
        if not previous:
            continue
        position, label = previous[-1]
        if match.start() - position > ROUND_PROXIMITY:
            continue
        day, month, year = match.groups()
        rounds[label].append(
            {
                "date": f"{year}-{month}-{day}",
                "raw": match.group(0),
                "time": _time_after(text, match.end()),
                "context": _context(text, *match.span()),
            }
        )
    return rounds


def _time_after(text: str, position: int) -> str | None:
    """Hora logo depois da data — `20/07/2026 às 14h00`."""
    window = text[position:position + 24]
    match = _TIME_NEARBY.search(window)
    if not match:
        return None
    if match.group(1) is not None:
        hour, minute = match.group(1), match.group(2)
    else:
        hour, minute = match.group(3), match.group(4) or "00"
    return f"{int(hour):02d}:{minute}"


# ─── Matrícula do imóvel ────────────────────────────────────────────────────

# Exige o substantivo `matrícula`, e não `matriculado`: o mesmo edital diz
# "matriculado na Junta Comercial ... sob o nº 798" a respeito do leiloeiro,
# e casar isso poria o registro do leiloeiro no campo do imóvel.
_REGISTRY = re.compile(
    r"matr[íi]cula\s*(?:imobili[áa]ria\s*)?n?[º°.]?\s*([\d][\d.\-/]*\d)",
    re.IGNORECASE,
)
_REGISTRY_OFFICE = re.compile(
    r"(registro\s+de\s+im[óo]ve|cart[óo]rio|oficial\s+de\s+registro|\bCRI\b|serventia)",
    re.IGNORECASE,
)
# Termos que indicam registro de OUTRA coisa que não o imóvel.
_NOT_PROPERTY = re.compile(r"junta\s+comercial|jucesp|leiloeiro|OAB|CRECI", re.IGNORECASE)


def property_registry(text: str) -> list[Found]:
    """Matrícula do imóvel no Registro de Imóveis.

    A condição é o que vem **depois** do número: precisa mencionar cartório ou
    registro de imóveis, e não pode mencionar Junta Comercial ou leiloeiro.

    Olhar o texto anterior parecia prudente e era errado: o edital cita o
    leiloeiro antes do imóvel, e a frase anterior contaminava a matrícula
    correta, que passava a ser descartada. O que vem depois é o que qualifica
    o número.
    """
    text = _flatten(text)
    out: list[Found] = []
    for match in _REGISTRY.finditer(text):
        after = text[match.end():match.end() + 140]
        if _NOT_PROPERTY.search(after):
            continue
        if not _REGISTRY_OFFICE.search(after):
            continue
        out.append(
            Found(
                value=match.group(1),
                raw=match.group(0),
                context=_context(text, *match.span()),
            )
        )
    return out


# ─── CPF e CNPJ ─────────────────────────────────────────────────────────────

_CPF = re.compile(r"\b(\d{3})\.(\d{3})\.(\d{3})-(\d{2})\b")
_CNPJ = re.compile(r"\b(\d{2})\.(\d{3})\.(\d{3})/(\d{4})-(\d{2})\b")


def _valid_cpf(digits: str) -> bool:
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for size in (9, 10):
        total = sum(int(digits[i]) * (size + 1 - i) for i in range(size))
        check = (total * 10) % 11 % 10
        if check != int(digits[size]):
            return False
    return True


def _valid_cnpj(digits: str) -> bool:
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    for size, weights in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
                          (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        total = sum(int(digits[i]) * weights[i] for i in range(size))
        check = 0 if total % 11 < 2 else 11 - total % 11
        if check != int(digits[size]):
            return False
    return True


def documents(text: str) -> dict[str, list[dict]]:
    """Localiza CPF e CNPJ, **mascarados**.

    Existe para proteger, não para coletar: o CPF de pessoa física executada
    consta do edital e não pode ir para a ficha nem para o banco. Devolvemos a
    posição e a forma mascarada para que o texto possa ser higienizado antes de
    ser persistido ou enviado a um provedor.

    O CNPJ do exequente é pessoa jurídica e identifica a natureza da dívida —
    ele é útil e vem sem máscara.
    """
    text = _flatten(text)

    people: list[dict] = []
    for match in _CPF.finditer(text):
        digits = "".join(match.groups())
        people.append(
            {
                "masked": f"***.{match.group(2)}.{match.group(3)}-**",
                "valid": _valid_cpf(digits),
                "context": _context(text, *match.span()),
            }
        )

    companies: list[dict] = []
    for match in _CNPJ.finditer(text):
        digits = "".join(match.groups())
        companies.append(
            {
                "value": match.group(0),
                "valid": _valid_cnpj(digits),
                "context": _context(text, *match.span()),
            }
        )

    return {"cpf": people, "cnpj": companies}


def redact_cpf(text: str) -> str:
    """Substitui todo CPF pela forma mascarada.

    Aplicado ao Markdown antes de persistir e antes de enviar ao provedor: o
    edital é público, mas reunir dado pessoal num banco indexado e devolvê-lo
    num chat é outra coisa.
    """
    return _CPF.sub(lambda m: f"***.{m.group(2)}.{m.group(3)}-**", text)


# ─── Percentuais e áreas ────────────────────────────────────────────────────

_PERCENT = re.compile(r"(\d{1,3}(?:,\d+)?)\s*%")
_AREA = re.compile(r"([\d.]+,\d+|\d+)\s*m\s*[²2]", re.IGNORECASE)


def percentages(text: str) -> list[Found]:
    text = _flatten(text)
    return [
        Found(
            value=float(m.group(1).replace(",", ".")),
            raw=m.group(0),
            context=_context(text, *m.span()),
        )
        for m in _PERCENT.finditer(text)
    ]


def areas(text: str) -> list[Found]:
    text = _flatten(text)
    return [
        Found(
            value=float(m.group(1).replace(".", "").replace(",", "."))
            if "," in m.group(1) else float(m.group(1)),
            raw=m.group(0),
            context=_context(text, *m.span()),
        )
        for m in _AREA.finditer(text)
    ]


# ─── Composição ─────────────────────────────────────────────────────────────

def extract_all(text: str) -> dict[str, Any]:
    """Tudo o que dá para afirmar sobre o edital sem envolver um modelo."""
    docs = documents(text)
    return {
        "money": [f.to_dict() for f in money(text)],
        "dates": [f.to_dict() for f in dates(text)],
        "auction_rounds": auction_rounds(text),
        "property_registry": [f.to_dict() for f in property_registry(text)],
        "percentages": [f.to_dict() for f in percentages(text)],
        "areas": [f.to_dict() for f in areas(text)],
        # Só a contagem e a forma mascarada: o valor real não sai daqui.
        "cpf_count": len(docs["cpf"]),
        "cpf_masked": [d["masked"] for d in docs["cpf"]],
        "cnpj": [{"value": d["value"], "valid": d["valid"]} for d in docs["cnpj"]],
    }
