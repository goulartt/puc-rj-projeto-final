/**
 * Testes do gateway de modelo.
 *
 *     node --test services/gateway/
 *
 * O que importa aqui não é "a função roda", é que as três diferenças reais
 * entre provedores estejam cobertas: onde vai o system prompt, como se pede
 * saída estruturada, e o formato do `usage`. Se alguma delas regredir, o
 * projeto perde a capacidade de trocar de provedor sem tocar nos fluxos.
 */

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const gw = require('./gateway');

const ENV_ANTHROPIC = {
  LLM_EXTRACTION_PROVIDER: 'anthropic',
  LLM_EXTRACTION_MODEL: 'claude-opus-5',
  LLM_EXTRACTION_BASE_URL: 'https://api.anthropic.com',
  LLM_EXTRACTION_API_KEY: 'chave-de-teste-nao-real',
};

const ENV_OLLAMA = {
  LLM_QA_PROVIDER: 'openai',
  LLM_QA_MODEL: 'qwen3:14b',
  LLM_QA_BASE_URL: 'http://host.docker.internal:11434',
  LLM_QA_API_KEY: 'ollama',
};

const PAYLOAD = {
  system: 'Voce e analista de leilao.',
  messages: [{ role: 'user', content: 'O imovel esta ocupado?' }],
  maxTokens: 1024,
};

// ─── Configuração ───────────────────────────────────────────────────────────

test('resolve a configuracao a partir do prefixo do papel', () => {
  const cfg = gw.resolveConfig('extraction', ENV_ANTHROPIC);
  assert.equal(cfg.provider, 'anthropic');
  assert.equal(cfg.model, 'claude-opus-5');
  assert.equal(cfg.baseUrl, 'https://api.anthropic.com');
});

test('apelidos de fornecedor resolvem para o protocolo certo', () => {
  // O valor nomeia o protocolo HTTP, nao a empresa. Apontar a DeepSeek com
  // PROVIDER=openai confunde quem le o .env, entao o nome do fornecedor vale.
  for (const alias of ['deepseek', 'ollama', 'openrouter', 'groq', 'openai-compatible']) {
    const cfg = gw.resolveConfig('extraction', { ...ENV_ANTHROPIC, LLM_EXTRACTION_PROVIDER: alias });
    assert.equal(cfg.provider, 'openai', alias);
  }
  for (const alias of ['anthropic', 'claude']) {
    const cfg = gw.resolveConfig('extraction', { ...ENV_ANTHROPIC, LLM_EXTRACTION_PROVIDER: alias });
    assert.equal(cfg.provider, 'anthropic', alias);
  }
});

test('barra provider desconhecido em vez de tentar adivinhar', () => {
  assert.throws(
    () => gw.resolveConfig('extraction', { ...ENV_ANTHROPIC, LLM_EXTRACTION_PROVIDER: 'gemini' }),
    /PROVIDER invalido/,
  );
});

test('exige modelo e base url', () => {
  assert.throws(
    () => gw.resolveConfig('extraction', { ...ENV_ANTHROPIC, LLM_EXTRACTION_MODEL: '' }),
    /MODEL nao definido/,
  );
  assert.throws(
    () => gw.resolveConfig('extraction', { ...ENV_ANTHROPIC, LLM_EXTRACTION_BASE_URL: '' }),
    /BASE_URL nao definido/,
  );
});

test('remove barra final da base url para nao gerar // na rota', () => {
  const cfg = gw.resolveConfig('extraction', {
    ...ENV_ANTHROPIC,
    LLM_EXTRACTION_BASE_URL: 'https://api.anthropic.com/',
  });
  assert.equal(gw.buildRequest(cfg, PAYLOAD).url, 'https://api.anthropic.com/v1/messages');
});

// ─── Diferença 1: onde vai o system prompt ──────────────────────────────────

test('anthropic: system em campo separado, fora de messages', () => {
  const req = gw.buildRequest(gw.resolveConfig('extraction', ENV_ANTHROPIC), PAYLOAD);
  assert.deepEqual(req.body.system, [{ type: 'text', text: PAYLOAD.system }]);
  assert.equal(req.body.messages.length, 1);
  assert.equal(req.body.messages[0].role, 'user');
});

test('openai: system vira a primeira mensagem', () => {
  const req = gw.buildRequest(gw.resolveConfig('qa', ENV_OLLAMA), PAYLOAD);
  assert.equal(req.body.system, undefined);
  assert.equal(req.body.messages.length, 2);
  assert.equal(req.body.messages[0].role, 'system');
  assert.equal(req.body.messages[0].content, PAYLOAD.system);
});

