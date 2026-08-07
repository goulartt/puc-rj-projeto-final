#!/usr/bin/env python3
"""Avalia todos os editais já processados, e não só o de exemplo.

Os números do README vinham de um único documento do TJSP. Isso mede aquele
edital, não o produto: os extratores de praça e de matrícula foram ajustados
olhando um documento só, e é exatamente esse tipo de regra que não generaliza.

Este script lê o que o pipeline realmente produziu — o Markdown e a ficha
persistidos — e roda as mesmas conferências de `run_eval.py` sobre cada um.
Não precisa do PDF: a fonte é o banco, que é o que o usuário de fato recebeu.

    # 1. mande os editais ao bot no Telegram, um de cada vez
    # 2. rode:
    python3 scripts/eval-corpus.py --out docs/evidence/

Editais que também devam ficar versionados vão em `data/editais/`, mas o
script não depende disso.

**Privacidade:** o `chat_id` do Telegram identifica uma pessoa e o relatório vai
para o repositório. Ele nunca é escrito — só um apelido derivado por hash.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(ROOT / "tests"))

from run_eval import _pct, eval_agreement, eval_citations  # noqa: E402


def psql(query: str) -> str:
    """Consulta pelo contêiner, para o script não exigir driver de Postgres.

    O resto do projeto também não exige: nenhuma dependência Python fora da
    biblioteca padrão até aqui, e não vale a pena quebrar isso por um relatório.
    """
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres",
         "psql", "-U", "leilao", "-d", "leilao", "-t", "-A", "-F", "\x1f", "-c", query],
        cwd=ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"psql falhou: {result.stderr.strip()[:300]}")
    return result.stdout


def nickname(chat_id: str) -> str:
    """Apelido estável e não reversível para o dono da conversa."""
    return "chat-" + hashlib.sha256(chat_id.encode()).hexdigest()[:6]


def load_notices() -> list[dict]:
    rows = psql(
        # base64 porque o Markdown tem quebras de linha e estragaria o
        # parsing por linha. O `replace` e necessario porque o proprio
        # `encode` quebra a saida a cada 76 caracteres.
        "SELECT id, chat_id, file_name, sha256, "
        "replace(encode(convert_to(markdown, 'UTF8'), 'base64'), chr(10), ''), "
        "replace(encode(convert_to(analysis::text, 'UTF8'), 'base64'), chr(10), '') "
        "FROM auction_notices ORDER BY id;"
    )
    import base64

    notices = []
    for line in rows.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("\x1f")
        if len(parts) < 6:
            continue
        notices.append({
            "id": parts[0],
            "chat": nickname(parts[1]),
            "file_name": parts[2],
            "sha256": parts[3][:12],
            "markdown": base64.b64decode(parts[4]).decode(),
            "analysis": json.loads(base64.b64decode(parts[5]).decode()),
        })
    return notices


def evaluate(notice: dict) -> dict:
    ficha = {k: v for k, v in notice["analysis"].items() if k != "_meta"}
    citations = eval_citations(notice["markdown"], ficha)
    agreement = eval_agreement(notice["markdown"], ficha)

    procedure = (ficha.get("procedure") or {}).get("type")
    if isinstance(procedure, dict):
        procedure = procedure.get("value")

    return {
        "id": notice["id"],
        "chat": notice["chat"],
        "file_name": notice["file_name"],
        "sha256": notice["sha256"],
        "procedure": procedure or "?",
        "chars": len(notice["markdown"]),
        "citations": citations,
        "agreement": agreement,
    }


def render(results: list[dict], date: str) -> str:
    lines = [
        f"# Avaliação do corpus — {date}",
        "",
        f"{len(results)} edital(is) processado(s) pelo pipeline, medidos pelo",
        "Markdown e pela ficha que ficaram no banco — a mesma coisa que o",
        "usuário recebeu.",
        "",
        "| Edital | Rito | Tamanho | Citação verificável | Determinístico × modelo |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        c, a = r["citations"], r["agreement"]
        lines.append(
            f"| `{r['sha256']}` {r['file_name'][:28]} | {r['procedure']} | "
            f"{r['chars'] // 1000}k | {c['hits']}/{c['total']} ({_pct(c['rate'])}) | "
            f"{a['hits']}/{a['total']} ({_pct(a['rate'])}) |"
        )

    total_c = sum(r["citations"]["hits"] for r in results)
    total_ct = sum(r["citations"]["total"] for r in results)
    total_a = sum(r["agreement"]["hits"] for r in results)
    total_at = sum(r["agreement"]["total"] for r in results)
    if total_ct:
        lines += ["", f"**Agregado:** citação {total_c}/{total_ct} "
                      f"({_pct(total_c / total_ct)}) · "
                      f"determinístico × modelo {total_a}/{total_at} "
                      f"({_pct(total_a / total_at) if total_at else '—'})"]

    for r in results:
        misses = r["citations"]["misses"]
        divergent = [row for row in r["agreement"]["rows"] if row["agree"] is False]
        if not misses and not divergent:
            continue
        lines += ["", f"## `{r['sha256']}` {r['file_name']}", ""]
        if divergent:
            lines.append("**Divergência entre regex e modelo** — cada uma merece "
                         "olhar, porque uma das duas está errada:")
            lines += [f"- `{d['field']}`: regex `{d['deterministic']}`, "
                      f"modelo `{d['model']}`" for d in divergent]
            lines.append("")
        if misses:
            lines.append("**Citações não localizadas no edital:**")
            lines += [f"- `{m['field']}` — \"{m['quote']}…\"" for m in misses]

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path,
                        help="diretorio onde gravar o relatorio")
    args = parser.parse_args()

    notices = load_notices()
    if not notices:
        print("nenhum edital processado ainda — mande um PDF ao bot primeiro",
              file=sys.stderr)
        return 1

    results = [evaluate(n) for n in notices]
    date = datetime.date.today().isoformat()
    text = render(results, date)
    print(text)

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        destination = args.out / f"{date}-avaliacao-corpus.md"
        destination.write_text(text)
        shown = destination.resolve()
        print(f"relatorio em "
              f"{shown.relative_to(ROOT) if shown.is_relative_to(ROOT) else shown}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
