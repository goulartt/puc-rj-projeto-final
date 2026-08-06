/**
 * Gateway de modelo — lógica pura, sem I/O.
 *
 * Todo fluxo do n8n chama o LLM por aqui, e nunca fala com provedor direto.
 * O nó nativo de chat model do n8n não serve: ele esconde `cache_control`,
 * `thinking` e structured output, que são exatamente os controles de custo e
 * de contrato deste projeto.
 *
 * O trabalho real não é trocar URL — é normalizar as três coisas que diferem
 * de verdade entre provedores: onde vai o system prompt, como se pede saída
 * estruturada, e o formato do `usage`. É a normalização de `usage` que permite
 * contabilizar custo e aplicar o teto de orçamento independentemente de quem
 * atendeu a chamada.
 *
 * Este arquivo é a fonte da verdade. `scripts/build-workflows.py` o embute no
 * nó de código de `workflows/00-llm-gateway.json`, porque o n8n não consegue
 * importar arquivo local. Editar o JSON à mão faz o próximo build sobrescrever.
 */

'use strict';

const ANTHROPIC = 'anthropic';
const OPENAI = 'openai';

/**
 * Preço por milhão de tokens, em USD. Conferido em 05/08/2026.
 *
 * Só entram modelos cujo preço foi verificado. Modelo ausente da tabela não
 * vira custo zero: vira `costKnown: false`, para o teto de orçamento não ser
 * furado em silêncio por um modelo que ninguém precificou.
 */
const PRICING = {
  'claude-opus-5': { input: 5, output: 25 },
  'claude-opus-4-8': { input: 5, output: 25 },
  'claude-sonnet-5': { input: 3, output: 15 },
  'claude-haiku-4-5': { input: 1, output: 5 },
};

/** Leitura em cache custa ~10% da entrada; ver docs de prompt caching. */
const CACHE_READ_MULTIPLIER = 0.1;

/** Endereços que caracterizam modelo local — custo real zero. */
const LOCAL_HOSTS = ['localhost', '127.0.0.1', '::1', 'host.docker.internal', 'ollama'];

/**
 * Extrai o host de uma URL sem depender do global `URL`.
 *
 * O sandbox do nó de código do n8n não expõe `URL`. Uma versão anterior usava
 * `new URL()` dentro de try/catch: o ReferenceError era engolido, todo endereço
 * virava "remoto", e o Ollama local passou a ser barrado como modelo pago sem
 * preço. Regex não tem essa dependência.
 */
function hostOf(baseUrl) {
  const match = /^[a-z][a-z0-9+.-]*:\/\/(?:[^@/]*@)?(\[[^\]]+\]|[^:/?#]+)/i.exec(
    String(baseUrl || '').trim(),
  );
  if (!match) return '';
  return match[1].replace(/^\[|\]$/g, '').toLowerCase();
}

function isLocal(baseUrl) {
  return LOCAL_HOSTS.includes(hostOf(baseUrl));
}

/**
 * Resolve a configuração de um papel a partir das variáveis de ambiente.
 *
 * `role` é `extraction` (Estágio 1, caro, roda uma vez por edital) ou `qa`
 * (Estágio 3, barato, roda a cada pergunta). Trocar o provedor de um estágio
 * é mexer só nestas variáveis — nenhum fluxo precisa saber quem atende.
 */
function resolveConfig(role, env) {
  const prefix = `LLM_${role.toUpperCase()}_`;
  const provider = (env[`${prefix}PROVIDER`] || '').trim().toLowerCase();

  if (provider !== ANTHROPIC && provider !== OPENAI) {
    throw new Error(
      `${prefix}PROVIDER invalido: ${JSON.stringify(provider)}. Use "${ANTHROPIC}" ou "${OPENAI}".`,
    );
  }

  const model = (env[`${prefix}MODEL`] || '').trim();
  const baseUrl = (env[`${prefix}BASE_URL`] || '').trim().replace(/\/+$/, '');
  if (!model) throw new Error(`${prefix}MODEL nao definido.`);
  if (!baseUrl) throw new Error(`${prefix}BASE_URL nao definido.`);

  return { role, provider, model, baseUrl, apiKey: env[`${prefix}API_KEY`] || '' };
}

/**
 * Monta a requisição HTTP do provedor.
 *
 * `system` é string única; `messages` é [{role, content}]. `schema` opcional
 * pede saída estruturada. `cacheSystem` marca o bloco de sistema para cache —
 * é o que torna barato repetir o mesmo edital em perguntas seguintes.
 */
