"""Transforma a ficha no que a pessoa lê.

A ficha é escrita para ser precisa e auditável: chaves em inglês, caminhos de
schema, um bloco `{value, quote, confidence, source}` em cada campo. Isso está
certo para o banco e para a avaliação, e errado para uma conversa no Telegram —
`debts.enforced_claim` não é português.

Três coisas acontecem aqui, e nenhuma delas envolve modelo:

1. **Rótulo.** Caminho de schema vira frase em português.
2. **Risco genérico sai do resumo.** Prazo de pagamento e comissão do leiloeiro
   existem em todo leilão; ocupam as três linhas do resumo sem informar nada
   sobre *este* imóvel. Continuam na ficha — só não competem por espaço.
3. **Fato favorável.** Deságio na segunda praça, ausência de ônus e processo sem
   sinal de cancelamento são fatos verificáveis, e a pessoa merece vê-los tanto
   quanto os riscos.

Sobre o item 3, uma distinção que o produto inteiro depende: isto **não** é
recomendação. "Segunda praça com 40% de deságio" é um fato do edital, como
"imóvel ocupado" é. Dizer que vale a pena arrematar seria outra coisa, e
continua recusado em `scope.py`. A diferença entre relatar e aconselhar é a
mesma que separa este assistente de um corretor.
"""

from __future__ import annotations

import re
from typing import Any

# ─── Rótulos ────────────────────────────────────────────────────────────────
#
# Derivados dos caminhos reais do `ficha.schema.json`. O modelo cita o campo em
# `gaps[].field`, e sem tradução o caminho cru vaza para a tela — foi o que
# aconteceu na primeira ficha entregue por Telegram.

LABELS: dict[str, str] = {
    "procedure": "Tipo de leilão (judicial ou extrajudicial)",
    "procedure.type": "Tipo de leilão (judicial ou extrajudicial)",
    "court_case": "Processo judicial",
    "court_case.number": "Número do processo",
    "court_case.court": "Tribunal",
    "court_case.court_division": "Vara",
    "court_case.creditor": "Quem move a execução",
    "court_case.status": "Situação do processo",
    "court_case.cited_numbers": "Processos citados no edital",
    "property": "Dados do imóvel",
    "property.type": "Tipo do imóvel",
    "property.address": "Endereço",
    "property.registry_number": "Matrícula",
    "property.registry_office": "Cartório de registro",
    "property.municipal_id": "Inscrição municipal (IPTU)",
    "property.total_area_m2": "Área total",
    "property.private_area_m2": "Área privativa",
    "property.parking_space": "Vaga de garagem",
    "auction": "Dados do leilão",
    "auction.portal": "Site do leilão",
    "auction.auctioneer": "Leiloeiro",
    "auction.first_round": "Primeira praça",
    "auction.first_round.starts_at": "Início da primeira praça",
    "auction.first_round.ends_at": "Fim da primeira praça",
    "auction.first_round.minimum_bid": "Lance mínimo da primeira praça",
    "auction.first_round.criterion": "Critério de lance da primeira praça",
    "auction.second_round": "Segunda praça",
    "auction.second_round.starts_at": "Início da segunda praça",
    "auction.second_round.ends_at": "Fim da segunda praça",
    "auction.second_round.minimum_bid": "Lance mínimo da segunda praça",
    "auction.second_round.criterion": "Critério de lance da segunda praça",
    "appraisal": "Avaliação do imóvel",
    "appraisal.value": "Valor da avaliação",
    "appraisal.updated_value": "Valor da avaliação atualizado",
    "appraisal.note": "Observação sobre a avaliação",
    "occupancy": "Ocupação do imóvel",
    "occupancy.status": "Se o imóvel está ocupado",
    "occupancy.occupied_by": "Quem ocupa o imóvel",
    "encumbrances": "Ônus na matrícula (hipoteca, penhora, usufruto)",
    "debts": "Dívidas do imóvel",
    "debts.property_tax": "IPTU em aberto",
    "debts.condo_fees": "Dívida de condomínio",
    "debts.enforced_claim": "Valor cobrado na execução",
    "debts.buyer_liability": "Quais dívidas o arrematante assume",
    "auctioneer_fee": "Comissão do leiloeiro",
    "payment": "Forma de pagamento",
    "payment.cash_deadline": "Prazo para pagamento à vista",
    "payment.installments_allowed": "Se aceita parcelamento",
    "payment.installment_terms": "Condições do parcelamento",
    "risks": "Riscos",
    "gaps": "O que o edital não informa",
}

