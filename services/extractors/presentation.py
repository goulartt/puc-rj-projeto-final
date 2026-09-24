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
    r"|multa por inadimpl"
    # Entrada de 25% com o restante parcelado é o que o art. 895 do CPC
    # permite, e por isso aparece em quase todo edital judicial. Descrever
    # "exige disponibilidade de capital" como risco do lote é descrever o
    # leilão, não este imóvel.
    r"|(25|vinte e cinco)\s*%.{0,40}(entrada|[àa] vista)"
    r"|(entrada|sinal).{0,40}(25|vinte e cinco)\s*%"
    r"|parcelad[oa].{0,60}(30|trinta) (meses|parcelas)"
    r"|disponibilidade de capital",
    re.IGNORECASE,
)

# Lacunas que o edital de fato não traz e que ninguém precisa que ele traga:
# são dados de outra fonte, mais confiável e de acesso direto. Listá-las gasta
# uma das três linhas do resumo com uma pendência que não é pendência.
#
# Área privativa é o exemplo: consta da matrícula, que o comprador vai puxar de
# qualquer forma antes de dar lance.
_LOW_VALUE_GAPS = re.compile(
    r"private_area|area privativa|área privativa"
    r"|total_area|area total|área total"
    r"|municipal_id|inscri[çc][ãa]o municipal"
    r"|parking|vaga de garagem",
    re.IGNORECASE,
)


def is_low_value_gap(field: str, label: str = "") -> bool:
    """Lacuna que a matrícula responde melhor que o edital."""
    return bool(_LOW_VALUE_GAPS.search(f"{field or ''} {label or ''}"))


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


# ─── Lotes de um edital ─────────────────────────────────────────────────────

def describe_lot(lot: dict) -> str:
    """Uma linha que deixa a pessoa reconhecer o imóvel.

    No catálogo da Caixa vale mais o endereço e o item que o tipo: "Item 461 —
    Apartamento — Alameda Casa Branca, 438, apto 111, Jardim Paulista, São
    Paulo/SP" é o que a pessoa procura no anúncio. Imóvel anulado diz isso
    primeiro, porque não há o que analisar nele.
    """
    partes = []
    if lot.get("item"):
        partes.append(f"Item {lot['item']}")
    partes.append(lot.get("kind") or "Imóvel")
    if lot.get("address"):
        local = lot["address"].title()
        if lot.get("district"):
            local += f", {lot['district']}"
        partes.append(local)
    if lot.get("city"):
        partes.append(lot["city"])
    partes.append(f"matrícula {lot['registry']}")
    if lot.get("annulled"):
        partes.insert(0, "ANULADO")
    elif lot.get("minimum_bid") and lot.get("appraisal"):
        partes.append(f"venda {_brl(lot['minimum_bid'])} (avaliação {_brl(lot['appraisal'])})")
    elif lot.get("appraisal"):
        partes.append(f"avaliado em {_brl(lot['appraisal'])}")
    return " — ".join(partes)


def describe_lots(lots: list[dict]) -> list[str]:
    """Uma linha por imóvel, para a pessoa escolher qual quer analisar.

    Montada sem modelo: tudo sai de expressão regular sobre o documento. Custa
    zero e sai em segundos, o que é o ponto — a pergunta precisa chegar antes
    da extração, e não depois dela.
    """
    return [describe_lot(lot) for lot in lots]


# ─── A ficha como texto em português ────────────────────────────────────────
#
# O Q&A recebia a ficha em JSON cru, com as chaves em inglês do schema. Duas
# consequências observadas numa conversa real:
#
#   "…extinguished_by_sale: true"      — o modelo repetiu a chave na resposta
#   "A penhora (grávida do processo)"  — e tentou traduzir outra, inventando
#                                         uma palavra que não existe em edital
#                                         nenhum, nem em nenhuma ficha
#
# A raiz é a mesma: pedir a um modelo pequeno que leia inglês estruturado e
# responda em português jurídico convida à improvisação. Aqui a tradução é
# feita antes, por regra, e o modelo só vê português.

_ENUMS = {
    "judicial": "judicial", "extrajudicial": "extrajudicial",
    "occupied": "ocupado", "vacant": "desocupado",
    "not_informed": "não informado no edital",
    "high": "alto", "medium": "médio", "low": "baixo",
    "deterministic": "extraído por regra", "llm": "extraído pelo modelo",
    "both": "confirmado pelos dois", "derived": "derivado",
}


