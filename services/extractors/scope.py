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
            "Não consigo dizer se vale a pena — isso depende do seu objetivo e de "
            "valores que o edital não traz.\n\n"
            "O que dá para fazer é listar os custos que o edital menciona e o que "
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
            "Não estimo valor de mercado nem projeto valorização — o edital não "
            "traz essa informação e eu não tenho como apurá-la.\n\n"
            "Posso mostrar a avaliação judicial e o lance mínimo de cada praça, "
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
            "Não dou orientação jurídica nem digo o que você deve fazer legalmente. "
            "Para isso procure um advogado.\n\n"
            "O que posso fazer é explicar o que o edital diz sobre riscos, prazos e "
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


# ─── Escolha do imóvel ──────────────────────────────────────────────────────
#
# Vive aqui, junto do outro leitor de intenção da conversa, porque é o mesmo
# tipo de trabalho: interpretar uma resposta curta de pessoa sem envolver
# modelo. Uma escolha de lote errada faria a ficha descrever o imóvel errado —
# pior que não entender e perguntar de novo.

_AFFIRMATIVE = re.compile(
    r"^\s*(sim|s|isso|esse|este|e esse|e este|confirmo|pode ser|ok|certo|"
    r"exato|isso mesmo|correto|positivo|👍|✅)\s*[.!]?\s*$"
)
_NEGATIVE = re.compile(r"^\s*(nao|n|nenhum|outro|errado|nem um)\s*[.!]?\s*$")
_ORDINAL_WORDS = {
    "primeiro": 1, "primeira": 1, "segundo": 2, "segunda": 2, "terceiro": 3,
    "terceira": 3, "quarto": 4, "quarta": 4, "quinto": 5, "quinta": 5,
    "sexto": 6, "sexta": 6, "setimo": 7, "setima": 7, "oitavo": 8, "oitava": 8,
    "nono": 9, "nona": 9, "decimo": 10, "decima": 10,
}


# Acima disto a lista não cabe numa mensagem e a pessoa aponta o imóvel pelo
# item, pela matrícula, pelo número do bem ou por parte do endereço.
LIST_LIMIT = 12
# Até quantos candidatos vale listar para a pessoa escolher.
CANDIDATES_LIMIT = 10

_STOPWORDS = {
    "rua", "avenida", "alameda", "travessa", "estrada", "rodovia", "praca",
    # "casa" e "apartamento" ficam de fora desta lista: o tipo do imóvel entra
    # no texto comparado, e "casa" também é nome de rua — "Alameda Casa
    # Branca" sem ela virava qualquer imóvel em Areia Branca.
    "apto", "numero", "quero", "imovel", "bloco", "lote",
    "item", "matricula", "bem", "cidade", "bairro", "esse", "este", "aquele",
    "analisar", "analise", "sobre", "com", "que", "para", "por", "uma", "dos",
    "das", "del", "sim",
}


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _numbers(normalized: str) -> set[str]:
    """Números da resposta, sem pontuação: "81.909" e "81909" são o mesmo."""
    return {_digits(n) for n in re.findall(r"\d[\d.\-/]*\d|\d", normalized)}


def _lot_text(lot: dict) -> str:
    partes = (lot.get("development"), lot.get("address"), lot.get("district"),
              lot.get("city"), lot.get("kind"))
    return _normalize(" ".join(p for p in partes if p))


