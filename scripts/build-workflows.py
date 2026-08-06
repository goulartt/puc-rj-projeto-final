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
  wants_json: Boolean(input.schema),
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
                    "(role, provider, model, input_tokens, output_tokens, cached_tokens, cost_usd) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7);"
                ),
                "options": {
                    "queryReplacement": (
                        "={{ $json.role }},={{ $json.provider }},={{ $json.model }},"
                        "={{ $json.usage.input }},={{ $json.usage.output }},"
                        "={{ $json.usage.cached }},={{ $json.cost_usd }}"
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


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n")
    print(f"escrito: {OUTPUT.relative_to(ROOT)}")

    SMOKE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    SMOKE_OUTPUT.write_text(json.dumps(build_smoke(), indent=2, ensure_ascii=False) + "\n")
    print(f"escrito: {SMOKE_OUTPUT.relative_to(ROOT)}")
