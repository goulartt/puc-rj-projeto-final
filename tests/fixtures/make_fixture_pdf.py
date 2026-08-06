#!/usr/bin/env python3
"""Gera um edital sintético em PDF, com camada de texto real.

Serve para dois fins: smoke test do serviço Docling sem depender de um edital
de terceiros, e fixture determinística dos extratores (CNJ, praças, valores).
Não usa dependências externas — escreve o PDF na mão com fontes base-14.

    python3 tests/fixtures/make_fixture_pdf.py
"""

from __future__ import annotations

import pathlib
import zlib

OUT = pathlib.Path(__file__).parent / "edital-sintetico.pdf"


def cnj_check_digits(sequential: str, year: str, segment: str, court: str, origin: str) -> str:
    """Dígito verificador do número CNJ — módulo 97 base 10 (ISO 7064).

    Resolução CNJ 65/2008: concatena NNNNNNN+AAAA+J+TR+OOOO, multiplica por 100
    (equivale a acrescentar o campo DD zerado) e o DV é 98 menos o resto.
    """
    base = int(f"{sequential}{year}{segment}{court}{origin}") * 100
    return f"{98 - (base % 97):02d}"


SEQUENTIAL, YEAR, SEGMENT, COURT, ORIGIN = "1234567", "2024", "8", "19", "0001"
CHECK = cnj_check_digits(SEQUENTIAL, YEAR, SEGMENT, COURT, ORIGIN)
CASE_NUMBER = f"{SEQUENTIAL}-{CHECK}.{YEAR}.{SEGMENT}.{COURT}.{ORIGIN}"

LINES = [
    "EDITAL DE LEILAO JUDICIAL",
    "",
    f"Processo n. {CASE_NUMBER}",
    "3a Vara Civel da Comarca do Rio de Janeiro - TJRJ",
    "Exequente: Condominio Edificio Exemplo",
    "",
    "1. DO IMOVEL",
    "Apartamento 402, Rua Fictícia 100, Tijuca, Rio de Janeiro/RJ.",
    "Matricula n. 45.678 do 9o Oficio de Registro de Imoveis.",
    "Area privativa de 78,00 m2. Imovel OCUPADO pelo executado.",
    "",
    "2. DA AVALIACAO",
    "Valor da avaliacao: R$ 480.000,00",
    "",
    "3. DAS PRACAS",
    "1a praca: 12/09/2026 as 14:00, lance minimo R$ 480.000,00",
    "2a praca: 26/09/2026 as 14:00, lance minimo R$ 288.000,00",
    "Nao sera aceito lance inferior a 60% da avaliacao.",
    "",
    "4. DOS ONUS E DEBITOS",
    "Consta penhora nos autos deste processo.",
    "Debito de IPTU de R$ 12.400,00 ate a data deste edital.",
    "Debito condominial de R$ 31.900,00, de natureza propter rem.",
    "",
    "5. DA COMISSAO",
    "Comissao do leiloeiro de 5% sobre o valor da arrematacao,",
    "a ser paga pelo arrematante.",
    "",
    "6. DISPOSICOES GERAIS",
    "A venda e feita em carater ad corpus.",
    "A imissao na posse correra por conta do arrematante.",
]


def escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_content() -> bytes:
    parts = ["BT", "/F1 11 Tf", "13 TL", "1 0 0 1 56 780 Tm"]
    for line in LINES:
        parts.append(f"({escape(line)}) Tj" if line else "()Tj")
        parts.append("T*")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1")


def build_pdf() -> bytes:
    stream = zlib.compress(build_content())

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n"
        + stream + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


if __name__ == "__main__":
    OUT.write_bytes(build_pdf())
    print(f"processo valido gerado: {CASE_NUMBER}")
    print(f"escrito: {OUT} ({OUT.stat().st_size} bytes)")
