"""Diz se o documento convertido é mesmo um edital de leilão de imóvel.

Roda depois da conversão e antes de tudo o que custa: se o PDF for um contrato,
uma matrícula avulsa ou um boleto, a pessoa descobre em segundos, e não depois
de dois minutos recebendo uma ficha inventada sobre um documento que não é
edital.

**O erro caro aqui é o oposto do de `scope.py`.** Lá, recusar demais quebra o
produto. Aqui, recusar um edital legítimo com redação incomum impede a pessoa
de usar o sistema, enquanto aceitar um documento errado custa meio centavo e
produz uma ficha visivelmente sem sentido, que ela percebe na hora. Por isso o
limiar é baixo: só barra o que claramente não é edital.
"""

from __future__ import annotations

import re
from typing import Any

# Markdown abaixo disto não tem texto suficiente para nada — quase sempre um
# PDF digitalizado sem camada de texto, em que o Docling devolve o resíduo dos
# poucos elementos que conseguiu ler.
MIN_CHARS = 600

# Cada sinal vale um ponto, e o peso está na variedade, não na repetição: um
# contrato de compra e venda cita "imóvel" e "matrícula" à exaustão sem nunca
# mencionar praça, leiloeiro ou lance.
SIGNALS: list[tuple[str, str, str]] = [
    ("leilao", r"leil[ãa]o|leiloeir", "menciona leilão"),
    ("praca", r"\bpra[çc]a\b|hasta p[úu]blica|1[ºªo°]\s*leil|2[ºªo°]\s*leil", "fala em praça ou leilão numerado"),
    ("arremata", r"arremata[çc][ãa]o|arrematante|arrematar", "fala em arrematação"),
    ("lance", r"\blan[çc]e[s]?\b|\blan[çc]o\b|maior lan", "fala em lance"),
    ("edital", r"\bedital\b", "identifica-se como edital"),
    ("avaliacao", r"avalia[çc][ãa]o|avaliado em", "traz avaliação"),
    ("imovel", r"\bim[óo]vel\b|apartamento|terreno|casa\b", "descreve um imóvel"),
    ("matricula", r"matr[íi]cula", "cita matrícula"),
    ("valor", r"R\$\s*\d", "traz valor em reais"),
    ("rito", r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}|aliena[çc][ãa]o fiduci[áa]ria"
             r"|lei\s*n?[º°.]?\s*9\.?514|CPC|c[óo]digo de processo civil",
     "indica o rito (judicial ou extrajudicial)"),
]

_COMPILED = [(key, re.compile(pattern, re.IGNORECASE), label)
             for key, pattern, label in SIGNALS]

# Quatro sinais distintos. Um edital de meia página tem todos; um contrato de
# locação, uma certidão ou uma matrícula avulsa não passam de dois.
MIN_SIGNALS = 4

# Sinais sem os quais não é edital de leilão, por mais que o resto apareça. Uma
# matrícula avulsa marca imóvel, matrícula, valor e avaliação — quatro pontos —
# e não é edital nenhum.
REQUIRED = {"leilao", "arremata", "praca"}


def inspect(text: str) -> dict[str, Any]:
    """Classifica o documento convertido.

    Devolve o veredito e **quais** sinais faltaram, porque a mensagem de recusa
    precisa dizer o que o sistema procurou — "não parece um edital" sem mais
    nada deixa a pessoa sem saber se o problema é o arquivo ou o serviço.
    """
    content = text or ""
    if len(content.strip()) < MIN_CHARS:
        return {
            "is_notice": False,
            "reason": "no_text",
            "chars": len(content.strip()),
            "found": [],
            "missing": [label for _, _, label in SIGNALS],
        }

    found = [(key, label) for key, regex, label in _COMPILED if regex.search(content)]
    keys = {key for key, _ in found}
    missing = [label for key, _, label in SIGNALS if key not in keys]

    enough = len(found) >= MIN_SIGNALS
    has_core = bool(keys & REQUIRED)

    if enough and has_core:
        reason = "ok"
    elif not has_core:
        reason = "no_auction_terms"
    else:
        reason = "too_few_signals"

    return {
        "is_notice": enough and has_core,
        "reason": reason,
        "chars": len(content.strip()),
        "signals": len(found),
        "found": [label for _, label in found],
        "missing": missing,
    }


# Mensagem por motivo. Cada uma diz o que houve e o que fazer — recusa que não
# oferece saída só transfere o problema.
MESSAGES = {
    "no_text": (
        "Não consegui ler texto neste PDF. Ele parece ser digitalizado, uma "
        "imagem de página em vez de texto.\n\n"
        "Tente um PDF gerado pelo site do leilão ou pelo tribunal, que costuma "
        "vir com o texto embutido."
    ),
    "no_auction_terms": (
        "Este documento não parece um edital de leilão: não encontrei menção a "
        "leilão, praça ou arrematação.\n\n"
        "Eu só analiso edital de leilão de imóvel. Se for uma matrícula, um "
        "contrato ou uma certidão, não consigo ajudar com ele."
    ),
    "too_few_signals": (
        "Este documento menciona leilão, mas não tem a estrutura de um edital — "
        "faltam elementos como descrição do imóvel, avaliação ou datas de "
        "praça.\n\n"
        "Se for um trecho ou um anexo, envie o edital completo."
    ),
}


def rejection_message(verdict: dict) -> str:
    return MESSAGES.get(verdict.get("reason"), MESSAGES["no_auction_terms"])
