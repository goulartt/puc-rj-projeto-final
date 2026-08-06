# Arquitetura

## Por que dois estágios

O desenho ingênuo de um chatbot sobre documento é reenviar o documento inteiro
a cada pergunta. Isso faz o custo crescer com `perguntas × tamanho_do_edital` e
produz respostas que não conseguem apontar de onde saiu cada afirmação.

A separação abaixo paga o token caro uma vez e deixa as perguntas baratas:

```
ESTÁGIO 1 — uma vez por edital
  PDF → Docling → Markdown (~30k tokens)
    ├─ 1a. extratores determinísticos (regex + validação)
    └─ 1b. LLM forte, recebendo 1a como dica → ficha.json com trecho citado

ESTÁGIO 2 — condicional, se houver número de processo válido
  DataJud (CNJ) → movimentos processuais → resumo de risco

ESTÁGIO 3 — N vezes por edital
  pergunta + ficha (~3k tokens) + glossário → LLM barato → resposta com citação
```

Três consequências que valem mais que a economia:

1. **Rastreabilidade.** Cada campo da ficha carrega o trecho do edital que o
   sustenta, então toda resposta pode citar a cláusula.
2. **A ficha é entregável.** Serve fora do chat.
3. **O modelo pequeno vira viável.** O Estágio 3 é leitura de JSON curto, não
   compreensão de documento jurídico denso — que é onde um 8B falharia.

## Determinístico antes do LLM

Os extratores de regex rodam **antes** do modelo e servem a dois propósitos:
alimentam o prompt como dica e **conferem** a saída dele. A divergência entre
os dois vira `confianca: baixa` na ficha, nunca um desempate silencioso.

O número de processo é o caso mais forte: o dígito verificador módulo 97
(ISO 7064, Res. CNJ 65/2008) separa "sequência de 20 dígitos" de "processo
real". Sem essa validação, um código de barras viraria um número de processo.
Com ela, o campo fica confiável o bastante para consultar uma fonte externa.

## Serviços

| Serviço | Papel | Porta local |
|---|---|---|
| `n8n` | orquestração dos fluxos | 5678 |
| `docling` | PDF → Markdown | 5001 |
| `postgres` | fichas, custo de LLM, dedupe do scrape | 5434 |
| `cloudflared` | URL HTTPS pública para o webhook do Telegram | — |
| `ollama` | LLM local do Estágio 3 (profile `local-llm`) | 11434 |

### Decisões de infraestrutura, e por quê

**O n8n 2.x removeu a flag `--tunnel`.** O `n8n start` só aceita `-h` e `-o`;
a flag é silenciosamente ignorada. Trocamos por um *quick tunnel* do
Cloudflare, que não exige conta e sorteia um subdomínio a cada restart.
`./scripts/tunnel-url.sh` lê a URL dos logs, grava em `WEBHOOK_URL` e recria o
n8n. Para URL fixa seria preciso conta na Cloudflare com domínio próprio.

**Postgres em 5434.** A 5433 já está ocupada por outro projeto na máquina
(`consolidador-db`).

**Modelos do Docling embutidos na imagem.** Com `DOCLING_ARTIFACTS_PATH`
apontando para um volume, o Docling desliga o download automático e falha no
primeiro `/convert`. Baixamos `layout`, `tableformer` e `rapidocr` no build,
para um caminho fora de volume. A imagem fica em ~3GB, mas o container sobe
pronto e a demo não depende de rede na hora do request.

**OCR desligado por padrão.** Editais costumam ser PDFs nativos com camada de
texto; rodar OCR neles é lento e introduz ruído de transcrição. O parâmetro
`ocr=true` cobre os digitalizados, e usa `rapidocr` explicitamente — o seletor
automático poderia escolher um engine que não está na imagem.

**Docker sem GPU nesta máquina.** Falta o `nvidia-container-toolkit`, então o
serviço `ollama` fica num profile opcional. Isso não bloqueia o projeto: o
Estágio 1 usa API e só o Q&A local precisaria de GPU. `LLM_QA_BASE_URL` aponta
para onde houver um Ollama — container, WSL ou Windows via
`host.docker.internal`.

## Camada de modelo

Toda chamada de LLM passa pelo sub-workflow `00-llm-gateway`. Nenhum outro
fluxo fala com provedor diretamente, e nenhum usa o nó nativo de chat model do
n8n — ele esconde `cache_control`, `thinking` e structured output.

O trabalho real do gateway é normalizar o que **não** é igual entre provedores:

| Conceito | `anthropic` | `openai` |
|---|---|---|
| System prompt | campo `system` separado | primeira mensagem `role: "system"` |
| Structured output | `output_config.format` | `response_format.json_schema` |
| Uso de tokens | `input_tokens` / `output_tokens` | `prompt_tokens` / `completion_tokens` |

É a normalização de `usage` que permite gravar `cost_usd` em `llm_calls` e
recusar a chamada quando o acumulado passa de `BUDGET_USD_LIMIT` — o teto do
slide 8 implementado, e não apenas documentado.

## DataJud

`POST https://api-publica.datajud.cnj.jus.br/<alias>/_search`, autenticado com
`Authorization: APIKey <chave pública do CNJ>`. O `<alias>` é derivado dos
segmentos J e TR do próprio número do processo — mapeamento determinístico.

Verificado que o endpoint responde, é Elasticsearch por baixo, aceita o esquema
`ApiKey` e reconhece índices por tribunal (`api_publica_tjal` devolve erro de
autenticação, não 404). A consulta usa o número **sem pontuação**, em
`{"query": {"match": {"numeroProcesso": "..."}}}`.

Cobrimos só a Justiça Estadual (segmento 8), que é onde corre a execução que
leva o imóvel a leilão. Outros segmentos devolvem `datajud_alias = None` e o
fluxo responde "fora de cobertura" em vez de chutar um índice inexistente.
