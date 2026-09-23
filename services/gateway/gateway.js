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
// formato da Messages API, `openai` é o formato `chat/completions`, que a
// OpenRouter fala — e, por trás dela, DeepSeek, Qwen, Kimi e o resto do
// catálogo. O provedor padrão do projeto é a OpenRouter; a Messages API segue
// disponível para quem quiser falar com a Anthropic direto.
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

// Chamar de "openai" o endpoint da OpenRouter confunde quem lê o `.env`, então
// aceitamos apelidos que dizem a mesma coisa com nomes menos enganosos.
const PROVIDER_ALIASES = {
  anthropic: ANTHROPIC,
  claude: ANTHROPIC,
  openai: OPENAI,
  'openai-compatible': OPENAI,
  openrouter: OPENAI,
};

/**
 * Preço por milhão de tokens, em USD, para provedores que **não** informam o
 * custo na resposta. Conferido em 06/08/2026.
 *
 * Com a OpenRouter esta tabela não é consultada: ela devolve em `usage.cost` o
 * valor efetivamente cobrado, e esse número vale mais que qualquer tabela — o
 * preço de um mesmo modelo muda conforme o provedor que ela escolhe por trás.
 * `deepseek/deepseek-v4-flash` apareceu no catálogo com saída a US$ 0,131/MTok,
 * e as variantes datadas do mesmo modelo, entre US$ 0,55 e 0,64.
 *
 * A tabela sobra para a Messages API da Anthropic, chamada direto. Modelo sem
 * preço aqui e num provedor que não informa custo é barrado por `canProceed`,
 * para o teto de orçamento não ser furado em silêncio.
 */
const PRICING = {
  'claude-opus-5': { input: 5, output: 25, cachedInput: 0.5 },
  'claude-opus-4-8': { input: 5, output: 25, cachedInput: 0.5 },
  'claude-sonnet-5': { input: 3, output: 15, cachedInput: 0.3 },
  'claude-haiku-4-5': { input: 1, output: 5, cachedInput: 0.1 },
};

/** Hosts que devolvem o custo cobrado em `usage.cost`. */
const REPORTS_COST = ['openrouter.ai'];

/**
 * Extrai o host de uma URL sem depender do global `URL`.
 *
 * O sandbox do nó de código do n8n não expõe `URL`. Uma versão anterior usava
 * `new URL()` dentro de try/catch: o ReferenceError era engolido em silêncio e
 * a classificação de endereço errava sem avisar. Regex não tem essa
 * dependência.
 */
function hostOf(baseUrl) {
  const match = /^[a-z][a-z0-9+.-]*:\/\/(?:[^@/]*@)?(\[[^\]]+\]|[^:/?#]+)/i.exec(
    String(baseUrl || '').trim(),
  );
  if (!match) return '';
  return match[1].replace(/^\[|\]$/g, '').toLowerCase();
}

