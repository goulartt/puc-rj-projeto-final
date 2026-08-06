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

// Os dois valores nomeiam **protocolos HTTP**, não empresas: `anthropic` é o
// formato da Messages API, `openai` é o formato `chat/completions`, que
// DeepSeek, Ollama, OpenRouter, Together, Groq e vLLM também falam.
const ANTHROPIC = 'anthropic';
const OPENAI = 'openai';

// Quanto de estrutura o provedor consegue impor por conta própria. Em todos os
// casos quem garante o contrato é a validação posterior; isto só decide o que
// pedimos ao provedor, e o que precisa ir no texto do prompt.
const STRUCTURED_SCHEMA = 'schema';  // schema completo, decodificação restrita
const STRUCTURED_JSON = 'json';      // só "responda JSON", sem estrutura
const STRUCTURED_NONE = 'none';      // nada; o schema vai no prompt

const STRUCTURED_MODES = [STRUCTURED_SCHEMA, STRUCTURED_JSON, STRUCTURED_NONE];

// Quanto o modelo pensa antes de responder. `none` desliga.
//
// Medido na extração do edital de exemplo: 14.366 dos 18.684 tokens de saída
// eram raciocínio — 77% do tempo gasto pensando, não escrevendo a ficha. Num
// trabalho de leitura e transcrição, onde o bloco determinístico já entrega os
// números conferidos, boa parte desse esforço é redundante.
//
// Não é um botão de "ficar mais rápido de graça": o efeito na qualidade é
// medido em docs/evidence/, não presumido.
const REASONING_NONE = 'none';

// Chamar de "openai" um endpoint da DeepSeek confunde quem lê o `.env`, então
// aceitamos apelidos que dizem a mesma coisa com nomes menos enganosos.
const PROVIDER_ALIASES = {
  anthropic: ANTHROPIC,
  claude: ANTHROPIC,
  openai: OPENAI,
  'openai-compatible': OPENAI,
  deepseek: OPENAI,
  ollama: OPENAI,
  openrouter: OPENAI,
  groq: OPENAI,
  together: OPENAI,
  vllm: OPENAI,
};

/**
 * Preço por milhão de tokens, em USD. Conferido em 06/08/2026.
 *
 * `cachedInput` é o preço de token lido do cache, e é **por modelo**: a
 * Anthropic cobra ~10% da entrada, a DeepSeek cobra 2%. Uma constante única
 * faria a contabilidade errar em um dos dois, e o teto de orçamento depende
 * desse número estar certo.
 *
 * Só entram modelos cujo preço foi verificado. Modelo ausente da tabela não
 * vira custo zero: `canProceed` o barra, para o teto não ser furado em
 * silêncio por um modelo que ninguém precificou.
 */
const PRICING = {
  'claude-opus-5': { input: 5, output: 25, cachedInput: 0.5 },
  'claude-opus-4-8': { input: 5, output: 25, cachedInput: 0.5 },
  'claude-sonnet-5': { input: 3, output: 15, cachedInput: 0.3 },
  'claude-haiku-4-5': { input: 1, output: 5, cachedInput: 0.1 },

  // DeepSeek — a documentação avisa que os preços vão subir "significativamente
  // em breve". Reconferir antes de confiar no custo projetado.
  'deepseek-v4-flash': { input: 0.14, output: 0.28, cachedInput: 0.0028 },
  'deepseek-v4-pro': { input: 0.435, output: 0.87, cachedInput: 0.003625 },
};

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
  const declared = (env[`${prefix}PROVIDER`] || '').trim().toLowerCase();
  const provider = PROVIDER_ALIASES[declared] || declared;

  if (provider !== ANTHROPIC && provider !== OPENAI) {
    throw new Error(
      `${prefix}PROVIDER invalido: ${JSON.stringify(declared)}. ` +
        `Valores aceitos: ${Object.keys(PROVIDER_ALIASES).sort().join(', ')}.`,
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
 * `system` é string única; `messages` é [{role, content}].
 *
 * `schema` pede **decodificação restrita** ao provedor. É otimização, não
 * garantia: quem garante o formato é a validação que roda depois, no serviço
 * de documentos. Nem todo provedor dá conta — o Ollama compila o schema numa
 * gramática GBNF e falha com "failed to parse grammar" em schemas do tamanho
 * do nosso, para qualquer geração acima de ~200 tokens. Por isso o campo é
 * opcional e o pipeline continua correto sem ele.
 *
 * `cacheSystem` marca o bloco de sistema para cache — é o que torna barato
 * repetir o mesmo prompt de analista entre editais.
 */
function buildRequest(config, {
  system, messages, schema, maxTokens = 4096, cacheSystem = false,
  structuredMode = STRUCTURED_SCHEMA, reasoning = null,
}) {
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
    // A Messages API aceita schema completo; não há razão para degradar aqui.
    if (schema && structuredMode !== STRUCTURED_NONE) {
      body.output_config = { format: { type: 'json_schema', schema } };
    }
    // Nos modelos Claude atuais o raciocínio é ligado por `thinking: adaptive`,
    // e a ausência do campo é o desligado. `budget_tokens` foi removido: os
    // modelos da geração 5 respondem 400 se ele vier.
    if (reasoning && reasoning !== REASONING_NONE) {
      body.thinking = { type: 'adaptive' };
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

  if (structuredMode === STRUCTURED_SCHEMA && schema) {
    body.response_format = {
      type: 'json_schema',
      json_schema: { name: 'output', strict: true, schema },
    };
  } else if (structuredMode === STRUCTURED_JSON) {
    // Só garante JSON sintático, não a estrutura. É o máximo que a DeepSeek
    // oferece hoje: `json_schema` responde
    // "This response_format type is unavailable now".
    body.response_format = { type: 'json_object' };
  }
  // Verificado contra a API da DeepSeek: `none` zera de fato o raciocínio
  // (27,5s → 3,1s no mesmo prompt), e `minimal` é ignorado em silêncio — que é
  // o comportamento padrão de uma API compatível com OpenAI diante de um valor
  // que ela não conhece. Por isso o campo passa adiante o que foi pedido, sem
  // traduzir: inventar um mapeamento esconderia esse silêncio.
  if (reasoning) body.reasoning_effort = reasoning;

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

  // `input` já inclui os tokens lidos do cache; só a diferença paga preço cheio.
  const uncachedInput = Math.max(0, (usage.input || 0) - (usage.cached || 0));
  const usd =
    (uncachedInput * price.input +
      (usage.cached || 0) * price.cachedInput +
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
  REASONING_NONE,
  STRUCTURED_SCHEMA,
  STRUCTURED_JSON,
  STRUCTURED_NONE,
  STRUCTURED_MODES,
  isLocal,
};