# Frases inteiras primeiro: traduzir palavra a palavra produz ordem errada em
# português. `auction minimum bid amount` vira "Leilão lance mínimo valor" pelo
# caminho ingênuo, e "Lance mínimo do leilão" por este.
_PHRASES = {
    "auction minimum bid amount": "Lance mínimo do leilão",
    "auction minimum bid": "Lance mínimo do leilão",
    "minimum bid amount": "Lance mínimo",
    "appraisal value": "Valor da avaliação",
    "occupancy status": "Se o imóvel está ocupado",
    "property registry number": "Matrícula do imóvel",
    "court case number": "Número do processo",
}

_TERMS = {
    "occupancy": "ocupação", "appraisal": "avaliação", "auction": "leilão",
    "property": "imóvel", "debts": "dívidas", "payment": "pagamento",
    "encumbrances": "ônus", "value": "valor", "status": "situação",
    "minimum bid": "lance mínimo", "amount": "valor", "court case": "processo",
    "registry": "matrícula", "fee": "comissão", "claim": "crédito",
}


def _key(text: str) -> str:
    """Forma canônica para comparação: só letras, números e espaço simples."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


# Termos finais ambíguos ou genéricos demais para identificar um campo sozinhos:
# `type` é de `procedure` e de `property`, `value` aparece em toda parte.
_TOO_GENERIC = {"type", "status", "value", "number", "court", "portal", "note",
                "address", "criterion", "creditor"}


def _build_aliases() -> dict[str, str]:
    """Índice de apelidos derivado dos próprios rótulos.

    O modelo nem sempre escreve o caminho completo: já mandou `condo fees` sem
    o prefixo `debts.` e `enforced claim value` com uma palavra a mais. Ambos
    caíam no tradutor palavra a palavra e saíam como "Condo fees" e "Enforced
    crédito valor" — inglês na tela do usuário, que é justamente o defeito que
    este módulo existe para eliminar.

    Indexar o último segmento resolve, desde que ele identifique o campo
    sozinho. Os que não identificam ficam de fora, porque um apelido ambíguo
    erraria em silêncio, que é pior que degradar.
    """
    last_segments: dict[str, list[str]] = {}
    for path in LABELS:
        last_segments.setdefault(path.rsplit(".", 1)[-1], []).append(path)

    aliases: dict[str, str] = {}
    for path, text in LABELS.items():
        aliases[_key(path)] = text

    for segment, paths in last_segments.items():
        if len(paths) > 1 or segment in _TOO_GENERIC:
            continue
        aliases.setdefault(_key(segment), LABELS[paths[0]])
    return aliases


_ALIASES = _build_aliases()


def label(field: str) -> str:
    """Rótulo em português para um campo da ficha.

    O modelo nem sempre devolve um caminho do schema: já escreveu
    `appraisal.value / auction minimum bid amount`, que é prosa. Por isso há
    degradação em vez de erro — traduzimos o que dá e devolvemos algo legível,
    em vez de mostrar o caminho cru ou engolir a lacuna.
    """
    raw = (field or "").strip()
    if not raw:
        return "Campo não identificado"
    if raw in LABELS:
        return LABELS[raw]

    # Caminho composto: traduz cada parte reconhecida e junta.
    parts = [p.strip() for p in re.split(r"\s*/\s*|\s+e\s+", raw) if p.strip()]
    if len(parts) > 1:
        return " e ".join(label(p) for p in parts)

    key = _key(raw)
    if key in _ALIASES:
        return _ALIASES[key]

    # Apelido contido no texto, do mais específico para o mais genérico:
    # `debts.enforced_claim value` contém `debts enforced claim`, e é isso que
    # o campo é — a palavra extra não muda nada.
    for alias in sorted(_ALIASES, key=len, reverse=True):
        if len(alias) >= 8 and alias in key:
            return _ALIASES[alias]

    # Prefixo conhecido com sufixo desconhecido: melhor o rótulo do pai do que
    # o caminho cru.
    if "." in raw:
        head = raw.rsplit(".", 1)[0]
        if head in LABELS:
            return LABELS[head]

    return _humanize(raw)


def _humanize(raw: str) -> str:
    text = raw.replace("_", " ").replace(".", " ").strip()
    for phrase, translation in _PHRASES.items():
        if phrase in text.lower():
            return translation
    for english, portuguese in _TERMS.items():
        text = re.sub(rf"\b{english}\b", portuguese, text, flags=re.IGNORECASE)
    return text[:1].upper() + text[1:]


# ─── Riscos genéricos ───────────────────────────────────────────────────────
#
# Presentes em praticamente todo edital de leilão. Não são falsos nem
# irrelevantes — são apenas iguais em todos, e no resumo de três linhas eles
# roubam o lugar do que distingue este imóvel dos outros.

_GENERIC_RISK = re.compile(
    # As duas ordens aparecem em edital: "prazo de pagamento ... 24 horas" e
    # "prazo de 24 horas para pagamento". Casar so uma deixava metade passar.
    r"prazo de pagamento"
    r"|(24|vinte e quatro)\s*(horas?|h)\b.{0,60}pagamento"
    r"|pagamento.{0,60}(24|vinte e quatro)\s*(horas?|h)\b"
    r"|comiss[ãa]o do leiloeiro"
    r"|perda (da |do )?(cau[çc][ãa]o|sinal)"
    r"|no estado em que se encontra"
    r"|ad corpus"
    r"|desist[êe]ncia (do |da )?arremata"
    r"|multa por inadimpl",
    re.IGNORECASE,
)


def is_generic_risk(description: str) -> bool:
    """Risco que vale para qualquer leilão, e não para este imóvel."""
    return bool(_GENERIC_RISK.search(description or ""))


def rank_risks(risks: list[dict]) -> list[dict]:
    """Ordena por severidade, com os genéricos no fim.

    Nenhum risco é descartado: quem chama decide quantos mostrar. Um risco
    genérico ainda é verdadeiro, e sumir com ele seria esconder informação — o
    que se faz é tirá-lo da frente do que é específico.
    """
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        risks or [],
        key=lambda r: (is_generic_risk(r.get("description", "")),
                       order.get(r.get("severity"), 3)),
    )


# ─── Fatos favoráveis ───────────────────────────────────────────────────────

def _amount(node: Any) -> float | None:
    if isinstance(node, dict):
        for key in ("amount_brl", "value"):
            value = node.get(key)
            if isinstance(value, (int, float)):
                return float(value)
    return float(node) if isinstance(node, (int, float)) else None


def _brl(value: float) -> str:
    text = f"{value:,.2f}".replace(",", "·").replace(".", ",").replace("·", ".")
    return f"R$ {text}"


def highlights(ficha: dict, case: dict | None = None) -> list[dict]:
    """Fatos do edital que jogam a favor de quem arremata.

    Cada um é verificável no documento ou na consulta processual — nenhum é
    opinião. A ausência de um fato favorável nunca vira um alerta: o que não
    dá para afirmar simplesmente não aparece.
    """
    found: list[dict] = []
    ficha = ficha or {}

    # Deságio na segunda praça, contra a avaliação atualizada quando houver.
    appraisal = ficha.get("appraisal") or {}
    reference = _amount(appraisal.get("updated_value")) or _amount(appraisal.get("value"))
    second = ((ficha.get("auction") or {}).get("second_round") or {})
    minimum = _amount(second.get("minimum_bid"))
    if reference and minimum and 0 < minimum < reference:
        discount = round((1 - minimum / reference) * 100)
        if discount >= 5:
            found.append({
                "kind": "second_round_discount",
                "text": (f"Segunda praça aceita lance a partir de {_brl(minimum)}, "
                         f"{discount}% abaixo da avaliação de {_brl(reference)}."),
            })

    # Nenhum ônus registrado. Só afirmamos quando o edital se pronunciou: lista
    # vazia pode significar "não há" ou "o modelo não achou", e a distinção
    # importa demais para ser resolvida no chute.
    encumbrances = ficha.get("encumbrances")
    if isinstance(encumbrances, list) and not encumbrances:
        if not _gap_about(ficha, "encumbrances"):
            found.append({
                "kind": "no_encumbrances",
                "text": "O edital não relaciona ônus na matrícula — sem hipoteca, "
                        "penhora ou usufruto declarados.",
            })

    # Ônus que se extinguem com a venda são declarados como tal.
    if isinstance(encumbrances, list) and encumbrances:
        extinguished = [e for e in encumbrances if e.get("extinguished_by_sale") is True]
        if extinguished and len(extinguished) == len(encumbrances):
            found.append({
                "kind": "encumbrances_extinguished",
                "text": f"O edital declara que {'o ônus registrado se extingue' if len(extinguished) == 1 else f'os {len(extinguished)} ônus registrados se extinguem'} "
                        "com a arrematação.",
            })

    # Imóvel desocupado, quando o edital afirma.
    occupancy = (ficha.get("occupancy") or {}).get("status")
    status = occupancy.get("value") if isinstance(occupancy, dict) else occupancy
    if status == "vacant":
        found.append({
            "kind": "vacant",
            "text": "O edital declara o imóvel desocupado, o que dispensa ação de "
                    "imissão na posse.",
        })

    # Parcelamento admitido.
    installments = (ficha.get("payment") or {}).get("installments_allowed")
    allowed = installments.get("value") if isinstance(installments, dict) else installments
    if allowed is True:
        found.append({
            "kind": "installments",
            "text": "O edital admite proposta de pagamento parcelado.",
        })

    # Processo sem sinal que ameace a arrematação. Exige consulta feita: sem
    # ela, silêncio — e não uma afirmação tranquilizadora sem base.
    if case and case.get("found"):
        active = case.get("active_signals") or []
        if not active:
            movements = case.get("movement_count") or 0
            found.append({
                "kind": "clean_case",
                "text": (f"A consulta ao DataJud não encontrou sinal de suspensão, "
                         f"embargos, acordo ou adjudicação"
                         + (f" nos {movements} movimentos do processo." if movements
                            else " no processo.")),
            })

    return found


def _gap_about(ficha: dict, prefix: str) -> bool:
    return any((g.get("field") or "").startswith(prefix) for g in (ficha.get("gaps") or []))


# ─── Composição ─────────────────────────────────────────────────────────────

def present(ficha: dict, case: dict | None = None, *, max_items: int = 3) -> dict:
    """Blocos prontos para exibição, já em português e já priorizados."""
    ficha = ficha or {}
    ranked = rank_risks(ficha.get("risks") or [])
    specific = [r for r in ranked if not is_generic_risk(r.get("description", ""))]

    return {
        "risks": [
            {"description": r.get("description"), "severity": r.get("severity")}
            for r in (specific or ranked)[:max_items]
        ],
        "risks_generic_hidden": len(ranked) - len(specific) if specific else 0,
        "gaps": [
            {"label": label(g.get("field")),
             "why_it_matters": g.get("why_it_matters"),
             "how_to_verify": g.get("how_to_verify")}
            for g in (ficha.get("gaps") or [])[:max_items]
        ],
        "highlights": highlights(ficha, case)[:max_items],
    }
