"""Extração e validação de números de processo no padrão CNJ.

O número do processo é o campo mais valioso da ficha: é o único que pode ser
conferido contra uma fonte externa (DataJud). Por isso vale validar o dígito
verificador antes de confiar — a validação descarta sequências de dígitos que
apenas *parecem* um número de processo.

Formato (Resolução CNJ 65/2008):

    NNNNNNN-DD.AAAA.J.TR.OOOO
    |       |  |    | |  └── unidade de origem (4)
    |       |  |    | └───── tribunal (2)
    |       |  |    └─────── segmento do Judiciário (1)
    |       |  └──────────── ano de ajuizamento (4)
    |       └─────────────── dígito verificador (2)
    └─────────────────────── sequencial por origem/ano (7)
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

# Aceita o número formatado e também a forma "crua" de 20 dígitos, que aparece
# em alguns editais copiados de sistemas processuais.
_FORMATTED = re.compile(r"\b(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})\b")
_BARE = re.compile(r"(?<!\d)(\d{7})(\d{2})(\d{4})(\d)(\d{2})(\d{4})(?!\d)")

SEGMENTS = {
    "1": "Supremo Tribunal Federal",
    "2": "Conselho Nacional de Justiça",
    "3": "Superior Tribunal de Justiça",
    "4": "Justiça Federal",
    "5": "Justiça do Trabalho",
    "6": "Justiça Eleitoral",
    "7": "Justiça Militar da União",
    "8": "Justiça Estadual",
    "9": "Justiça Militar Estadual",
}

# Justiça Estadual (segmento 8): código do tribunal -> UF.
# É o segmento que interessa a leilão de imóvel; os demais raramente aparecem.
STATE_COURTS = {
    "01": "AC", "02": "AL", "03": "AP", "04": "AM", "05": "BA", "06": "CE",
    "07": "DF", "08": "ES", "09": "GO", "10": "MA", "11": "MT", "12": "MS",
    "13": "MG", "14": "PA", "15": "PB", "16": "PR", "17": "PE", "18": "PI",
    "19": "RJ", "20": "RN", "21": "RS", "22": "RO", "23": "RR", "24": "SC",
    "25": "SE", "26": "SP", "27": "TO",
}

# Um edital costuma citar jurisprudência no meio do juridiquês. Esses números
# são de processos alheios ao imóvel: consultá-los enriqueceria a ficha com a
# situação de outra causa, o que é pior do que não consultar nada.
_CITATION_MARKERS = re.compile(
    r"agravo|apela[çc][ãa]o|precedent|s[úu]mula|recurso\s+(especial|extraordin[áa]rio)"
    r"|jurisprud|ac[óo]rd[ãa]o|REsp|RE\s+\d",
    re.IGNORECASE,
)

# Contexto que indica o processo do próprio leilão.
_MAIN_CASE_MARKERS = re.compile(
    r"processo\s*n|autos|execu[çc][ãa]o|a[çc][ãa]o\s+de|cumprimento\s+de\s+senten[çc]a",
    re.IGNORECASE,
)

# Quantos caracteres antes do número olhar para classificar.
_CONTEXT_WINDOW = 160

FIRST_INSTANCE = "first_instance"
SECOND_INSTANCE = "second_instance"

ROLE_MAIN = "main"
ROLE_CITED = "cited"
ROLE_UNDETERMINED = "undetermined"


@dataclass(frozen=True)
class CNJNumber:
    number: str          # sempre normalizado no formato com pontuação
    sequential: str
    check_digits: str
    year: str
    segment: str
    court: str
    origin: str
    valid: bool          # dígito verificador confere?
    segment_name: str
    state: str | None    # só para a Justiça Estadual
    datajud_alias: str | None  # índice da API pública, ex.: api_publica_tjrj
    instance: str        # FIRST_INSTANCE | SECOND_INSTANCE
    role: str            # ROLE_MAIN | ROLE_CITED | ROLE_UNDETERMINED
    context: str         # trecho ao redor, para auditoria e para a ficha

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_check_digits(
    sequential: str, year: str, segment: str, court: str, origin: str
) -> str:
    """Calcula o DV pelo módulo 97 base 10 (ISO 7064).

    Concatena os campos na ordem NNNNNNN AAAA J TR OOOO, multiplica por 100
    (o mesmo que anexar o campo DD zerado) e o dígito é 98 menos o resto.
    """
    base = int(f"{sequential}{year}{segment}{court}{origin}") * 100
    return f"{98 - (base % 97):02d}"


def _datajud_alias(segment: str, court: str) -> str | None:
    """Índice do DataJud correspondente ao tribunal, derivado do próprio número.

    Só cobrimos a Justiça Estadual: é onde ocorre a execução que leva o imóvel
    a leilão. Outros segmentos devolvem None e o fluxo trata como fora de
    cobertura, em vez de chutar um índice que não existe.
    """
    if segment != "8":
        return None
    state = STATE_COURTS.get(court)
    return f"api_publica_tj{state.lower()}" if state else None


def _classify(origin: str, context: str) -> tuple[str, str]:
    """Decide instância e papel a partir da origem e do texto que antecede.

    A origem `0000` significa que o processo corre no próprio tribunal, ou seja,
    segundo grau. A execução que leva um imóvel a leilão corre em primeiro grau,
    então um número de segundo grau num edital é quase sempre jurisprudência
    citada — e é exatamente esse o caso que precisamos descartar.
    """
    instance = SECOND_INSTANCE if origin == "0000" else FIRST_INSTANCE

    if instance == SECOND_INSTANCE or _CITATION_MARKERS.search(context):
        return instance, ROLE_CITED
    if _MAIN_CASE_MARKERS.search(context):
        return instance, ROLE_MAIN
    return instance, ROLE_UNDETERMINED


def _build(
    sequential: str, check: str, year: str, segment: str, court: str, origin: str,
    context: str = "",
) -> CNJNumber:
    state = STATE_COURTS.get(court) if segment == "8" else None
    instance, role = _classify(origin, context)
    return CNJNumber(
        number=f"{sequential}-{check}.{year}.{segment}.{court}.{origin}",
        sequential=sequential,
        check_digits=check,
        year=year,
        segment=segment,
        court=court,
        origin=origin,
        valid=(check == compute_check_digits(sequential, year, segment, court, origin)),
        segment_name=SEGMENTS.get(segment, "desconhecido"),
        state=state,
        datajud_alias=_datajud_alias(segment, court),
        instance=instance,
        role=role,
        context=context.strip(),
    )


def parse(text: str) -> CNJNumber | None:
    """Interpreta uma única string como número CNJ. None se não casar o formato."""
    match = _FORMATTED.search(text) or _BARE.search(text)
    if not match:
        return None
    context = text[max(0, match.start() - _CONTEXT_WINDOW):match.start()]
    return _build(*match.groups(), context=context)


def extract(text: str, *, valid_only: bool = True) -> list[CNJNumber]:
    """Extrai todos os números CNJ do texto, sem repetição e na ordem de aparição.

    Por padrão devolve só os que passam no dígito verificador. Passe
    ``valid_only=False`` para inspecionar candidatos reprovados — útil ao
    diagnosticar um edital em que a conversão embaralhou dígitos.
    """
    # Espaços normalizados: o Markdown do Docling quebra linha no meio de
    # frases, e a janela de contexto ficaria truncada sem isso.
    text = re.sub(r"\s+", " ", text)

    found: list[CNJNumber] = []
    seen: set[str] = set()

    for regex in (_FORMATTED, _BARE):
        for match in regex.finditer(text):
            context = text[max(0, match.start() - _CONTEXT_WINDOW):match.start()]
            item = _build(*match.groups(), context=context)
            if item.number in seen:
                continue
            if valid_only and not item.valid:
                continue
            seen.add(item.number)
            found.append(item)

    return found


def main_case(text: str) -> CNJNumber | None:
    """Devolve o processo do leilão, descartando jurisprudência citada.

    Um edital cita precedentes no meio do texto jurídico; consultar esses
    números no DataJud traria a situação de causas alheias ao imóvel, o que é
    pior do que não consultar nada. Preferimos o candidato explicitamente
    marcado como principal; na falta dele, o primeiro de primeiro grau.

    Devolve None quando nada sobra — e aí o fluxo trata como leilão sem
    processo identificável, em vez de escolher um número no chute.
    """
    candidates = [n for n in extract(text) if n.role != ROLE_CITED]
    if not candidates:
        return None
    for candidate in candidates:
        if candidate.role == ROLE_MAIN:
            return candidate
    return candidates[0]
