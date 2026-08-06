"""Serviço de processamento de documentos do assistente.

Três responsabilidades, reunidas aqui porque compartilham o mesmo ambiente
Python e o mesmo código já testado:

- `POST /convert`  — PDF do edital vira Markdown, preservando tabelas e
  cláusulas numeradas. Tudo o que vem depois lê essa saída, então a qualidade
  da conversão limita a qualidade de todo o resto.
- `POST /extract`  — extratores determinísticos sobre o Markdown. Ficam aqui, e
  não num nó de código do n8n, porque o nó é JavaScript: reimplementar a
  validação de dígito verificador em outra linguagem criaria uma segunda cópia
  sem teste.
- `POST /validate` — valida uma ficha contra `ficha.schema.json`. Mesma razão:
  o validador de JSON Schema vive no ambiente que já o tem.

O OCR é opcional e desligado por padrão: editais costumam ser PDFs nativos com
camada de texto, e rodar OCR neles é lento e acrescenta ruído de transcrição.
Use `ocr=true` para documentos digitalizados.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import jsonschema
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

sys.path.insert(0, "/app/lib")
from extractors import cnj, fields, movements, scope  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("docling-service")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # editais raramente passam de alguns MB

app = FastAPI(title="Edital PDF converter", version="1.0.0")

# Converters são caros de construir (carregam modelos de layout), então
# guardamos um por configuração de OCR.
_converters: dict[bool, object] = {}


def _get_converter(use_ocr: bool):
    if use_ocr in _converters:
        return _converters[use_ocr]

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = use_ocr
    # Estrutura de tabela importa: praças, valores e débitos costumam vir
    # tabelados no edital, e perder isso degrada a extração determinística.
    options.do_table_structure = True

    if use_ocr:
        # Explícito em vez do auto: só o rapidocr está embutido na imagem, e
        # o seletor automático poderia escolher um engine que não existe aqui.
        options.ocr_options = RapidOcrOptions()

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    _converters[use_ocr] = converter
    return converter


SCHEMA_PATH = Path("/app/schemas/ficha.schema.json")
PROMPTS_DIR = Path("/app/prompts")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "artifacts_path": os.getenv("DOCLING_ARTIFACTS_PATH"),
        "schema_loaded": SCHEMA_PATH.is_file(),
        "prompts": sorted(p.stem for p in PROMPTS_DIR.glob("*.md")) if PROMPTS_DIR.is_dir() else [],
    }


@app.get("/prompt/{name}")
def prompt(name: str) -> JSONResponse:
    """Devolve um prompt do repositório como texto.

    Serve daqui, e não de um nó de leitura de arquivo do n8n, por dois motivos:
    o nó de leitura entrega binário e exigiria um nó extra de conversão para
    cada arquivo, e assim os prompts continuam sendo `.md` revisáveis em diff
    em vez de texto embutido no JSON do fluxo.
    """
    # Impede que `name` escape do diretório de prompts.
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise HTTPException(status_code=400, detail="nome de prompt invalido")

    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"prompt nao encontrado: {name}")

    return JSONResponse({"name": name, "text": path.read_text()})


def _dereference(node: Any, defs: dict, depth: int = 0) -> Any:
    """Expande `$ref` locais, devolvendo um schema sem `$defs`.

    O Ollama compila o schema numa gramática GBNF para decodificação
    restrita e falha com "failed to parse grammar" diante de `$ref`. A API da
    Anthropic aceita referências, mas a forma achatada funciona nos dois, então
    é ela que servimos.

    A profundidade é limitada porque `$ref` recursivo geraria expansão
    infinita. O schema da ficha não tem recursão; o limite é rede de proteção.
    """
    if depth > 20:
        raise HTTPException(status_code=500, detail="schema com referencia recursiva")

    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if not ref.startswith("#/$defs/"):
                raise HTTPException(status_code=500, detail=f"referencia externa nao suportada: {ref}")
            target = defs.get(ref.removeprefix("#/$defs/"))
            if target is None:
                raise HTTPException(status_code=500, detail=f"referencia quebrada: {ref}")
            # Campos irmãos do $ref (como `description`) prevalecem sobre o alvo.
            merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
            return _dereference(merged, defs, depth + 1)
        return {k: _dereference(v, defs, depth + 1) for k, v in node.items() if k != "$defs"}

    if isinstance(node, list):
        return [_dereference(item, defs, depth + 1) for item in node]

    return node


@app.get("/schema")
def schema(dereference: bool = True) -> JSONResponse:
    """Devolve o schema da ficha, para o fluxo pedir saída estruturada.

    Por padrão sem `$ref`, que é a forma que todo provedor aceita. Use
    `?dereference=false` para inspecionar o schema como está no repositório.
    """
    if not SCHEMA_PATH.is_file():
        raise HTTPException(status_code=500, detail=f"schema ausente em {SCHEMA_PATH}")

    raw = json.loads(SCHEMA_PATH.read_text())
    if not dereference:
        return JSONResponse({"schema": raw})

    flat = _dereference(raw, raw.get("$defs", {}))
    # Metadados de documento não ajudam o decodificador e só gastam tokens.
    for key in ("$schema", "$id", "title"):
        flat.pop(key, None)
    return JSONResponse({"schema": flat})


@app.post("/extract")
def extract(payload: dict = Body(...)) -> JSONResponse:
    """Extração determinística sobre o Markdown do edital.

    A saída alimenta o prompt do modelo como dica **e** serve de conferência da
    resposta dele. Divergência entre os dois vira `confidence: low` na ficha,
    em vez de um desempate silencioso.
    """
    markdown = payload.get("markdown") or ""
    if not markdown.strip():
        raise HTTPException(status_code=400, detail="markdown vazio")

    main = cnj.main_case(markdown)
    all_numbers = cnj.extract(markdown)

    return JSONResponse(
        {
            **fields.extract_all(markdown),
            "court_case": main.to_dict() if main else None,
            # Precedentes citados no juridiquês do edital. Registrados para
            # auditoria e explicitamente fora da consulta ao DataJud: são
            # causas alheias ao imóvel.
            "cited_numbers": [n.number for n in all_numbers if n.role == cnj.ROLE_CITED],
            "candidates": [n.to_dict() for n in all_numbers],
        }
    )


@app.post("/scope")
def check_scope(payload: dict = Body(...)) -> JSONResponse:
    """Diz quais perguntas o assistente não deve responder.

    O limite do produto — não opinar se vale a pena arrematar, não estimar
    valor de mercado, não orientar juridicamente — está escrito no prompt de
    sistema e, ainda assim, o modelo local o ignorou na primeira vez que foi
    testado. Aqui ele é regra em vez de pedido.

    Aceita uma lista porque o fluxo do n8n classifica todas as mensagens do
    lote numa chamada só.
    """
    texts = payload.get("texts")
    if texts is None:
        texts = [payload.get("text") or ""]
    if not isinstance(texts, list):
        raise HTTPException(status_code=400, detail="`texts` deve ser uma lista")

    return JSONResponse({"results": scope.classify_all([str(t or "") for t in texts])})


@app.post("/movements/analyze")
def analyze_movements(payload: dict = Body(...)) -> JSONResponse:
    """Traduz a resposta do DataJud em sinais de risco para quem arremata.

    Aceita tanto a resposta crua da API (`hits.hits[0]._source`) quanto o
    `_source` já extraído, porque o fluxo do n8n passa uma e os testes passam a
    outra.
    """
    source = payload.get("source") or payload
    if "hits" in source:
        hits = (source.get("hits") or {}).get("hits") or []
        if not hits:
            return JSONResponse({"found": False, "reason": "not_found"})
        source = hits[0].get("_source") or {}

    if not source.get("movimentos") and not source.get("classe"):
        return JSONResponse({"found": False, "reason": "not_found"})

    return JSONResponse({"found": True, **movements.analyze(source)})


@app.post("/validate")
def validate(payload: dict = Body(...)) -> JSONResponse:
    """Valida uma ficha contra o schema. Ficha inválida não deve ser persistida."""
    if not SCHEMA_PATH.is_file():
        raise HTTPException(status_code=500, detail=f"schema ausente em {SCHEMA_PATH}")

    document: Any = payload.get("document")
    if document is None:
        raise HTTPException(status_code=400, detail="campo `document` ausente")

    # `_meta` é anotação nossa, não faz parte do contrato do modelo.
    if isinstance(document, dict):
        document = {k: v for k, v in document.items() if k != "_meta"}

    schema = json.loads(SCHEMA_PATH.read_text())
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))

    return JSONResponse(
        {
            "valid": not errors,
            "errors": [
                {"path": "/".join(str(p) for p in e.path) or "(raiz)", "message": e.message[:300]}
                for e in errors[:20]
            ],
            "error_count": len(errors),
        }
    )


@app.post("/convert")
async def convert(
    file: UploadFile = File(...),
    ocr: bool = Form(False),
) -> JSONResponse:
    data = await file.read()

    if not data:
        raise HTTPException(status_code=400, detail="arquivo vazio")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"arquivo acima do limite de {MAX_UPLOAD_BYTES // (1024 * 1024)}MB",
        )
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=415, detail="o arquivo nao e um PDF")

    started = time.monotonic()

    # Conversão por caminho em disco: é a via mais estável da API do Docling.
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "input.pdf"
        pdf_path.write_bytes(data)

        try:
            result = _get_converter(ocr).convert(str(pdf_path))
            markdown = result.document.export_to_markdown()
        except Exception as exc:  # noqa: BLE001 — a causa vai para o log e para o cliente
            log.exception("falha na conversao")
            raise HTTPException(status_code=500, detail=f"falha na conversao: {exc}") from exc

    elapsed = time.monotonic() - started
    pages = getattr(result.document, "num_pages", None)
    if callable(pages):
        pages = pages()

    log.info(
        "convertido file=%s bytes=%d ocr=%s paginas=%s chars=%d em %.1fs",
        file.filename, len(data), ocr, pages, len(markdown), elapsed,
    )

    # CPF mascarado ja na conversao: e o ponto por onde o Markdown entra no
    # sistema, e mascarar aqui garante que nenhum caminho adiante — banco,
    # prompt, resposta — veja o numero inteiro.
    markdown = fields.redact_cpf(markdown)

    return JSONResponse(
        {
            "markdown": markdown,
            # Hash dos bytes do PDF, não do Markdown: é a chave de deduplicação,
            # e o Markdown mudaria numa atualização do Docling, fazendo o mesmo
            # arquivo parecer novo.
            "sha256": hashlib.sha256(data).hexdigest(),
            "chars": len(markdown),
            "pages": pages,
            "ocr": ocr,
            "elapsed_seconds": round(elapsed, 2),
            "source_filename": file.filename,
        }
    )
