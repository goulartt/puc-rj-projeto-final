# Arremata AI — assistente de edital de leilão de imóvel

> **Não é advogado e não substitui análise jurídica.** Explica o que o edital
> diz, mostra de onde tirou cada resposta e diz o que o documento **não**
> informa. Não opina se vale a pena arrematar, não estima valor de mercado e
> não dá orientação jurídica — e isso é verificado por teste, não prometido em
> texto.

Projeto final da disciplina de IA Generativa & LLMs — PUC-Rio.
Chatbot no Telegram orquestrado em n8n.

---

## Problema

Quem arremata imóvel em leilão assina um documento de trinta páginas em
juridiquês e descobre o que aceitou depois de pagar. O edital de exemplo deste
repositório traz, em cláusulas separadas por dezenas de páginas:

- a imissão na posse por conta do arrematante — **sem declarar se o imóvel está
  ocupado**;
- uma contradição sobre IPTU, que o próprio edital resolve citando o Tema 1134
  do STJ contra o art. 130 do CTN;
- cinco números de processo, dos quais **apenas um é o processo do leilão** —
  os outros quatro são jurisprudência citada;
- duas "matrículas": a do imóvel no cartório e a do leiloeiro na JUCESP.

Nenhuma dessas coisas está escondida. Todas estão espalhadas, em linguagem que
pressupõe formação jurídica, num documento que a pessoa lê uma vez na vida.

O assistente não decide por ela. Ele torna o edital legível, aponta o que ficou
em aberto e recusa as perguntas cuja resposta seria um palpite caro.

## Demo

```bash
cp .env.example .env      # preencha as chaves
docker compose up -d
# abra http://localhost:5678 e crie a conta de dono ANTES de publicar o túnel:
# ele expõe o editor inteiro, não só o webhook
./scripts/expose-bot.sh   # publica o webhook do Telegram
```

No Telegram, envie o PDF do edital ao bot. Em segundos ele mostra o imóvel que
encontrou — ou a lista, quando o edital cobre vários — e pergunta qual você
quer analisar. Confirmado, monta a ficha em ~2 minutos, em três blocos: **a favor** (deságio da segunda praça, ausência de ônus,
processo sem sinal de cancelamento), **pontos de atenção** e **o que o edital
não informa**. Depois é conversa normal:

> **você:** esse imóvel está ocupado?
> **bot:** O edital não informa a ocupação. Ele atribui a imissão na posse ao
> arrematante, o que significa que desocupar é problema de quem comprar.
> Verifique com uma visita ao imóvel antes de dar lance.

> **você:** vale a pena comprar?
> **bot:** Não consigo dizer se vale a pena — isso depende do seu objetivo e de
> valores que o edital não traz. O que dá para fazer é listar os custos que o
> edital menciona e o que ele deixa em aberto. Quer?

Comandos: `/ajuda` explica o que o bot faz e o que faz com os dados; `/apagar`
remove os editais e fichas da conversa.

## Arquitetura

O desenho ingênuo — mandar o edital inteiro no contexto a cada pergunta — faz o
custo crescer com `perguntas × tamanho_do_edital` e produz resposta sem
rastreabilidade. A separação abaixo é o que torna o produto viável:

```
┌─ ESTÁGIO 1 — uma vez por edital ────────────────────────────────┐
│  PDF → Docling → Markdown (~12k tokens)                         │
│    ├─ extratores determinísticos: CNJ, valores, datas de praça, │
│    │  matrícula, percentuais, áreas — e mascaramento de CPF     │
│    └─ modelo forte + prompt de analista, recebendo o bloco      │
│       determinístico como âncora, devolvendo ficha com citação  │
└─────────────────────────────────────────────────────────────────┘
              ↓ ficha validada contra JSON Schema (~3k tokens)
┌─ ESTÁGIO 2 — se houver CNJ válido, disparado pela ingestão ─────┐
│  DataJud (API pública do CNJ) → movimentos → sinais de risco    │
│  gravado em case_lookups; leilão extrajudicial simplesmente     │
│  não consulta, e isso não é falha                               │
└─────────────────────────────────────────────────────────────────┘
              ↓
┌─ ESTÁGIO 3 — N vezes por edital ────────────────────────────────┐
│  filtro de escopo (determinístico) → pergunta + ficha +         │
│  situação processual → modelo pequeno local → resposta          │
│  citando o documento e separando as duas fontes                 │
└─────────────────────────────────────────────────────────────────┘
```

O token caro é pago uma vez. A pergunta lê uma ficha curta em vez de um
documento jurídico denso — que é onde um modelo pequeno deixa de ser risco e o
custo por pergunta vai a zero.

### Camada de modelo trocável

Toda chamada passa por `00-llm-gateway.json`. Dois adaptadores cobrem o
mercado: `anthropic` (Messages API) e `openai` (chat/completions, que serve
Ollama, DeepSeek, OpenRouter, Groq, vLLM). O gateway normaliza as três coisas
que de fato diferem entre provedores — posição do prompt de sistema, formato de
saída estruturada e forma do `usage` — e é essa normalização que torna o teto
de custo possível independente de quem responde.

Trocar de provedor é trocar variável de ambiente:

```bash
LLM_EXTRACTION_PROVIDER=openai      # deepseek
LLM_EXTRACTION_MODEL=deepseek-v4-flash
LLM_QA_PROVIDER=openai              # ollama local
LLM_QA_MODEL=qwen3:14b
BUDGET_USD_LIMIT=5
```