test('anthropic: cacheSystem marca o bloco, que e a economia entre perguntas', () => {
  const req = gw.buildRequest(gw.resolveConfig('extraction', ENV_ANTHROPIC), {
    ...PAYLOAD,
    cacheSystem: true,
  });
  assert.deepEqual(req.body.system[0].cache_control, { type: 'ephemeral' });
});

test('openai: cacheSystem e ignorado, nao vaza campo invalido', () => {
  const req = gw.buildRequest(gw.resolveConfig('qa', ENV_OLLAMA), { ...PAYLOAD, cacheSystem: true });
  assert.equal(JSON.stringify(req.body).includes('cache_control'), false);
});

// ─── Diferença 2: como se pede saída estruturada ────────────────────────────

const SCHEMA = { type: 'object', properties: { ok: { type: 'boolean' } }, required: ['ok'] };

test('anthropic: schema vai em output_config.format', () => {
  const req = gw.buildRequest(gw.resolveConfig('extraction', ENV_ANTHROPIC), { ...PAYLOAD, schema: SCHEMA });
  assert.deepEqual(req.body.output_config, { format: { type: 'json_schema', schema: SCHEMA } });
  assert.equal(req.body.response_format, undefined);
});

test('openai: schema vai em response_format.json_schema', () => {
  const req = gw.buildRequest(gw.resolveConfig('qa', ENV_OLLAMA), { ...PAYLOAD, schema: SCHEMA });
  assert.equal(req.body.response_format.type, 'json_schema');
  assert.equal(req.body.response_format.json_schema.schema, SCHEMA);
  assert.equal(req.body.output_config, undefined);
});

test('sem schema, nenhum campo de saida estruturada e enviado', () => {
  for (const [role, env] of [['extraction', ENV_ANTHROPIC], ['qa', ENV_OLLAMA]]) {
    const req = gw.buildRequest(gw.resolveConfig(role, env), PAYLOAD);
    assert.equal(req.body.output_config, undefined);
    assert.equal(req.body.response_format, undefined);
  }
});

test('cabecalhos de autenticacao diferem por provedor', () => {
  const a = gw.buildRequest(gw.resolveConfig('extraction', ENV_ANTHROPIC), PAYLOAD);
  assert.equal(a.headers['x-api-key'], 'chave-de-teste-nao-real');
  assert.equal(a.headers['anthropic-version'], '2023-06-01');

  const o = gw.buildRequest(gw.resolveConfig('qa', ENV_OLLAMA), PAYLOAD);
  assert.equal(o.headers.authorization, 'Bearer ollama');
});

test('messages vazio e erro, nao requisicao malformada', () => {
  assert.throws(
    () => gw.buildRequest(gw.resolveConfig('qa', ENV_OLLAMA), { ...PAYLOAD, messages: [] }),
    /messages vazio/,
  );
});

test('modo json pede JSON sintatico, nao schema — e o que a DeepSeek aceita', () => {
  const cfg = gw.resolveConfig('extraction', {
    LLM_EXTRACTION_PROVIDER: 'deepseek',
    LLM_EXTRACTION_MODEL: 'deepseek-v4-flash',
    LLM_EXTRACTION_BASE_URL: 'https://api.deepseek.com',
    LLM_EXTRACTION_API_KEY: 'x',
  });
  const req = gw.buildRequest(cfg, { ...PAYLOAD, schema: SCHEMA, structuredMode: gw.STRUCTURED_JSON });
  assert.deepEqual(req.body.response_format, { type: 'json_object' });
});

test('modo none nao envia response_format algum', () => {
  const req = gw.buildRequest(gw.resolveConfig('qa', ENV_OLLAMA), {
    ...PAYLOAD, schema: SCHEMA, structuredMode: gw.STRUCTURED_NONE,
  });
  assert.equal(req.body.response_format, undefined);
});

test('modo schema continua sendo o padrao', () => {
  const req = gw.buildRequest(gw.resolveConfig('qa', ENV_OLLAMA), { ...PAYLOAD, schema: SCHEMA });
  assert.equal(req.body.response_format.type, 'json_schema');
});

test('anthropic mantem o schema completo mesmo no modo json', () => {
  // Degradar aqui seria perda gratuita: a Messages API aceita schema.
  const req = gw.buildRequest(gw.resolveConfig('extraction', ENV_ANTHROPIC), {
    ...PAYLOAD, schema: SCHEMA, structuredMode: gw.STRUCTURED_JSON,
  });
  assert.deepEqual(req.body.output_config, { format: { type: 'json_schema', schema: SCHEMA } });
});

