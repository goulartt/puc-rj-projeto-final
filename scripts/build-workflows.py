#!/usr/bin/env python3
"""Gera os workflows do n8n que embutem código-fonte deste repositório.

O n8n não consegue importar arquivo local de dentro de um nó de código, então a
lógica do gateway precisa viver inline no JSON do fluxo. Manter as duas cópias
na mão convidaria a divergirem — e a cópia do JSON não tem teste.

Aqui `services/gateway/gateway.js` é a fonte da verdade: ele é testado com
`node --test` e depois embutido. Editar o JSON à mão é perder o trabalho no
próximo build.

    python3 scripts/build-workflows.py
"""

from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
GATEWAY_SOURCE = ROOT / "services" / "gateway" / "gateway.js"
OUTPUT = ROOT / "workflows" / "00-llm-gateway.json"

# Sem `module` no escopo do nó de código, o export final quebraria a execução.
_EXPORTS_BLOCK = re.compile(r"\nmodule\.exports\s*=\s*\{.*?\};\s*$", re.DOTALL)


def gateway_library() -> str:
    """Fonte do gateway pronta para colar num nó de código do n8n."""
    source = GATEWAY_SOURCE.read_text()
    source = _EXPORTS_BLOCK.sub("\n", source)
    return source.replace("'use strict';\n", "").strip()


PREPARE = """
// ─── Gerado por scripts/build-workflows.py — não edite aqui ────────────────
{library}
// ─── Fim da biblioteca embutida ────────────────────────────────────────────

// Lê do gatilho pelo nome, e nao de $input: a consulta de orcamento fica entre
// os dois, e $input sempre aponta para o no imediatamente anterior — que aqui
// devolveria {{total_usd}} em vez da carga do chamador.
const items = $('Chamada de outro fluxo').all();
const input = items.length ? items[0].json : {{}};
const role = input.role || 'qa';

// Diagnostico do que efetivamente chegou ao sub-fluxo. Sem isso, "messages
// vazio" nao distingue chamador errado de payload malformado.
const received = {{ item_count: items.length, keys: Object.keys(input) }};

const spentUsd = Number($('Consultar gasto').first().json.total_usd || 0);
const limitUsd = Number($env.BUDGET_USD_LIMIT || 0);

let config;
try {{
  config = resolveConfig(role, $env);
}} catch (error) {{
  return [{{ json: {{ allowed: false, blocked_reason: error.message, role, received }} }}];
}}

const decision = canProceed(config, {{ spentUsd, limitUsd }});
if (!decision.allowed) {{
  return [{{ json: {{
    allowed: false,
    blocked_reason: decision.reason,
    role,
    provider: config.provider,
    model: config.model,
    spent_usd: spentUsd,
    limit_usd: limitUsd,
    received,
  }} }}];
}}

let request;
try {{
  request = buildRequest(config, {{
    system: input.system,
    messages: input.messages,
    schema: input.schema,
    maxTokens: input.maxTokens || 4096,
    cacheSystem: Boolean(input.cacheSystem),
    structuredMode: input.structuredMode || 'schema',
  }});
}} catch (error) {{
  return [{{ json: {{ allowed: false, blocked_reason: error.message, role, received }} }}];
}}

return [{{ json: {{
  allowed: true,
  role,
  provider: config.provider,
  model: config.model,
  base_url: config.baseUrl,
  billable: decision.billable,
  url: request.url,
  headers: request.headers,
  body: request.body,
  wants_json: input.expectJson !== undefined ? Boolean(input.expectJson) : Boolean(input.schema),
}} }}];
"""

FINALIZE = """
// ─── Gerado por scripts/build-workflows.py — não edite aqui ────────────────
{library}
// ─── Fim da biblioteca embutida ────────────────────────────────────────────

const prepared = $('Preparar chamada').first().json;
const raw = $input.first().json;

// O nó HTTP está em continueRegularOutput: erro do provedor chega como dado,
// não como exceção, para o custo ainda ser registrado e a causa ser devolvida.
if (raw.error || raw.__httpError) {{
  return [{{ json: {{
    ok: false,
    role: prepared.role,
    provider: prepared.provider,
    model: prepared.model,
    error: JSON.stringify(raw.error || raw.__httpError).slice(0, 500),
    usage: {{ input: 0, output: 0, cached: 0 }},
    cost_usd: 0,
    cost_known: true,
    billable: prepared.billable,
  }} }}];
}}

const config = {{
  provider: prepared.provider,
  model: prepared.model,
  baseUrl: prepared.base_url,
}};

const normalized = normalizeResponse(prepared.provider, raw);
const cost = computeCost(config, normalized.usage);
const parsed = prepared.wants_json ? parseJsonOutput(normalized.text) : null;

return [{{ json: {{
  ok: !normalized.refusal,
  role: prepared.role,
  provider: prepared.provider,
  model: normalized.model || prepared.model,
  text: normalized.text,
  parsed,
  // Pedimos JSON e não veio JSON: o chamador precisa saber, senão persiste vazio.
  parse_failed: prepared.wants_json && parsed === null,
  refusal: normalized.refusal,
  finish_reason: normalized.finishReason,
  usage: normalized.usage,
  cost_usd: cost.usd,
  cost_known: cost.known,
  billable: prepared.billable,
  error: null,
}} }}];
"""

