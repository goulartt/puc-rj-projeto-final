#!/usr/bin/env python3
"""Confere que toda citação de uma ficha existe mesmo no edital.

É a verificação que sustenta a promessa central do projeto: se o assistente
cita uma cláusula, a cláusula está no documento. Uma ficha em que o modelo
parafraseou o edital passa no schema e falha aqui.

    python3 tests/check_citations.py docs/evidence/ficha-edital-exemplo.json \\
        --notice-markdown caminho/para/edital.md

Sem `--notice-markdown`, converte o PDF apontado em `_meta.edital` pelo
serviço Docling (precisa da stack no ar).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Iterator

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services"))

from extractors.text import contains_citation  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def collect_quotes(node, path: str = "") -> Iterator[tuple[str, str]]:
    """Percorre a ficha e devolve (caminho_do_campo, citação) de cada `quote`."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "_meta":
                continue
            child = f"{path}.{key}" if path else key
            if key == "quote" and isinstance(value, str) and value.strip():
                yield path or "(raiz)", value
            else:
                yield from collect_quotes(value, child)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from collect_quotes(item, f"{path}[{index}]")


def markdown_from_docling(pdf: pathlib.Path, base_url: str) -> str:
    import urllib.request
    import uuid

    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{pdf.name}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode() + pdf.read_bytes() + f"\r\n--{boundary}--\r\n".encode()

    request = urllib.request.Request(
        f"{base_url}/convert", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)["markdown"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=pathlib.Path)
    parser.add_argument("--notice-markdown", type=pathlib.Path)
    parser.add_argument("--docling-url", default="http://localhost:5001")
    args = parser.parse_args()

    report = json.loads(args.report.read_text())

    if args.notice_markdown:
        markdown = args.notice_markdown.read_text()
    else:
        pdf = ROOT / report.get("_meta", {}).get("edital", "")
        if not pdf.is_file():
            print(f"erro: informe --notice-markdown ou aponte _meta.edital para um PDF ({pdf})")
            return 2
        markdown = markdown_from_docling(pdf, args.docling_url)

    quotes = list(collect_quotes(report))
    failures = [(field, q) for field, q in quotes if not contains_citation(markdown, q)]

    for field, quote in failures:
        print(f"NAO LOCALIZADA  {field}\n                {quote[:110]}…")

    checked = len(quotes)
    print(f"\ncitacoes: {checked} | localizadas: {checked - len(failures)} | falhas: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
