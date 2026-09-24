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
from extractors import cnj, document, fields, location, movements, presentation, scope  # noqa: E402

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

    Parte dos provedores de decodificação restrita não resolve `$ref` e falha
    ao compilar o schema. A forma achatada funciona em todos, então é ela que
    servimos.

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

    return JSONResponse(_deterministic(markdown))


def _deterministic(markdown: str) -> dict:
    """O bloco determinístico, compartilhado por `/extract` e `/focus`."""
    main = cnj.main_case(markdown)
    all_numbers = cnj.extract(markdown)
    return {
        **fields.extract_all(markdown),
        "court_case": main.to_dict() if main else None,
        # Precedentes citados no juridiquês do edital. Registrados para
        # auditoria e explicitamente fora da consulta ao DataJud: são causas
        # alheias ao imóvel.
        "cited_numbers": [n.number for n in all_numbers if n.role == cnj.ROLE_CITED],
        "candidates": [n.to_dict() for n in all_numbers],
    }


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


@app.post("/present")
def present(payload: dict = Body(...)) -> JSONResponse:
    """Traduz a ficha para o que a pessoa lê.

    Vive aqui, e não num nó de código do n8n, pela mesma razão dos outros
    extratores: em Python tem teste. A tradução de campo, o desempate de risco
    genérico e a derivação de fato favorável são regras, e regra sem teste
    apodrece em silêncio — o caminho `debts.enforced_claim` foi parar na tela
    de um usuário justamente porque não havia nada afirmando o contrário.
    """
    ficha = payload.get("ficha") or payload.get("analysis") or {}
    if not isinstance(ficha, dict):
        raise HTTPException(status_code=400, detail="`ficha` deve ser um objeto")

    case = payload.get("case")
    max_items = int(payload.get("max_items") or 3)
    # O bloco determinístico traz a contagem de imóveis do documento, que a
    # ficha não tem: ela descreve um lote e não sabe quantos existem.
    deterministic = payload.get("deterministic")
    loc = payload.get("location")
    view = presentation.present(ficha, case, max_items=max_items,
                                deterministic=deterministic, location=loc)
    # A ficha em português, para o Q&A ler no lugar do JSON com chaves em
    # inglês. Sem isto, o modelo repete a chave na resposta
    # ("extinguished_by_sale: true") ou tenta traduzi-la e inventa.
    return JSONResponse({**view,
                         "ficha_text": presentation.ficha_to_text(ficha),
                         "case_text": presentation.case_to_text(case),
                         "location_text": presentation.location_to_text(loc)})


@app.post("/inspect")
def inspect_document(payload: dict = Body(...)) -> JSONResponse:
    """Diz se o documento convertido é mesmo um edital de leilão.

    Chamado logo depois da conversão e antes de tudo o que custa. Devolve a
    mensagem pronta quando recusa, para o fluxo não ter de saber o motivo.
    """
    markdown = payload.get("markdown") or ""
    verdict = document.inspect(markdown)
    return JSONResponse({
        **verdict,
        "message": None if verdict["is_notice"] else document.rejection_message(verdict),
    })


@app.post("/location")
def location_score(payload: dict = Body(...)) -> JSONResponse:
    """Índice de entorno a partir do endereço do imóvel.

    Chama Nominatim e Overpass, serviços públicos do OpenStreetMap. Falha de
    rede ou endereço não localizado devolve `found: false` com o motivo, e não
    erro: a ficha vale sem o entorno, e a ingestão não pode cair por causa dele.
    """
    address = (payload.get("address") or "").strip()
    if not address:
        return JSONResponse({"found": False, "reason": "edital sem endereço"})
    try:
        return JSONResponse(location.evaluate(address))
    except Exception as erro:  # noqa: BLE001 — serviço externo; a ficha segue sem ele
        log.warning("entorno indisponivel para %r: %s", address, erro)
        return JSONResponse({"found": False, "reason": "serviço de mapas indisponível",
                             "address": address})


@app.post("/lot-choice")
def lot_choice(payload: dict = Body(...)) -> JSONResponse:
    """Interpreta a resposta à pergunta "qual imóvel você quer analisar?"."""
    lots = payload.get("lots") or []
    if not isinstance(lots, list):
        raise HTTPException(status_code=400, detail="`lots` deve ser uma lista")

    verdict = scope.parse_lot_choice(payload.get("text") or "", lots)
    # `index` nulo com `understood` verdadeiro é o edital sem matrícula legível:
    # a pessoa confirmou a análise, mas não há lote a apontar.
    chosen = lots[verdict["index"] - 1] if verdict["index"] else None
    # Num catálogo de 482 imóveis a lista inteira não vai para a conversa: só a
    # do imóvel escolhido e a dos candidatos, quando a resposta casou com mais
    # de um.
    grande = len(lots) > scope.LIST_LIMIT
    return JSONResponse({
        **verdict,
        "lot": chosen,
        "lot_label": presentation.describe_lot(chosen) if chosen else None,
        "catalog": grande,
        "total": len(lots),
        "options": [] if grande else presentation.describe_lots(lots),
        "candidate_options": [presentation.describe_lot(lots[i - 1])
                              for i in verdict.get("candidates") or []],
    })


@app.post("/focus")
def focus(payload: dict = Body(...)) -> JSONResponse:
    """O edital recortado no imóvel escolhido, com o bloco determinístico dele.

    Num catálogo, o documento vai à extração sem as linhas dos outros imóveis:
    no de referência, 525 mil caracteres viraram 90 mil, e a extração deixou de
    receber 159 mil tokens para escolher sozinha entre 482 imóveis. O bloco
    determinístico é recalculado sobre o texto recortado; calculado sobre o
    catálogo, a dica do prompt levaria os valores e as datas de todos os lotes.

    Fora de catálogo, devolve o documento como veio: num edital de poucos
    imóveis em parágrafos, a parte comum e a de cada lote se misturam, e
    recortar deixaria a ficha sem prazos.
    """
    markdown = payload.get("markdown") or ""
    lot = payload.get("lot") or {}
    if lot.get("item") is None or not markdown.strip():
        return JSONResponse({"markdown": markdown, "focused": False})
    recortado = fields.focus_catalog(markdown, lot)
    return JSONResponse({"markdown": recortado, "focused": recortado != markdown,
                         "deterministic": _deterministic(recortado),
                         "chars_before": len(markdown), "chars_after": len(recortado)})


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