RESPONSE = """
// O ultimo no da cadeia e quem responde ao chamador. Sem este passo o gateway
// devolveria o resultado do INSERT ({ success: true }) em vez da resposta do
// modelo — a contabilidade funcionaria e o chamador ficaria sem o texto.
return [{ json: $('Normalizar e precificar').first().json }];
"""

BLOCKED = """
const blocked = $input.first().json;
return [{ json: {
  ok: false,
  blocked: true,
  reason: blocked.blocked_reason,
  role: blocked.role,
  received: blocked.received ?? null,
  spent_usd: blocked.spent_usd ?? null,
  limit_usd: blocked.limit_usd ?? null,
} }];
"""


def check_code_nodes(workflow: dict, label: str) -> None:
    """Roda `node --check` em cada nó de código gerado.

    Os nós de código são montados por concatenação de string, e um escape mal
    resolvido produz JavaScript inválido que só falha em tempo de execução,
    dentro do container. Isso já aconteceu duas vezes; a verificação custa
    milissegundos e transforma o erro em falha de build.
    """
    import subprocess
    import tempfile

    for item in workflow["nodes"]:
        if item["type"] != "n8n-nodes-base.code":
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tmp:
            tmp.write(item["parameters"]["jsCode"])
            path = tmp.name
        result = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        pathlib.Path(path).unlink(missing_ok=True)
        if result.returncode:
            detail = result.stderr.strip().splitlines()[-1] if result.stderr else "erro desconhecido"
            raise SystemExit(f"JS invalido em {label} :: {item['name']}\n  {detail}")