/** O provedor informa quanto cobrou? Então não precisamos de tabela de preço. */
function reportsCost(config) {
  return REPORTS_COST.includes(hostOf(config.baseUrl));
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
 * de documentos. Nem todo provedor dá conta — a API direta da DeepSeek
 * recusava `json_schema`, e um modelo local não compilava um schema do nosso
 * tamanho. Por isso o campo é opcional e o pipeline continua correto sem ele.
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

  // OPENAI: o formato `chat/completions`, que é o da OpenRouter.
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
    // Só garante JSON sintático, não a estrutura. Serve de degrau quando o
    // modelo por trás não aceita `json_schema`.
    body.response_format = { type: 'json_object' };
  }
  // `reasoning_effort` consta dos parâmetros aceitos pelos modelos DeepSeek no
  // catálogo da OpenRouter, que o repassa ao provedor. Medido contra a
  // DeepSeek: `none` zera o raciocínio (27,5s → 3,1s num prompt de controle) e
  // `minimal` o reduz a um quinto.
  if (reasoning) body.reasoning_effort = reasoning;

  // `cacheSystem` não tem equivalente aqui e é ignorado de propósito: o cache
  // de prefixo da DeepSeek é automático, e já lia 12.288 de 12.291 tokens de
  // entrada na extração.

  const headers = {
    'content-type': 'application/json',
    authorization: `Bearer ${config.apiKey}`,
  };
  // Identificação opcional da aplicação no painel da OpenRouter: separa o gasto
  // deste projeto do de outros que usem a mesma conta.
  if (hostOf(config.baseUrl) === 'openrouter.ai') {
    headers['http-referer'] = 'https://github.com/goulartt/puc-rj-projeto-final';
    headers['x-title'] = 'Arremata AI';
  }

  return { url: `${config.baseUrl}/v1/chat/completions`, headers, body };
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
 * Remove o raciocínio que veio no corpo da resposta.
 *
 * O normal é o provedor separar: a OpenRouter devolve `message.reasoning` e
 * deixa `content` limpo, e a Messages API manda o raciocínio em bloco próprio.
 * Mas a separação depende de o modelo abrir o bloco com `<think>`, e isso é
 * geração, não protocolo — observado em produção, uma resposta veio com um
 * token de lixo (`栋`) no lugar da abertura, o provedor não reconheceu o bloco,
 * e o raciocínio inteiro — em inglês, discutindo o que responder — foi entregue
 * à pessoa como se fosse a resposta.
 *
 * O fechamento sobreviveu, e é nele que dá para confiar: o que estiver antes
 * de um `</think>` é raciocínio, tenha a abertura casado ou não.
 */
function stripReasoning(text) {
  if (typeof text !== 'string' || !text) return text;

  const closing = text.toLowerCase().lastIndexOf('</think>');
  const cleaned = (closing >= 0
    ? text.slice(closing + '</think>'.length)
    // Sem fechamento: ou nao ha raciocinio nenhum, ou a resposta foi cortada
    // no meio dele. Uma abertura solta significa o segundo caso, e o que vem
    // depois dela e raciocinio ate onde o texto acabou.
    : text.replace(/<think\b[^>]*>[\s\S]*$/i, '')
  ).trim();

  // Resposta truncada dentro do raciocínio nao deixa nada depois do
  // fechamento. Devolver o raciocínio seria o defeito de novo, e devolver
  // texto vazio deixa o fluxo chamador tratar como resposta que nao veio.
  return cleaned;
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
  const normalizedUsage = {
    input: usage.prompt_tokens || 0,
    output: usage.completion_tokens || 0,
    cached: (usage.prompt_tokens_details && usage.prompt_tokens_details.cached_tokens) || 0,
  };
  // O que a OpenRouter efetivamente cobrou. Só entra quando é número: ausência
  // não pode virar custo zero, senão o teto de orçamento para de contar.
  if (typeof usage.cost === 'number') normalizedUsage.costUsd = usage.cost;

  return {
    text: stripReasoning((choice.message && choice.message.content) || ''),
    parsed: null,
    usage: normalizedUsage,
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
 * Quando o provedor informa o valor cobrado, vale ele — é o número da fatura,
 * e não uma estimativa. Sem isso, a tabela de preços. Modelo fora da tabela
 * devolve `known: false`: o chamador precisa saber que o valor é incerto,
 * senão o teto de orçamento vira ficção.
 */
function computeCost(config, usage) {
  if (typeof usage.costUsd === 'number') {
    return { usd: Number(usage.costUsd.toFixed(6)), known: true, reason: 'informado pelo provedor' };
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
 * existe depois da resposta. O que dá para saber de antemão é se o custo vai
 * poder ser contado — o provedor informa, ou o modelo está na tabela — e
 * quanto já foi gasto.
 *
 * Modelo sem preço num provedor que não informa custo é barrado. Deixar passar
 * transformaria o teto em ficção — gastaria de verdade e contabilizaria zero.
 */
function canProceed(config, { spentUsd, limitUsd }) {
  if (!reportsCost(config) && !PRICING[config.model]) {
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
  reportsCost,
};
