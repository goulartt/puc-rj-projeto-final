#!/usr/bin/env python3
"""Suíte de avaliação do assistente.

Mede as promessas do produto em vez de repeti-las. Cinco medidas, cada uma
ligada a uma afirmação que o README faz:

1. **Recusa correta** — o assistente recusa o que declara não fazer.
2. **Falso positivo de recusa** — e não recusa o que sabe responder. Esta é a
   contramedida da anterior: um filtro que recusa tudo tira nota máxima em
   recusa correta e destrói o produto.
3. **Extração determinística** — CNJ, valores, matrícula e datas conferidos
   contra o edital de exemplo.
4. **Acordo determinístico × modelo** — onde os dois olham o mesmo campo,
   concordam? Divergência é o sinal que vira `confidence: low` na ficha.
5. **Citação verificável** — toda `quote` da ficha existe no edital.

Com `--live`, faz também um passe de perguntas contra o modelo de Q&A real,
que mede a segunda camada: o que o filtro determinístico deixa passar, o
prompt segura? É o único trecho que gasta tempo e, em provedor pago, dinheiro
— por isso é opcional e desligado por padrão.

    python3 tests/run_eval.py
    python3 tests/run_eval.py --live --out docs/evidence/
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from extractors import cnj, fields, scope  # noqa: E402
from extractors.text import contains_citation  # noqa: E402

CASES = ROOT / "tests" / "cases.yaml"
REFERENCE = ROOT / "docs" / "evidence" / "ficha-edital-exemplo.json"


# ─── Leitura dos casos ──────────────────────────────────────────────────────

def load_cases(path: pathlib.Path) -> dict[str, list[dict]]:
    """Lê o arquivo de casos.

    Usa PyYAML quando disponível e cai num leitor próprio quando não — a suíte
    não deveria exigir dependência para rodar num clone recém-feito, e o
    subconjunto de YAML usado aqui é pequeno e estável.
    """
    try:
        import yaml  # type: ignore
        return yaml.safe_load(path.read_text())
    except ImportError:
        return _parse_simple_yaml(path.read_text())


def _parse_simple_yaml(text: str) -> dict[str, list[dict]]:
    """Leitor do subconjunto de YAML usado em `cases.yaml`.

    Cobre: chaves de topo, listas de mapas, escalares entre aspas e listas
    inline `[a, b]`. Não cobre o resto de propósito — se o arquivo crescer para
    além disso, instale o PyYAML em vez de aumentar este leitor.
    """
    out: dict[str, list[dict]] = {}
    section: list[dict] | None = None
    current: dict | None = None

    for raw in text.splitlines():
        line = raw.split("#")[0].rstrip() if not _in_quotes(raw, "#") else raw.rstrip()
        if not line.strip():
            continue

        if not line.startswith(" ") and line.rstrip().endswith(":"):
            section = []
            out[line.rstrip()[:-1].strip()] = section
            current = None
            continue

        stripped = line.strip()
        if stripped.startswith("- "):
            current = {}
            if section is not None:
                section.append(current)
            stripped = stripped[2:]

        if current is None or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        current[key.strip()] = _scalar(value.strip())

    return out


def _in_quotes(line: str, char: str) -> bool:
    index = line.find(char)
    return index > 0 and line[:index].count('"') % 2 == 1


def _scalar(value: str) -> Any:
    if not value:
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_scalar(v.strip()) for v in inner.split(",")] if inner else []
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value in ("true", "false"):
        return value == "true"
    return value


# ─── Serviço ────────────────────────────────────────────────────────────────

def post(url: str, payload: dict, timeout: int = 120) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


# ─── 1 e 2. Escopo ──────────────────────────────────────────────────────────

def eval_refusal(cases: dict) -> dict[str, Any]:
    """Recusa correta e — igualmente importante — ausência de recusa indevida."""
    failures: list[dict] = []

    hits = 0
    for case in cases.get("refusal", []):
        verdict = scope.classify(case["question"])
        if verdict["in_scope"]:
            failures.append({"id": case["id"], "problem": "nao recusou",
                             "question": case["question"]})
            continue
        hits += 1
        expected = case.get("kind")
        if expected and verdict["kind"] != expected:
            failures.append({"id": case["id"], "problem":
                             f"classificou como {verdict['kind']}, esperado {expected}"})

    allowed = 0
    for case in cases.get("not_refusal", []):
        verdict = scope.classify(case["question"])
        if verdict["in_scope"]:
            allowed += 1
        else:
            failures.append({"id": case["id"],
                             "problem": f"recusou indevidamente ({verdict['kind']})",
                             "question": case["question"]})

    total_refusal = len(cases.get("refusal", []))
    total_allowed = len(cases.get("not_refusal", []))
    return {
        "refusal_rate": _rate(hits, total_refusal),
        "refusal_hits": hits, "refusal_total": total_refusal,
        "false_refusal_rate": _rate(total_allowed - allowed, total_allowed),
        "allowed_hits": allowed, "allowed_total": total_allowed,
        "failures": failures,
    }


# ─── 3. Extração de CNJ ─────────────────────────────────────────────────────

def eval_cnj(cases: dict) -> dict[str, Any]:
    failures: list[dict] = []
    hits = 0

    for case in cases.get("cnj", []):
        found = cnj.extract(case["text"])
        match = next((n for n in found if n.number == case["expect"]), None)

        # Numero com digito invalido nao deve ser aceito como processo. O
        # extrator o descarta na origem, entao ausencia e o resultado correto —
        # e nao um numero devolvido com `valid: False`.
        if case.get("valid") is False:
            if match is None:
                hits += 1
            else:
                failures.append({"id": case["id"],
                                 "problem": "digito invalido foi aceito como processo"})
            continue

        if match is None:
            failures.append({"id": case["id"], "problem": "numero nao localizado"})
            continue
        if match.valid is not True:
            failures.append({"id": case["id"], "problem": "digito verificador reprovado"})
            continue
        if case.get("role") and match.role != case["role"]:
            failures.append({"id": case["id"],
                             "problem": f"role={match.role}, esperado {case['role']}"})
            continue
        hits += 1

    total = len(cases.get("cnj", []))
    return {"rate": _rate(hits, total), "hits": hits, "total": total,
            "failures": failures}


# ─── 4. Campos determinísticos contra a ficha de referência ─────────────────

# Cada linha compara o mesmo fato visto por dois caminhos independentes: a
# regex sobre o Markdown e o modelo lendo o edital. Onde discordam, a ficha
# deveria trazer `confidence: low`.
# Cada linha compara o mesmo fato por dois caminhos independentes. A conferência
# é de **pertencimento**, e não de igualdade posicional: o valor que o modelo
# afirmou está entre os que a regex encontrou no documento?
#
# A primeira versão comparava contra números fixos do edital de exemplo —
# `772545.0`, `800933.79` — e por isso reprovava qualquer outro documento por
# construção. Um avaliador que só funciona no caso que ele foi escrito para
# medir não é um avaliador.
AGREEMENT_FIELDS = [
    ("processo",
     lambda d: {(d.get("court_case") or {}).get("number")},
     lambda f: (f.get("court_case") or {}).get("number")),
    ("matricula",
     lambda d: {m["value"] for m in d.get("property_registry", [])},
     lambda f: ((f.get("property") or {}).get("registry_number") or {}).get("value")),
    ("avaliacao",
     lambda d: {m["value"] for m in d.get("money", [])},
     lambda f: (((f.get("appraisal") or {}).get("value")) or {}).get("amount_brl")),
    ("avaliacao_atualizada",
     lambda d: {m["value"] for m in d.get("money", [])},
     lambda f: (((f.get("appraisal") or {}).get("updated_value")) or {}).get("amount_brl")),
    ("primeira_praca",
     lambda d: {x["date"] for x in d.get("auction_rounds", {}).get("first", [])},
     lambda f: _date_of(((f.get("auction") or {}).get("first_round") or {}).get("starts_at"))),
    ("segunda_praca_fim",
     lambda d: {x["date"] for x in d.get("auction_rounds", {}).get("second", [])},
     lambda f: _date_of(((f.get("auction") or {}).get("second_round") or {}).get("ends_at"))),
]


def _date_of(timestamp: str | None) -> str | None:
    """Compara só a data: o modelo às vezes omite o fuso, e isso não é
    divergência de fato — ambos apontam o mesmo dia."""
    return timestamp[:10] if isinstance(timestamp, str) and len(timestamp) >= 10 else None


def eval_agreement(markdown: str, ficha: dict) -> dict[str, Any]:
    deterministic = fields.extract_all(markdown)
    main = cnj.main_case(markdown)
    deterministic["court_case"] = main.to_dict() if main else None

    rows: list[dict] = []
    agree = 0
    checked = 0
    for name, regex_set, from_model in AGREEMENT_FIELDS:
        claimed = from_model(ficha)
        # Sem afirmação do modelo não há o que conferir. Contar isso como
        # divergência puniria o comportamento correto: nem todo edital tem
        # avaliação atualizada, e inventar uma seria o defeito de verdade. A
        # ausência já é registrada em `gaps`, que é onde ela pertence.
        if claimed is None:
            rows.append({"field": name, "deterministic": None, "model": None,
                         "agree": None})
            continue

        found = {v for v in regex_set(deterministic) if v is not None}
        same = any(_equivalent(v, claimed) for v in found)
        checked += 1
        agree += bool(same)
        rows.append({"field": name,
                     "deterministic": sorted(map(str, found))[:4] or None,
                     "model": claimed, "agree": same})

    return {"rate": _rate(agree, checked), "hits": agree, "total": checked,
            "rows": rows}


def _equivalent(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    if isinstance(left, float) or isinstance(right, float):
        try:
            return abs(float(left) - float(right)) < 0.01
        except (TypeError, ValueError):
            return False
    return str(left).strip() == str(right).strip()


# ─── 5. Citações ────────────────────────────────────────────────────────────

def eval_citations(markdown: str, ficha: dict) -> dict[str, Any]:
    from check_citations import collect_quotes  # noqa: PLC0415

    misses: list[dict] = []
    total = 0
    for path, quote in collect_quotes(ficha):
        total += 1
        if not contains_citation(markdown, quote):
            misses.append({"field": path, "quote": quote[:110]})

    return {"rate": _rate(total - len(misses), total), "hits": total - len(misses),
            "total": total, "misses": misses}


# ─── Passe ao vivo contra o modelo de Q&A ───────────────────────────────────

def eval_live(cases: dict, ficha: dict, base_url: str, model: str,
              api_key: str) -> dict[str, Any]:
    """Pergunta ao modelo real e confere a resposta.

    Mede a segunda camada. As perguntas de `refusal` que o filtro pegaria não
    chegariam aqui em produção — mandamos assim mesmo, de propósito, para saber
    quanto o prompt sozinho seguraria se o filtro não existisse.
    """
    system = (ROOT / "prompts" / "qa-system.md").read_text() + "\n\n# GLOSSARIO\n" \
        + (ROOT / "prompts" / "glossary.md").read_text() \
        + "\n\n# FICHA DO EDITAL CARREGADO\n" + json.dumps(ficha, ensure_ascii=False)

    rows: list[dict] = []
    for group in ("glossary", "edital", "refusal"):
        for case in cases.get(group, []):
            answer = _ask(base_url, model, api_key, system, case["question"])
            rows.append({**_score(group, case, answer), "group": group,
                         "id": case["id"], "question": case["question"],
                         "answer": (answer or "")[:400]})

    summary: dict[str, Any] = {}
    for group in ("glossary", "edital", "refusal"):
        subset = [r for r in rows if r["group"] == group]
        hits = sum(1 for r in subset if r["ok"])
        summary[group] = {"rate": _rate(hits, len(subset)), "hits": hits,
                          "total": len(subset)}

    return {"summary": summary, "rows": rows, "model": model}


def _ask(base_url: str, model: str, api_key: str, system: str, question: str) -> str:
    import os
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": question}],
        # Folga para o raciocínio: com 900 tokens um modelo que pensa gasta o
        # orçamento inteiro antes de escrever, e a resposta volta vazia.
        "max_tokens": 4000,
        "temperature": 0,
    }
    reasoning = os.getenv("LLM_QA_REASONING", "minimal").strip()
    if reasoning:
        payload["reasoning_effort"] = reasoning
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            body = json.loads(response.read())
        return (body["choices"][0]["message"].get("content") or "").strip()
    except (urllib.error.URLError, KeyError, TimeoutError) as exc:
        return f"(erro: {exc})"


_REFUSAL_MARKERS = re.compile(
    r"nao (consigo|posso|dou|estimo|projeto|avalio)|nao (e|eh) (algo|uma)"
    r"|fora do (meu )?escopo|procure um advogado|nao substituo",
    re.IGNORECASE,
)


def _score(group: str, case: dict, answer: str) -> dict[str, Any]:
    normalized = scope._normalize(answer)  # noqa: SLF001 — mesma normalização do filtro

    if group == "refusal":
        return {"ok": bool(_REFUSAL_MARKERS.search(scope._normalize(answer))),  # noqa: SLF001
                "why": "recusou" if _REFUSAL_MARKERS.search(normalized) else "respondeu"}

    wanted = case.get("must_mention") or []
    if isinstance(wanted, str):
        wanted = [wanted]
    found = [w for w in wanted if scope._normalize(str(w)) in normalized]  # noqa: SLF001
    ok = bool(found) if case.get("match") == "any" else len(found) == len(wanted)

    forbidden = case.get("must_not_mention") or []
    if isinstance(forbidden, str):
        forbidden = [forbidden]
    leaked = [f for f in forbidden if scope._normalize(str(f)) in normalized]  # noqa: SLF001
    if leaked:
        ok = False

    return {"ok": ok, "why": f"achou {len(found)}/{len(wanted)}"
            + (f", afirmou indevidamente {leaked}" if leaked else "")}


# ─── Relatório ──────────────────────────────────────────────────────────────

def _relative(path: pathlib.Path) -> str:
    """Caminho legivel no relatorio, aceitando entrada relativa ou absoluta."""
    resolved = path.resolve()
    return str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) else str(path)


def _rate(hits: int, total: int) -> float:
    return round(hits / total, 4) if total else 0.0


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def render(report: dict) -> str:
    lines = [
        f"# Avaliação — {report['date']}",
        "",
        f"Ficha medida: `{report['ficha']}`.",
        "",
        "Gerado por `python3 tests/run_eval.py`. Cada medida corresponde a uma",
        "afirmação que o README faz sobre o produto.",
        "",
        "| Medida | Resultado | |",
        "|---|---|---|",
    ]

    refusal = report["refusal"]
    ok = "✓" if not refusal["failures"] else "✗"
    lines += [
        f"| Recusa correta | {refusal['refusal_hits']}/{refusal['refusal_total']} "
        f"({_pct(refusal['refusal_rate'])}) | {ok} |",
        f"| Recusa indevida | {refusal['allowed_total'] - refusal['allowed_hits']}"
        f"/{refusal['allowed_total']} ({_pct(refusal['false_refusal_rate'])}) | "
        f"{'✓' if refusal['false_refusal_rate'] == 0 else '✗'} |",
    ]

    for key, label in (("cnj", "Extração de CNJ"), ("agreement", "Acordo determinístico × modelo"),
                       ("citations", "Citação verificável")):
        section = report.get(key)
        if not section:
            continue
        mark = "✓" if section["rate"] == 1.0 else ("~" if section["rate"] >= 0.8 else "✗")
        lines.append(f"| {label} | {section['hits']}/{section['total']} "
                     f"({_pct(section['rate'])}) | {mark} |")

    if report.get("live"):
        for group, data in report["live"]["summary"].items():
            lines.append(f"| Resposta ao vivo — {group} | {data['hits']}/{data['total']} "
                         f"({_pct(data['rate'])}) | |")

    if report.get("cost"):
        lines.append(f"| Custo acumulado | US$ {report['cost']:.4f} | |")

    # ── detalhamento ──
    if refusal["failures"]:
        lines += ["", "## Falhas de escopo", ""]
        lines += [f"- `{f['id']}` — {f['problem']}" for f in refusal["failures"]]

    if report.get("cnj", {}).get("failures"):
        lines += ["", "## Falhas de extração de CNJ", ""]
        lines += [f"- `{f['id']}` — {f['problem']}" for f in report["cnj"]["failures"]]

    if report.get("agreement"):
        lines += ["", "## Determinístico × modelo, campo a campo", "",
                  "| Campo | Regex | Modelo | |", "|---|---|---|---|"]
        for row in report["agreement"]["rows"]:
            mark = "—" if row["agree"] is None else ("✓" if row["agree"] else "✗")
            lines.append(f"| {row['field']} | `{row['deterministic']}` | "
                         f"`{row['model']}` | {mark} |")

    if report.get("citations", {}).get("misses"):
        lines += ["", "## Citações não localizadas no edital", "",
                  "Paráfrase onde deveria haver cópia. É o alvo do bloco",
                  "determinístico no prompt de extração.", ""]
        for miss in report["citations"]["misses"]:
            lines.append(f"- `{miss['field']}` — \"{miss['quote']}…\"")

    if report.get("live"):
        lines += ["", "## Respostas ao vivo", "",
                  f"Modelo: `{report['live']['model']}`. As perguntas de recusa foram",
                  "enviadas ao modelo de propósito, sem o filtro na frente, para medir",
                  "quanto o prompt sozinho seguraria.", "",
                  "| Caso | | Observação |", "|---|---|---|"]
        for row in report["live"]["rows"]:
            lines.append(f"| `{row['id']}` | {'✓' if row['ok'] else '✗'} | {row['why']} |")

    return "\n".join(lines) + "\n"


# ─── Entrada ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=pathlib.Path, default=CASES)
    parser.add_argument("--ficha", type=pathlib.Path, default=REFERENCE)
    parser.add_argument("--notice-markdown", type=pathlib.Path,
                        help="Markdown do edital; sem isso, converte o PDF pelo Docling")
    parser.add_argument("--docling-url", default="http://localhost:5001")
    parser.add_argument("--live", action="store_true",
                        help="pergunta ao modelo de Q&A real")
    parser.add_argument("--out", type=pathlib.Path,
                        help="diretorio onde gravar o relatorio")
    parser.add_argument("--json", action="store_true", help="imprime o relatorio bruto")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    ficha = json.loads(args.ficha.read_text())
    ficha_clean = {k: v for k, v in ficha.items() if k != "_meta"}

    report: dict[str, Any] = {
        "date": datetime.date.today().isoformat(),
        # Qual ficha foi medida importa: a de referencia foi conferida a mao e
        # naturalmente tira 100% em citacao. O numero que diz algo sobre o
        # produto e o da ficha gerada pelo modelo.
        "ficha": _relative(args.ficha),
        "refusal": eval_refusal(cases),
        "cnj": eval_cnj(cases),
    }

    markdown = _load_markdown(args, ficha)
    if markdown:
        report["agreement"] = eval_agreement(markdown, ficha_clean)
        report["citations"] = eval_citations(markdown, ficha_clean)
    else:
        print("aviso: sem o Markdown do edital, pulando acordo e citacoes",
              file=sys.stderr)

    if args.live:
        import os
        # Os mesmos padrões do docker-compose: OpenRouter, com a chave única.
        api_key = os.getenv("LLM_QA_API_KEY") or os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise SystemExit("--live precisa de OPENROUTER_API_KEY no ambiente "
                             "(set -a; . ./.env; set +a)")
        report["live"] = eval_live(
            cases, ficha_clean,
            os.getenv("LLM_QA_BASE_URL", "https://openrouter.ai/api"),
            os.getenv("LLM_QA_MODEL", "deepseek/deepseek-v4-flash"),
            api_key,
        )

    text = render(report)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else text)

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        destination = args.out / f"{report['date']}-avaliacao.md"
        destination.write_text(text)
        print(f"\nrelatorio em {_relative(destination)}", file=sys.stderr)

    # Recusa é o critério de aceite: falhar nela reprova a suíte.
    return 1 if report["refusal"]["failures"] else 0


def _load_markdown(args, ficha: dict) -> str | None:
    if args.notice_markdown and args.notice_markdown.is_file():
        return args.notice_markdown.read_text()

    # `_meta.edital` guarda o caminho relativo a raiz do repositorio.
    pdf_name = (ficha.get("_meta") or {}).get("edital")
    pdf = ROOT / pdf_name if pdf_name else None
    if not pdf or not pdf.is_file():
        print(f"aviso: edital nao encontrado em {pdf}", file=sys.stderr)
        return None

    sys.path.insert(0, str(ROOT / "tests"))
    from check_citations import markdown_from_docling  # noqa: PLC0415
    try:
        return markdown_from_docling(pdf, args.docling_url)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"aviso: Docling indisponivel ({exc})", file=sys.stderr)
        return None


if __name__ == "__main__":
    raise SystemExit(main())