// ─── Diferença 3: formato do usage ──────────────────────────────────────────

// Resposta real do Ollama, capturada em 05/08/2026.
const RAW_OLLAMA = {
  model: 'qwen3:14b',
  choices: [{ message: { role: 'assistant', content: 'Brasília' }, finish_reason: 'stop' }],
  usage: { prompt_tokens: 26, completion_tokens: 110, total_tokens: 136 },
};

// Formato documentado da Messages API.
const RAW_ANTHROPIC = {
  model: 'claude-opus-5',
  content: [{ type: 'thinking', thinking: '' }, { type: 'text', text: 'Brasília' }],
  stop_reason: 'end_turn',
  usage: { input_tokens: 26, output_tokens: 110, cache_read_input_tokens: 900 },
};

test('normaliza usage do openai', () => {
  const r = gw.normalizeResponse('openai', RAW_OLLAMA);
  assert.deepEqual(r.usage, { input: 26, output: 110, cached: 0 });
  assert.equal(r.text, 'Brasília');
  assert.equal(r.finishReason, 'stop');
});

test('normaliza usage do anthropic, incluindo leitura de cache', () => {
  const r = gw.normalizeResponse('anthropic', RAW_ANTHROPIC);
  assert.deepEqual(r.usage, { input: 26, output: 110, cached: 900 });
  assert.equal(r.text, 'Brasília');
});

test('anthropic: junta blocos de texto e descarta thinking', () => {
  const r = gw.normalizeResponse('anthropic', {
    content: [
      { type: 'thinking', thinking: 'raciocinio interno' },
      { type: 'text', text: 'parte um. ' },
      { type: 'text', text: 'parte dois.' },
    ],
    usage: {},
  });
  assert.equal(r.text, 'parte um. parte dois.');
});

// Raciocinio vazando no corpo da resposta. O provedor normalmente separa, mas
// a separacao depende de o modelo abrir o bloco com `<think>` — e isso e
// geracao, nao protocolo.

test('openai: descarta o raciocinio delimitado que veio no corpo', () => {
  const r = gw.normalizeResponse('openai', {
    choices: [{ message: { content: '<think>penso em ingles</think>\n\nBrasília' } }],
    usage: {},
  });
  assert.equal(r.text, 'Brasília');
});

test('openai: descarta o raciocinio mesmo sem a abertura casada', () => {
  // Observado em producao: o qwen3 emitiu um token de lixo no lugar de
  // `<think>`, o Ollama nao reconheceu o bloco e mandou tudo em `content`. A
  // pessoa recebeu o raciocinio em ingles como se fosse a resposta.
  const r = gw.normalizeResponse('openai', {
    choices: [{ message: {
      content: '栋\n\nOkay, the user is asking about the case.\n</think>\n\nO processo segue ativo.',
    } }],
    usage: {},
  });
  assert.equal(r.text, 'O processo segue ativo.');
});

test('openai: resposta truncada dentro do raciocinio vira texto vazio, nao raciocinio', () => {
  const r = gw.normalizeResponse('openai', {
    choices: [{ message: { content: '<think>comecei a pensar e o limite chegou' } }],
    usage: {},
  });
  assert.equal(r.text, '');
});

test('openai: resposta sem raciocinio nenhum passa intacta', () => {
  const r = gw.normalizeResponse('openai', {
    choices: [{ message: { content: 'A comissão é de 5% e o texto fala de <b>ônus</b>.' } }],
    usage: {},
  });
  assert.equal(r.text, 'A comissão é de 5% e o texto fala de <b>ônus</b>.');
});

test('anthropic: recusa por classificador e sinalizada, nao confundida com resposta vazia', () => {
  const r = gw.normalizeResponse('anthropic', { content: [], stop_reason: 'refusal', usage: {} });
  assert.equal(r.refusal, true);
  assert.equal(r.text, '');
});

test('resposta sem usage nao quebra a contabilidade', () => {
  assert.deepEqual(gw.normalizeResponse('openai', { choices: [] }).usage, { input: 0, output: 0, cached: 0 });
});

// ─── Saída JSON ─────────────────────────────────────────────────────────────

test('parseia JSON puro e tambem cercado por bloco de codigo', () => {
  assert.deepEqual(gw.parseJsonOutput('{"ok":true}'), { ok: true });
  assert.deepEqual(gw.parseJsonOutput('```json\n{"ok":true}\n```'), { ok: true });
});