def write(path: pathlib.Path, workflow: dict) -> None:
    check_code_nodes(workflow, path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n")
    print(f"escrito: {path.relative_to(ROOT)}")


def node(name, node_type, type_version, position, parameters, **extra):
    return {
        "parameters": parameters,
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "type": node_type,
        "typeVersion": type_version,
        "position": position,
        **extra,
    }


def build() -> dict:
    library = gateway_library()

    nodes = [
        node(
            "Chamada de outro fluxo",
            "n8n-nodes-base.executeWorkflowTrigger",
            1.2,
            [0, 0],
            {"inputSource": "passthrough"},
        ),
        node(
            "Consultar gasto",
            "n8n-nodes-base.postgres",
            2.7,
            [200, 0],
            {
                "operation": "executeQuery",
                "query": "SELECT total_usd FROM budget_spent;",
                "options": {},
            },
            credentials={"postgres": {"id": "leilao-postgres", "name": "Postgres do projeto"}},
        ),
        node(
            "Preparar chamada",
            "n8n-nodes-base.code",
            2,
            [400, 0],
            {"jsCode": PREPARE.format(library=library)},
        ),
        node(
            "Orcamento permite?",
            "n8n-nodes-base.if",
            2.3,
            [600, 0],
            {
                "conditions": {
                    "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
                    "conditions": [
                        {
                            "id": "allowed",
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                            "leftValue": "={{ $json.allowed }}",
                            "rightValue": "",
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
        ),
        node(
            "Chamar provedor",
            "n8n-nodes-base.httpRequest",
            4.5,
            [820, -110],
            {
                "method": "POST",
                "url": "={{ $json.url }}",
                "sendHeaders": True,
                "specifyHeaders": "json",
                "jsonHeaders": "={{ JSON.stringify($json.headers) }}",
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify($json.body) }}",
                "options": {"timeout": 600000},
            },
            onError="continueRegularOutput",
        ),
        node(
            "Normalizar e precificar",
            "n8n-nodes-base.code",
            2,
            [1040, -110],
            {"jsCode": FINALIZE.format(library=library)},
        ),
        node(
            "Registrar custo",
            "n8n-nodes-base.postgres",
            2.7,
            [1260, -110],
            {
                "operation": "executeQuery",
                "query": (
                    "INSERT INTO llm_calls "
                    "(role, provider, model, input_tokens, output_tokens, cached_tokens, cost_usd, error) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8);"
                ),
                "options": {
                    "queryReplacement": (
                        "={{ [$json.role, $json.provider, $json.model, $json.usage.input,"
                    " $json.usage.output, $json.usage.cached, $json.cost_usd, $json.error] }}"
                    )
                },
            },
            credentials={"postgres": {"id": "leilao-postgres", "name": "Postgres do projeto"}},
        ),
        node(
            "Resposta",
            "n8n-nodes-base.code",
            2,
            [1480, -110],
            {"jsCode": RESPONSE},
        ),
        node(
            "Bloqueado",
            "n8n-nodes-base.code",
            2,
            [820, 110],
            {"jsCode": BLOCKED},
        ),
    ]

    connections = {
        "Chamada de outro fluxo": {"main": [[{"node": "Consultar gasto", "type": "main", "index": 0}]]},
        "Consultar gasto": {"main": [[{"node": "Preparar chamada", "type": "main", "index": 0}]]},
        "Preparar chamada": {"main": [[{"node": "Orcamento permite?", "type": "main", "index": 0}]]},
        "Orcamento permite?": {
            "main": [
                [{"node": "Chamar provedor", "type": "main", "index": 0}],
                [{"node": "Bloqueado", "type": "main", "index": 0}],
            ]
        },
        "Chamar provedor": {"main": [[{"node": "Normalizar e precificar", "type": "main", "index": 0}]]},
        "Normalizar e precificar": {"main": [[{"node": "Registrar custo", "type": "main", "index": 0}]]},
        "Registrar custo": {"main": [[{"node": "Resposta", "type": "main", "index": 0}]]},
    }

    return {
        # ID fixo: reimportar atualiza o mesmo fluxo em vez de criar cópia, e
        # os fluxos chamadores podem referenciá-lo sem depender do que o n8n
        # sorteou na primeira importação.
        "id": "llmgateway0000001",
        "name": "00 - Gateway de modelo",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
    }


GATEWAY_ID = "llmgateway0000001"
SMOKE_OUTPUT = ROOT / "tests" / "workflows" / "99-gateway-smoke.json"

SMOKE_PAYLOAD = """
// Carga fixa para o smoke test. Pergunta trivial de proposito: o que se quer
// verificar e o caminho — env var -> adaptador -> provedor -> usage
// normalizado -> custo registrado —, nao a qualidade da resposta.
return [{ json: {
  role: 'qa',
  system: 'Responda em uma unica palavra, sem pontuacao.',
  messages: [{ role: 'user', content: 'Capital do Brasil?' }],
  maxTokens: 2048,
} }];
"""


def build_smoke() -> dict:
    """Fluxo mínimo que exercita o gateway ponta a ponta.

    Existe porque `n8n execute --id` não aceita dados de entrada, então um
    sub-workflow com Execute Workflow Trigger não roda sozinho pela CLI. Este
    fluxo é também a forma como a Fase 4 vai chamar o gateway de verdade.
    """
    nodes = [
        node("Disparo manual", "n8n-nodes-base.manualTrigger", 1, [0, 0], {}),
        node("Carga de teste", "n8n-nodes-base.code", 2, [200, 0], {"jsCode": SMOKE_PAYLOAD}),
        node(
            "Chamar gateway",
            "n8n-nodes-base.executeWorkflow",
            1.3,
            [400, 0],
            {
                "workflowId": {"__rl": True, "value": GATEWAY_ID, "mode": "id"},
                # Sem `workflowInputs`: o gateway usa trigger em passthrough e
                # recebe os itens do chamador como estão. Definir um mapa aqui,
                # ainda que vazio, sobrescreve isso e entrega payload em branco.
                "options": {"waitForSubWorkflow": True},
            },
        ),
    ]
    connections = {
        "Disparo manual": {"main": [[{"node": "Carga de teste", "type": "main", "index": 0}]]},
        "Carga de teste": {"main": [[{"node": "Chamar gateway", "type": "main", "index": 0}]]},
    }
    return {
        "id": "gatewaysmoke00001",
        "name": "99 - Smoke test do gateway",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
    }


INGEST_SMOKE_OUTPUT = ROOT / "tests" / "workflows" / "98-ingest-smoke.json"


def build_ingest_smoke() -> dict:
    """Roda a ingestão com o edital versionado, sem depender do Telegram."""
    nodes = [
        node("Disparo manual", "n8n-nodes-base.manualTrigger", 1, [0, 0], {}),
        node("Ler edital do disco", "n8n-nodes-base.readWriteFile", 1.1, [200, 0], {
            "fileSelector": "/data/editais/edital-exemplo.pdf",
            "options": {"dataPropertyName": "data"},
        }),
        node("Identificar o chat", "n8n-nodes-base.code", 2, [400, 0], {"jsCode": """
// chat_id fixo de teste: a ingestao guarda a ficha por chat, e o smoke test
// precisa de um identificador estavel para poder reexecutar sem duplicar.
return [{ json: { chat_id: 'smoke-test', file_name: 'edital-exemplo.pdf' },
          binary: $input.first().binary }];
"""}),
        node("Chamar ingestao", "n8n-nodes-base.executeWorkflow", 1.3, [600, 0], {
            "workflowId": {"__rl": True, "value": INGEST_ID, "mode": "id"},
            "options": {"waitForSubWorkflow": True},
        }),
    ]
    connections = {
        "Disparo manual": {"main": [[{"node": "Ler edital do disco", "type": "main", "index": 0}]]},
        "Ler edital do disco": {"main": [[{"node": "Identificar o chat", "type": "main", "index": 0}]]},
        "Identificar o chat": {"main": [[{"node": "Chamar ingestao", "type": "main", "index": 0}]]},
    }
    return {
        "id": "ingestsmoke00001",
        "name": "98 - Smoke test da ingestao",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
    }


# ─── 02 — Ingestão do edital (Estágio 1) ────────────────────────────────────

INGEST_ID = "editalingest0001"
INGEST_OUTPUT = ROOT / "workflows" / "02-edital-ingest.json"

INGEST_PREPARE_PROMPT = """
// Monta a chamada de extração. O prompt e o schema vêm de arquivos montados no
// container, e não embutidos aqui: assim continuam revisáveis em diff e o
// mesmo texto que o repositório versiona é o que o modelo recebe.
const trigger = $('Chamada de outro fluxo').first().json;
const converted = $('Converter PDF').first().json;
const markdown = converted.markdown || '';
const deterministic = $('Extrair campos deterministicos').first().json;
const systemPrompt = $('Buscar prompt de analista').first().json.text;
const schema = $('Buscar schema da ficha').first().json.schema;

// Decodificação restrita é otimização, não garantia: quem valida a ficha é o
// serviço de documentos, logo adiante. O Ollama nao consegue compilar um
// schema deste tamanho em gramatica GBNF, entao ela fica desligavel por
// variavel de ambiente sem que o pipeline perca correcao.
// schema | json | none — o que o provedor consegue impor. A DeepSeek recusa
// json_schema ("This response_format type is unavailable now") e aceita json;
// o Ollama nao compila um schema deste tamanho em gramatica. Em qualquer modo
// quem garante o contrato e a validacao posterior.
const structuredMode = String($env.LLM_EXTRACTION_STRUCTURED || 'schema');
const providerEnforcesSchema = structuredMode === 'schema';

if (!markdown.trim()) {
  throw new Error('Docling devolveu markdown vazio — edital ilegivel ou conversao falhou.');
}

// O bloco determinístico entra como conferência, não como verdade. O prompt
// instrui o modelo a marcar confidence: low onde discordar do texto.
//
// Valores, datas e matrícula vão aqui porque sao exatamente o tipo de dado em
// que parafrase e inaceitavel: na primeira ficha gerada por modelo, 10 das 35
// citacoes nao existiam literalmente no edital.
const hint = JSON.stringify({
  court_case: deterministic.court_case,
  cited_numbers: deterministic.cited_numbers,
  money: (deterministic.money || []).map((m) => m.raw),
  auction_rounds: deterministic.auction_rounds,
  property_registry: (deterministic.property_registry || []).map((m) => m.value),
  percentages: (deterministic.percentages || []).map((p) => p.raw),
  areas: (deterministic.areas || []).map((a) => a.raw),
  cnpj: deterministic.cnpj,
  // CPF nunca vai para o prompt; so a contagem, para o modelo saber que o
  // documento tem dado pessoal e nao tentar reproduzi-lo.
  cpf_count: deterministic.cpf_count,
}, null, 2);

// Sem decodificação restrita o modelo precisa VER o schema: descrever os
// campos em prosa não basta, e a primeira versão deste fluxo os omitia por
// completo — o modelo inventava a estrutura e a validação reprovava tudo.
// Montado com join em vez de escapes: este bloco vive dentro de uma string
// Python, onde uma barra-n sozinha viraria quebra de linha de verdade e
// partiria o literal JavaScript ao meio.
const schemaInstruction = providerEnforcesSchema ? '' : [
  '',
  '',
  'ESQUEMA OBRIGATORIO DA RESPOSTA (JSON Schema). Responda com um unico objeto',
  'JSON que satisfaca este esquema, sem texto ao redor. Nao invente campos:',
  '`additionalProperties` e false em todos os niveis.',
  '',
  // Sem esta linha o modelo aninhou `auctioneer_fee` e `payment` dentro de
  // `debts`. Enumerar as chaves de topo custa poucos tokens e evita o erro.
  'As chaves de PRIMEIRO NIVEL sao exatamente estas, nenhuma aninhada em outra: '
    + Object.keys(schema.properties).join(', ') + '.',
  JSON.stringify(schema),
].join(String.fromCharCode(10));

return [{ json: {
  role: 'extraction',
  system: systemPrompt + schemaInstruction,
  schema,
  structuredMode,
  // Cache no bloco de sistema: o prompt de analista é idêntico entre editais.
  cacheSystem: true,
  // Modelo de raciocinio gasta boa parte do orcamento de saida pensando: o
  // deepseek-v4-flash consumiu 24 mil tokens de reasoning neste edital e so
  // depois escreveu a ficha. Com 16 mil ele terminava em finish_reason=length
  // e conteudo vazio.
  maxTokens: Number($env.LLM_EXTRACTION_MAX_TOKENS || 16000),
  expectJson: true,
  messages: [{ role: 'user', content:
    'EXTRACAO DETERMINISTICA (confira contra o texto):\\n' + hint +
    '\\n\\nEDITAL:\\n' + markdown }],
  // Carregado adiante, na persistência.
  _chat_id: trigger.chat_id,
  _file_name: trigger.file_name,
  _markdown: markdown,
  _sha256: converted.sha256,
  _deterministic: deterministic,
} }];
"""

INGEST_CHECK = """
// Decide se a ficha pode ser persistida. Ficha que não valida não entra no
// banco: uma ficha parcial envenena silenciosamente toda pergunta seguinte.
const prepared = $('Preparar extracao').first().json;
const gateway = $('Extrair ficha').first().json;
const check = $('Validar ficha').first().json;

const problems = [];
if (gateway.blocked) problems.push('gateway bloqueou: ' + gateway.reason);
if (gateway.refusal) problems.push('modelo recusou a requisicao');
if (gateway.error) problems.push('erro do provedor: ' + String(gateway.error).slice(0, 200));
if (gateway.parse_failed) problems.push('resposta do modelo nao era JSON');
if (check && check.valid === false) {
  problems.push('ficha invalida (' + check.error_count + ' erro(s)): ' +
    (check.errors || []).slice(0, 3).map((e) => e.path + ': ' + e.message).join(' | '));
}

return [{ json: {
  ok: problems.length === 0,
  problems,
  chat_id: prepared._chat_id,
  file_name: prepared._file_name,
  // Hash calculado pelo serviço sobre os bytes do PDF. O sandbox do nó de
  // código nao expõe `crypto`, e o hash do arquivo é a chave certa de qualquer
  // forma — o do Markdown mudaria numa atualização do conversor.
  sha256: prepared._sha256,
  markdown: prepared._markdown,
  deterministic: prepared._deterministic,
  analysis: gateway.parsed || null,
  usage: gateway.usage || null,
  cost_usd: gateway.cost_usd ?? null,
} }];
"""


def build_ingest() -> dict:
    """Estágio 1: PDF do edital vira ficha estruturada e persistida.

    Sub-workflow de propósito: o gatilho do Telegram entra na Fase 6, e manter
    a ingestão separada permite testá-la com um arquivo local, sem depender de
    bot configurado.
    """
    nodes = [
        node("Chamada de outro fluxo", "n8n-nodes-base.executeWorkflowTrigger", 1.2,
             [0, 0], {"inputSource": "passthrough"}),
        node("Converter PDF", "n8n-nodes-base.httpRequest", 4.5, [200, 0], {
            "method": "POST",
            "url": "={{ $env.DOCLING_URL }}/convert",
            "sendBody": True,
            "contentType": "multipart-form-data",
            "bodyParameters": {"parameters": [
                {"parameterType": "formBinaryData", "name": "file", "inputDataFieldName": "data"},
            ]},
            "options": {"timeout": 900000},
        }),
        node("Extrair campos deterministicos", "n8n-nodes-base.httpRequest", 4.5, [400, 0], {
            "method": "POST",
            "url": "={{ $env.DOCLING_URL }}/extract",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ markdown: $json.markdown }) }}",
            "options": {"timeout": 60000},
        }),
        # Prompt e schema vêm por HTTP do serviço de documentos, e não de nós
        # de leitura de arquivo: aquele nó entrega binário e exigiria um nó de
        # conversão para cada um. Assim continuam sendo arquivos do repositório.
        node("Buscar prompt de analista", "n8n-nodes-base.httpRequest", 4.5, [600, -110], {
            "url": "={{ $env.DOCLING_URL }}/prompt/analyst-extraction",
            "options": {"timeout": 30000},
        }),
        node("Buscar schema da ficha", "n8n-nodes-base.httpRequest", 4.5, [600, 110], {
            "url": "={{ $env.DOCLING_URL }}/schema",
            "options": {"timeout": 30000},
        }),
        node("Preparar extracao", "n8n-nodes-base.code", 2, [820, 0],
             {"jsCode": INGEST_PREPARE_PROMPT}),
        node("Extrair ficha", "n8n-nodes-base.executeWorkflow", 1.3, [1020, 0], {
            "workflowId": {"__rl": True, "value": GATEWAY_ID, "mode": "id"},
            "options": {"waitForSubWorkflow": True},
        }),
        node("Validar ficha", "n8n-nodes-base.httpRequest", 4.5, [1220, 0], {
            "method": "POST",
            "url": "={{ $env.DOCLING_URL }}/validate",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ document: $json.parsed }) }}",
            "options": {"timeout": 60000},
        }, onError="continueRegularOutput"),
        node("Conferir resultado", "n8n-nodes-base.code", 2, [1420, 0],
             {"jsCode": INGEST_CHECK}),
        node("Ficha valida?", "n8n-nodes-base.if", 2.3, [1620, 0], {
            "conditions": {
                "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
                "conditions": [{
                    "id": "ok",
                    "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                    "leftValue": "={{ $json.ok }}",
                    "rightValue": "",
                }],
                "combinator": "and",
            },
            "options": {},
        }),
        node("Persistir ficha", "n8n-nodes-base.postgres", 2.7, [1840, -110], {
            "operation": "executeQuery",
            "query": (
                "INSERT INTO auction_notices "
                "(chat_id, file_name, sha256, markdown, deterministic, analysis) "
                "VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb) "
                "ON CONFLICT (chat_id, sha256) DO UPDATE SET "
                "  analysis = EXCLUDED.analysis, "
                "  deterministic = EXCLUDED.deterministic, "
                "  created_at = now() "
                "RETURNING id, chat_id, created_at;"
            ),
            # Array, e não string separada por vírgula: o n8n divide a forma
            # em string por vírgula, e o Markdown do edital tem centenas
            # delas — os parâmetros se desalinhavam e o INSERT recebia JSON
            # cortado ao meio ("invalid input syntax for type json").
            "options": {"queryReplacement":
                "={{ [$json.chat_id, $json.file_name, $json.sha256, $json.markdown,"
                " JSON.stringify($json.deterministic), JSON.stringify($json.analysis)] }}"},
        }, credentials={"postgres": {"id": "leilao-postgres", "name": "Postgres do projeto"}}),
        node("Resposta de sucesso", "n8n-nodes-base.code", 2, [2040, -110], {"jsCode": """
const saved = $input.first().json;
const result = $('Conferir resultado').first().json;
return [{ json: {
  ok: true,
  notice_id: saved.id,
  chat_id: result.chat_id,
  analysis: result.analysis,
  usage: result.usage,
  cost_usd: result.cost_usd,
} }];
"""}),
        node("Resposta de falha", "n8n-nodes-base.code", 2, [1840, 110], {"jsCode": """
// Falha explícita e com causa. Persistir ficha parcial seria pior: toda
// pergunta seguinte responderia com base em dado que ninguém conferiu.
const r = $input.first().json;
return [{ json: { ok: false, problems: r.problems, chat_id: r.chat_id, file_name: r.file_name } }];
"""}),
    ]

    connections = {
        "Chamada de outro fluxo": {"main": [[{"node": "Converter PDF", "type": "main", "index": 0}]]},
        "Converter PDF": {"main": [[{"node": "Extrair campos deterministicos", "type": "main", "index": 0}]]},
        "Extrair campos deterministicos": {"main": [[{"node": "Buscar prompt de analista", "type": "main", "index": 0}]]},
        "Buscar prompt de analista": {"main": [[{"node": "Buscar schema da ficha", "type": "main", "index": 0}]]},
        "Buscar schema da ficha": {"main": [[{"node": "Preparar extracao", "type": "main", "index": 0}]]},
        "Preparar extracao": {"main": [[{"node": "Extrair ficha", "type": "main", "index": 0}]]},
        "Extrair ficha": {"main": [[{"node": "Validar ficha", "type": "main", "index": 0}]]},
        "Validar ficha": {"main": [[{"node": "Conferir resultado", "type": "main", "index": 0}]]},
        "Conferir resultado": {"main": [[{"node": "Ficha valida?", "type": "main", "index": 0}]]},
        "Ficha valida?": {"main": [
            [{"node": "Persistir ficha", "type": "main", "index": 0}],
            [{"node": "Resposta de falha", "type": "main", "index": 0}],
        ]},
        "Persistir ficha": {"main": [[{"node": "Resposta de sucesso", "type": "main", "index": 0}]]},
    }

    return {
        "id": INGEST_ID,
        "name": "02 - Ingestao do edital",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
    }


    write(SMOKE_OUTPUT, build_smoke())

    write(INGEST_OUTPUT, build_ingest())

    write(INGEST_SMOKE_OUTPUT, build_ingest_smoke())


