"""Classificação de escopo da pergunta, antes de ela chegar ao modelo.

Existe porque o prompt não bastou. O `qa-system.md` diz, em texto claro, que o
assistente não opina se vale a pena arrematar — e o qwen3:14b, perguntado
"vale a pena comprar esse imóvel?", respondeu com análise de investimento,
pontos positivos e comentário sobre valorização do bairro. Instrução em prompt
é pedido; num modelo pequeno, um pedido que ele às vezes atende.

O limite que o produto promete não pode depender disso. Aqui ele é uma regra.

**Deliberadamente conservador.** Recusar uma pergunta respondível é pior que
deixar passar uma duvidosa: a primeira quebra o produto, a segunda ainda
encontra a instrução do prompt como segunda camada. Por isso só entram padrões
inequívocos. `quanto vale` ficou de fora de propósito — "quanto vale a comissão
do leiloeiro?" é pergunta sobre o edital e tem resposta na ficha.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Cada regra traz a frase que o assistente devolve. A recusa nomeia o limite e
# oferece o que dá para fazer — recusa sem alternativa é só uma porta fechada.
RULES: list[dict[str, Any]] = [
    {
        "kind": "investment_advice",
        "pattern": (
            r"vale a pena|compensa (comprar|arrematar|investir|dar lance)"
            r"|devo (comprar|arrematar|investir|dar (um )?lance|participar)"
            r"|(e|eh) um bom (negocio|investimento)"
            r"|(voce |vc )?(me )?(recomenda|aconselha|indica)"
            r"|(vale|valeria) o investimento"
        ),
        "reply": (
            "Nao consigo dizer se vale a pena — isso depende do seu objetivo e de "
            "valores que o edital nao traz.\n\n"
            "O que da para fazer e listar os custos que o edital menciona e o que "
            "ele deixa em aberto. Quer?"
        ),
    },
    {
        "kind": "valuation",
        "pattern": (
            r"valor de mercado|preco de mercado|vai valoriz|valorizacao"
            r"|quanto (vou|posso|da para) (lucrar|ganhar|revender)"
            r"|retorno (do |sobre o )?investimento|margem de lucro"
            r"|quanto (vale|custa) (esse |este |o )?(imovel|apartamento|bem) hoje"
        ),
        "reply": (
            "Nao estimo valor de mercado nem projeto valorizacao — o edital nao "
            "traz essa informacao e eu nao tenho como apura-la.\n\n"
            "Posso mostrar a avaliacao judicial e o lance minimo de cada praca, "
            "que constam do documento."
        ),
    },
    {
        "kind": "legal_advice",
        "pattern": (
            r"posso processar|entrar com (uma )?acao|abrir (um )?processo contra"
            r"|tenho direito (a|de)|me (representa|defende)"
            r"|(qual|preciso de) advogado|como (eu )?processo"
        ),
        "reply": (
            "Nao dou orientacao juridica nem digo o que voce deve fazer legalmente. "
            "Para isso procure um advogado.\n\n"
            "O que posso fazer e explicar o que o edital diz sobre riscos, prazos e "
            "responsabilidades do arrematante."
        ),
    },
]

_COMPILED = [(rule, re.compile(rule["pattern"])) for rule in RULES]


def _normalize(text: str) -> str:
    """Minúsculas e sem acento: `você recomenda` e `voce recomenda` são a mesma
    pergunta, e ninguém acentua com pressa no Telegram."""
    stripped = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in stripped if unicodedata.category(c) != "Mn")


def classify(question: str) -> dict[str, Any]:
    """Decide se a pergunta pode ir ao modelo.

    Devolve `in_scope: False` com a resposta pronta quando a pergunta pede
    justamente o que o assistente não faz.
    """
    normalized = _normalize(question or "")
    for rule, regex in _COMPILED:
        match = regex.search(normalized)
        if match:
            return {
                "in_scope": False,
                "kind": rule["kind"],
                "reply": rule["reply"],
                "matched": match.group(0),
            }
    return {"in_scope": True, "kind": None, "reply": None, "matched": None}


def classify_all(questions: list[str]) -> list[dict[str, Any]]:
    return [classify(q) for q in questions]
