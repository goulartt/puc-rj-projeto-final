-- Esquema da aplicação. Roda uma vez, na primeira subida do container.

-- ─── Editais processados (Estágio 1) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS editais (
    id           BIGSERIAL PRIMARY KEY,
    chat_id      TEXT        NOT NULL,
    file_name    TEXT,
    sha256       TEXT        NOT NULL,
    markdown     TEXT        NOT NULL,   -- saída do Docling
    deterministic JSONB      NOT NULL,   -- extratores regex (1a)
    ficha        JSONB       NOT NULL,   -- ficha validada contra o schema (1b)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Reprocessar o mesmo PDF no mesmo chat substitui a ficha em vez de duplicar
    UNIQUE (chat_id, sha256)
);

-- Último edital de cada chat: é o contexto padrão das perguntas do Estágio 3
CREATE INDEX IF NOT EXISTS editais_chat_recente ON editais (chat_id, created_at DESC);

-- ─── Consultas processuais (Estágio 2) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS processo_lookups (
    numero_cnj  TEXT        PRIMARY KEY,   -- formato NNNNNNN-DD.AAAA.J.TR.OOOO
    tribunal    TEXT        NOT NULL,      -- alias DataJud, ex.: api_publica_tjrj
    -- 'ok' | 'nao_encontrado' | 'segredo_de_justica' | 'fora_de_cobertura' | 'erro'
    status      TEXT        NOT NULL,
    movimentos  JSONB,                     -- resposta crua do DataJud
    resumo      JSONB,                     -- risco sintetizado pelo LLM
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Contabilidade de custo — é o que sustenta o teto do slide 8 ────────────
CREATE TABLE IF NOT EXISTS llm_calls (
    id            BIGSERIAL PRIMARY KEY,
    role          TEXT        NOT NULL,    -- 'extraction' | 'qa'
    provider      TEXT        NOT NULL,    -- 'anthropic' | 'openai'
    model         TEXT        NOT NULL,
    input_tokens  INTEGER     NOT NULL DEFAULT 0,
    output_tokens INTEGER     NOT NULL DEFAULT 0,
    cached_tokens INTEGER     NOT NULL DEFAULT 0,
    cost_usd      NUMERIC(10,6) NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS llm_calls_created ON llm_calls (created_at DESC);

-- O gateway consulta esta view antes de cada chamada e recusa se o acumulado
-- passar de BUDGET_USD_LIMIT. Modelo local (custo zero) nunca bloqueia.
CREATE OR REPLACE VIEW budget_spent AS
    SELECT COALESCE(SUM(cost_usd), 0)::NUMERIC(10,6) AS total_usd
      FROM llm_calls;

-- ─── Deduplicação do scrape (iteração 2) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS scrape_seen (
    lote_url    TEXT        PRIMARY KEY,
    chat_id     TEXT        NOT NULL,
    payload     JSONB,
    notified_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