# ─── 03 — Consulta processual no DataJud (Estágio 2) ────────────────────────

LOOKUP_ID = "processolookup01"
LOOKUP_OUTPUT = ROOT / "workflows" / "03-processo-lookup.json"
LOOKUP_SMOKE_OUTPUT = ROOT / "tests" / "workflows" / "97-lookup-smoke.json"

LOOKUP_PREPARE = """
// O DataJud consulta pelo numero sem pontuacao, e o indice e derivado do
// proprio numero (segmentos J e TR). Sem alias, o segmento nao e Justica
// Estadual e esta fora da cobertura — dizemos isso em vez de chutar um indice.
const input = $input.first().json;
const number = String(input.cnj_number || '');
const alias = input.datajud_alias || '';
const digits = number.replace(/\\D/g, '');

if (digits.length !== 20) {
  return [{ json: { skip: true, status: 'invalid_number', cnj_number: number } }];
}
if (!alias) {
  return [{ json: { skip: true, status: 'out_of_coverage', cnj_number: number } }];
}

return [{ json: {
  skip: false,
  cnj_number: number,
  digits,
  alias,
  url: `${$env.DATAJUD_BASE_URL}/${alias}/_search`,
} }];
"""

LOOKUP_INTERPRET = """
const prepared = $('Preparar consulta').first().json;
const analysis = $input.first().json;

// Sigilo nao e ausencia de risco: e ausencia de informacao, e a resposta
// precisa dizer isso com essas letras em vez de silenciar.
if (!analysis.found) {
  return [{ json: {
    found: false,
    status: analysis.reason || 'not_found',
    cnj_number: prepared.cnj_number,
    court_alias: prepared.alias,
    needs_summary: false,
  } }];
}

const sealed = Number(analysis.nivel_sigilo || 0) > 0;

return [{ json: {
  found: true,
  status: sealed ? 'sealed' : 'ok',
  cnj_number: prepared.cnj_number,
  court_alias: prepared.alias,
  analysis,
  // Sem sinal ativo nao ha o que resumir: gastar uma chamada de LLM para
  // dizer "nada de relevante" e desperdicio, e a ficha ja carrega os dados.
  needs_summary: !sealed && (analysis.active_signals || []).length > 0,
} }];
"""

