"""Leitura de movimentos processuais do DataJud, em chave de risco para quem arremata.

O que o DataJud devolve é metadado e andamento — classe, assunto, órgão, datas e
movimentos com códigos das Tabelas Processuais Unificadas. Não devolve peças nem
decisões, então um movimento diz que **algo aconteceu**, não o que foi decidido.
Toda a lógica aqui respeita esse limite: ela sinaliza, não conclui.

Recência é parte do sinal, e não um detalhe. No edital de exemplo há uma
"Homologação de Acordo em Execução" — que, isolada, sugeriria leilão prestes a
ser cancelado. A data desmente: o acordo é de 2023, e o processo seguiu com
quase duzentos movimentos até semanas antes da praça. Acordo descumprido e
execução retomada é o oposto de leilão em risco de cancelamento.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Iterable

# Meses a partir dos quais um movimento deixa de ser tratado como situação
# corrente. Não é um corte rígido: movimentos antigos continuam listados, só
# perdem severidade.
RECENT_MONTHS = 12

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"


@dataclass(frozen=True)
class Signal:
    kind: str
    description: str
    severity: str
    movement: str
    movement_code: int | None
    date: str | None
    recent: bool
    occurrences: int = 1
    first_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SEVERITY_ORDER = {SEVERITY_LOW: 0, SEVERITY_MEDIUM: 1, SEVERITY_HIGH: 2}


def _collapse(signals: list[Signal]) -> list[Signal]:
    """Agrupa sinais do mesmo tipo num só, preservando contagem e datas.

    Uma execução com seis movimentos de embargos gerava seis sinais idênticos.
    Isso afoga o resumo e sugere seis problemas onde há um. O que interessa é:
    houve embargos, quantas vezes, e quando foi o mais recente.
    """
    grouped: dict[str, list[Signal]] = {}
    for signal in signals:
        grouped.setdefault(signal.kind, []).append(signal)

    collapsed: list[Signal] = []
    for group in grouped.values():
        dated = [s for s in group if s.date]
        latest = max(dated, key=lambda s: s.date) if dated else group[-1]
        worst = max(group, key=lambda s: _SEVERITY_ORDER[s.severity])
        collapsed.append(
            Signal(
                kind=latest.kind,
                description=latest.description,
                severity=worst.severity,
                movement=latest.movement,
                movement_code=latest.movement_code,
                date=latest.date,
                recent=any(s.recent for s in group),
                occurrences=len(group),
                first_date=min((s.date for s in dated), default=None),
            )
        )

    collapsed.sort(key=lambda s: (-_SEVERITY_ORDER[s.severity], s.date or ""), reverse=False)
    return sorted(collapsed, key=lambda s: (-_SEVERITY_ORDER[s.severity], s.date or ""))


# Cada regra casa pelo nome do movimento. Usamos nome, e não só código, porque
# os tribunais preenchem os códigos das TPU de forma desigual, mas o nome
# aparece sempre. `severity_recent` vale quando o movimento é recente;
# `severity_old` quando não é.
RULES: list[dict[str, Any]] = [
    {
        "kind": "embargos",
        "pattern": r"embargos?\b.*(arremata|execu)|embargos de terceiro",
        "description": "Há embargos nos autos. Enquanto pendentes, a arrematação pode não se consolidar.",
        "severity_recent": SEVERITY_HIGH,
        "severity_old": SEVERITY_LOW,
    },
    {
        "kind": "recurso",
        "pattern": r"\bagravo\b|\bapela[çc][ãa]o\b|recurso especial|recurso extraordin",
        "description": "Há recurso nos autos. Decisão em contrário pode desfazer a alienação.",
        "severity_recent": SEVERITY_MEDIUM,
        "severity_old": SEVERITY_LOW,
    },
    {
        "kind": "suspensao",
        "pattern": r"suspens[ãa]o|sobrestamento|suspenso",
        "description": "O processo registra suspensão. A praça pode não ocorrer na data do edital.",
        "severity_recent": SEVERITY_HIGH,
        "severity_old": SEVERITY_LOW,
    },
    {
        "kind": "acordo",
        "pattern": r"acordo|composi[çc][ãa]o|transa[çc][ãa]o",
        "description": "Houve acordo entre as partes. Se vigente, o leilão tende a ser cancelado.",
        "severity_recent": SEVERITY_HIGH,
        "severity_old": SEVERITY_LOW,
    },
    {
        "kind": "pagamento",
        "pattern": r"pagamento.*d[ée]bito|remi[çc][ãa]o|quita[çc][ãa]o|extin[çc][ãa]o.*pagamento",
        "description": "Registro de pagamento ou remição. A dívida quitada encerra a execução.",
        "severity_recent": SEVERITY_HIGH,
        "severity_old": SEVERITY_LOW,
    },
    {
        "kind": "adjudicacao",
        "pattern": r"adjudica",
        "description": "Registro de adjudicação: o credor pode ficar com o bem em vez de leiloá-lo.",
        "severity_recent": SEVERITY_HIGH,
        "severity_old": SEVERITY_LOW,
    },
    {
        "kind": "arrematacao",
        "pattern": r"arremata|carta de arremata|auto de arremata",
        "description": "Já há registro de arrematação. O edital pode estar desatualizado.",
        "severity_recent": SEVERITY_HIGH,
        "severity_old": SEVERITY_MEDIUM,
    },
    {
        "kind": "extincao",
        "pattern": r"extin[çc][ãa]o|arquivamento definitivo",
        "description": "Registro de extinção ou arquivamento. O processo pode não estar mais ativo.",
        "severity_recent": SEVERITY_HIGH,
        "severity_old": SEVERITY_LOW,
    },
    {
        "kind": "penhora",
        "pattern": r"penhora",
        "description": "Movimentação de penhora nos autos.",
        "severity_recent": SEVERITY_LOW,
        "severity_old": SEVERITY_LOW,
    },
]

_COMPILED = [(rule, re.compile(rule["pattern"], re.IGNORECASE)) for rule in RULES]


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value)
    # O DataJud usa ISO-8601 nos movimentos e AAAAMMDDHHMMSS em dataAjuizamento.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    if len(text) >= 8 and text[:8].isdigit():
        try:
            return datetime.strptime(text[:8], "%Y%m%d").date()
        except ValueError:
            return None
    return None


def _months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def analyze(source: dict, *, today: date | None = None) -> dict[str, Any]:
    """Transforma um `_source` do DataJud em sinais de risco para o arrematante.

    `today` é injetável para os testes não dependerem da data de execução.
    """
    today = today or date.today()
    movements: Iterable[dict] = source.get("movimentos") or []

    dated: list[tuple[date | None, dict]] = [(_parse_date(m.get("dataHora")), m) for m in movements]
    dated.sort(key=lambda pair: (pair[0] or date.min))

    last_date = next((d for d, _ in reversed(dated) if d), None)

    signals: list[Signal] = []
    for moment, movement in dated:
        name = movement.get("nome") or ""
        for rule, regex in _COMPILED:
            if not regex.search(name):
                continue
            recent = bool(moment and _months_between(moment, today) <= RECENT_MONTHS)
            signals.append(
                Signal(
                    kind=rule["kind"],
                    description=rule["description"],
                    severity=rule["severity_recent"] if recent else rule["severity_old"],
                    movement=name,
                    movement_code=movement.get("codigo"),
                    date=moment.isoformat() if moment else None,
                    recent=recent,
                )
            )
            break

    # Processo parado há muito tempo também é sinal: o edital pode não refletir
    # o estado atual dos autos.
    stale_months = _months_between(last_date, today) if last_date else None
    if stale_months is not None and stale_months > RECENT_MONTHS:
        signals.append(
            Signal(
                kind="inatividade",
                description=(
                    f"Sem movimentação há cerca de {stale_months} meses. "
                    "O edital pode não refletir o estado atual do processo."
                ),
                severity=SEVERITY_MEDIUM,
                movement="(nenhum movimento recente)",
                movement_code=None,
                date=last_date.isoformat(),
                recent=False,
            )
        )

    signals = _collapse(signals)

    return {
        "classe": (source.get("classe") or {}).get("nome"),
        "orgao_julgador": (source.get("orgaoJulgador") or {}).get("nome"),
        "assuntos": [a.get("nome") for a in (source.get("assuntos") or []) if a.get("nome")],
        "grau": source.get("grau"),
        "nivel_sigilo": source.get("nivelSigilo"),
        "data_ajuizamento": (
            _parse_date(source.get("dataAjuizamento")).isoformat()
            if _parse_date(source.get("dataAjuizamento")) else None
        ),
        "movement_count": len(list(movements)),
        "last_movement": (
            {"name": dated[-1][1].get("nome"), "date": last_date.isoformat()}
            if dated and last_date else None
        ),
        "signals": [s.to_dict() for s in signals],
        # Só os que merecem atenção agora, para o resumo não afogar o que importa.
        "active_signals": [s.to_dict() for s in signals if s.severity != SEVERITY_LOW],
    }
