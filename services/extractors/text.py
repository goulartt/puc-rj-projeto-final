"""Normalização de texto convertido de PDF.

A conversão de PDF para Markdown deixa marcas que não existem no documento
original: espaço antes de vírgula e de parêntese de fechamento, aspas de
abertura e fechamento trocadas, hífen colado na palavra seguinte, quebra de
linha no meio da frase.

Nada disso muda o conteúdo, mas tudo isso quebra comparação literal. Como o
projeto inteiro se apoia em citar trechos do edital — e em *conferir* que a
citação existe —, a normalização precisa ser explícita e compartilhada entre a
extração e a avaliação, e não reinventada em cada script.
"""

from __future__ import annotations

import re
import unicodedata

# Aspas tipográficas e variantes de apóstrofo que o conversor mistura.
_QUOTES = {
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "‘": "'", "’": "'", "‚": "'", "′": "'", "´": "'",
}
# Travessões e hifens variados viram hífen simples.
_DASHES = {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"}

_TRANSLATION = str.maketrans({**_QUOTES, **_DASHES})

_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,;.:!?)\]}])")
_SPACE_AFTER_OPENING = re.compile(r"([(\[{])\s+")
_MULTIPLE_SPACES = re.compile(r"\s+")
_ANY_QUOTE = re.compile(r"[\"']")
_PADDED_QUOTE = re.compile(r'\s*"\s*')

CITATION_PART_SEPARATOR = "[...]"
MIN_CITATION_LENGTH = 12


def normalize(text: str, *, casefold: bool = True) -> str:
    """Reduz o texto a uma forma comparável, preservando as palavras.

    Colapsa espaços, remove espaço adjacente à pontuação e uniformiza aspas.
    Não remove pontuação nem acento: um edital que diz "não" e outro que diz
    "nao" são coisas diferentes, e apagar isso esconderia erro de conversão.
    """
    result = unicodedata.normalize("NFC", text).translate(_TRANSLATION)
    result = _MULTIPLE_SPACES.sub(" ", result)
    result = _SPACE_BEFORE_PUNCT.sub(r"\1", result)
    result = _SPACE_AFTER_OPENING.sub(r"\1", result)
    result = result.strip()
    return result.casefold() if casefold else result


def _for_comparison(text: str) -> str:
    """Normalização mais permissiva, só para conferir citação.

    Faz duas coisas além de `normalize`, ambas por causa de artefatos reais do
    conversor observados no edital de exemplo:

    - achata aspa simples e dupla, porque a conversão as troca — o edital traz
      `"AD CORPUS '`, abrindo com dupla e fechando com simples;
    - remove espaço colado à aspa, pelo mesmo motivo (`CORPUS '`).

    A identidade e o espaçamento da aspa não carregam sentido; exigi-los
    reprovaria citação correta.
    """
    return _PADDED_QUOTE.sub('"', _ANY_QUOTE.sub('"', normalize(text)))


def contains_citation(
    document: str, citation: str, *, minimum_length: int = MIN_CITATION_LENGTH
) -> bool:
    """Diz se `citation` aparece em `document`, ignorando artefatos de conversão.

    Citações compostas usam `[...]` para juntar partes distantes do texto; cada
    parte é conferida separadamente. Fragmentos muito curtos são ignorados,
    porque casariam por acaso e não provariam nada.
    """
    haystack = _for_comparison(document)
    for part in citation.split(CITATION_PART_SEPARATOR):
        needle = _for_comparison(part)
        if len(needle) < minimum_length:
            continue
        if needle not in haystack:
            return False
    return True