test('texto que nao e JSON devolve null em vez de estourar', () => {
  assert.equal(gw.parseJsonOutput('desculpe, nao consegui'), null);
  assert.equal(gw.parseJsonOutput(''), null);
});

// ─── Custo e teto de orçamento ──────────────────────────────────────────────

test('modelo local custa zero, e isso e sabido', () => {
  const cost = gw.computeCost(gw.resolveConfig('qa', ENV_OLLAMA), { input: 5000, output: 2000 });
  assert.deepEqual(cost, { usd: 0, known: true, reason: 'modelo local' });
});

test('calcula custo do anthropic pela tabela', () => {
  const cfg = gw.resolveConfig('extraction', ENV_ANTHROPIC);
  // 1M entrada sem cache + 1M saida em opus-5 = 5 + 25
  const cost = gw.computeCost(cfg, { input: 1e6, output: 1e6, cached: 0 });
  assert.equal(cost.known, true);
  assert.equal(cost.usd, 30);
});

test('token lido do cache usa o preco de cache do proprio modelo', () => {
  const anthropic = gw.resolveConfig('extraction', ENV_ANTHROPIC);
  assert.equal(gw.computeCost(anthropic, { input: 1e6, output: 0, cached: 0 }).usd, 5);
  assert.equal(gw.computeCost(anthropic, { input: 1e6, output: 0, cached: 1e6 }).usd, 0.5);
});

test('preco de cache e por modelo, nao um multiplicador unico', () => {
  // A Anthropic cobra 10% da entrada pelo cache; a DeepSeek cobra 2%. Uma
  // constante compartilhada erraria um dos dois, e o teto de orcamento depende
  // desse numero.
  const deepseek = gw.resolveConfig('extraction', {
    LLM_EXTRACTION_PROVIDER: 'openai',
    LLM_EXTRACTION_MODEL: 'deepseek-v4-flash',
    LLM_EXTRACTION_BASE_URL: 'https://api.deepseek.com',
    LLM_EXTRACTION_API_KEY: 'x',
  });
  assert.equal(gw.computeCost(deepseek, { input: 1e6, output: 0, cached: 0 }).usd, 0.14);
  assert.equal(gw.computeCost(deepseek, { input: 1e6, output: 0, cached: 1e6 }).usd, 0.0028);

  const ratioAnthropic = 0.5 / 5;
  const ratioDeepseek = 0.0028 / 0.14;
  assert.notEqual(ratioAnthropic, ratioDeepseek);
});

test('deepseek e remoto e faturavel, entao respeita o teto', () => {
  const cfg = gw.resolveConfig('extraction', {
    LLM_EXTRACTION_PROVIDER: 'openai',
    LLM_EXTRACTION_MODEL: 'deepseek-v4-flash',
    LLM_EXTRACTION_BASE_URL: 'https://api.deepseek.com',
    LLM_EXTRACTION_API_KEY: 'x',
  });
  assert.equal(gw.canProceed(cfg, { spentUsd: 0, limitUsd: 5 }).billable, true);
  assert.equal(gw.canProceed(cfg, { spentUsd: 5, limitUsd: 5 }).allowed, false);
});

test('custo real de uma extracao de edital cabe no orcamento', () => {
  // ~12k tokens de entrada e ~4k de saida: e a ordem de grandeza medida no
  // edital de exemplo. Serve para o custo por edital nao mudar sem alguem ver.
  const cfg = gw.resolveConfig('extraction', {
    LLM_EXTRACTION_PROVIDER: 'openai',
    LLM_EXTRACTION_MODEL: 'deepseek-v4-flash',
    LLM_EXTRACTION_BASE_URL: 'https://api.deepseek.com',
    LLM_EXTRACTION_API_KEY: 'x',
  });
  const cost = gw.computeCost(cfg, { input: 12000, output: 4000, cached: 0 });
  assert.ok(cost.usd < 0.01, `esperado abaixo de 1 centavo, veio ${cost.usd}`);
});

test('modelo remoto fora da tabela nao vira custo zero', () => {
  const cfg = gw.resolveConfig('extraction', {
    ...ENV_ANTHROPIC,
    LLM_EXTRACTION_MODEL: 'modelo-que-ninguem-precificou',
  });
  const cost = gw.computeCost(cfg, { input: 1e6, output: 1e6 });
  assert.equal(cost.known, false);
});