def _render_value(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "sim" if value else "não"
    if isinstance(value, (int, float)):
        return _brl(float(value))
    text = str(value)
    return _ENUMS.get(text, text)


def _render_leaf(node: dict) -> str | None:
    """Um campo `{value, quote, confidence}` numa linha legível."""
    # `value` na maioria dos campos, `amount_brl` nos monetários e `type` em
    # `procedure` — três formas de folha no mesmo schema.
    raw = node.get("value")
    if raw is None:
        raw = node.get("amount_brl", node.get("type"))
    rendered = _render_value(raw)
    if rendered is None:
        return None

    # `confidence` fica de fora de propósito. Anexada ao valor, o modelo a leu
    # como se fosse o valor: perguntado sobre ocupação, respondeu que "a
    # ocupação é baixa", lendo o "(confiança baixa)" que vinha ao lado de "não
    # informado". A confiança é anotação de auditoria e continua no JSON da
    # ficha, que é onde ela serve.
    if node.get("reference_date"):
        rendered += f" (referência {node['reference_date']})"
    if node.get("quote"):
        rendered += f'\n    trecho: "{node["quote"]}"'
    return rendered


_LEAF_KEYS = {"value", "type", "quote", "confidence", "source", "amount_brl",
              "reference_date", "currency"}


def _is_leaf(node: dict) -> bool:
    """Folha é o bloco `{value, quote, confidence, source}` e suas variantes.

    Não basta olhar as chaves: `appraisal` tem só `value`, e o valor dela é
    outro objeto monetário. Tratá-la como folha imprimia o dicionário Python
    cru na cara do modelo — chaves em inglês incluídas, que é justamente o que
    esta função existe para evitar.
    """
    if not node or set(node) - _LEAF_KEYS:
        return False
    return not any(isinstance(v, (dict, list)) for v in node.values())


# Ordem de leitura, e não a ordem em que o modelo devolveu o JSON: quem lê quer
# saber o que é o imóvel antes de saber o que pode dar errado com ele.
_SECTION_ORDER = [
    "procedure", "court_case", "property", "auction", "appraisal", "occupancy",
    "encumbrances", "debts", "auctioneer_fee", "payment", "risks", "gaps",
]

# Detalhe de máquina: não ajuda quem lê e ocupa espaço no contexto.
_INTERNAL = {"datajud_alias", "valid", "source", "cited_numbers"}


def ficha_to_text(ficha: dict, path: str = "", depth: int = 0) -> str:
    """A ficha inteira em português, com os trechos que a sustentam.

    Formato de texto e não de JSON: o modelo lê melhor, gasta menos token e —
    o que importa — não tem nenhuma chave em inglês para repetir ou traduzir
    errado.
    """
    if depth > 6 or not isinstance(ficha, dict):
        return ""

    linhas: list[str] = []
    recuo = "  " * depth
    ordem = {name: i for i, name in enumerate(_SECTION_ORDER)}
    entradas = sorted(ficha.items(), key=lambda kv: ordem.get(kv[0], len(ordem)))

    for key, node in entradas:
        if key.startswith("_") or key in _INTERNAL:
            continue
        caminho = f"{path}.{key}" if path else key

        # `quote` aparece solto dentro de blocos compostos e, sem este caso,
        # herdava o rótulo do pai — "Primeira praça: <texto do trecho>".
        if key == "quote" and isinstance(node, str) and node.strip():
            linhas.append(f'{recuo}trecho: "{node}"')
            continue

        titulo = label(caminho)

        if isinstance(node, dict) and _is_leaf(node):
            rendered = _render_leaf(node)
            if rendered:
                linhas.append(f"{recuo}{titulo}: {rendered}")
        elif isinstance(node, dict):
            interior = ficha_to_text(node, caminho, depth + 1)
            if interior:
                linhas.append(f"{recuo}{titulo}:")
                linhas.append(interior)
        elif isinstance(node, list):
            if not node:
                continue
            linhas.append(f"{recuo}{titulo}:")
            for item in node:
                if isinstance(item, dict):
                    partes = []
                    for k, v in item.items():
                        rendered = _render_value(v)
                        if rendered is None:
                            continue
                        rotulo = _ITEM_LABELS.get(k, label(k))
                        partes.append(f"{rotulo}: {rendered}")
                    if partes:
                        linhas.append(f"{recuo}  - " + "; ".join(partes))
                else:
                    rendered = _render_value(item)
                    if rendered:
                        linhas.append(f"{recuo}  - {rendered}")
        else:
            rendered = _render_value(node)
            if rendered:
                linhas.append(f"{recuo}{titulo}: {rendered}")

    return "\n".join(linhas)


# Chaves que só aparecem dentro de listas, e por isso não estão em LABELS.
_ITEM_LABELS = {
    "type": "tipo",
    "description": "descrição",
    "severity": "gravidade",
    "quote": "trecho",
    "registry_entry": "ato na matrícula",
    "creditor": "credor",
    "extinguished_by_sale": "extingue-se com a venda",
    "field": "campo",
    "why_it_matters": "por que importa",
    "how_to_verify": "como verificar",
}


# ─── A situação processual como texto ───────────────────────────────────────

_SIGNAL_LABELS = {
    "embargos": "embargos", "recurso": "recurso", "suspensao": "suspensão",
    "acordo": "acordo entre as partes", "pagamento": "pagamento ou remição",
    "adjudicacao": "adjudicação", "arrematacao": "arrematação já registrada",
    "extincao": "extinção ou arquivamento", "penhora": "penhora",
    "inatividade": "processo sem movimentação recente",
}


def case_to_text(case: dict | None) -> str:
    """A consulta ao DataJud em português, para o Q&A poder responder por ela.

    Sem isto o dado ficava no banco e não chegava à conversa: perguntado sobre
    o processo do edital, o assistente respondia que a informação "não está
    presente no conteúdo do edital" e mandava a pessoa consultar os autos — com
    446 movimentos já consultados e gravados.
    """
    if not case or not case.get("found"):
        return ""

    linhas = ["Situação do processo, conforme consulta ao DataJud (CNJ):"]
    for chave, rotulo in (("classe", "Classe"), ("orgao_julgador", "Órgão julgador"),
                          ("data_ajuizamento", "Ajuizado em")):
        if case.get(chave):
            linhas.append(f"  {rotulo}: {case[chave]}")
    if case.get("assuntos"):
        linhas.append("  Assuntos: " + "; ".join(case["assuntos"]))
    if case.get("movement_count"):
        linhas.append(f"  Movimentos registrados: {case['movement_count']}")
    if case.get("last_movement"):
        ultimo = case["last_movement"]
        linhas.append(f"  Último movimento: {ultimo.get('name')} em {ultimo.get('date')}")

    ativos = case.get("active_signals") or []
    if ativos:
        linhas.append("  Sinais que merecem atenção:")
        for sinal in ativos:
            nome = _SIGNAL_LABELS.get(sinal.get("kind"), sinal.get("kind"))
            quando = sinal.get("date")
            vezes = sinal.get("occurrences", 1)
            detalhe = f" ({vezes}x, mais recente em {quando})" if vezes > 1 else (
                f" (em {quando})" if quando else "")
            linhas.append(f"    - {nome}{detalhe}: {sinal.get('description')}")
    else:
        linhas.append("  Nenhum sinal de suspensão, embargos, acordo, adjudicação "
                      "ou arrematação nos movimentos consultados.")

    linhas.append("  Limite desta fonte: o DataJud traz metadados e andamentos, "
                  "não peças nem decisões. Um movimento diz que algo aconteceu, "
                  "não o que foi decidido.")
    return "\n".join(linhas)




# ─── Composição ─────────────────────────────────────────────────────────────

def _rank_gaps(gaps: list[dict], max_items: int) -> list[dict]:
    """Lacunas do resumo, com as de baixo valor no fim.

    Não são descartadas — seguem na ficha. O que muda é quem ocupa as três
    linhas: "o edital não diz se está ocupado" e "não informa a área privativa"
    não têm o mesmo peso, e listar as duas lado a lado sugere que têm.
    """
    described = [
        {"label": label(g.get("field")),
         "why_it_matters": g.get("why_it_matters"),
         "how_to_verify": g.get("how_to_verify"),
         "field": g.get("field")}
        for g in gaps
    ]
    ranked = sorted(described,
                    key=lambda g: is_low_value_gap(g["field"], g["label"]))
    relevant = [g for g in ranked if not is_low_value_gap(g["field"], g["label"])]
    chosen = (relevant or ranked)[:max_items]
    return [{k: v for k, v in g.items() if k != "field"} for g in chosen]


# ─── Entorno ────────────────────────────────────────────────────────────────

def _metros(m: int) -> str:
    return f"{m} m" if m < 1000 else f"{m / 1000:.1f} km".replace(".", ",")


def location_lines(loc: dict | None) -> list[str]:
    """A seção "Entorno" da ficha: o índice e o que há perto.

    Descritiva de propósito. Nota baixa não vira ponto de atenção: o
    OpenStreetMap é irregular fora das capitais, e ausência no mapa não prova
    ausência na rua. Por isso a linha da fonte diz o que o número é.
    """
    if not loc or not loc.get("found"):
        return []
    linhas = [f"Índice {loc['score']}/100 — {loc['band']}."]
    for cat in loc.get("categories") or []:
        if cat.get("nearest_m") is None:
            continue
        nome = f" ({cat['nearest_name']})" if cat.get("nearest_name") else ""
        linhas.append(f"{cat['label']}: {_metros(cat['nearest_m'])}{nome}")
    ausentes = [c["label"].lower() for c in loc.get("categories") or []
                if c.get("nearest_m") is None]
    if ausentes:
        linhas.append("Nada mapeado a 1 km: " + ", ".join(ausentes) + ".")
    if loc.get("precision") == "rua":
        # Sem o número, o ponto cai no meio da rua: numa avenida longa o
        # entorno pode ser de outro trecho.
        linhas.append("O mapa não achou o número, só a rua; as distâncias são "
                      "aproximadas.")
    # Atribuição exigida pela licença ODbL dos dados do OpenStreetMap.
    linhas.append("Distâncias em linha reta. Dados © colaboradores do "
                  "OpenStreetMap, que pode estar incompleto fora das capitais.")
    return linhas


def location_to_text(loc: dict | None) -> str:
    """O entorno para o contexto do Q&A, com a fonte e o limite dela."""
    linhas = location_lines(loc)
    if not linhas:
        return ""
    return "Entorno do imóvel, calculado a partir do OpenStreetMap:\n" + "\n".join(
        f"  {l}" for l in linhas)


def present(ficha: dict, case: dict | None = None, *, max_items: int = 3,
            deterministic: dict | None = None, location: dict | None = None) -> dict:
    """Blocos prontos para exibição, já em português e já priorizados."""
    ficha = ficha or {}
    ranked = rank_risks(ficha.get("risks") or [])
    specific = [r for r in ranked if not is_generic_risk(r.get("description", ""))]

    # Aviso de documento com vários imóveis. Vem antes de tudo na mensagem
    # porque muda o sentido de tudo que vem depois: a ficha descreve um lote, e
    # sem esta linha ela seria lida como se descrevesse o edital.
    lots = (deterministic or {}).get("multi_lot") or {}
    warning = None
    warning_lots: list[str] = []
    if lots.get("multi"):
        todos = lots.get("lots") or []
        if len(todos) <= 12:
            warning = (
                f"Este edital cobre {lots['properties']} imóveis. A ficha abaixo "
                "descreve apenas um deles:"
            )
            # Listar vale mais que contar: com a lista a pessoa reconhece o
            # imóvel que procura e sabe se a ficha é dele.
            warning_lots = describe_lots(todos)
        else:
            # Catálogo: a lista não cabe numa mensagem — 483 linhas empurravam
            # a ficha inteira para fora do limite do Telegram. Diz qual dos
            # imóveis a ficha descreve, que é a informação que importa.
            matricula = str((((ficha.get("property") or {}).get("registry_number")
                              or {}).get("value")) or "")
            escolhido = next((l for l in todos
                              if matricula and re.sub(r"\D", "", str(l.get("registry")))
                              == re.sub(r"\D", "", matricula)), None)
            alvo = describe_lot(escolhido) if escolhido else f"matrícula {matricula or '?'}"
            warning = (f"Este edital é um catálogo com {len(todos)} imóveis. A ficha "
                       f"abaixo descreve só este: {alvo}.")

    return {
        "warning": warning,
        "warning_lots": warning_lots,
        "risks": [
            {"description": r.get("description"), "severity": r.get("severity")}
            for r in (specific or ranked)[:max_items]
        ],
        "risks_generic_hidden": len(ranked) - len(specific) if specific else 0,
        "gaps": _rank_gaps(ficha.get("gaps") or [], max_items),
        "highlights": highlights(ficha, case)[:max_items],
        "location": location_lines(location),
    }
