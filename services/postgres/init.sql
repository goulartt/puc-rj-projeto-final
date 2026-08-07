-- Esquema da aplicação. Roda uma vez, na primeira subida do container.

-- ─── Editais processados (Estágio 1) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS auction_notices (
    id            BIGSERIAL PRIMARY KEY,
    chat_id       TEXT        NOT NULL,
    file_name     TEXT,
    sha256        TEXT        NOT NULL,
    markdown      TEXT        NOT NULL,  -- saída do Docling
    deterministic JSONB       NOT NULL,  -- extratores regex (1a)
    analysis      JSONB       NOT NULL,  -- ficha validada contra o schema (1b)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Reprocessar o mesmo PDF no mesmo chat substitui a ficha em vez de duplicar
    UNIQUE (chat_id, sha256)
);

-- Último edital de cada chat: é o contexto padrão das perguntas do Estágio 3
CREATE INDEX IF NOT EXISTS auction_notices_recent ON auction_notices (chat_id, created_at DESC);

-- ─── Consultas processuais (Estágio 2) ──────────────────────────────────────
-- Edital convertido e à espera de a pessoa dizer qual imóvel quer analisar.
--
-- Existe porque a confirmação parte a ingestão em duas mensagens do Telegram, e
-- cada execução do n8n é independente: sem isto, o Markdown já convertido se
-- perderia entre a pergunta e a resposta, e a pessoa teria de reenviar o PDF.
--
-- Guarda o Markdown, e não o PDF: a conversão já foi paga e não se repete.
-- Uma pendência por conversa — mandar um segundo edital antes de responder
-- substitui o primeiro, que é o que a pessoa espera ao trocar de documento.
CREATE TABLE IF NOT EXISTS pending_notices (
    chat_id       TEXT        PRIMARY KEY,
    file_name     TEXT        NOT NULL,
    sha256        TEXT        NOT NULL,
    markdown      TEXT        NOT NULL,
    deterministic JSONB       NOT NULL,
    lots          JSONB       NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS case_lookups (
    cnj_number  TEXT        PRIMARY KEY,  -- formato NNNNNNN-DD.AAAA.J.TR.OOOO
    court_alias TEXT        NOT NULL,     -- índice DataJud, ex.: api_publica_tjrj
    -- 'ok' | 'not_found' | 'sealed' | 'out_of_coverage' | 'error'
    status      TEXT        NOT NULL,
    movements   JSONB,                    -- resposta crua do DataJud
    summary     JSONB,                    -- risco sintetizado pelo LLM
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Contabilidade de custo — é o que sustenta o teto do slide 8 ────────────
CREATE TABLE IF NOT EXISTS llm_calls (
    id            BIGSERIAL PRIMARY KEY,
    role          TEXT          NOT NULL,  -- 'extraction' | 'qa'
    -- Conversa que originou a chamada. Sem isto da para saber quanto se gastou,
    -- mas nao com quem — e uma pergunta cara de um usuario fica indistinguivel
    -- do uso normal de outro.
    chat_id       TEXT,
    provider      TEXT          NOT NULL,  -- 'anthropic' | 'openai'
    model         TEXT          NOT NULL,
    input_tokens  INTEGER       NOT NULL DEFAULT 0,
    output_tokens INTEGER       NOT NULL DEFAULT 0,
    cached_tokens INTEGER       NOT NULL DEFAULT 0,
    cost_usd      NUMERIC(10,6) NOT NULL DEFAULT 0,
    -- Tempo da chamada ao provedor. Praticamente todo o relogio do fluxo esta
    -- aqui: a conversao do PDF leva ~4s e o modelo, minutos.
    duration_ms   INTEGER,
    -- Chamada que falhou também é registrada: saber que houve tentativa importa.
    -- Sem esta coluna, uma falha (custo zero, tokens zero) fica indistinguível
    -- de uma chamada local bem-sucedida no mesmo log.
    error         TEXT,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS llm_calls_created ON llm_calls (created_at DESC);

-- O gateway consulta esta view antes de cada chamada e recusa se o acumulado
-- passar de BUDGET_USD_LIMIT. Modelo local (custo zero) nunca bloqueia.
CREATE OR REPLACE VIEW budget_spent AS
    SELECT COALESCE(SUM(cost_usd), 0)::NUMERIC(10,6) AS total_usd
      FROM llm_calls;

-- Taxa de erro por modelo, para a avaliação da Fase 7.
-- Uso por conversa: quantas chamadas, quanto custou, quando foi a ultima.
CREATE OR REPLACE VIEW usage_by_chat AS
    SELECT chat_id,
           count(*)                                  AS calls,
           count(*) FILTER (WHERE error IS NOT NULL) AS failures,
           round(SUM(cost_usd), 6)                   AS cost_usd,
           max(created_at)                           AS last_call
      FROM llm_calls
     WHERE chat_id IS NOT NULL
     GROUP BY chat_id
     ORDER BY cost_usd DESC;

DROP VIEW IF EXISTS llm_call_health;
CREATE VIEW llm_call_health AS
    SELECT provider, model, role,
           count(*)                                  AS calls,
           count(*) FILTER (WHERE error IS NOT NULL) AS failures,
           -- Quantas foram cronometradas: linhas anteriores a `duration_ms`
           -- ficam de fora das medias, e a contagem torna isso visivel.
           count(*) FILTER (WHERE duration_ms IS NOT NULL) AS timed,
           round(SUM(cost_usd), 6)                   AS cost_usd,
           round(avg(duration_ms) / 1000.0, 1)       AS avg_seconds,
           round(max(duration_ms) / 1000.0, 1)       AS max_seconds,
           -- Tokens de saida por segundo: e o que explica a diferenca entre
           -- uma extracao de 2 e uma de 4 minutos no mesmo modelo.
           -- Só as linhas cronometradas entram no cálculo. Somar os tokens de
           -- todas e dividir pelo tempo de algumas produz número impossível:
           -- a primeira versão desta view reportou 4260 tokens/s.
           round(SUM(output_tokens) FILTER (WHERE duration_ms IS NOT NULL) /
                 NULLIF(SUM(duration_ms) / 1000.0, 0), 1) AS output_tokens_per_second
      FROM llm_calls
     GROUP BY provider, model, role;

-- ─── Deduplicação do scrape (iteração 2) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS scraped_listings (
    listing_url TEXT        PRIMARY KEY,
    chat_id     TEXT        NOT NULL,
    payload     JSONB,
    notified_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