def _parse_catalog_choice(normalized: str, lots: list[dict]) -> dict[str, Any]:
    """Escolha num catálogo, onde a pessoa não vê a lista.

    Aceita, nesta ordem de precisão: número do bem, matrícula, "item N" e parte
    do endereço ou do nome do empreendimento. Número solto que casa com o item
    de um imóvel e a matrícula de outro é ambíguo, e volta como candidatos em
    vez de ser resolvido no palpite.
    """
    numeros = _numbers(normalized)
    palavras = [w for w in re.findall(r"[a-z]{3,}", normalized) if w not in _STOPWORDS]

    def achou(indices: list[int], motivo: str) -> dict[str, Any] | None:
        unicos = sorted(set(indices))
        if len(unicos) == 1:
            return {"understood": True, "index": unicos[0], "reason": motivo}
        if 1 < len(unicos) <= CANDIDATES_LIMIT:
            return {"understood": False, "index": None, "reason": "varios",
                    "candidates": unicos}
        if len(unicos) > CANDIDATES_LIMIT:
            return {"understood": False, "index": None, "reason": "muitos",
                    "count": len(unicos)}
        return None

    # Endereço ou empreendimento: todas as palavras e todos os números da
    # resposta precisam aparecer no imóvel. "Casa Branca 438" exige as três.
    if palavras:
        indices = []
        for i, lot in enumerate(lots, start=1):
            texto = _lot_text(lot)
            numeros_texto = _numbers(texto)
            if all(re.search(rf"\b{w}", texto) for w in palavras) and \
               all(n in numeros_texto or n == _digits(lot.get("item")) for n in numeros):
                indices.append(i)
        resultado = achou(indices, "endereco")
        if resultado:
            return resultado

    if not numeros:
        return {"understood": False, "index": None, "reason": "nao entendi"}

    por_bem = [i for i, l in enumerate(lots, 1) if _digits(l.get("asset_id")) in numeros]
    if por_bem:
        return achou(por_bem, "numero do bem")

    pede_item = re.search(r"\b(item|lote)\b", normalized)
    pede_matricula = re.search(r"\bmatr", normalized)
    por_matricula = [i for i, l in enumerate(lots, 1) if _digits(l.get("registry")) in numeros]
    por_item = [i for i, l in enumerate(lots, 1) if _digits(l.get("item")) in numeros]

    if pede_matricula and por_matricula:
        return achou(por_matricula, "matricula")
    if pede_item and por_item:
        return achou(por_item, "item")
    return achou(por_matricula + por_item, "matricula ou item") or {
        "understood": False, "index": None, "reason": "nao encontrado"}


def parse_lot_choice(text: str, lots: list[dict]) -> dict[str, Any]:
    """Interpreta a resposta à pergunta "qual imóvel?".

    Aceita o número da lista, a matrícula, uma palavra ordinal, e "sim" quando
    há um imóvel só. Devolve `index` de base 1 quando entendeu.

    Não adivinha. Resposta ambígua devolve `understood: False`, e o fluxo
    pergunta de novo — perguntar duas vezes custa uma mensagem, escolher o lote
    errado custa a ficha inteira e a confiança na resposta.
    """
    normalized = _normalize(text or "").strip()
    total = len(lots or [])

    if _NEGATIVE.match(normalized):
        return {"understood": False, "index": None, "reason": "recusou"}

    # Sem matrícula legível não há lista, e o fluxo pergunta "analiso assim
    # mesmo?". Recusar o "sim" aqui fazia a pergunta se repetir para sempre: o
    # bot pedia uma confirmação que ele mesmo não sabia aceitar. Não há lote a
    # escolher, então `index` fica nulo e a extração segue com o documento
    # inteiro — que é exatamente o que a pergunta prometeu.
    if not total:
        if _AFFIRMATIVE.match(normalized):
            return {"understood": True, "index": None,
                    "reason": "confirmou sem lote"}
        return {"understood": False, "index": None, "reason": "sem lotes"}

    # "sim" só resolve quando não há o que desambiguar.
    if _AFFIRMATIVE.match(normalized):
        if total == 1:
            return {"understood": True, "index": 1, "reason": "confirmou o unico"}
        return {"understood": False, "index": None,
                "reason": "confirmou sem dizer qual"}

    if lots[0].get("item") is not None or total > LIST_LIMIT:
        return _parse_catalog_choice(normalized, lots)

    # Matrícula: mais específica que o número da lista, e por isso vem antes —
    # "quero a 81.909" traz um número que não é índice. Comparação **exata**
    # por número: a versão anterior procurava os dígitos da matrícula dentro dos
    # dígitos da resposta, e num catálogo "41437" casaria antes com um imóvel
    # de matrícula "1437" que viesse primeiro na lista.
    numeros = _numbers(normalized)
    for position, lot in enumerate(lots, start=1):
        if _digits(lot.get("registry")) in numeros:
            return {"understood": True, "index": position, "reason": "matricula"}

    for word, value in _ORDINAL_WORDS.items():
        if re.search(rf"\b{word}\b", normalized) and 1 <= value <= total:
            return {"understood": True, "index": value, "reason": "ordinal"}

    numbers = [int(n) for n in re.findall(r"\b(\d{1,2})\b", normalized)]
    valid = [n for n in numbers if 1 <= n <= total]
    if len(valid) == 1:
        return {"understood": True, "index": valid[0], "reason": "numero"}
    if len(valid) > 1:
        return {"understood": False, "index": None, "reason": "mais de um numero"}

    return {"understood": False, "index": None, "reason": "nao entendi"}
