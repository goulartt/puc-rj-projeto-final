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
// Quem pediu. Vai ate `llm_calls` para o custo ser atribuivel a uma
// conversa, e nao so contabilizado em bloco.
const chatId = input.chatId || input.chat_id || null;

// Diagnostico do que efetivamente chegou ao sub-fluxo. Sem isso, "messages
// vazio" nao distingue chamador errado de payload malformado.
const received = {{ item_count: items.length, keys: Object.keys(input) }};

const spentUsd = Number($('Consultar gasto').first().json.total_usd || 0);
const limitUsd = Number($env.BUDGET_USD_LIMIT || 0);

let config;
try {{
  config = resolveConfig(role, $env);
}} catch (error) {{
  return [{{ json: {{ allowed: false, blocked_reason: error.message, role, chatId, received }} }}];
}}

const decision = canProceed(config, {{ spentUsd, limitUsd }});
if (!decision.allowed) {{
  return [{{ json: {{
    allowed: false,
    blocked_reason: decision.reason,
    role,
  chatId,
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
    // Vazio significa "deixe o provedor decidir", que e o comportamento
    // anterior a esta variavel existir.
    reasoning: ($env[`LLM_${{role.toUpperCase()}}_REASONING`] || '').trim() || null,
  }});
}} catch (error) {{
  return [{{ json: {{ allowed: false, blocked_reason: error.message, role, chatId, received }} }}];
}}

return [{{ json: {{
  allowed: true,
  role,
  chatId,
  // Cravado logo antes da chamada HTTP: o que fica entre este no e o proximo
  // e o tempo do provedor, que e onde praticamente todo o relogio do fluxo
  // esta. Sem isto, medir uma etapa exige abrir o SQLite do n8n.
  started_at: Date.now(),
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
    chat_id: prepared.chatId,
    duration_ms: Date.now() - (prepared.started_at || Date.now()),
    chatId: prepared.chatId,
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
  chat_id: prepared.chatId,
  duration_ms: Date.now() - (prepared.started_at || Date.now()),
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
  chat_id: blocked.chatId || null,
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
                    "(role, chat_id, provider, model, input_tokens, output_tokens, cached_tokens, "
                    "cost_usd, duration_ms, error) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10);"
                ),
                "options": {
                    "queryReplacement": (
                        "={{ [$json.role, $json.chat_id, $json.provider, $json.model, $json.usage.input,"
                    " $json.usage.output, $json.usage.cached, $json.cost_usd,"
                    " $json.duration_ms, $json.error] }}"
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
        # As duas fases, na ordem em que a conversa as executa. O smoke pula a
        # pergunta de lote de proposito: ele mede a extracao, e o edital de
        # exemplo tem um imovel so.
        node("Chamar preparacao", "n8n-nodes-base.executeWorkflow", 1.3, [600, 0], {
            "workflowId": {"__rl": True, "value": PREPARE_ID, "mode": "id"},
            "options": {"waitForSubWorkflow": True},
        }),
        node("Escolher o unico lote", "n8n-nodes-base.code", 2, [800, 0], {"jsCode": """
const prepared = $input.first().json;
const lots = prepared.lots || [];
return [{ json: {
  chat_id: prepared.chat_id,
  file_name: prepared.file_name,
  sha256: prepared.sha256,
  markdown: prepared.markdown,
  deterministic: prepared.deterministic,
  lot: lots.length > 1 ? lots[0] : null,
} }];
"""}),
        node("Chamar ingestao", "n8n-nodes-base.executeWorkflow", 1.3, [1000, 0], {
            "workflowId": {"__rl": True, "value": INGEST_ID, "mode": "id"},
            "options": {"waitForSubWorkflow": True},
        }),
    ]
    connections = {
        "Disparo manual": {"main": [[{"node": "Ler edital do disco", "type": "main", "index": 0}]]},
        "Ler edital do disco": {"main": [[{"node": "Identificar o chat", "type": "main", "index": 0}]]},
        "Identificar o chat": {"main": [[{"node": "Chamar preparacao", "type": "main", "index": 0}]]},
        "Chamar preparacao": {"main": [[{"node": "Escolher o unico lote", "type": "main", "index": 0}]]},
        "Escolher o unico lote": {"main": [[{"node": "Chamar ingestao", "type": "main", "index": 0}]]},
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

INGEST_PROGRESS = """
// Segundo aviso, disparado quando o PDF ja virou texto e a analise vai comecar.
//
// Nao e um cronometro: um no Wait suspenderia a execucao inteira, inclusive a
// propria extracao, entao "avise se passar de 1 minuto" nao existe dentro de
// uma execucao do n8n. O que existe e este marco — a conversao termina em
// segundos e o que vem depois leva minutos —, e nele da para dizer algo mais
// util que "aguarde": quantas paginas o documento tem e quanto falta.
const conversion = $input.first().json;
const trigger = $('Chamada de outro fluxo').first().json;
const chatId = String(trigger.chat_id || '');

// Chat de teste nao existe no Telegram; devolver lista vazia encerra este ramo
// sem erro, e o smoke test continua rodando sem falar com a rede.
if (!/^-?[0-9]+$/.test(chatId)) return [];

const pages = conversion.pages;
const NL = String.fromCharCode(10);
return [{ json: { chat_id: chatId, text:
  'Documento lido' + (pages ? ' — ' + pages + (pages === 1 ? ' página' : ' páginas') : '') +
  '.' + NL + NL +
  'Agora estou extraindo prazos, valores, ônus e débitos, e conferindo cada um ' +
  'contra o texto. Essa parte leva cerca de dois minutos.' } }];
"""

INGEST_PREPARE_PROMPT = """
// Monta a chamada de extração. O prompt e o schema vêm de arquivos montados no
// container, e não embutidos aqui: assim continuam revisáveis em diff e o
// mesmo texto que o repositório versiona é o que o modelo recebe.
// A conversao e a extracao deterministica acontecem em `04-edital-preparar`,
// antes da pergunta sobre qual imovel analisar. Aqui o documento ja chega em
// texto: a conversao foi paga uma vez e nao se repete se a pessoa demorar a
// responder.
const trigger = $('Chamada de outro fluxo').first().json;
const markdown = trigger.markdown || '';
const deterministic = trigger.deterministic || {};
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
  // Sem isto o gasto da extracao — que e todo o gasto do projeto — fica sem
  // dono em `llm_calls`, e a view `usage_by_chat` mostra so zeros.
  chatId: trigger.chat_id,
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
  messages: [{ role: 'user', content: [
    // Quando o edital cobre varios imoveis, o documento inteiro vai junto — a
    // parte comum (datas, regras, comissao) vale para todos os lotes e fatiar
    // o texto deixaria a ficha sem prazos. O que muda e a instrucao.
    trigger.lot
      ? 'ESTE EDITAL COBRE VARIOS IMOVEIS. Descreva SOMENTE o imovel de ' +
        'matricula ' + trigger.lot.registry + '. Ignore os demais; as regras ' +
        'de leilao, prazos e pagamento valem para todos e devem ser extraidas.'
      : '',
    'EXTRACAO DETERMINISTICA (confira contra o texto):',
    hint,
    '',
    'EDITAL:',
    markdown,
  ].filter(Boolean).join(String.fromCharCode(10)) }],
  // Carregado adiante, na persistência.
  _chat_id: trigger.chat_id,
  _file_name: trigger.file_name,
  _markdown: markdown,
  _sha256: trigger.sha256,
  _lot: trigger.lot || null,
  _deterministic: deterministic,
} }];
"""

INGEST_CASE_REQUEST = """
// Monta a consulta processual a partir do bloco deterministico.
//
// O numero vem do extrator, e nao da ficha do modelo, por dois motivos: ele ja
// validou o digito verificador, e ja distinguiu o processo do leilao dos
// precedentes citados no juridiques. Consultar um precedente injetaria na
// ficha a situacao de uma causa alheia ao imovel.
let result;
try {
  result = $('Conferir correcao').first().json;
} catch (e) {
  result = $('Conferir resultado').first().json;
}

const main = (result.deterministic || {}).court_case || null;
const number = main && main.valid ? main.number : null;
const alias = main ? main.datajud_alias : null;

// Leilao extrajudicial nao tem processo, e tribunal fora da cobertura do
// DataJud nao tem alias. Nos dois casos o certo e nao consultar, e nao e uma
// falha — a ficha segue sem situacao processual.
return [{ json: {
  should_lookup: Boolean(number && alias),
  cnj_number: number,
  datajud_alias: alias,
} }];
"""

INGEST_REPAIR_PROMPT = """
// Pede a correção dos erros de validação, em vez de jogar a ficha fora.
//
// Sem isto, uma ficha reprovada por formato — trecho acima de 600 caracteres,
// campo nulo onde o schema quer objeto — custa os tokens e nao entrega nada. O
// erro observado em producao e desse tipo: o modelo entendeu o edital e
// escreveu a citacao longa demais.
//
// Mandamos a ficha inteira de volta com os erros apontados e pedimos o
// documento corrigido. Um patch seria mais barato e muito mais fragil: erra o
// caminho, aplica no lugar errado, e o defeito fica invisivel.
const prepared = $('Preparar extracao').first().json;
const gateway = $('Extrair ficha').first().json;
const check = $('Validar ficha').first().json;

const errors = (check.errors || [])
  .map((e) => '- ' + e.path + ': ' + e.message)
  .join(String.fromCharCode(10));

const NL = String.fromCharCode(10);
const instruction = [
  'A ficha abaixo foi reprovada pela validacao do schema. Corrija APENAS os',
  'erros listados e devolva o documento JSON inteiro, sem comentarios.',
  '',
  '# ERROS',
  errors,
  '',
  '# COMO CORRIGIR OS CASOS MAIS COMUNS',
  '- trecho longo demais: encurte a citacao mantendo o sentido, e use [...]',
  '  para juntar dois pontos distantes em vez de copiar o texto do meio;',
  '- campo nulo onde o schema pede objeto: use o objeto com os campos',
  '  internos nulos, ou remova o campo se ele for opcional;',
  '- valor fora do enum: escolha um dos valores permitidos pelo schema.',
  '',
  'Nao reescreva o que esta correto. Nao invente informacao nova: se um trecho',
  'precisa encurtar, corte-o, nao o reformule com outras palavras.',
  '',
  '# FICHA A CORRIGIR',
  JSON.stringify(gateway.parsed, null, 2),
].join(NL);

return [{ json: {
  role: 'extraction',
  chatId: prepared._chat_id,
  system: prepared.system,
  schema: prepared.schema,
  structuredMode: prepared.structuredMode,
  maxTokens: prepared.maxTokens,
  cacheSystem: true,
  expectJson: true,
  messages: [{ role: 'user', content: instruction }],
} }];
"""

INGEST_CHECK_REPAIR = """
// Mesma conferencia de `Conferir resultado`, agora sobre a ficha corrigida.
// Se ainda houver erro, o relato menciona a tentativa — "invalida" e uma
// informacao diferente de "invalida mesmo depois de corrigir".
const prepared = $('Preparar extracao').first().json;
const first = $('Extrair ficha').first().json;
const gateway = $('Corrigir ficha').first().json;
const check = $('Validar correcao').first().json;

const problems = [];
if (gateway.blocked) problems.push('gateway bloqueou na correcao: ' + gateway.reason);
if (gateway.parse_failed) problems.push('correcao nao era JSON');
if (check && check.valid === false) {
  problems.push('ficha invalida mesmo apos correcao (' + check.error_count + ' erro(s)): ' +
    (check.errors || []).slice(0, 3).map((e) => e.path + ': ' + e.message).join(' | '));
}

const firstCost = first.cost_usd || 0;
const repairCost = gateway.cost_usd || 0;

return [{ json: {
  ok: problems.length === 0,
  problems,
  repaired: problems.length === 0,
  chat_id: prepared._chat_id,
  file_name: prepared._file_name,
  sha256: prepared._sha256,
  markdown: prepared._markdown,
  deterministic: prepared._deterministic,
  analysis: gateway.parsed || null,
  usage: gateway.usage || null,
  // Custo total do edital: a tentativa que falhou tambem foi paga.
  cost_usd: Math.round((firstCost + repairCost) * 1e6) / 1e6,
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
        node("Preparar consulta processual", "n8n-nodes-base.code", 2, [2040, -110],
             {"jsCode": INGEST_CASE_REQUEST}),
        node("Tem processo judicial?", "n8n-nodes-base.if", 2.2, [2240, -110], {
            "conditions": {
                "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
                "conditions": [{"id": "lookup",
                                "operator": {"type": "boolean", "operation": "true",
                                             "singleValue": True},
                                "leftValue": "={{ $json.should_lookup }}"}],
                "combinator": "and"}}),
        # DataJud indisponivel nao pode derrubar a ingestao: a ficha ja esta
        # gravada e vale sem a situacao processual. Sem isto, uma falha na API
        # do CNJ faria a pessoa perder o edital inteiro.
        node("Consultar processo", "n8n-nodes-base.executeWorkflow", 1.3, [2440, -210], {
            "workflowId": {"__rl": True, "value": LOOKUP_ID, "mode": "id"},
            "options": {"waitForSubWorkflow": True},
        }, onError="continueRegularOutput"),
        # Cache da consulta. O chat le desta tabela para dizer se o processo
        # registra algo que ameace a arrematacao; sem esta gravacao ela fica
        # vazia e o destaque nunca aparece — que era exatamente o estado
        # anterior a esta mudanca.
        node("Registrar consulta", "n8n-nodes-base.postgres", 2.7, [2640, -210], {
            "operation": "executeQuery",
            "query": ("INSERT INTO case_lookups "
                      "(cnj_number, court_alias, status, movements, summary, fetched_at) "
                      "VALUES ($1, $2, $3, $4, $5, now()) "
                      "ON CONFLICT (cnj_number) DO UPDATE SET "
                      "court_alias = EXCLUDED.court_alias, status = EXCLUDED.status, "
                      "movements = EXCLUDED.movements, summary = EXCLUDED.summary, "
                      "fetched_at = now();"),
            "options": {"queryReplacement":
                        "={{ [$json.cnj_number, $json.court_alias, $json.status,"
                        " JSON.stringify($json.analysis), JSON.stringify($json.summary)] }}"},
        }, credentials={"postgres": {"id": "leilao-postgres", "name": "Postgres do projeto"}},
           alwaysOutputData=True, onError="continueRegularOutput"),
        node("Resposta de sucesso", "n8n-nodes-base.code", 2, [2860, -110], {"jsCode": """
// A ficha ja foi gravada; o id vem do no que a gravou, e nao de `$input`, que
// aqui pode chegar de tres lugares diferentes conforme houve correcao e houve
// consulta processual.
const saved = $('Persistir ficha').first().json;

// `Persistir ficha` recebe de dois caminhos: a extracao que passou de primeira
// e a que passou depois de corrigida. Referenciar o no errado devolveria a
// ficha reprovada. O try existe porque `$()` num no que nao executou lanca.
let result;
let repaired = false;
try {
  result = $('Conferir correcao').first().json;
  repaired = true;
} catch (e) {
  result = $('Conferir resultado').first().json;
}

return [{ json: {
  ok: true,
  notice_id: saved.id,
  // Consulta processual: `null` quando o leilao e extrajudicial ou o tribunal
  // esta fora da cobertura do DataJud.
  court_case_status: (() => {
    try { return $('Consultar processo').first().json.status; } catch (e) { return null; }
  })(),
  chat_id: result.chat_id,
  analysis: result.analysis,
  usage: result.usage,
  // Quando houve correcao, o custo ja soma as duas chamadas.
  cost_usd: result.cost_usd,
  repaired,
} }];
"""}),
        node("Preparar correcao", "n8n-nodes-base.code", 2, [1700, 240],
             {"jsCode": INGEST_REPAIR_PROMPT}),
        node("Corrigir ficha", "n8n-nodes-base.executeWorkflow", 1.3, [1900, 240], {
            "workflowId": {"__rl": True, "value": GATEWAY_ID, "mode": "id"},
            "options": {"waitForSubWorkflow": True},
        }),
        node("Validar correcao", "n8n-nodes-base.httpRequest", 4.2, [2100, 240], {
            "method": "POST", "url": "={{ $env.DOCLING_URL }}/validate",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ document: $json.parsed }) }}",
            "options": {"timeout": 60000}}),
        node("Conferir correcao", "n8n-nodes-base.code", 2, [2300, 240],
             {"jsCode": INGEST_CHECK_REPAIR}),
        node("Correcao valida?", "n8n-nodes-base.if", 2.2, [2500, 240], {
            "conditions": {
                "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
                "conditions": [{"id": "repaired",
                                "operator": {"type": "boolean", "operation": "true",
                                             "singleValue": True},
                                "leftValue": "={{ $json.ok }}"}],
                "combinator": "and"}}),
        node("Resposta de falha", "n8n-nodes-base.code", 2, [1840, 110], {"jsCode": """
// Falha explícita e com causa. Persistir ficha parcial seria pior: toda
// pergunta seguinte responderia com base em dado que ninguém conferiu.
const r = $input.first().json;
return [{ json: { ok: false, problems: r.problems, chat_id: r.chat_id, file_name: r.file_name } }];
"""}),
    ]

    connections = {
        "Chamada de outro fluxo": {"main": [[
            {"node": "Buscar prompt de analista", "type": "main", "index": 0}]]},
        "Buscar prompt de analista": {"main": [[{"node": "Buscar schema da ficha", "type": "main", "index": 0}]]},
        "Buscar schema da ficha": {"main": [[{"node": "Preparar extracao", "type": "main", "index": 0}]]},
        "Preparar extracao": {"main": [[{"node": "Extrair ficha", "type": "main", "index": 0}]]},
        "Extrair ficha": {"main": [[{"node": "Validar ficha", "type": "main", "index": 0}]]},
        "Validar ficha": {"main": [[{"node": "Conferir resultado", "type": "main", "index": 0}]]},
        "Conferir resultado": {"main": [[{"node": "Ficha valida?", "type": "main", "index": 0}]]},
        # Ficha reprovada nao vai direto para a falha: vale uma tentativa de
        # correcao com os erros apontados. Os erros observados sao de formato,
        # nao de compreensao, e descartar uma extracao ja paga por causa de uma
        # citacao longa demais e desperdicio.
        "Ficha valida?": {"main": [
            [{"node": "Persistir ficha", "type": "main", "index": 0}],
            [{"node": "Preparar correcao", "type": "main", "index": 0}],
        ]},
        "Preparar correcao": {"main": [[{"node": "Corrigir ficha", "type": "main", "index": 0}]]},
        "Corrigir ficha": {"main": [[{"node": "Validar correcao", "type": "main", "index": 0}]]},
        "Validar correcao": {"main": [[{"node": "Conferir correcao", "type": "main", "index": 0}]]},
        "Conferir correcao": {"main": [[{"node": "Correcao valida?", "type": "main", "index": 0}]]},
        # Uma tentativa so. Duas falhas seguidas no mesmo documento indicam
        # problema no edital ou no schema, e nao algo que insistir resolva.
        "Correcao valida?": {"main": [
            [{"node": "Persistir ficha", "type": "main", "index": 0}],
            [{"node": "Resposta de falha", "type": "main", "index": 0}],
        ]},
        "Persistir ficha": {"main": [[
            {"node": "Preparar consulta processual", "type": "main", "index": 0}]]},
        "Preparar consulta processual": {"main": [[
            {"node": "Tem processo judicial?", "type": "main", "index": 0}]]},
        # Os dois ramos terminam na mesma resposta: nao ter processo nao e
        # falha, e a ficha vale igual.
        "Tem processo judicial?": {"main": [
            [{"node": "Consultar processo", "type": "main", "index": 0}],
            [{"node": "Resposta de sucesso", "type": "main", "index": 0}],
        ]},
        "Consultar processo": {"main": [[{"node": "Registrar consulta", "type": "main", "index": 0}]]},
        "Registrar consulta": {"main": [[{"node": "Resposta de sucesso", "type": "main", "index": 0}]]},
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


# ─── 01 — Chat no Telegram (Estágio 3) ──────────────────────────────────────

CHAT_ID_WF = "telegramchat0001"
CHAT_OUTPUT = ROOT / "workflows" / "01-telegram-chat.json"

TELEGRAM_CRED = {"telegramApi": {"id": "leilao-telegram", "name": "Bot do Telegram"}}
POSTGRES_CRED = {"postgres": {"id": "leilao-postgres", "name": "Postgres do projeto"}}

CHAT_ROUTE = """
// Classifica a mensagem em um de cinco caminhos. A classificacao e
// deterministica de proposito: gastar uma chamada de LLM para descobrir que a
// pessoa mandou `/ajuda` seria desperdicio, e comando tem de responder sempre.
return $input.all().map((entry) => {
const message = entry.json.message || {};
const chatId = String((message.chat || {}).id || '');
const text = (message.text || message.caption || '').trim();
const document = message.document || null;

const isPdf = Boolean(document) && (
  (document.mime_type || '').includes('pdf') ||
  /\\.pdf$/i.test(document.file_name || '')
);

// O que a pessoa mandou, quando nao foi PDF nem texto. Nomear o anexo torna a
// recusa util: "envie como documento, nao como foto" so ajuda quem mandou
// foto, e o Telegram tem meia duzia de outros tipos.
let attachment = null;
if ((message.photo || []).length) attachment = 'foto';
else if (message.video || message.video_note) attachment = 'video';
else if (message.voice || message.audio) attachment = 'audio';
else if (message.sticker) attachment = 'figurinha';
else if (message.location) attachment = 'localizacao';
else if (message.contact) attachment = 'contato';
else if (document) attachment = 'arquivo';

let route = 'question';
if (isPdf) route = 'document';
else if (attachment) route = 'unsupported_file';
else if (/^\\/(start|ajuda|help)\\b/i.test(text)) route = 'help';
else if (/^\\/apagar\\b/i.test(text)) route = 'forget';
else if (!text) route = 'unsupported_file';

// O binario segue junto: e o PDF que a ingestao manda para o Docling. Um Code
// node que devolve so `json` descarta o anexo silenciosamente.
return {
  json: {
    route,
    chat_id: chatId,
    text,
    file_name: isPdf ? (document.file_name || 'edital.pdf') : null,
    attachment,
    attachment_name: document ? (document.file_name || null) : null,
  },
  binary: entry.binary,
};
});
"""


CHAT_FORMAT_HELPERS = r"""
// Formatacao das mensagens em HTML, e nao no Markdown legado do Telegram.
//
// O Markdown legado quebra com um unico caractere desemparelhado, e a mensagem
// inteira e recusada com 'Bad request' — a resposta some sem chegar a pessoa e
// sem erro visivel na conversa. Aconteceu de verdade: a ficha trazia o campo
// `debts.enforced_claim` na secao de lacunas, e aquele underscore solto
// derrubou o envio. Como os nomes de campo do schema sao snake_case, a falha
// era sistematica: quase toda ficha com lacunas quebraria.
//
// Em HTML o escape resolve na origem. Escapamos tudo o que e dinamico e so
// depois envolvemos com as tags que nos mesmos geramos, entao nao ha como
// produzir marcacao desbalanceada.

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function bold(value) { return '<b>' + esc(value) + '</b>'; }

// O modelo responde em Markdown por habito, e em HTML isso apareceria literal
// como `**negrito**`. Convertemos os poucos casos que ele usa, sempre DEPOIS
// do escape — os pares abaixo sao gerados aqui e por isso sempre fecham.
function fromMarkdown(text) {
  return esc(text)
    .replace(/^\s*#{1,6}\s*(.+)$/gm,
             (m, title) => '<b>' + title.trim().replace(/\*\*/g, '') + '</b>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<i>$2</i>')
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/^\s*[-*]\s+/gm, '• ');
}

// O Telegram corta em 4096 caracteres. Truncar com aviso e melhor que a
// mensagem sumir; o corte respeita a ultima quebra de linha para nao partir
// uma tag ao meio.
function fit(text) {
  if (text.length <= 3900) return text;
  const cut = text.slice(0, 3900);
  const at = cut.lastIndexOf(String.fromCharCode(10));
  return (at > 3000 ? cut.slice(0, at) : cut) +
         String.fromCharCode(10, 10) + '<i>(resposta truncada)</i>';
}
"""

CHAT_SCOPE_REQUEST = """
// Manda ao servico so as mensagens que sao pergunta; comando e documento nao
// passam por escopo.
const questions = $input.all()
  .filter((i) => i.json.route === 'question')
  .map((i) => i.json.text);
return [{ json: { texts: questions } }];
"""

CHAT_SCOPE_APPLY = """
// Reescreve a rota das perguntas que pedem justamente o que o assistente nao
// faz. A decisao e deterministica e vem antes do modelo: o limite prometido
// pelo produto nao pode depender de o modelo obedecer ao prompt — e, testado,
// ele nao obedeceu.
const verdicts = $input.first().json.results || [];
let index = 0;

return $('Rotear mensagem').all().map((entry) => {
  if (entry.json.route !== 'question') return entry;
  const verdict = verdicts[index++] || { in_scope: true };
  if (verdict.in_scope) return entry;
  return {
    json: { ...entry.json, route: 'out_of_scope', refusal: verdict.reply,
            refusal_kind: verdict.kind },
    binary: entry.binary,
  };
});
"""

CHAT_REFUSAL = CHAT_FORMAT_HELPERS + """
// A recusa vem de scope.py, e texto nosso e nao tem marcacao — so escapamos.
return $input.all().map((entry) => ({
  json: { chat_id: entry.json.chat_id, text: esc(entry.json.refusal) },
}));
"""

CHAT_RESOLVE_PENDING = r"""
// Reconhece a resposta a "qual imovel?" como rota propria.
//
// O roteador anterior nao tem como saber disso: ele so ve o texto, e "2" e uma
// mensagem valida em qualquer contexto. E a existencia de um edital pendente
// nesta conversa que transforma a mensagem numa escolha.
const pending = $input.all().map((i) => i.json).filter((r) => r && r.lots);
const waiting = pending.length ? pending[0] : null;

// Com um edital pendente, nem toda mensagem e escolha de lote. Sem esta
// distincao, "Esse imovel esta ocupado?" viraria uma escolha invalida e a
// pessoa receberia "nao entendi qual imovel" no lugar da resposta.
//
// Pergunta se reconhece por forma, nao por conteudo: termina em interrogacao
// ou comeca com palavra interrogativa. O resto — "2", "sim", "a 81.909" — e
// escolha, que e o que se espera logo depois da pergunta.
const QUESTION = /\?\s*$|^\s*(qual|quais|quanto|quantos|quando|como|onde|quem|por que|porque|o que|oq|existe|tem |ha )/i;

return $('Aplicar escopo').all().map((entry) => {
  if (!waiting) return entry;
  if (entry.json.route !== 'question' && entry.json.route !== 'out_of_scope') {
    return entry;
  }
  if (QUESTION.test(entry.json.text || '')) return entry;
  return { json: { ...entry.json, route: 'lot_choice', pending: waiting },
           binary: entry.binary };
});
"""

CHAT_ASK_LOT = CHAT_FORMAT_HELPERS + """
// A pergunta que evita analisar o imovel errado.
//
// Confirmar tambem quando ha um imovel so e decisao de produto: alem de
// validar que e o bem certo, protege do caso em que a deteccao erra e existe
// um segundo lote que nao foi visto.
// Duas origens: a preparacao tem o edital e os lotes; o servico tem as
// descricoes prontas. Ler tudo de `$input` pegaria so a segunda, que nao traz
// chat_id nem nome de arquivo — foi o que fez a primeira versao dizer "nao
// consegui identificar a matricula" para um edital que tinha uma.
const prepared = $('Chamar preparacao').first().json;

// Documento recusado na conferencia: a mensagem ja vem pronta do servico, que
// e quem sabe por que recusou.
if (prepared.rejected) {
  return [{ json: { chat_id: prepared.chat_id, text: esc(prepared.message) } }];
}

const described = $input.first().json;
const options = described.options || [];
const lots = prepared.lots || [];
const NL = String.fromCharCode(10);
const linhas = [];

if (lots.length > 1) {
  linhas.push(bold('Este edital cobre ' + lots.length + ' imóveis.') +
              ' Qual deles você quer analisar?', '');
  options.forEach((o, i) => linhas.push((i + 1) + '. ' + esc(o)));
  linhas.push('', 'Responda com o número ou com a matrícula.');
} else if (lots.length === 1) {
  linhas.push(bold('Encontrei este imóvel no edital:'), '',
              esc(options[0] || 'imovel'), '',
              'É esse que você quer analisar? Responda ' + bold('sim') +
              ' para eu montar a ficha.');
} else {
  // Sem matricula legivel nao da para listar, mas da para seguir: a extracao
  // funciona igual, so nao ha o que confirmar.
  linhas.push(bold('Recebi o edital') + ' — ' + esc(prepared.file_name || 'documento') +
              '.', '', 'Não consegui identificar a matrícula do imóvel no texto. ' +
              'Responda ' + bold('sim') + ' para eu analisar o documento assim mesmo.');
}

return [{ json: { chat_id: prepared.chat_id, text: linhas.join(NL) } }];
"""

CHAT_LOT_DECISION = CHAT_FORMAT_HELPERS + """
// Traduz a leitura da escolha em "extrai" ou "pergunta de novo".
const verdict = $input.first().json;
const routed = $('Resolver pendente').all()
  .filter((i) => i.json.route === 'lot_choice')[0].json;
const pending = routed.pending;
const NL = String.fromCharCode(10);

if (!verdict.understood) {
  const opcoes = (verdict.options || [])
    .map((o, i) => (i + 1) + '. ' + esc(o)).join(NL);
  const pedido = (pending.lots || []).length > 1
    ? 'Não entendi qual imóvel você quer.' + NL + NL + opcoes + NL + NL +
      'Responda com o número ou com a matrícula.'
    : 'Responda ' + bold('sim') + ' para eu analisar este imóvel.';
  return [{ json: { proceed: false, chat_id: routed.chat_id, text: pedido } }];
}

// Segue para a extracao com tudo que a preparacao ja produziu: o Markdown nao
// e reconvertido e a pessoa nao reenvia o PDF.
return [{ json: {
  proceed: true,
  chat_id: routed.chat_id,
  // Como o imovel foi descrito na pergunta. Repetir isso no aviso deixa a
  // pessoa conferir que a escolha foi entendida — dois minutos depois seria
  // tarde para descobrir que o bot leu outro numero.
  lot_label: (verdict.options || [])[verdict.index - 1] || null,
  file_name: pending.file_name,
  sha256: pending.sha256,
  markdown: pending.markdown,
  deterministic: pending.deterministic,
  // `lot` so quando ha o que desambiguar: com um imovel so, instruir o modelo a
  // ignorar os demais seria instrucao sobre algo que nao existe.
  lot: (pending.lots || []).length > 1 ? verdict.lot : null,
} }];
"""

CHAT_CHOICE_ACK = CHAT_FORMAT_HELPERS + """
// Confirma o que foi entendido e diz quanto tempo leva.
//
// Entre o "sim" e a ficha ha cerca de dois minutos de silencio. E o mesmo
// buraco do envio do PDF, agora no segundo turno: sem retorno, a pessoa nao
// sabe se a resposta dela foi lida.
const decision = $input.first().json;
const NL = String.fromCharCode(10);

const alvo = decision.lot_label
  ? 'Vou analisar ' + bold(esc(decision.lot_label)) + '.'
  : 'Vou analisar este edital.';

return [{ json: { chat_id: decision.chat_id, text: [
  'Entendido. ' + alvo,
  '',
  'Estou lendo o documento inteiro e montando a ficha com prazos, valores,',
  'ônus, débitos e o que o edital não informa. Leva cerca de dois minutos —',
  'aviso aqui quando terminar.',
].join(NL) } }];
"""

CHAT_QUESTION_ACK = CHAT_FORMAT_HELPERS + """
// Aviso imediato de que a pergunta chegou.
//
// A resposta leva de quinze a trinta segundos no modelo local, e nesse
// intervalo a conversa fica parada sem sinal nenhum. O mesmo problema do PDF,
// em escala menor: sem retorno, a pessoa nao distingue "pensando" de "quebrou"
// e reformula a pergunta, o que gera duas chamadas para uma duvida.
//
// So o caminho de pergunta passa por aqui. Recusa fora de escopo e comando sao
// instantaneos, e avisar antes deles seria ruido.
const asked = $input.all();
if (!asked.length) return [];

const chatId = asked[0].json.chat_id;
return [{ json: { chat_id: chatId, text:
  asked.length > 1
    ? 'Recebi ' + asked.length + ' perguntas. Conferindo na ficha…'
    : 'Deixa eu conferir na ficha do edital…' } }];
"""

CHAT_HELP = CHAT_FORMAT_HELPERS + """
const chatId = $('Aplicar escopo').all()
  .filter((i) => i.json.route === 'help')[0].json.chat_id;

// O bloco de transparencia nao e cortesia: a pessoa precisa saber que fala com
// um sistema automatizado, o que acontece com o PDF que ela enviar, e como
// apagar. Esta em docs/privacy.md e aparece no primeiro contato.
const text = [
  bold('Arremata AI') + ' — assistente para editais de leilão de imóvel.',
  '',
  'Sou um sistema automatizado, ' + bold('não sou advogado') + ' e não substituo análise',
  'jurídica. Explico termos, prazos e riscos do edital, e mostro de onde',
  'tirei cada resposta.',
  '',
  bold('Como usar'),
  'Envie o PDF do edital e eu monto uma ficha com prazos, valores, ônus,',
  'débitos e o que o documento <i>não</i> informa. Depois pergunte o que quiser',
  'sobre ele, em português normal. Sem edital carregado, respondo dúvidas',
  'gerais de leilão.',
  '',
  bold('O que faço com seus dados'),
  'Guardo o texto do edital e a ficha para responder suas perguntas. A',
  'extração usa um modelo de terceiro. CPF que apareça no documento é',
  'mascarado antes de qualquer coisa ser gravada.',
  'Use /apagar para remover tudo desta conversa.',
  '',
  bold('O que não faço'),
  'Não digo se vale a pena arrematar, não estimo valor de mercado e não dou',
  'orientação jurídica.',
].join(String.fromCharCode(10));

return [{ json: { chat_id: chatId, text } }];
"""

CHAT_CONTEXT = """
// Contexto do Estagio 3: a ficha, nao o edital inteiro. E isso que mantem a
// pergunta barata — ~3 mil tokens em vez de ~12 mil.
// A consulta devolve no maximo uma ficha (LIMIT 1), e ela vale para todas as
// perguntas deste chat.
const rows = $input.all().map((i) => i.json).filter((r) => r && r.analysis);
const notice = rows.length ? rows[0] : null;

// Item por pergunta: cada uma vira uma chamada propria ao modelo.
//
// A selecao e pela rota, e nao pelo indice da saida do Switch: `.all()` num no
// de multiplas saidas devolve a primeira delas, que aqui e o ramo de documento.
// Filtrar pelo campo diz o que se quer e independe da ordem das saidas.
const asked = $('Aplicar escopo').all().filter((i) => i.json.route === 'question');

return asked.map((entry) => ({ json: {
  chat_id: entry.json.chat_id,
  question: entry.json.text,
  has_notice: Boolean(notice),
  file_name: notice ? notice.file_name : null,
  analysis: notice ? notice.analysis : null,
} }));
"""

CHAT_PREPARE = """
const systemPrompt = $('Buscar prompt de Q&A').first().json.text;
const glossary = $('Buscar glossario').first().json.text;

// O bloco de sistema e identico entre perguntas do mesmo chat, entao e montado
// uma vez so e reaproveitado — e o que torna o cache util.
const contexts = $('Montar contexto').all().map((i) => i.json);
const rendered = $('Ficha em portugues').first().json;
if (contexts.length) {
  contexts[0].ficha_text = rendered.ficha_text;
  contexts[0].case_text = rendered.case_text;
}

const parts = [systemPrompt, '', '# GLOSSARIO', glossary];
if (contexts.length && contexts[0].has_notice) {
  // A ficha em portugues, e nao o JSON do schema. Com as chaves em ingles o
  // modelo as repetia na resposta ("extinguished_by_sale: true") e chegou a
  // inventar traducao — "a penhora (gravida do processo)", palavra que nao
  // existe em edital nenhum. Sem ingles a frente, nao ha o que improvisar.
  parts.push('', '# FICHA DO EDITAL CARREGADO', contexts[0].ficha_text);
  // A consulta processual e fonte propria, e nao parte do edital: separa-la
  // deixa claro de onde veio cada afirmacao quando a resposta cita as duas.
  if (contexts[0].case_text) {
    parts.push('', '# SITUACAO PROCESSUAL (consulta ao DataJud)',
               contexts[0].case_text);
  }
} else {
  parts.push('', '# SEM EDITAL CARREGADO',
    'A pessoa ainda nao enviou nenhum edital. Responda duvidas conceituais pelo',
    'glossario. Se a pergunta depender de um edital especifico, peca o PDF.');
}

const system = parts.join(String.fromCharCode(10));
return contexts.map((context) => ({ json: {
  role: 'qa',
  chatId: context.chat_id,
  system,
  cacheSystem: true,
  maxTokens: Number($env.LLM_QA_MAX_TOKENS || 8192),
  messages: [{ role: 'user', content: context.question }],
} }));
"""

CHAT_ANSWER = CHAT_FORMAT_HELPERS + """
// Pareado com `Montar contexto`, que esta no mesmo ramo e tem os mesmos itens
// na mesma ordem. Referenciar o roteador seria errado: `.first()` la traria o
// primeiro item de todos os ramos, nao o desta pergunta.
const contexts = $('Montar contexto').all().map((i) => i.json);

return $input.all().map((item, index) => {
  const gateway = item.json;
  let text;
  if (gateway.blocked) {
    text = esc('Não consegui responder agora: ' + (gateway.reason || 'limite de uso atingido') +
               '. Tente de novo mais tarde.');
  } else if (!gateway.text) {
    text = 'Não consegui formular uma resposta para isso. Pode reformular a pergunta?';
  } else {
    // O modelo escreve em Markdown; sem converter, apareceria `**assim**`.
    text = fromMarkdown(gateway.text);
  }

  return { json: { chat_id: (contexts[index] || contexts[0]).chat_id, text: fit(text) } };
});
"""

CHAT_ACK = CHAT_FORMAT_HELPERS + """
// Aviso imediato de que o edital chegou.
//
// A leitura do PDF, a extracao e a validacao levam alguns minutos. Sem isto a
// pessoa manda o arquivo e nao recebe nada — nao da para distinguir "esta
// processando" de "o bot morreu", e a reacao natural e reenviar o edital, o
// que dobra o custo da extracao.
const routed = $input.first().json;

return [{ json: { chat_id: routed.chat_id, text: [
  'Recebi o edital' + (routed.file_name ? ' — ' + esc(routed.file_name) : '') + '.',
  '',
  'Estou lendo o documento e montando a ficha. Costuma levar de dois a cinco',
  'minutos, dependendo do tamanho. Aviso aqui quando terminar; não precisa',
  'reenviar.',
].join(String.fromCharCode(10)) } }];
"""

CHAT_INGEST_REPLY = CHAT_FORMAT_HELPERS + """
const result = $('Chamar ingestao').first().json;
// Riscos, lacunas e fatos favoraveis chegam prontos do servico: rotulo em
// portugues, risco generico ja despriorizado e destaque ja derivado. Aqui so
// se formata.
const view = $input.first().json;
// O nome do arquivo vem de `Decidir escolha`, e nao do roteador.
//
// Desde que a confirmacao de lote existe, a ficha e montada no turno em que a
// pessoa responde "sim" — e nesse turno nao ha documento nenhum na conversa.
// Filtrar por `route === 'document'` devolvia lista vazia e o `[0].json`
// derrubava a execucao depois de a extracao ja ter sido paga.
const routed = $('Decidir escolha').first().json;
const NL = String.fromCharCode(10);

if (!result.ok) {
  const problems = (result.problems || []).join('; ');
  return [{ json: { chat_id: routed.chat_id, text:
    'Não consegui analisar este edital.' + NL + NL + esc(problems || 'causa desconhecida') +
    NL + NL + 'Se o PDF for digitalizado, a leitura pode falhar. Tente outro arquivo.' } }];
}

const a = result.analysis || {};
const brl = (n) => (n == null ? null
  : n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }));
const line = (label, value) => (value ? bold(label + ':') + ' ' + esc(value) : null);

// Data e hora da praça no formato de quem lê, com o lance mínimo quando o
// edital o traz — é a informação que decide se dá tempo de participar.
const quando = (iso) => {
  if (!iso) return null;
  const [data, hora] = String(iso).split('T');
  const [ano, mes, dia] = (data || '').split('-');
  if (!dia) return null;
  const hm = (hora || '').slice(0, 5);
  return dia + '/' + mes + '/' + ano + (hm ? ' às ' + hm : '');
};

const praca = (round) => {
  if (!round) return null;
  const inicio = quando(round.starts_at);
  const fim = quando(round.ends_at);
  const minimo = ((round.minimum_bid || {}).amount_brl);
  const periodo = inicio && fim ? inicio + ' até ' + fim : (inicio || fim);
  if (!periodo) return null;
  return periodo + (minimo ? ' — lance mínimo ' + brl(minimo) : '');
};

const occupancy = {
  occupied: 'ocupado', vacant: 'desocupado', not_informed: 'NÃO INFORMADO no edital',
}[((a.occupancy || {}).status || {}).value || (a.occupancy || {}).status] || null;

const appraisal = a.appraisal || {};

const out = [];
// O aviso de multiplos imoveis vem antes de tudo: ele muda o sentido de tudo
// que vem depois, porque a ficha descreve um lote e nao o edital.
if (view.warning) {
  out.push('⚠️ ' + bold(esc(view.warning)));
  (view.warning_lots || []).forEach((l, i) => out.push((i + 1) + '. ' + esc(l)));
  out.push('');
}
out.push(
  bold('Ficha do edital') + ' — ' + esc(routed.file_name || 'documento'),
  '',
  line('Imóvel', ((a.property || {}).type || {}).value),
  line('Matrícula', ((a.property || {}).registry_number || {}).value),
  line('Avaliação', brl((appraisal.updated_value || {}).amount_brl
       || (appraisal.value || {}).amount_brl)),
  line('Ocupação', occupancy),
  line('Processo', (a.court_case || {}).number),
  line('1ª praça', praca((a.auction || {}).first_round)),
  line('2ª praça', praca((a.auction || {}).second_round)),
  '',
);
const filtered = out.filter((l) => l !== null);
out.length = 0;
out.push(...filtered);

// Favoravel antes de risco: quem le tres linhas de alerta e nada em contrario
// conclui que o lote e ruim, mesmo quando o edital nao diz isso. Sao fatos do
// documento, nao recomendacao — dizer se vale a pena continua recusado.
if ((view.highlights || []).length) {
  out.push(bold('A favor'));
  view.highlights.forEach((h) => out.push('• ' + esc(h.text)));
  out.push('');
}
if ((view.risks || []).length) {
  out.push(bold('Pontos de atenção'));
  view.risks.forEach((r) => out.push('• ' + esc(r.description)));
  if (view.risks_generic_hidden) {
    out.push('<i>(' + view.risks_generic_hidden + ' aviso' +
             (view.risks_generic_hidden > 1 ? 's' : '') +
             ' padrão de leilão omitido' + (view.risks_generic_hidden > 1 ? 's' : '') +
             ' — pergunte se quiser ver)</i>');
  }
  out.push('');
}
if ((view.gaps || []).length) {
  out.push(bold('O que o edital NÃO informa'));
  view.gaps.forEach((g) => out.push('• ' + bold(g.label) + ' — ' + esc(g.why_it_matters)));
  out.push('');
}
out.push('Pergunte o que quiser sobre este edital. Não sou advogado e não digo se',
         'vale a pena arrematar.');

return [{ json: { chat_id: routed.chat_id, text: fit(out.join(NL)) } }];
"""

CHAT_FORGET_REPLY = """
const routed = $('Aplicar escopo').all()
  .filter((i) => i.json.route === 'forget')[0].json;
const NL = String.fromCharCode(10);
return [{ json: { chat_id: routed.chat_id, text:
  'Pronto. Apaguei os editais e as fichas desta conversa.' + NL + NL +
  'Envie um novo PDF quando quiser começar de novo.' } }];
"""

CHAT_UNSUPPORTED = CHAT_FORMAT_HELPERS + """
const NL = String.fromCharCode(10);

// Cada tipo tem um conserto diferente, e dizer qual poupa uma tentativa.
const CONSERTO = {
  foto: 'Foto de documento eu não consigo ler. Reenvie o arquivo pelo clipe, ' +
        'escolhendo ' + bold('Arquivo') + ' em vez de ' + bold('Galeria') + '.',
  arquivo: (r) => 'Recebi ' + esc(r.attachment_name || 'um arquivo') +
                  ', que não é PDF. Envie o edital em PDF.',
  video: 'Não leio vídeo.',
  audio: 'Não entendo áudio — sou só texto por enquanto.',
  figurinha: 'Bonita, mas não dá para analisar.',
  localizacao: 'Localização não me diz nada sobre o leilão.',
  contato: 'Não faço nada com contato.',
};

return $('Resolver pendente').all()
  .filter((i) => i.json.route === 'unsupported_file')
  .map((entry) => {
    const routed = entry.json;
    const conserto = typeof CONSERTO[routed.attachment] === 'function'
      ? CONSERTO[routed.attachment](routed)
      : (CONSERTO[routed.attachment] || 'Só consigo ler edital em PDF.');
    return { json: { chat_id: routed.chat_id, text:
      conserto + NL + NL +
      'Envie o PDF do edital, ou use /ajuda para ver o que eu faço.' } };
  });
"""


def _telegram_send(name: str, position: list[int]) -> dict:
    return node(name, "n8n-nodes-base.telegram", 1.2, position, {
        "chatId": "={{ $json.chat_id }}",
        "text": "={{ $json.text }}",
        # HTML, e nao Markdown: no Markdown legado um unico caractere
        # desemparelhado faz o Telegram recusar a mensagem inteira.
        "additionalFields": {"parse_mode": "HTML", "appendAttribution": False},
    }, credentials=TELEGRAM_CRED)


def build_chat() -> dict:
    """Estágio 3: a conversa. Responde pela ficha, não pelo edital inteiro."""
    nodes = [
        # `webhookId` fixo e obrigatorio: e ele que compoe a URL registrada no
        # Telegram. Sem o campo, o fluxo importado tenta registrar uma URL com
        # `undefined` no caminho e o Telegram responde "Bad request".
        # Fixo, e nao gerado, para o JSON continuar reprodutivel em diff.
        {**node("Mensagem no Telegram", "n8n-nodes-base.telegramTrigger", 1.2, [0, 0], {
            "updates": ["message"],
            "additionalFields": {"download": True},
        }, credentials=TELEGRAM_CRED),
         "webhookId": "5f1a0c2e-7b64-4d18-9a3f-telegramchat01"},
        node("Rotear mensagem", "n8n-nodes-base.code", 2, [200, 0], {"jsCode": CHAT_ROUTE}),
        node("Classificar escopo", "n8n-nodes-base.code", 2, [380, 0],
             {"jsCode": CHAT_SCOPE_REQUEST}),
        node("Consultar escopo", "n8n-nodes-base.httpRequest", 4.2, [540, 0], {
            "method": "POST", "url": "={{ $env.DOCLING_URL }}/scope",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ texts: $json.texts }) }}",
            "options": {"timeout": 15000}}),
        node("Aplicar escopo", "n8n-nodes-base.code", 2, [700, 0],
             {"jsCode": CHAT_SCOPE_APPLY}),
        # Um edital pendente nesta conversa muda o sentido da proxima mensagem:
        # "2" deixa de ser pergunta e vira escolha de lote.
        node("Buscar pendente", "n8n-nodes-base.postgres", 2.7, [700, 0], {
            "operation": "executeQuery",
            "query": ("DELETE FROM pending_notices WHERE created_at < now() - interval '6 hours'; "
                      "SELECT file_name, sha256, markdown, deterministic, lots "
                      "FROM pending_notices WHERE chat_id = $1;"),
            "options": {"queryReplacement": "={{ [$json.chat_id] }}"},
        }, credentials=POSTGRES_CRED, alwaysOutputData=True),
        node("Resolver pendente", "n8n-nodes-base.code", 2, [780, 0],
             {"jsCode": CHAT_RESOLVE_PENDING}),
        node("Caminho", "n8n-nodes-base.switch", 3.2, [860, 0], {
            "rules": {"values": [
                {"conditions": {
                    "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
                    "conditions": [{"id": route,
                                    "operator": {"type": "string", "operation": "equals"},
                                    "leftValue": "={{ $json.route }}", "rightValue": route}],
                    "combinator": "and"},
                 "outputKey": route}
                for route in ("document", "question", "help", "forget",
                              "unsupported_file", "out_of_scope", "lot_choice")
            ]},
            "options": {"fallbackOutput": "none"},
        }),

        # ── documento ──
        node("Chamar preparacao", "n8n-nodes-base.executeWorkflow", 1.3, [660, -320], {
            "workflowId": {"__rl": True, "value": PREPARE_ID, "mode": "id"},
            "options": {"waitForSubWorkflow": True},
        }),
        node("Descrever lotes", "n8n-nodes-base.httpRequest", 4.2, [760, -320], {
            "method": "POST", "url": "={{ $env.DOCLING_URL }}/lot-choice",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": ("={{ JSON.stringify({ text: '', lots: $json.lots }) }}"),
            "options": {"timeout": 30000}}),
        node("Perguntar qual imovel", "n8n-nodes-base.code", 2, [820, -320],
             {"jsCode": CHAT_ASK_LOT}),
        _telegram_send("Responder pergunta de lote", [900, -320]),

        # Escolha do imovel: le a resposta e, se entendeu, extrai.
        node("Ler escolha", "n8n-nodes-base.httpRequest", 4.2, [660, 700], {
            "method": "POST", "url": "={{ $env.DOCLING_URL }}/lot-choice",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": ("={{ JSON.stringify({ text: $json.text,"
                         " lots: $json.pending.lots }) }}"),
            "options": {"timeout": 30000}}),
        node("Decidir escolha", "n8n-nodes-base.code", 2, [760, 700],
             {"jsCode": CHAT_LOT_DECISION}),
        node("Entendeu a escolha?", "n8n-nodes-base.if", 2.2, [860, 700], {
            "conditions": {
                "options": {"caseSensitive": True, "typeValidation": "strict",
                            "version": 2},
                "conditions": [{"id": "proceed",
                                "operator": {"type": "boolean", "operation": "true",
                                             "singleValue": True},
                                "leftValue": "={{ $json.proceed }}"}],
                "combinator": "and"}}),
        _telegram_send("Repetir pergunta de lote", [960, 820]),
        # A POSICAO IMPORTA: com `executionOrder: v1` o n8n resolve empates
        # entre ramos pela coordenada, e y menor roda antes. Em [960, 620] o
        # aviso sai antes da extracao; mais abaixo, chegaria junto com a ficha.
        node("Aviso de analise", "n8n-nodes-base.code", 2, [960, 620],
             {"jsCode": CHAT_CHOICE_ACK}),
        _telegram_send("Responder aviso de analise", [1160, 620]),
        node("Chamar ingestao", "n8n-nodes-base.executeWorkflow", 1.3, [960, 700], {
            "workflowId": {"__rl": True, "value": INGEST_ID, "mode": "id"},
            "options": {"waitForSubWorkflow": True},
        }),
        node("Limpar pendente", "n8n-nodes-base.postgres", 2.7, [1000, 700], {
            "operation": "executeQuery",
            "query": "DELETE FROM pending_notices WHERE chat_id = $1;",
            "options": {"queryReplacement":
                        "={{ [$('Decidir escolha').first().json.chat_id] }}"},
        }, credentials=POSTGRES_CRED, alwaysOutputData=True,
           onError="continueRegularOutput"),
        node("Aviso de processamento", "n8n-nodes-base.code", 2, [660, -480],
             {"jsCode": CHAT_ACK}),

        # Situacao processual, quando ja consultada. `alwaysOutputData` porque
        # a ausencia de consulta e um resultado legitimo: sem ela o resumo
        # simplesmente nao afirma nada sobre o processo, em vez de tranquilizar
        # sem base.
        node("Buscar situacao do processo", "n8n-nodes-base.postgres", 2.7, [880, -320], {
            "operation": "executeQuery",
            "query": ("SELECT movements AS case_analysis FROM case_lookups "
                      "WHERE cnj_number = $1 LIMIT 1;"),
            "options": {"queryReplacement":
                        "={{ [ (($json.analysis || {}).court_case || {}).number || '' ] }}"},
        }, credentials=POSTGRES_CRED, alwaysOutputData=True),
        node("Traduzir para exibicao", "n8n-nodes-base.httpRequest", 4.2, [1080, -320], {
            "method": "POST", "url": "={{ $env.DOCLING_URL }}/present",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": ("={{ JSON.stringify({ "
                         "ficha: ($('Chamar ingestao').first().json.analysis || {}), "
                         "deterministic: ($('Chamar ingestao').first().json.deterministic "
                         "|| null), "
                         "case: ($json.case_analysis || null) }) }}"),
            "options": {"timeout": 30000}}),
        node("Resposta da ficha", "n8n-nodes-base.code", 2, [1280, -320],
             {"jsCode": CHAT_INGEST_REPLY}),

        # ── pergunta ──
        node("Carregar ficha do chat", "n8n-nodes-base.postgres", 2.7, [660, -120], {
            "operation": "executeQuery",
            "query": ("SELECT id, file_name, analysis FROM auction_notices "
                      "WHERE chat_id = $1 ORDER BY created_at DESC LIMIT 1;"),
            "options": {"queryReplacement": "={{ [$json.chat_id] }}"},
        }, credentials=POSTGRES_CRED, alwaysOutputData=True),
        node("Montar contexto", "n8n-nodes-base.code", 2, [880, -120],
             {"jsCode": CHAT_CONTEXT}),
        # A situacao processual ja consultada na ingestao. Sem ela, perguntado
        # sobre o processo do edital o assistente respondia que a informacao
        # "nao esta presente no conteudo do edital" — com 446 movimentos
        # gravados no banco.
        node("Buscar processo do chat", "n8n-nodes-base.postgres", 2.7, [940, -120], {
            "operation": "executeQuery",
            "query": ("SELECT movements AS case_analysis FROM case_lookups "
                      "WHERE cnj_number = $1 LIMIT 1;"),
            "options": {"queryReplacement":
                        "={{ [ (($json.analysis || {}).court_case || {}).number || '' ] }}"},
        }, credentials=POSTGRES_CRED, alwaysOutputData=True),
        node("Ficha em portugues", "n8n-nodes-base.httpRequest", 4.2, [980, -120], {
            "method": "POST", "url": "={{ $env.DOCLING_URL }}/present",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": ("={{ JSON.stringify({"
                         " ficha: ($('Montar contexto').first().json.analysis || {}),"
                         " case: ($json.case_analysis || null) }) }}"),
            "options": {"timeout": 30000}}),
        node("Buscar prompt de Q&A", "n8n-nodes-base.httpRequest", 4.2, [1080, -120], {
            "url": "={{ $env.DOCLING_URL }}/prompt/qa-system", "options": {"timeout": 30000}}),
        node("Buscar glossario", "n8n-nodes-base.httpRequest", 4.2, [1280, -120], {
            "url": "={{ $env.DOCLING_URL }}/prompt/glossary", "options": {"timeout": 30000}}),
        node("Preparar pergunta", "n8n-nodes-base.code", 2, [1480, -120],
             {"jsCode": CHAT_PREPARE}),
        node("Perguntar ao modelo", "n8n-nodes-base.executeWorkflow", 1.3, [1680, -120], {
            "workflowId": {"__rl": True, "value": GATEWAY_ID, "mode": "id"},
            # Uma execucao por pergunta: `once` mandaria so a primeira ao modelo.
            "mode": "each",
            "options": {"waitForSubWorkflow": True},
        }),
        node("Resposta da pergunta", "n8n-nodes-base.code", 2, [1880, -120],
             {"jsCode": CHAT_ANSWER}),

        # ── ajuda, apagar, arquivo nao suportado ──
        # A POSICAO IMPORTA. Com `executionOrder: v1`, o n8n resolve empates
        # entre ramos pela coordenada do no — x primeiro, depois y, ambos
        # crescentes. Este aviso precisa ficar antes de `Carregar ficha do
        # chat` ([660, -120]) na ordem do canvas, senao ele sai DEPOIS da
        # resposta e nao serve para nada. Ja aconteceu: com x=1060 o aviso
        # chegava junto com a resposta pronta.
        node("Aviso de pergunta", "n8n-nodes-base.code", 2, [660, -200],
             {"jsCode": CHAT_QUESTION_ACK}),
        node("Texto de ajuda", "n8n-nodes-base.code", 2, [660, 100], {"jsCode": CHAT_HELP}),
        node("Apagar dados do chat", "n8n-nodes-base.postgres", 2.7, [660, 260], {
            "operation": "executeQuery",
            "query": "DELETE FROM auction_notices WHERE chat_id = $1;",
            "options": {"queryReplacement": "={{ [$json.chat_id] }}"},
        }, credentials=POSTGRES_CRED, alwaysOutputData=True),
        node("Confirmar exclusao", "n8n-nodes-base.code", 2, [880, 260],
             {"jsCode": CHAT_FORGET_REPLY}),
        node("Arquivo nao suportado", "n8n-nodes-base.code", 2, [1060, 420],
             {"jsCode": CHAT_UNSUPPORTED}),
        node("Recusa fora de escopo", "n8n-nodes-base.code", 2, [1060, 560],
             {"jsCode": CHAT_REFUSAL}),

        _telegram_send("Responder aviso", [880, -480]),
        _telegram_send("Responder ficha", [1480, -320]),
        _telegram_send("Responder pergunta", [2080, -120]),
        _telegram_send("Responder aviso de pergunta", [860, -200]),
        _telegram_send("Responder ajuda", [880, 100]),
        _telegram_send("Responder exclusao", [1100, 260]),
        _telegram_send("Responder nao suportado", [1280, 420]),
        _telegram_send("Responder recusa", [1280, 560]),
    ]

    def chain(*names: str) -> dict:
        return {a: {"main": [[{"node": b, "type": "main", "index": 0}]]}
                for a, b in zip(names, names[1:])}

    connections = {
        **chain("Mensagem no Telegram", "Rotear mensagem", "Classificar escopo",
                "Consultar escopo", "Aplicar escopo", "Buscar pendente",
                "Resolver pendente", "Caminho"),
        "Caminho": {"main": [
            # Duas saidas para o mesmo ramo: o aviso vai primeiro e a ingestao
            # segue em paralelo. Em serie nao funcionaria — o no do Telegram
            # devolve a resposta da API no lugar do item, e o PDF se perderia
            # antes de chegar ao Docling.
            [{"node": "Aviso de processamento", "type": "main", "index": 0},
             {"node": "Chamar preparacao", "type": "main", "index": 0}],
            # Aviso primeiro, consulta em paralelo. Em serie o no do Telegram
            # substituiria o item e a pergunta se perderia.
            [{"node": "Aviso de pergunta", "type": "main", "index": 0},
             {"node": "Carregar ficha do chat", "type": "main", "index": 0}],
            [{"node": "Texto de ajuda", "type": "main", "index": 0}],
            [{"node": "Apagar dados do chat", "type": "main", "index": 0}],
            [{"node": "Arquivo nao suportado", "type": "main", "index": 0}],
            [{"node": "Recusa fora de escopo", "type": "main", "index": 0}],
            [{"node": "Ler escolha", "type": "main", "index": 0}],
        ]},
        **chain("Aviso de processamento", "Responder aviso"),
        **chain("Chamar preparacao", "Descrever lotes", "Perguntar qual imovel",
                "Responder pergunta de lote"),
        **chain("Ler escolha", "Decidir escolha", "Entendeu a escolha?"),
        **chain("Aviso de analise", "Responder aviso de analise"),
        "Entendeu a escolha?": {"main": [
            [{"node": "Aviso de analise", "type": "main", "index": 0},
             {"node": "Chamar ingestao", "type": "main", "index": 0}],
            [{"node": "Repetir pergunta de lote", "type": "main", "index": 0}],
        ]},
        **chain("Chamar ingestao", "Limpar pendente", "Buscar situacao do processo",
                "Traduzir para exibicao", "Resposta da ficha", "Responder ficha"),
        **chain("Carregar ficha do chat", "Montar contexto",
                "Buscar processo do chat", "Ficha em portugues", "Buscar prompt de Q&A",
                "Buscar glossario",
                "Preparar pergunta", "Perguntar ao modelo", "Resposta da pergunta",
                "Responder pergunta"),
        **chain("Aviso de pergunta", "Responder aviso de pergunta"),
        **chain("Texto de ajuda", "Responder ajuda"),
        **chain("Apagar dados do chat", "Confirmar exclusao", "Responder exclusao"),
        **chain("Arquivo nao suportado", "Responder nao suportado"),
        **chain("Recusa fora de escopo", "Responder recusa"),
    }

    return {
        "id": CHAT_ID_WF,
        "name": "01 - Chat no Telegram",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
    }


CHAT_SMOKE_ID = "chatsmoke00000001"
CHAT_SMOKE_OUTPUT = ROOT / "tests" / "workflows" / "96-chat-smoke.json"

CHAT_SMOKE_MESSAGES = """
// Mensagens sinteticas no formato que o Telegram entrega, uma por caminho do
// roteador. O chat_id e o mesmo que a ingestao usou no smoke test, entao o
// caminho de pergunta encontra a ficha real do edital de exemplo.
//
// A primeira leva o PDF de verdade. Sem ela o ramo de documento — o mais caro
// e o que ja quebrou duas vezes, primeiro perdendo o binario e depois falhando
// no envio — ficava fora do teste.
const chat = { id: 'smoke-test' };
const pdf = $('Ler edital do disco').first();
return [
  { json: { message: { chat, document: {
      file_name: 'edital-exemplo.pdf', mime_type: 'application/pdf' } } },
    binary: pdf.binary },
  { json: { message: { chat, text: 'Esse imovel esta ocupado?' } } },
  { json: { message: { chat, text: 'O que o processo judicial mostra?' } } },
  { json: { message: { chat, text: 'Vale a pena comprar esse imovel?' } } },
  { json: { message: { chat, text: 'O que e comissao do leiloeiro?' } } },
  { json: { message: { chat, text: 'Posso processar o antigo dono?' } } },
  { json: { message: { chat, text: '/ajuda' } } },
  { json: { message: { chat, photo: [{ file_id: 'x' }] } } },
  { json: { message: { chat, document: {
      file_name: 'planilha.xlsx', mime_type: 'application/vnd.ms-excel' } } } },
  // Escolha do imovel. Na primeira execucao nao ha edital pendente e isto e
  // so uma mensagem qualquer; na segunda, com o pendente gravado pelo turno
  // anterior, vira a confirmacao que dispara a extracao.
  { json: { message: { chat, text: 'sim' } } },
];
"""


CHAT_CHOICE_SMOKE_ID = "chatchoicesmoke01"
CHAT_CHOICE_SMOKE_OUTPUT = ROOT / "tests" / "workflows" / "95-chat-escolha-smoke.json"

CHAT_CHOICE_MESSAGES = """
// Segundo turno isolado: so a resposta da pessoa, sem o PDF na conversa.
//
// O smoke do chat manda todas as mensagens numa execucao so, e por isso o item
// de documento existe mesmo quando se testa a escolha. Isso escondeu um
// defeito que chegou ao usuario: `Resposta da ficha` buscava o nome do arquivo
// filtrando `route === 'document'`, e em producao esse item nao existe no
// turno da confirmacao. Aqui ele nao existe tambem.
//
// Exige um edital pendente no banco — rode o 96 antes.
return [{ json: { message: { chat: { id: 'smoke-test' }, text: 'sim' } } }];
"""


def build_chat_choice_smoke() -> dict:
    """O turno da confirmação, sozinho — sem documento na conversa."""
    smoke = build_chat_smoke()
    for original in smoke["nodes"]:
        if original["name"] == "Mensagem no Telegram":
            original["parameters"]["jsCode"] = CHAT_CHOICE_MESSAGES
    # Sem PDF: este turno não tem anexo, e ler o disco aqui seria mentira.
    smoke["nodes"] = [n for n in smoke["nodes"] if n["name"] != "Ler edital do disco"]
    smoke["connections"]["Disparo manual"] = {
        "main": [[{"node": "Mensagem no Telegram", "type": "main", "index": 0}]]}
    smoke["connections"].pop("Ler edital do disco", None)
    smoke["id"] = CHAT_CHOICE_SMOKE_ID
    smoke["name"] = "95 - Smoke da escolha de lote"
    return smoke


def build_chat_smoke() -> dict:
    """Exercita o chat sem o Telegram na frente nem atras.

    Deriva do fluxo real em vez de reimplementa-lo: o gatilho vira disparo
    manual com mensagens sinteticas e os envios viram passagem direta. Tudo no
    meio — roteamento, carga da ficha, montagem do prompt, gateway — e
    exatamente o codigo que roda em producao, e nao uma copia que envelhece.
    """
    chat = build_chat()
    nodes = []
    for original in chat["nodes"]:
        if original["type"] == "n8n-nodes-base.telegramTrigger":
            nodes.append(node("Disparo manual", "n8n-nodes-base.manualTrigger", 1,
                              [-600, 0], {}))
            nodes.append(node("Ler edital do disco", "n8n-nodes-base.readWriteFile", 1,
                              [-400, 0], {"fileSelector": "/data/editais/edital-exemplo.pdf",
                                          "options": {"dataPropertyName": "data"}}))
            nodes.append(node("Mensagem no Telegram", "n8n-nodes-base.code", 2,
                              [-200, 0], {"jsCode": CHAT_SMOKE_MESSAGES}))
            continue
        if original["type"] == "n8n-nodes-base.telegram":
            # Sem credencial e sem rede: so devolve o que teria sido enviado.
            nodes.append(node(original["name"], "n8n-nodes-base.code", 2,
                              original["position"],
                              {"jsCode": "return $input.all();"}))
            continue
        nodes.append(original)

    connections = dict(chat["connections"])
    connections["Disparo manual"] = {
        "main": [[{"node": "Ler edital do disco", "type": "main", "index": 0}]]}
    connections["Ler edital do disco"] = {
        "main": [[{"node": "Mensagem no Telegram", "type": "main", "index": 0}]]}

    return {
        "id": CHAT_SMOKE_ID,
        "name": "96 - Smoke do chat",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
    }


# ─── 04 — Preparação do edital: converter, detectar lotes, guardar ──────────

PREPARE_ID = "editalprepare001"
PREPARE_OUTPUT = ROOT / "workflows" / "04-edital-preparar.json"

PREPARE_RESULT = """
// Fecha a fase barata: o edital virou texto, os campos determinísticos foram
// extraídos e sabemos quantos imoveis o documento cobre.
//
// A extracao cara nao acontece aqui de proposito. Ela custa dois minutos e
// alguns milesimos de dolar, e gastar isso antes de saber QUAL imovel a pessoa
// quer e desperdicio quando o edital tem sete.
const trigger = $('Chamada de outro fluxo').first().json;
const converted = $('Converter PDF').first().json;
const deterministic = $('Extrair campos deterministicos').first().json;
const lots = (deterministic.multi_lot || {}).lots || [];

return [{ json: {
  ok: true,
  chat_id: trigger.chat_id,
  file_name: trigger.file_name,
  sha256: converted.sha256,
  pages: converted.pages,
  markdown: converted.markdown,
  deterministic,
  lots,
  lot_count: lots.length,
} }];
"""


def build_prepare() -> dict:
    """Converte o PDF e descobre os imóveis, sem chamar modelo.

    Separado da ingestão porque a pergunta "qual imóvel você quer analisar?"
    precisa acontecer entre as duas: depois da conversão, que dá a lista, e
    antes da extração, que é o que custa.
    """
    nodes = [
        node("Chamada de outro fluxo", "n8n-nodes-base.executeWorkflowTrigger", 1.1,
             [0, 0], {"inputSource": "passthrough"}),
        node("Converter PDF", "n8n-nodes-base.httpRequest", 4.2, [220, 0], {
            "method": "POST", "url": "={{ $env.DOCLING_URL }}/convert",
            "sendBody": True, "contentType": "multipart-form-data",
            "bodyParameters": {"parameters": [
                {"parameterType": "formBinaryData", "name": "file",
                 "inputDataFieldName": "data"}]},
            "options": {"timeout": 900000}}),
        # Le do no que converteu, e nao de `$json`: entre os dois passaram a
        # conferencia de documento e o IF, e o item que chega aqui e o veredito,
        # nao o Markdown.
        node("Extrair campos deterministicos", "n8n-nodes-base.httpRequest", 4.2,
             [640, -80], {
                 "method": "POST", "url": "={{ $env.DOCLING_URL }}/extract",
                 "sendBody": True, "specifyBody": "json",
                 "jsonBody": ("={{ JSON.stringify({ markdown:"
                              " $('Converter PDF').first().json.markdown }) }}"),
                 "options": {"timeout": 60000}}),
        node("Conferir se e edital", "n8n-nodes-base.httpRequest", 4.2, [560, 0], {
            "method": "POST", "url": "={{ $env.DOCLING_URL }}/inspect",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": ("={{ JSON.stringify({ markdown:"
                         " $('Converter PDF').first().json.markdown }) }}"),
            "options": {"timeout": 30000}}),
        node("E um edital?", "n8n-nodes-base.if", 2.2, [610, 0], {
            "conditions": {
                "options": {"caseSensitive": True, "typeValidation": "strict",
                            "version": 2},
                "conditions": [{"id": "notice",
                                "operator": {"type": "boolean", "operation": "true",
                                             "singleValue": True},
                                "leftValue": "={{ $json.is_notice }}"}],
                "combinator": "and"}}),
        node("Recusar documento", "n8n-nodes-base.code", 2, [660, 180], {"jsCode": """
const trigger = $('Chamada de outro fluxo').first().json;
const verdict = $input.first().json;
return [{ json: {
  ok: false, rejected: true, reason: verdict.reason, message: verdict.message,
  chat_id: trigger.chat_id, file_name: trigger.file_name,
} }];
"""}),
        node("Montar resultado", "n8n-nodes-base.code", 2, [660, 0],
             {"jsCode": PREPARE_RESULT}),
        # Guarda o Markdown à espera da resposta. `ON CONFLICT` porque mandar um
        # segundo edital antes de responder substitui o primeiro, que é o que a
        # pessoa espera ao trocar de documento.
        node("Guardar pendente", "n8n-nodes-base.postgres", 2.7, [880, 0], {
            "operation": "executeQuery",
            "query": ("INSERT INTO pending_notices "
                      "(chat_id, file_name, sha256, markdown, deterministic, lots) "
                      "VALUES ($1, $2, $3, $4, $5, $6) "
                      "ON CONFLICT (chat_id) DO UPDATE SET "
                      "file_name = EXCLUDED.file_name, sha256 = EXCLUDED.sha256, "
                      "markdown = EXCLUDED.markdown, "
                      "deterministic = EXCLUDED.deterministic, "
                      "lots = EXCLUDED.lots, created_at = now();"),
            "options": {"queryReplacement":
                        "={{ [$json.chat_id, $json.file_name, $json.sha256,"
                        " $json.markdown, JSON.stringify($json.deterministic),"
                        " JSON.stringify($json.lots)] }}"},
        }, credentials=POSTGRES_CRED),
        node("Resposta", "n8n-nodes-base.code", 2, [1100, 0], {"jsCode": """
// O item que sai daqui e o de `Montar resultado`, e nao o do Postgres: quem
// chamou quer a lista de lotes, nao o resultado do INSERT.
return [{ json: $('Montar resultado').first().json }];
"""}),
    ]

    def chain(*names: str) -> dict:
        return {a: {"main": [[{"node": b, "type": "main", "index": 0}]]}
                for a, b in zip(names, names[1:])}

    return {
        "id": PREPARE_ID,
        "name": "04 - Preparacao do edital",
        "nodes": nodes,
        "connections": {
            **chain("Chamada de outro fluxo", "Converter PDF",
                    "Conferir se e edital", "E um edital?"),
            # Documento recusado nao passa pela extracao deterministica nem
            # ocupa uma pendencia: sai por aqui, tendo custado uma conversao.
            "E um edital?": {"main": [
                [{"node": "Extrair campos deterministicos", "type": "main", "index": 0}],
                [{"node": "Recusar documento", "type": "main", "index": 0}],
            ]},
            **chain("Extrair campos deterministicos", "Montar resultado",
                    "Guardar pendente", "Resposta"),
        },
        "settings": {"executionOrder": "v1"},
    }


if __name__ == "__main__":
    write(OUTPUT, build())
    write(SMOKE_OUTPUT, build_smoke())
    write(INGEST_OUTPUT, build_ingest())
    write(INGEST_SMOKE_OUTPUT, build_ingest_smoke())
    write(LOOKUP_OUTPUT, build_lookup())
    write(LOOKUP_SMOKE_OUTPUT, build_lookup_smoke())
    write(PREPARE_OUTPUT, build_prepare())
    write(CHAT_OUTPUT, build_chat())
    write(CHAT_SMOKE_OUTPUT, build_chat_smoke())
    write(CHAT_CHOICE_SMOKE_OUTPUT, build_chat_choice_smoke())
