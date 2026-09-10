# Arremata AI: assistente conversacional para leitura de editais de leilão de imóvel

#### Aluno: [João Victor Goulart de Almeida](https://github.com/goulartt)
#### Orientador: [Heitor](mailto:heitor@ica.ele.puc-rio.br)

---

Trabalho apresentado ao curso [GenAI e LLM MASTER](https://ica.ele.puc-rio.br/cursos/ia-generativa-large-language-models/) como pré-requisito para conclusão de curso e obtenção de crédito na disciplina "Projetos de Sistemas Inteligentes de Apoio à Decisão".

- [Link para o código](https://github.com/goulartt/puc-rj-projeto-final).

---

### Resumo

Quem arremata um imóvel em leilão assina um edital de dezenas de cláusulas
escrito para advogados e costuma descobrir o que aceitou depois de pagar. Este
trabalho apresenta o Arremata AI, um assistente conversacional no Telegram,
orquestrado em n8n, que recebe o PDF do edital e o torna legível para um
comprador sem formação jurídica: explica o que o documento diz, cita o trecho
de onde tirou cada afirmação e aponta o que o edital **não** informa. O
assistente não opina se vale a pena arrematar, não estima valor de mercado e
não dá orientação jurídica.

A arquitetura separa o processamento em três estágios. No primeiro, executado
uma vez por edital, o PDF é convertido em Markdown pelo Docling, extratores
determinísticos leem número de processo (com dígito verificador ISO 7064),
valores, datas e matrícula, e um modelo de linguagem de maior capacidade produz
uma ficha estruturada, validada contra JSON Schema, em que cada campo carrega a
citação literal que o sustenta. No segundo, quando há número de processo
válido, a API pública DataJud do CNJ fornece os movimentos processuais. No
terceiro, executado a cada pergunta, um modelo pequeno rodando localmente
responde a partir da ficha, e não do documento inteiro, depois de um filtro
determinístico de escopo. Todas as chamadas de modelo passam por um gateway
que torna o provedor trocável por variável de ambiente e impõe um teto de custo.

A avaliação mediu recusa correta de perguntas fora do escopo (10/10), ausência
de recusa indevida (0/6), acordo entre extratores determinísticos e modelo
(6/6) e citação verificável na ficha gerada (33/36 no edital de exemplo; 140/148
num corpus de cinco editais reais), a um custo de US$ 0,004 por edital e custo
zero por pergunta. O achado central foi que o prompt de sistema sozinho recusou
apenas 1 de 10 perguntas proibidas, o que levou a transformar o limite do
produto em regra de código aplicada antes do modelo.

### Abstract

People who buy real estate at auction sign a notice written for lawyers and
often learn what they agreed to only after paying. This work presents Arremata
AI, a conversational assistant on Telegram, orchestrated with n8n, that takes
the auction notice PDF and makes it readable for a buyer with no legal
training: it explains what the document says, quotes the passage behind every
statement and points out what the notice does **not** disclose. The assistant
does not advise whether to buy, does not estimate market value and does not
give legal advice.

The architecture splits processing into three stages. In the first, run once
per notice, the PDF is converted to Markdown with Docling, deterministic
extractors read the case number (with its ISO 7064 check digits), amounts,
dates and property registration, and a stronger language model produces a
structured record, validated against a JSON Schema, in which every field
carries the verbatim quote that supports it. In the second, when a valid case
number exists, the CNJ's public DataJud API supplies the procedural history.
In the third, run for every question, a small locally hosted model answers from
the structured record rather than the full document, after a deterministic
scope filter. Every model call goes through a gateway that makes the provider
swappable through environment variables and enforces a spending cap.

The evaluation measured correct refusal of out-of-scope questions (10/10), no
wrongful refusals (0/6), agreement between deterministic extractors and the
model (6/6) and verifiable citations in the generated record (33/36 on the
sample notice; 140/148 over a corpus of five real notices), at US$ 0.004 per
notice and zero cost per question. The key finding was that the system prompt
alone refused only 1 of 10 forbidden questions, which led to turning the
product's boundary into a code rule applied before the model.

### 1. Introdução

Editais de leilão judicial são documentos públicos, publicados no portal do
leiloeiro por exigência do art. 887, § 2º do Código de Processo Civil. Públicos
não quer dizer legíveis. O edital de exemplo deste repositório traz, em
cláusulas separadas por páginas:

- a imissão na posse por conta do arrematante — **sem declarar se o imóvel está
  ocupado**;
- uma contradição sobre IPTU, que o próprio edital resolve citando o Tema 1134
  do STJ contra o art. 130 do CTN;
- cinco números de processo, dos quais **apenas um é o processo do leilão** —
  os outros quatro são jurisprudência citada;
- duas "matrículas": a do imóvel no cartório e a do leiloeiro na JUCESP.

Nenhuma dessas coisas está escondida. Todas estão espalhadas, em linguagem que
pressupõe formação jurídica, num documento que a pessoa lê uma vez na vida.

**Pessoa.** Comprador pessoa física, no primeiro ou segundo leilão, sem
formação jurídica. Encontrou um lote num portal de leiloeiro e não tem equipe
para ler matrícula nem sabe distinguir um ônus que se extingue de um que
acompanha o imóvel.

**Momento.** À noite, com o edital aberto, antes do prazo do leilão — e com uma
dúvida pontual que não sabe formular por escrito. Ouviu "imissão na posse" e
não sabe se é "emissão"; quer perguntar sobre a dívida que acompanha o imóvel e
não sabe que isso se chama *propter rem*. É aí que uma interface conversacional
ganha do formulário: a pessoa descreve o problema com as palavras que tem, e o
assistente traduz.

**Objetivo.** O assistente ajuda um comprador iniciante de imóveis em leilão a
entender termos, prazos e riscos do edital antes de dar um lance, usando o PDF
do edital e perguntas em linguagem natural. Dois critérios observáveis definem
o acerto:

1. **Toda afirmação sobre o edital cita o trecho que a sustenta**, verificável
   por máquina contra o documento convertido.
2. **O assistente recusa o que está fora do escopo** — aconselhamento jurídico,
   "vale a pena comprar?" e previsão de valor.

> **Não é advogado e não substitui análise jurídica.** O assistente explica o
> que o edital diz, mostra de onde tirou cada resposta e diz o que o documento
> não informa. Não opina se vale a pena arrematar, não estima valor de mercado
> e não dá orientação jurídica — e isso é verificado por teste, não prometido
> em texto.

### 2. Modelagem

#### 2.1 Três estágios

O desenho ingênuo — mandar o edital inteiro no contexto a cada pergunta — faz o
custo crescer com `perguntas × tamanho_do_edital` e produz respostas sem
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
custo por pergunta vai a zero. A ficha também é rastreável (cada campo carrega
o trecho que o sustenta) e serve como entregável fora do chat.

#### 2.2 Determinístico antes do modelo

Extratores de expressão regular rodam **antes** do modelo e têm dois papéis:
entram no prompt como âncora e **conferem** a saída dele. A divergência entre
os dois vira `confidence: low` na ficha, nunca um desempate silencioso.

O número de processo é o caso mais forte. O dígito verificador módulo 97
(ISO 7064, Resolução CNJ 65/2008) separa "sequência de 20 dígitos" de "processo
real", e a classificação por instância separa o processo do leilão dos
precedentes citados no texto — sem ela, a ficha traria do DataJud a situação
processual de outra pessoa.

#### 2.3 A ficha

A saída do Estágio 1 é validada contra `schemas/ficha.schema.json`. Cada campo
carrega o valor extraído, o `quote` (trecho literal do edital, até 600
caracteres), a origem e o grau de confiança. O campo `gaps` — o que o edital não informa — é obrigatório: a
omissão costuma ser o achado mais valioso, e um extrator que só lê campos
preenchidos perderia exatamente o risco mais caro, como a ocupação não
declarada.

Ficha reprovada no schema não é descartada: os erros de validação voltam ao
modelo com o documento e um pedido de correção pontual, uma única vez.

#### 2.4 Conversa e escopo

A pergunta passa primeiro por `services/extractors/scope.py`, um filtro
determinístico que recusa pedidos de aconselhamento, previsão de valor e
opinião sobre a compra. O prompt `prompts/qa-system.md` repete o limite como
segunda camada. O filtro é conservador de propósito: recusar uma pergunta
respondível é pior que deixar passar uma duvidosa, porque a segunda ainda
encontra o prompt pela frente e a primeira não tem resgate.

A ficha é apresentada em três blocos: **a favor** (deságio da segunda praça,
ausência de ônus, processo sem sinal de cancelamento), **pontos de atenção** e
**o que o edital não informa**. O bloco "a favor" relata fatos, não recomenda:
existe porque três linhas de alerta sem contrapartida levam a pessoa a concluir
que o lote é ruim mesmo quando o documento não diz isso. Cada fato exige base —
ausência de ônus só é afirmada quando o edital se pronunciou, e "processo sem
sinal de cancelamento" só depois da consulta ao DataJud. Sem apuração, silêncio.

#### 2.5 Camada de modelo trocável

Toda chamada passa pelo sub-fluxo `00-llm-gateway`. Dois adaptadores cobrem o
mercado: `anthropic` (Messages API) e `openai` (chat/completions, que serve
Ollama, DeepSeek, OpenRouter, Groq e vLLM). O gateway normaliza o que de fato
difere entre provedores:

| Conceito | `anthropic` | `openai` |
|---|---|---|
| Prompt de sistema | campo `system` separado | primeira mensagem `role: "system"` |
| Saída estruturada | `output_config.format` | `response_format.json_schema` |
| Uso de tokens | `input_tokens` / `output_tokens` | `prompt_tokens` / `completion_tokens` |

É a normalização do `usage` que permite gravar o custo de cada chamada e
**recusar a chamada** quando o acumulado passa de `BUDGET_USD_LIMIT` — antes de
gastar, não depois.

Normaliza também uma quarta coisa, que só apareceu em produção: **o raciocínio
do modelo não chega à pessoa**. A Messages API o entrega em bloco próprio e o
Ollama em campo separado, mas essa separação depende de o modelo abrir o bloco
com `<think>` — e abertura de bloco é geração, não protocolo. Com o qwen3 uma
resposta veio com um token de lixo no lugar da abertura, e o raciocínio inteiro,
em inglês, foi entregue como se fosse a resposta. O gateway corta pelo
fechamento, que é o que sobrevive.

Configuração usada na avaliação: extração com `deepseek-v4-flash`
(`reasoning_effort=minimal`) e perguntas com `qwen3:14b` via Ollama, numa RTX
4070 Ti Super de 16 GB.

#### 2.6 Privacidade

O CPF do executado consta do edital público e **nunca** é persistido nem
enviado a um provedor: o Markdown é mascarado na conversão, que é o ponto por
onde o documento entra no sistema. A ficha não tem campo para nome ou CPF de
executado, e o `additionalProperties: false` do schema rejeitaria se o modelo
tentasse preencher. O Estágio 1 é o único que envia o documento a terceiro; as
perguntas seguintes trafegam só a ficha, e o `chat_id` nunca vai a provedor de
modelo. Não há scraping de portal de tribunal (e-SAJ, PJe): o DataJud é a rota
pública legítima, e onde ele não cobre, a resposta é dizer que não cobre.

#### 2.7 Implementação

| Componente | Papel |
|---|---|
| `n8n` | orquestração dos fluxos |
| `docling` | PDF → Markdown, com OCR opcional (`rapidocr`) |
| `postgres` | fichas, consultas processuais, custo por chamada |
| `cloudflared` | URL HTTPS pública para o webhook do Telegram |
| Ollama (host) | modelo local do Estágio 3 |

| Fluxo | Papel |
|---|---|
| `workflows/00-llm-gateway.json` | sub-fluxo: provedor trocável, custo, teto |
| `workflows/01-telegram-chat.json` | chat, roteamento, escopo, respostas |
| `workflows/02-edital-ingest.json` | PDF → ficha validada |
| `workflows/03-processo-lookup.json` | sub-fluxo: DataJud → sinais de risco |
| `workflows/04-edital-preparar.json` | PDF → texto + lotes, antes da confirmação |

Todos os fluxos são gerados por `scripts/build-workflows.py`, que roda
`node --check` em cada nó de código antes de escrever o JSON. O fluxo é código
versionado, não clique num editor.

Documentação complementar:

- [`docs/architecture.md`](docs/architecture.md) — decisões e o porquê de cada uma
- [`docs/domain.md`](docs/domain.md) — pessoa, momento, evidência, acerto
- [`docs/evaluation.md`](docs/evaluation.md) — o que cada número mede e onde ele para
- [`docs/privacy.md`](docs/privacy.md) — dado pessoal, LGPD, limites de coleta
- [`docs/operations.md`](docs/operations.md) — endereços, logs, custo por conversa, troca de modelo
- [`docs/evidence/`](docs/evidence/) — medições datadas, incluindo os erros

### 3. Resultados

#### 3.1 O assistente em uso

A pessoa envia o PDF do edital ao bot. Em segundos ele mostra o imóvel que
encontrou — ou a lista, quando o edital cobre vários — e pergunta qual analisar.
Confirmada a escolha, a ficha fica pronta em cerca de dois minutos. Depois é
conversa:

> **você:** esse imóvel está ocupado?
> **bot:** O edital não informa a ocupação. Ele atribui a imissão na posse ao
> arrematante, o que significa que desocupar é problema de quem comprar.
> Verifique com uma visita ao imóvel antes de dar lance.

> **você:** vale a pena comprar?
> **bot:** Não consigo dizer se vale a pena — isso depende do seu objetivo e de
> valores que o edital não traz. O que dá para fazer é listar os custos que o
> edital menciona e o que ele deixa em aberto. Quer?

#### 3.2 Suíte de avaliação

Medidas sobre o edital de exemplo e a ficha gerada pelo pipeline
(`python3 tests/run_eval.py --live`, relatório em
[`docs/evidence/2026-08-06-avaliacao.md`](docs/evidence/2026-08-06-avaliacao.md)):

| Medida | Resultado |
|---|---|
| Recusa correta (filtro determinístico) | **10/10** |
| Recusa indevida de pergunta respondível | **0/6** |
| Extração e validação de número CNJ | **3/3** |
| Acordo determinístico × modelo | **6/6** |
| Citação verificável na ficha gerada | **33/36 (92%)** |
| Custo por edital | **US$ 0,004** |
| Custo por pergunta | **US$ 0** (modelo local) |

A recusa correta e a recusa indevida só significam algo juntas: um filtro que
recusa tudo tira nota máxima na primeira e destrói o produto.

#### 3.3 Corpus de editais reais

A mesma medida de citação e de acordo, aplicada a todos os editais que o
pipeline já processou, lendo o Markdown e a ficha que ficaram no banco — o que
a pessoa de fato recebeu
([`docs/evidence/2026-08-07-avaliacao-corpus.md`](docs/evidence/2026-08-07-avaliacao-corpus.md)):

| Rito | Tamanho | Citação verificável | Determinístico × modelo |
|---|---|---|---|
| judicial | 5k | 26/26 (100%) | 5/5 (100%) |
| judicial | 10k | 32/34 (94%) | 6/6 (100%) |
| judicial | 17k | 30/32 (94%) | 5/6 (83%) |
| judicial | 13k | 31/35 (89%) | 5/5 (100%) |
| extrajudicial | 52k | 21/21 (100%) | 2/3 (67%) |
| **agregado** | | **140/148 (95%)** | **23/25 (92%)** |

As duas divergências são datas de primeira praça em que regex e modelo leram
coisas diferentes. Em cada uma, uma das duas leituras está errada — é
exatamente o sinal que a conferência cruzada existe para levantar.

#### 3.4 O número que mudou o desenho

Com as mesmas dez perguntas fora de escopo enviadas direto ao modelo, **sem o
filtro na frente**, o prompt sozinho segurou **1 de 10**. O `qa-system.md` diz
com todas as letras que o assistente não opina sobre compra; perguntado "vale a
pena comprar esse imóvel?", o modelo respondeu com análise de investimento e
inventou um bairro que não está na ficha.

Instrução em prompt é um pedido, e num modelo pequeno é um pedido que ele às
vezes atende. Foi essa medida que transformou o limite do produto em regra de
código, aplicada antes de qualquer chamada de modelo.

#### 3.5 O efeito das âncoras determinísticas

Entregar valores, datas e matrícula ao modelo como conferência subiu a taxa de
citação verificável de **71% para 92%**: ele passa a copiar onde há número a
ancorar. As três citações que restam são texto corrido, sem âncora possível.

#### 3.6 Tempo e custo

| Etapa | Tempo |
|---|---|
| Conversão do PDF (Docling) | 3,9 s |
| Extração da ficha, raciocínio padrão | ~147 s |
| Extração da ficha, `reasoning_effort=minimal` | ~110 s |
| Pergunta no Q&A (`qwen3:14b` local) | 13–33 s |

A chamada ao modelo de extração responde por 97% do tempo; nós de código,
banco e conversão somam menos de 100 ms além do Docling. Desligar o raciocínio
deixa a extração cinco vezes mais rápida, mas produziu ficha válida em apenas
1 de 3 execuções — por isso a configuração adotada é `minimal`
([`docs/evidence/2026-08-06-tempo-de-extracao.md`](docs/evidence/2026-08-06-tempo-de-extracao.md)).

### 4. Conclusões

Separar a leitura do documento (uma vez, com modelo forte) da conversa (muitas
vezes, com modelo pequeno sobre uma ficha curta) tornou o assistente barato,
rastreável e compatível com um modelo local. A citação obrigatória por campo
transformou a promessa central do projeto — "toda afirmação mostra de onde
veio" — em algo que a máquina confere.

O resultado mais importante veio de uma medida que foi construída para
confirmar o desenho e acabou por refutá-lo: o prompt sozinho segurou 1 de 10
perguntas proibidas. Limites que o produto promete não podem depender da
disposição do modelo em obedecer; precisam de uma regra que rode antes dele. O
mesmo raciocínio vale para os extratores determinísticos, que ancoram e
conferem a saída do modelo, e para o gateway, que recusa a chamada antes de o
teto de custo ser ultrapassado.

Limites conhecidos:

- **Amostra pequena.** O corpus tem cinco editais, quatro deles judiciais.
  Editais de outros tribunais, extrajudiciais ou digitalizados podem se
  comportar de outro jeito.
- **PDF digitalizado** sem camada de texto exige `ocr=true`, e a qualidade cai.
- **O filtro de escopo é regex.** Pega formulações inequívocas; uma pergunta
  rebuscada o suficiente passa e encontra só o prompt.
- **DataJud não traz peças nem decisões**, só movimentos. O assistente sinaliza
  risco procedimental; não conclui nada sobre o mérito.
- **Um edital por conversa** — a ficha carregada é sempre a mais recente.
- **Três citações em 36 ainda são paráfrase** na ficha do edital de exemplo.
  Estão listadas no relatório de avaliação, não escondidas.

Como trabalhos futuros: ampliar o corpus para outros tribunais e para leilões
extrajudiciais, definir prazo automático de retenção dos dados (hoje só há
remoção a pedido, por `/apagar`), permitir mais de um edital por conversa e
avaliar a qualidade das respostas com gabarito, e não só por cobertura de
termos.

### 5. Referências

1. BRASIL. Lei nº 13.105, de 16 de março de 2015. Código de Processo Civil. Art. 887, § 2º. Disponível em: <https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm>.
2. BRASIL. Lei nº 5.172, de 25 de outubro de 1966. Código Tributário Nacional. Art. 130. Disponível em: <https://www.planalto.gov.br/ccivil_03/leis/l5172compilado.htm>.
3. BRASIL. Lei nº 13.709, de 14 de agosto de 2018. Lei Geral de Proteção de Dados Pessoais (LGPD). Disponível em: <https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm>.
4. SUPERIOR TRIBUNAL DE JUSTIÇA. Tema Repetitivo 1134. Responsabilidade do arrematante por débitos tributários anteriores à arrematação.
5. CONSELHO NACIONAL DE JUSTIÇA. Resolução nº 65, de 16 de dezembro de 2008. Numeração única dos processos no Poder Judiciário.
6. CONSELHO NACIONAL DE JUSTIÇA. API Pública do DataJud. Disponível em: <https://datajud-wiki.cnj.jus.br/api-publica/>.
7. ISO/IEC 7064:2003. Information technology — Security techniques — Check character systems.
8. AUER, C. et al. Docling Technical Report. arXiv:2408.09869, 2024.
9. QWEN TEAM. Qwen3 Technical Report. arXiv:2505.09388, 2025.
10. n8n. Documentação oficial. Disponível em: <https://docs.n8n.io>.
11. Ollama. Disponível em: <https://github.com/ollama/ollama>.
12. ANTHROPIC. Messages API. Disponível em: <https://docs.anthropic.com/en/api/messages>.
13. DEEPSEEK. DeepSeek API Docs. Disponível em: <https://api-docs.deepseek.com>.
14. TELEGRAM. Telegram Bot API. Disponível em: <https://core.telegram.org/bots/api>.
15. JSON Schema. Disponível em: <https://json-schema.org>.

### 6. Instalação

Pré-requisitos: Docker com Compose, Python 3, Node.js, um bot criado no
[@BotFather](https://t.me/BotFather) e, para o Q&A local, o
[Ollama](https://ollama.com) instalado no host (com GPU, de preferência).

```bash
git clone https://github.com/goulartt/puc-rj-projeto-final.git
cd puc-rj-projeto-final
cp .env.example .env      # token do Telegram, chaves do provedor e do DataJud
docker compose up -d
```

A chave do DataJud é pública e publicada pelo CNJ, mas pode mudar a qualquer
momento; a atual fica em <https://datajud-wiki.cnj.jus.br/api-publica/acesso/>.

Modelo local do Q&A — o Ollama precisa escutar além do loopback para que os
contêineres o alcancem:

```bash
ollama pull qwen3:14b
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0"\n' \
  | sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

Quem não puder mexer no systemd usa o perfil `ollama-bridge` do compose, e
quem tiver `nvidia-container-toolkit` pode usar o Ollama em contêiner, no
perfil `local-llm`.

No n8n, abra `http://localhost:5678` **pelo localhost** e crie a conta de dono
antes de publicar o túnel: ele expõe o editor inteiro, não só o webhook. Crie
as credenciais "Bot do Telegram" (token do bot) e "Postgres do projeto" (host
`postgres`, porta 5432, base e usuário do `.env`) e importe os fluxos:

```bash
python3 scripts/build-workflows.py
for f in workflows/*.json; do
  docker compose cp "$f" n8n:/tmp/w.json
  docker compose exec n8n n8n import:workflow --input=/tmp/w.json
done
for id in llmgateway0000001 processolookup01 editalprepare001 editalingest0001 telegramchat0001; do
  docker compose exec n8n n8n publish:workflow --id=$id
done
docker compose exec n8n n8n update:workflow --id=telegramchat0001 --active=true
docker compose restart n8n
```

Se algum nó não encontrar a credencial sozinho, selecione-a no editor. Os
sub-fluxos precisam ser publicados para serem chamáveis, e `import` zera a flag
de ativação — detalhes em [`docs/operations.md`](docs/operations.md).

### 7. Como executar

```bash
./scripts/expose-bot.sh   # sobe o túnel, atualiza o .env e registra o webhook
```

O túnel rápido do Cloudflare sorteia um domínio a cada reinício; rodar o script
de novo refaz o registro do webhook.

No Telegram, envie o PDF do edital ao bot (há um de exemplo em
`data/editais/edital-exemplo.pdf`), escolha o imóvel e pergunte. `/ajuda`
explica o que o bot faz e o que faz com os dados; `/apagar` remove os editais
e fichas da conversa.

Trocar de provedor é trocar variável de ambiente e recriar o contêiner
(`docker compose up -d n8n` — `restart` mantém o ambiente antigo):

```bash
LLM_EXTRACTION_PROVIDER=openai      # deepseek
LLM_EXTRACTION_MODEL=deepseek-v4-flash
LLM_QA_PROVIDER=openai              # ollama local
LLM_QA_MODEL=qwen3:14b
BUDGET_USD_LIMIT=5
```

Testes e avaliação:

```bash
python3 -m pytest tests/ -q                    # extratores, escopo, citação
node --test services/gateway/gateway.test.js   # gateway e teto de custo
python3 scripts/build-workflows.py             # gera e valida os fluxos
python3 tests/run_eval.py                      # suíte de avaliação
python3 tests/run_eval.py --live --out docs/evidence/   # com o modelo real
python3 scripts/eval-corpus.py --out docs/evidence/     # todos os editais do banco
```

Os fluxos têm smoke tests próprios em `tests/workflows/`, executáveis com
`n8n execute --id=<id>`. O do chat deriva do fluxo real em vez de
reimplementá-lo: troca o gatilho do Telegram por mensagens sintéticas e mantém
roteamento, escopo e gateway idênticos aos de produção.

---

Matrícula: 252100217

Pontifícia Universidade Católica do Rio de Janeiro

Curso de Pós Graduação *Inteligência Artificial Generativa & Large Language Models*