O gateway grava o custo de cada chamada e **recusa a chamada** quando o
acumulado passa do teto — antes de gastar, não depois.

### Fluxos

| Arquivo | Papel |
|---|---|
| `workflows/00-llm-gateway.json` | sub-fluxo: provedor trocável, custo, teto |
| `workflows/01-telegram-chat.json` | chat, roteamento, escopo, respostas |
| `workflows/02-edital-ingest.json` | PDF → ficha validada |
| `workflows/03-processo-lookup.json` | sub-fluxo: DataJud → sinais de risco |
| `workflows/04-edital-preparar.json` | PDF → texto + lotes, antes da confirmação |

Todos são gerados por `scripts/build-workflows.py`, que roda `node --check` em
cada nó de código antes de escrever o JSON. O fluxo é código versionado, não
clique num editor.

## Avaliação

`python3 tests/run_eval.py --live --out docs/evidence/` — relatório datado em
[`docs/evidence/`](docs/evidence/).

| Medida | Resultado |
|---|---|
| Recusa correta (filtro determinístico) | **10/10** |
| Recusa indevida de pergunta respondível | **0/6** |
| Extração e validação de número CNJ | **3/3** |
| Acordo determinístico × modelo | **6/6** |
| Citação verificável na ficha gerada | **33/36 (92%)** |
| Custo por edital | **US$ 0,004** |
| Custo por pergunta | **US$ 0** (modelo local) |

### O número que mudou o desenho

Com as mesmas dez perguntas fora de escopo enviadas direto ao modelo, **sem o
filtro na frente**, o prompt sozinho segurou **1 de 10**. O `qa-system.md` diz
com todas as letras que o assistente não opina sobre compra; perguntado "vale a
pena comprar esse imóvel?", o modelo respondeu com análise de investimento e
inventou um bairro que não está na ficha.

Instrução em prompt é um pedido, e num modelo pequeno é um pedido que ele às
vezes atende. O limite que o produto promete virou regra em
`services/extractors/scope.py`, aplicada antes de qualquer chamada de modelo. O
prompt continua lá, como segunda camada.

O filtro é conservador de propósito: metade de `tests/test_scope.py` guarda as
perguntas que **precisam passar**, porque recusar uma pergunta respondível é
pior que deixar passar uma duvidosa — a segunda ainda encontra o prompt pela
frente, a primeira não tem resgate.

### Relatar não é aconselhar

O bloco "a favor" mostra fatos favoráveis — "segunda praça 40% abaixo da
avaliação" é um dado do edital, como "imóvel ocupado" é. Ele existe porque três
linhas de alerta sem contrapartida levam a pessoa a concluir que o lote é ruim,
mesmo quando o documento não diz isso.

Nada ali é recomendação, e cada fato exige base: ausência de ônus só é afirmada
quando o edital se pronunciou, e "processo sem sinal de cancelamento" só depois
de a consulta ao DataJud ter sido feita. Sem apuração, silêncio — nunca uma
frase tranquilizadora sem lastro. Dizer se vale a pena continua recusado.

### O efeito das âncoras determinísticas

Entregar valores, datas e matrícula ao modelo como conferência subiu a taxa de
citação verificável de **71% para 92%**: ele passa a copiar onde há número a
ancorar. As três citações que restam são texto corrido, sem âncora possível.

## Privacidade

O CPF do executado consta do edital público e **nunca** é persistido nem
enviado a um provedor: o Markdown é mascarado na conversão, que é o ponto por
onde o documento entra no sistema. Verificado no edital de exemplo — dois CPFs
no PDF, nenhum na ficha.

Não há scraping de portal de tribunal (e-SAJ, PJe). O DataJud é a rota pública
legítima, e onde ele não cobre, a resposta é dizer que não cobre.

Detalhes em [`docs/privacy.md`](docs/privacy.md).

## Testes

```bash
python3 -m pytest tests/ -q                    # extratores, escopo, citação
node --test services/gateway/gateway.test.js   # gateway e teto de custo
python3 scripts/build-workflows.py             # gera e valida os fluxos
python3 tests/run_eval.py                      # suíte de avaliação
```

Os fluxos têm smoke tests próprios em `tests/workflows/`, executáveis com
`n8n execute --id=<id>`. O do chat deriva do fluxo real em vez de
reimplementá-lo: troca o gatilho do Telegram por mensagens sintéticas e mantém
roteamento, escopo e gateway idênticos aos de produção.

## Documentação

- [`docs/operations.md`](docs/operations.md) — endereços, logs, custo por conversa, troca de modelo
- [`docs/architecture.md`](docs/architecture.md) — decisões e o porquê de cada uma
- [`docs/domain.md`](docs/domain.md) — pessoa, momento, evidência, acerto
- [`docs/privacy.md`](docs/privacy.md) — dado pessoal, LGPD, limites de coleta
- [`docs/evidence/`](docs/evidence/) — medições datadas, incluindo os erros

## Limites conhecidos

- **PDF digitalizado** sem camada de texto exige `ocr=true` e a qualidade cai.
- **Um edital por conversa** — a ficha carregada é sempre a mais recente.
- **DataJud não traz peças nem decisões**, só movimentos. O assistente sinaliza
  risco procedimental; não conclui nada sobre o mérito.
- **Túnel do Cloudflare é efêmero.** Cada reinício sorteia um domínio novo;
  `./scripts/expose-bot.sh` refaz o registro do webhook.
- **Três citações em 36 ainda são paráfrase** na ficha gerada. Estão listadas
  no relatório de avaliação, não escondidas.
