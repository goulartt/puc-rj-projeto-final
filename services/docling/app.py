"""PDF -> Markdown conversion service backed by Docling.

Single responsibility: turn an edital PDF into Markdown that preserves tables
and numbered clauses. Everything downstream (regex extractors, the LLM) reads
this output, so conversion quality caps the whole pipeline's quality.

OCR is off by default: auction editais are normally born-digital PDFs with a
text layer, and running OCR on those is slow and adds transcription noise.
Send `ocr=true` for scanned documents.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

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


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "artifacts_path": os.getenv("DOCLING_ARTIFACTS_PATH")}


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

    return JSONResponse(
        {
            "markdown": markdown,
            "chars": len(markdown),
            "pages": pages,
            "ocr": ocr,
            "elapsed_seconds": round(elapsed, 2),
            "source_filename": file.filename,
        }
    )