LOOKUP_SUMMARIZE = """
const interpreted = $('Classificar resultado').first().json;
const systemPrompt = $('Buscar prompt de resumo').first().json.text;

return [{ json: {
  role: 'qa',
  system: systemPrompt,
  expectJson: true,
  maxTokens: Number($env.LLM_QA_MAX_TOKENS || 4096),
  messages: [{ role: 'user', content:
    'MOVIMENTOS E SINAIS:\\n' + JSON.stringify(interpreted.analysis, null, 2) }],
} }];
"""

LOOKUP_RESULT = """
const interpreted = $('Classificar resultado').first().json;
const gateway = interpreted.needs_summary ? $('Resumir risco').first().json : null;

const summary = gateway && gateway.parsed ? gateway.parsed : {
  summary: interpreted.found
    ? 'Nenhum movimento recente indica risco para a arrematacao.'
    : 'Nao foi possivel obter a situacao processual.',
  signals: interpreted.found ? (interpreted.analysis.active_signals || []) : [],
  confidence: interpreted.found ? 'medium' : 'low',
};

return [{ json: {
  cnj_number: interpreted.cnj_number,
  court_alias: interpreted.court_alias,
  status: interpreted.status,
  found: interpreted.found,
  analysis: interpreted.analysis || null,
  summary,
} }];
"""


