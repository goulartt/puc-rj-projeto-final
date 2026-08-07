# Operação — endereços, observabilidade e troca de modelo

Onde as coisas ficam, como ver o que o bot está fazendo e como trocar o modelo
sem editar fluxo nenhum.

## Endereços

| O quê | URL | Autenticação |
|---|---|---|
| Editor do n8n | `http://localhost:5678` | conta de dono do n8n |
| Serviço de documentos | `http://localhost:5001` | nenhuma (só rede local) |
| Postgres | `localhost:5434`, base `leilao` | usuário `leilao` |
| Ollama (host) | `http://localhost:11434` | nenhuma |
| Webhook público | valor de `WEBHOOK_URL` no `.env` | — |
| Bot | [@LeilaoImovelAnaliseBot](https://t.me/LeilaoImovelAnaliseBot) | — |

A porta do Postgres é 5434, e não 5433, porque 5433 já estava ocupada por outro
contêiner na máquina de desenvolvimento.

### ⚠️ O túnel expõe o editor, não só o webhook

O túnel do Cloudflare publica o n8n inteiro. **Enquanto a conta de dono não for
criada, qualquer pessoa com a URL abre o editor, lê os fluxos e usa as
credenciais salvas** — token do bot, chave do provedor de LLM, chave do DataJud.

Abra `http://localhost:5678` **pelo localhost** e crie a conta antes de publicar
o túnel pela primeira vez.

A URL muda a cada reinício do túnel — é um domínio sorteado. `./scripts/expose-bot.sh`
gera a nova, grava no `.env`, recria o n8n e registra o webhook no Telegram, nessa
ordem, que não é opcional: fora dela o Telegram fica apontando para um domínio
morto e a mensagem não chega, sem erro visível em lugar nenhum.

## Ver o que o bot está fazendo

Três camadas, com finalidades diferentes.

### n8n → aba Executions

É o log mais rico e o lugar de depurar. Uma entrada por mensagem recebida, com
o payload cru do Telegram — `chat.id`, `username`, `first_name`, texto — e a
entrada e a saída de cada nó.

**Uma execução com status `crashed` quase nunca é falta de memória**, apesar da
mensagem que o n8n mostra ("n8n may have run out of memory"). Ele marca assim
toda execução que estava em voo quando o processo morreu — e a causa mais comum
é um `docker compose restart n8n` durante a execução. Antes de investigar
memória, compare o horário da falha com
`docker inspect -f '{{.State.StartedAt}}' leilao-assistente-n8n-1`.

### Postgres — o histórico durável

```bash
psql() { docker compose exec -T postgres psql -U leilao -d leilao "$@"; }

# editais recebidos, por conversa
psql -c "SELECT chat_id, file_name, created_at FROM auction_notices ORDER BY created_at DESC;"

# custo e falhas, chamada a chamada
psql -c "SELECT role, chat_id, model, input_tokens, output_tokens, cost_usd, error, created_at
           FROM llm_calls ORDER BY id DESC LIMIT 20;"

psql -c "SELECT * FROM budget_spent;"      # total gasto contra BUDGET_USD_LIMIT
psql -c "SELECT * FROM usage_by_chat;"     # uso e custo por conversa
psql -c "SELECT * FROM llm_call_health;"   # taxa de erro por modelo
```

`llm_calls` registra **também as chamadas que falharam**, com a causa na coluna
`error`. Sem isso, uma falha (custo zero, tokens zero) ficaria indistinguível de
uma chamada local bem-sucedida.

### Quanto tempo cada etapa leva

```bash
psql -c "SELECT * FROM llm_call_health;"   # média, pico e tokens/s por modelo
psql -c "SELECT role, model, duration_ms, output_tokens, cost_usd
           FROM llm_calls ORDER BY id DESC LIMIT 10;"
```

`duration_ms` mede a chamada ao provedor, que é onde praticamente todo o
relógio está. Medição de 06/08/2026, edital de exemplo:

| Etapa | Tempo | Fatia |
|---|---|---|
| Aviso de recebimento | 0,9 s | — |
| Conversão do PDF (Docling) | 3,9 s | 2,6% |
| **Extração da ficha (DeepSeek)** | **147 s** | **97,4%** |
| Consulta processual, tradução, envio | < 0,1 s | ~0% |
| Pergunta no Q&A (qwen3:14b local) | 13–33 s | — |

O modelo é o gargalo, e nada mais chega perto: os nós de código, as consultas
ao Postgres e as chamadas ao serviço de documentos somam menos de 100 ms.
Otimizar qualquer coisa que não seja a chamada ao modelo não muda nada.

A variação entre extrações — 147 s contra 236 s no mesmo edital — vem do número
de tokens de saída, não do tamanho do PDF: o modelo de raciocínio decide quanto
pensar. `output_tokens_per_second` na view separa as duas coisas.

### As mensagens que o bot manda durante a ingestão

| Quando | O quê |
|---|---|
| ~1 s | "Recebi o edital — <nome>." |
| ~5 s | "Documento lido — N páginas. Agora estou extraindo…" |
| ~110 s | a ficha |

Não há um terceiro aviso por tempo decorrido, e não é esquecimento: o nó Wait
do n8n suspende a **execução inteira**, não um ramo. Um "avise se passar de um
minuto" atrasaria em um minuto a própria extração que ele deveria acompanhar.

Os dois avisos saem de marcos reais do processamento — o recebimento e o fim da
conversão do PDF —, e por isso conseguem dizer algo além de "aguarde". Para um
aviso genuinamente cronometrado seria preciso um segundo fluxo em Schedule
Trigger varrendo execuções em andamento, o que não se pagou aqui.

### Logs dos contêineres

```bash
docker compose logs -f n8n        # ativação, webhook, erros de infraestrutura
docker compose logs -f docling    # conversão de PDF e tempo gasto
```

## Trocar de modelo

Tudo por `.env`, sem tocar em fluxo. `role` é `extraction` (uma vez por edital,
cara) ou `qa` (por pergunta, barata).

```bash
# Q&A no DeepSeek em vez do modelo local
LLM_QA_PROVIDER=openai
LLM_QA_MODEL=deepseek-v4-flash
LLM_QA_BASE_URL=https://api.deepseek.com
LLM_QA_API_KEY=<chave>

# Extração no Claude
LLM_EXTRACTION_PROVIDER=anthropic
LLM_EXTRACTION_MODEL=claude-sonnet-5
LLM_EXTRACTION_BASE_URL=https://api.anthropic.com
LLM_EXTRACTION_API_KEY=sk-ant-...
LLM_EXTRACTION_STRUCTURED=schema
```

Depois:

```bash
docker compose up -d n8n     # e NÃO `restart`
```

`docker compose restart` mantém o ambiente antigo do contêiner. A variável nova
não chega, o fluxo segue usando o modelo anterior e nada avisa.

### Duas armadilhas

**Modelo remoto sem preço é bloqueado.** O gateway recusa a chamada e diz o que
fazer. Não é bug: sem preço conhecido o teto de orçamento viraria ficção —
gastaria de verdade e contabilizaria zero. Modelos já precificados em
`services/gateway/gateway.js`:

`claude-opus-5` · `claude-opus-4-8` · `claude-sonnet-5` · `claude-haiku-4-5` ·
`deepseek-v4-flash` · `deepseek-v4-pro`

Modelo local (`localhost`, `host.docker.internal`, `ollama`) nunca é bloqueado,
porque não há o que contabilizar.

**`LLM_EXTRACTION_STRUCTURED` tem três níveis** porque os provedores diferem de
verdade. Só existe para a extração: a pergunta não pede saída estruturada, e
não há `LLM_QA_STRUCTURED`.

| Valor | Para quem | O que acontece se errar |
|---|---|---|
| `schema` | Anthropic | DeepSeek responde 400: "This response_format type is unavailable now" |
| `json` | DeepSeek e compatíveis | — |
| `none` | Ollama | com `schema`, falha em "failed to parse grammar" acima de ~200 tokens |

### Testar a troca sem envolver o Telegram

```bash
run() { docker compose exec -e N8N_RUNNERS_BROKER_PORT=5699 n8n n8n execute --id="$1"; }

run chatsmoke00000001   # chat: roteamento, escopo, Q&A
run ingestsmoke00001    # edital → ficha (gasta token do provedor de extração)
run lookupsmoke00001    # DataJud
run gatewaysmoke00001   # gateway isolado
```

`N8N_RUNNERS_BROKER_PORT=5699` evita conflito de porta com a instância que já
está rodando.

## Publicar um fluxo alterado

```bash
python3 scripts/build-workflows.py
docker cp workflows/01-telegram-chat.json leilao-assistente-n8n-1:/tmp/w.json
docker compose exec n8n n8n import:workflow --input=/tmp/w.json
docker compose exec n8n n8n publish:workflow --id=telegramchat0001
docker compose exec n8n n8n update:workflow --id=telegramchat0001 --active=true
docker compose restart n8n
```

Três detalhes que custaram tempo para descobrir:

- **`import` zera a flag de ativação.** Sem reativar depois, o bot fica mudo.
- **Sub-fluxo precisa de `publish`** para ser chamável, e o fluxo de chat precisa
  dele antes de a ativação ser sequer tentada.
- **Reiniciar mata execução em andamento**, que aparece como `crashed` com a
  mensagem enganosa de memória. Se alguém estiver usando o bot, espere:
  `SELECT count(*) FROM execution_entity WHERE status='running'` no banco do n8n.

## Limpar dados de uma conversa

A pessoa faz isso pelo `/apagar`. Manualmente:

```bash
docker compose exec -T postgres psql -U leilao -d leilao \
  -c "DELETE FROM auction_notices WHERE chat_id = '<id>';"
```

O `chat_id` do Telegram é um identificador direto de pessoa. Ele aparece nos
logs e no banco por necessidade operacional — ver [`privacy.md`](privacy.md).