function buildRequest(config, { system, messages, schema, maxTokens = 4096, cacheSystem = false }) {
  if (!Array.isArray(messages) || messages.length === 0) {
    throw new Error('messages vazio.');
  }

  if (config.provider === ANTHROPIC) {
    const body = {
      model: config.model,
      max_tokens: maxTokens,
      messages,
    };

    if (system) {
      // Bloco separado, não primeira mensagem — é assim na Messages API.
      const block = { type: 'text', text: system };
      if (cacheSystem) block.cache_control = { type: 'ephemeral' };
      body.system = [block];
    }
    if (schema) {
      body.output_config = { format: { type: 'json_schema', schema } };
    }

    return {
      url: `${config.baseUrl}/v1/messages`,
      headers: {
        'content-type': 'application/json',
        'x-api-key': config.apiKey,
        'anthropic-version': '2023-06-01',
      },
      body,
    };
  }

  // OPENAI: cobre Ollama, OpenRouter, Together, Groq, vLLM.
  const body = {
    model: config.model,
    max_tokens: maxTokens,
    // System vira a primeira mensagem, não campo separado.
    messages: system ? [{ role: 'system', content: system }, ...messages] : messages,
  };

  if (schema) {
    body.response_format = {
      type: 'json_schema',
      json_schema: { name: 'output', strict: true, schema },
    };
  }
  // `cacheSystem` não tem equivalente aqui e é ignorado de propósito: o
  // caminho OpenAI-compat não expõe controle de cache.

  return {
    url: `${config.baseUrl}/v1/chat/completions`,
    headers: {
      'content-type': 'application/json',
      authorization: `Bearer ${config.apiKey}`,
    },
    body,
  };
}

/** Junta os blocos de texto de uma resposta da Messages API. */
function anthropicText(content) {
  if (!Array.isArray(content)) return '';
  return content
    .filter((block) => block && block.type === 'text' && typeof block.text === 'string')
    .map((block) => block.text)
    .join('');
}

/**
 * Normaliza a resposta para um formato único.
 *
 * Devolve sempre {text, parsed, usage:{input,output,cached}, finishReason,
 * refusal}. `parsed` só vem quando havia schema e o texto era JSON válido —
 * texto que não parseia devolve parsed null em vez de estourar, para o fluxo
 * chamador decidir o que fazer.
 */
function normalizeResponse(provider, raw) {
  if (provider === ANTHROPIC) {
    const usage = raw.usage || {};
    return {
      text: anthropicText(raw.content),
      parsed: null,
      usage: {
        input: usage.input_tokens || 0,
        output: usage.output_tokens || 0,
        cached: usage.cache_read_input_tokens || 0,
      },
      finishReason: raw.stop_reason || null,
      // Classificador de segurança pode recusar com HTTP 200. Ler content
      // sem checar isso produziria uma ficha vazia sem explicação.
      refusal: raw.stop_reason === 'refusal',
      model: raw.model || null,
    };
  }

  const choice = (raw.choices && raw.choices[0]) || {};
  const usage = raw.usage || {};
  return {
    text: (choice.message && choice.message.content) || '',
    parsed: null,
    usage: {
      input: usage.prompt_tokens || 0,
      output: usage.completion_tokens || 0,
      cached: (usage.prompt_tokens_details && usage.prompt_tokens_details.cached_tokens) || 0,
    },
    finishReason: choice.finish_reason || null,
    refusal: false,
    model: raw.model || null,
  };
}

/** Tenta parsear a saída como JSON, tolerando cerca de bloco de código. */
function parseJsonOutput(text) {
  if (typeof text !== 'string' || !text.trim()) return null;
  const cleaned = text.trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
  try {
    return JSON.parse(cleaned);
  } catch {
    return null;
  }
}

/**
 * Custo em USD da chamada.
 *
 * Modelo local custa zero de verdade. Modelo remoto fora da tabela devolve
 * `known: false` — o chamador precisa saber que o valor é incerto, senão o
 * teto de orçamento vira ficção.
 */
function computeCost(config, usage) {
  if (isLocal(config.baseUrl)) {
    return { usd: 0, known: true, reason: 'modelo local' };
  }

  const price = PRICING[config.model];
  if (!price) {
    return { usd: 0, known: false, reason: `preco desconhecido para ${config.model}` };
  }

  const uncachedInput = Math.max(0, (usage.input || 0) - (usage.cached || 0));
  const usd =
    (uncachedInput * price.input +
      (usage.cached || 0) * price.input * CACHE_READ_MULTIPLIER +
      (usage.output || 0) * price.output) /
    1e6;

  return { usd: Number(usd.toFixed(6)), known: true, reason: null };
}

/**
 * Decide, **antes** de chamar, se a chamada pode prosseguir.
 *
 * A decisão não pode depender do custo da própria chamada: esse número só
 * existe depois da resposta. O que dá para saber de antemão é se o modelo é
 * local, se ele tem preço conhecido, e quanto já foi gasto.
 *
 * Modelo remoto sem preço na tabela é barrado. Deixar passar transformaria o
 * teto em ficção — gastaria de verdade e contabilizaria zero.
 */
function canProceed(config, { spentUsd, limitUsd }) {
  if (isLocal(config.baseUrl)) {
    return { allowed: true, reason: null, billable: false };
  }
  if (!PRICING[config.model]) {
    return {
      allowed: false,
      billable: true,
      reason:
        `modelo remoto sem preco conhecido: ${config.model}. ` +
        `Adicione a PRICING em services/gateway/gateway.js antes de usar, ` +
        `senao o teto de orcamento nao consegue contabilizar o gasto.`,
    };
  }
  if (spentUsd >= limitUsd) {
    return {
      allowed: false,
      billable: true,
      reason: `teto de orcamento atingido: US$ ${spentUsd.toFixed(4)} de US$ ${limitUsd.toFixed(2)}`,
    };
  }
  return { allowed: true, reason: null, billable: true };
}

module.exports = {
  ANTHROPIC,
  OPENAI,
  PRICING,
  resolveConfig,
  buildRequest,
  normalizeResponse,
  parseJsonOutput,
  computeCost,
  canProceed,
  isLocal,
};