def build_lookup() -> dict:
    """Estágio 2: situação processual pública, quando há processo identificado.

    Só dispara com número CNJ válido. É o único campo do edital conferível
    contra fonte externa, e o gancho natural de `v0.6-agente`: consultar o
    processo vira uma ferramenta que o agente decide chamar.
    """
    nodes = [
        node("Chamada de outro fluxo", "n8n-nodes-base.executeWorkflowTrigger", 1.2,
             [0, 0], {"inputSource": "passthrough"}),
        node("Preparar consulta", "n8n-nodes-base.code", 2, [200, 0], {"jsCode": LOOKUP_PREPARE}),
        node("Consultar DataJud", "n8n-nodes-base.httpRequest", 4.5, [400, 0], {
            "method": "POST",
            "url": "={{ $json.url }}",
            "sendHeaders": True,
            "specifyHeaders": "json",
            "jsonHeaders": '={{ JSON.stringify({ "Authorization": "APIKey " + $env.DATAJUD_API_KEY }) }}',
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": '={{ JSON.stringify({ query: { match: { numeroProcesso: $json.digits } } }) }}',
            "options": {"timeout": 60000},
        }, onError="continueRegularOutput"),
        node("Interpretar movimentos", "n8n-nodes-base.httpRequest", 4.5, [600, 0], {
            "method": "POST",
            "url": "={{ $env.DOCLING_URL }}/movements/analyze",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ source: $json }) }}",
            "options": {"timeout": 60000},
        }, onError="continueRegularOutput"),
        node("Classificar resultado", "n8n-nodes-base.code", 2, [800, 0],
             {"jsCode": LOOKUP_INTERPRET}),
        node("Precisa de resumo?", "n8n-nodes-base.if", 2.3, [1000, 0], {
            "conditions": {
                "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
                "conditions": [{
                    "id": "needs",
                    "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                    "leftValue": "={{ $json.needs_summary }}",
                    "rightValue": "",
                }],
                "combinator": "and",
            },
            "options": {},
        }),
        node("Buscar prompt de resumo", "n8n-nodes-base.httpRequest", 4.5, [1200, -110], {
            "url": "={{ $env.DOCLING_URL }}/prompt/processo-summary",
            "options": {"timeout": 30000},
        }),
        node("Preparar resumo", "n8n-nodes-base.code", 2, [1400, -110],
             {"jsCode": LOOKUP_SUMMARIZE}),
        node("Resumir risco", "n8n-nodes-base.executeWorkflow", 1.3, [1600, -110], {
            "workflowId": {"__rl": True, "value": GATEWAY_ID, "mode": "id"},
            "options": {"waitForSubWorkflow": True},
        }),
        node("Resultado com resumo", "n8n-nodes-base.code", 2, [1800, -110],
             {"jsCode": LOOKUP_RESULT}),
        node("Resultado sem resumo", "n8n-nodes-base.code", 2, [1200, 110],
             {"jsCode": LOOKUP_RESULT}),
    ]

    connections = {
        "Chamada de outro fluxo": {"main": [[{"node": "Preparar consulta", "type": "main", "index": 0}]]},
        "Preparar consulta": {"main": [[{"node": "Consultar DataJud", "type": "main", "index": 0}]]},
        "Consultar DataJud": {"main": [[{"node": "Interpretar movimentos", "type": "main", "index": 0}]]},
        "Interpretar movimentos": {"main": [[{"node": "Classificar resultado", "type": "main", "index": 0}]]},
        "Classificar resultado": {"main": [[{"node": "Precisa de resumo?", "type": "main", "index": 0}]]},
        "Precisa de resumo?": {"main": [
            [{"node": "Buscar prompt de resumo", "type": "main", "index": 0}],
            [{"node": "Resultado sem resumo", "type": "main", "index": 0}],
        ]},
        "Buscar prompt de resumo": {"main": [[{"node": "Preparar resumo", "type": "main", "index": 0}]]},
        "Preparar resumo": {"main": [[{"node": "Resumir risco", "type": "main", "index": 0}]]},
        "Resumir risco": {"main": [[{"node": "Resultado com resumo", "type": "main", "index": 0}]]},
    }

    return {
        "id": LOOKUP_ID,
        "name": "03 - Consulta processual (DataJud)",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
    }