test('modelo remoto sem preco e barrado antes de chamar', () => {
  const cfg = gw.resolveConfig('extraction', {
    ...ENV_ANTHROPIC,
    LLM_EXTRACTION_MODEL: 'modelo-que-ninguem-precificou',
  });
  const d = gw.canProceed(cfg, { spentUsd: 0, limitUsd: 5 });
  assert.equal(d.allowed, false);
  assert.match(d.reason, /sem preco conhecido/);
});

test('teto atingido barra a chamada, e a decisao nao depende do custo dela', () => {
  const cfg = gw.resolveConfig('extraction', ENV_ANTHROPIC);
  assert.equal(gw.canProceed(cfg, { spentUsd: 4.99, limitUsd: 5 }).allowed, true);
  assert.equal(gw.canProceed(cfg, { spentUsd: 5.0, limitUsd: 5 }).allowed, false);
  assert.match(gw.canProceed(cfg, { spentUsd: 7, limitUsd: 5 }).reason, /teto de orcamento/);
});

test('modelo local nunca e barrado pelo teto, e nao e faturavel', () => {
  const cfg = gw.resolveConfig('qa', ENV_OLLAMA);
  const d = gw.canProceed(cfg, { spentUsd: 999, limitUsd: 5 });
  assert.equal(d.allowed, true);
  assert.equal(d.billable, false);
});

// ─── Detecção de host local ─────────────────────────────────────────────────

test('nao depende do global URL, que o sandbox do n8n nao expoe', () => {
  // Regressao: a versao anterior usava `new URL()` dentro de try/catch. No no
  // de codigo do n8n o ReferenceError era engolido, todo host virava remoto, e
  // o Ollama local passou a ser barrado como modelo pago sem preco.
  const saved = globalThis.URL;
  try {
    delete globalThis.URL;
    assert.equal(gw.isLocal('http://ollama:11434'), true);
    assert.equal(gw.isLocal('https://api.anthropic.com'), false);
  } finally {
    globalThis.URL = saved;
  }
});

test('reconhece os enderecos locais usados no compose', () => {
  for (const url of [
    'http://localhost:11434',
    'http://127.0.0.1:11434',
    'http://host.docker.internal:11434',
    'http://ollama:11434',
    'http://ollama:11434/',
    'http://[::1]:11434',
  ]) {
    assert.equal(gw.isLocal(url), true, url);
  }
  assert.equal(gw.isLocal('https://api.anthropic.com'), false);
  assert.equal(gw.isLocal('https://openrouter.ai/api'), false);
});

// ─── Controle de raciocínio ────────────────────────────────────────────────
//
// A extração do edital de exemplo gastava 14.366 dos 18.684 tokens de saída
// pensando — 77%. Desligar o raciocínio é a única alavanca com efeito real
// sobre os 147 segundos, e cada provedor a expõe de um jeito.

test('openai: reasoning vira reasoning_effort, sem traducao', () => {
  const config = { provider: 'openai', model: 'deepseek-v4-flash',
                   baseUrl: 'https://api.deepseek.com', apiKey: 'k' };
  const { body } = gw.buildRequest(config, {
    messages: [{ role: 'user', content: 'oi' }], reasoning: 'none',
  });
  assert.equal(body.reasoning_effort, 'none');
});

test('openai: sem reasoning o campo nao vai no corpo', () => {
  const config = { provider: 'openai', model: 'qwen3:14b',
                   baseUrl: 'http://localhost:11434', apiKey: 'ollama' };
  const { body } = gw.buildRequest(config, { messages: [{ role: 'user', content: 'oi' }] });
  assert.ok(!('reasoning_effort' in body));
});

test('anthropic: raciocinio e thinking adaptive, nunca budget_tokens', () => {
  const config = { provider: 'anthropic', model: 'claude-sonnet-5',
                   baseUrl: 'https://api.anthropic.com', apiKey: 'k' };
  const { body } = gw.buildRequest(config, {
    messages: [{ role: 'user', content: 'oi' }], reasoning: 'high',
  });
  assert.deepEqual(body.thinking, { type: 'adaptive' });
  // Os modelos da geracao 5 respondem 400 se `budget_tokens` vier.
  assert.ok(!('budget_tokens' in body.thinking));
});

test('anthropic: none omite o campo em vez de mandar disabled', () => {
  const config = { provider: 'anthropic', model: 'claude-sonnet-5',
                   baseUrl: 'https://api.anthropic.com', apiKey: 'k' };
  const { body } = gw.buildRequest(config, {
    messages: [{ role: 'user', content: 'oi' }], reasoning: 'none',
  });
  assert.ok(!('thinking' in body));
});