def build_lookup_smoke() -> dict:
    """Consulta o processo do edital de exemplo, sem depender da ingestão."""
    payload = """
// Dois casos, para exercitar os dois ramos do fluxo:
//  - o processo do edital em data/editais/: sem sinal ativo, resposta sem LLM
//  - uma execucao com embargos recentes: dispara o resumo de risco
return [
  { json: { cnj_number: '1002465-53.2023.8.26.0100', datajud_alias: 'api_publica_tjsp' } },
  { json: { cnj_number: '4001238-08.2025.8.26.0358', datajud_alias: 'api_publica_tjsp' } },
];
"""
    nodes = [
        node("Disparo manual", "n8n-nodes-base.manualTrigger", 1, [0, 0], {}),
        node("Processo do edital", "n8n-nodes-base.code", 2, [200, 0], {"jsCode": payload}),
        node("Chamar consulta", "n8n-nodes-base.executeWorkflow", 1.3, [400, 0], {
            "workflowId": {"__rl": True, "value": LOOKUP_ID, "mode": "id"},
            # `each`: o sub-fluxo trata um processo por execucao, entao com o
            # modo `once` (padrao) so o primeiro item seria consultado.
            "mode": "each",
            "options": {"waitForSubWorkflow": True},
        }),
    ]
    connections = {
        "Disparo manual": {"main": [[{"node": "Processo do edital", "type": "main", "index": 0}]]},
        "Processo do edital": {"main": [[{"node": "Chamar consulta", "type": "main", "index": 0}]]},
    }
    return {
        "id": "lookupsmoke00001",
        "name": "97 - Smoke test da consulta processual",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
    }


if __name__ == "__main__":
    write(OUTPUT, build())
    write(SMOKE_OUTPUT, build_smoke())
    write(INGEST_OUTPUT, build_ingest())
    write(INGEST_SMOKE_OUTPUT, build_ingest_smoke())
    write(LOOKUP_OUTPUT, build_lookup())
    write(LOOKUP_SMOKE_OUTPUT, build_lookup_smoke())
