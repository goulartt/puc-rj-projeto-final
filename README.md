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
trabalho apresenta o Arremata AI, um assistente no Telegram, orquestrado em
n8n, que recebe o PDF do edital e o explica a um comprador sem formação
jurídica. Para cada afirmação, o assistente cita o trecho do edital de onde a
tirou, e também aponta o que o documento deixa de informar. A ficha termina com
o entorno do imóvel, calculado a partir do OpenStreetMap: um índice de 0 a 100
e a distância a pé até mercado, transporte, saúde e escola. Ele não opina se
vale a pena arrematar, não estima valor de mercado e não dá orientação
jurídica.

O processamento é dividido em três estágios. No primeiro, executado uma vez
por edital, o Docling converte o PDF em Markdown, extratores determinísticos
leem número de processo (com dígito verificador ISO 7064), valores, datas e
matrícula, e um modelo de linguagem de maior capacidade produz uma ficha
estruturada, validada contra JSON Schema, em que cada campo traz a citação
literal que o sustenta. No segundo, quando há número de processo válido, a API
pública DataJud do CNJ fornece os movimentos processuais. No terceiro,
executado a cada pergunta, um modelo pequeno responde lendo apenas a ficha,
depois que um filtro determinístico recusa as perguntas fora do escopo. Os dois
modelos são acessados pela OpenRouter e foram escolhidos por medição, entre
sete candidatos. Todas as chamadas passam por um gateway que torna o modelo
trocável por variável de ambiente, contabiliza o custo cobrado e impõe um teto
de gasto.

A avaliação mediu recusa correta de perguntas fora do escopo (10/10), recusa
indevida de perguntas respondíveis (0/6), acordo entre extratores
determinísticos e modelo (6/6) e citação verificável na ficha gerada (33/36 no
edital de exemplo; 140/148 num corpus de cinco editais reais). Na configuração
atual, cada edital custa cerca de US$ 0,003 e leva pouco mais de um minuto, e
cada pergunta custa cerca de US$ 0,0002. Sem o filtro, o prompt de sistema
sozinho fez um modelo local de 14B recusar só 1 de 10 perguntas proibidas; por
causa desse resultado, o limite do produto passou a ser uma regra de código,
aplicada antes de o modelo ser chamado.

### Abstract

People who buy real estate at auction sign a notice full of clauses written for
lawyers and often learn what they agreed to only after paying. This work
presents Arremata AI, a Telegram assistant orchestrated with n8n that takes the
auction notice PDF and explains it to a buyer with no legal training. For every
statement, the assistant quotes the passage of the notice it came from, and it
also points out what the document leaves out. The record ends with the
property's surroundings, computed from OpenStreetMap: a 0–100 index and the
walking distance to groceries, transit, health care and schools. It does not advise whether to
buy, does not estimate market value and does not give legal advice.

Processing is split into three stages. In the first, run once per notice,
Docling converts the PDF to Markdown, deterministic extractors read the case
number (with its ISO 7064 check digits), amounts, dates and property
registration, and a stronger language model produces a structured record,
validated against a JSON Schema, in which every field includes the verbatim
quote that supports it. In the second, when a valid case number exists, the
CNJ's public DataJud API supplies the procedural history. In the third, run for
every question, a small model answers by reading only the structured record,
after a deterministic filter has refused out-of-scope questions. Both models are
reached through OpenRouter and were chosen by measurement among seven
candidates. Every call goes through a gateway that makes the model swappable
through environment variables, records the amount actually charged and enforces
a spending cap.

The evaluation measured correct refusal of out-of-scope questions (10/10),
wrongful refusal of answerable questions (0/6), agreement between deterministic
extractors and the model (6/6) and verifiable citations in the generated record
(33/36 on the sample notice; 140/148 over a corpus of five real notices). In
the current setup each notice costs about US$ 0.003 and takes just over a
minute, and each question costs about US$ 0.0002. Without the filter, the
system prompt alone made a local 14B model refuse only 1 of 10 forbidden
questions; because of that result, the product's boundary became a code rule
applied before the model is called.

### 1. Introdução

Editais de leilão judicial são públicos: o art. 887, § 2º do Código de Processo
Civil exige que sejam publicados no portal do leiloeiro. A leitura, porém, é
difícil para quem não é da área. O edital de exemplo deste repositório traz, em
cláusulas separadas por páginas:

- a imissão na posse por conta do arrematante, sem declarar se o imóvel está
  ocupado;
- uma contradição sobre IPTU, que o próprio edital resolve citando o Tema 1134
  do STJ contra o art. 130 do CTN;
- cinco números de processo, dos quais só um é o processo do leilão (os outros
  quatro são jurisprudência citada);
- duas "matrículas": a do imóvel no cartório e a do leiloeiro na JUCESP.

Nada disso está escondido, mas está espalhado pelo documento em vocabulário
jurídico, e a pessoa costuma ler um edital desses uma única vez na vida.

O público-alvo é o comprador pessoa física, no primeiro ou segundo leilão, sem
formação jurídica. Ele encontrou um lote num portal de leiloeiro, não tem
equipe para ler matrícula e não sabe distinguir um ônus que se extingue de um
que acompanha o imóvel.

O momento de uso é à noite, com o edital aberto, antes do prazo do leilão,
quando surge uma dúvida pontual que a pessoa não sabe formular por escrito. Ela
ouviu "imissão na posse" e não sabe se é "emissão"; quer perguntar sobre a
dívida que acompanha o imóvel e não sabe que isso se chama *propter rem*. Nessa
situação a conversa funciona melhor que um formulário, porque a pessoa descreve
o problema com as palavras que tem e o assistente traduz.

O objetivo é ajudar esse comprador a entender termos, prazos e riscos do edital
antes de dar um lance, a partir do PDF e de perguntas em linguagem natural.
Dois critérios observáveis definem o acerto:

1. Toda afirmação sobre o edital cita o trecho que a sustenta, e um script
   confere a citação contra o documento convertido.
2. O assistente recusa perguntas fora do escopo: pedidos de orientação
   jurídica, "vale a pena comprar?" e previsão de valor.

> **O assistente não é advogado e não substitui análise jurídica.** Ele explica
> o que o edital diz, mostra de onde tirou cada resposta e informa o que o
> documento não diz. Não opina se vale a pena arrematar, não estima valor de
> mercado e não dá orientação jurídica. Esses limites são verificados por
> testes automatizados.

### 2. Modelagem

#### 2.1 Três estágios

A solução mais simples seria mandar o edital inteiro no contexto a cada
pergunta. O custo cresceria com `perguntas × tamanho_do_edital`, e as respostas
não teriam como apontar de onde veio cada afirmação. O projeto separa o
processamento em três estágios:

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
│  situação processual → modelo barato via OpenRouter → resposta  │
│  citando o documento e separando as duas fontes                 │
└─────────────────────────────────────────────────────────────────┘
```

A leitura cara do documento acontece uma vez por edital. Depois disso, cada
pergunta lê só a ficha, de cerca de 3 mil tokens. Um texto curto e estruturado
está ao alcance de um modelo pequeno, que teria dificuldade com o edital
inteiro, e como esse modelo roda na máquina, a pergunta não custa nada em API.
A ficha também guarda, em cada campo, o trecho do edital que o sustenta, e pode
ser usada fora do chat.

#### 2.2 Determinístico antes do modelo

Extratores de expressão regular rodam antes do modelo. O resultado deles entra
no prompt como âncora e depois serve para conferir a saída do modelo. Quando os
dois divergem, o campo recebe `confidence: low` na ficha, e o sistema não
escolhe um dos valores sem avisar.

O número de processo é o melhor exemplo. O dígito verificador módulo 97 (ISO
7064, Resolução CNJ 65/2008) distingue um número de processo válido de uma
sequência qualquer de 20 dígitos. A classificação por instância separa o
processo do leilão dos precedentes citados no texto; sem ela, a ficha traria do
DataJud a situação processual de outra pessoa.

#### 2.3 A ficha

A saída do Estágio 1 é validada contra `schemas/ficha.schema.json`. Cada campo
tem o valor extraído, o `quote` (trecho literal do edital, com até 600
caracteres), a origem e o grau de confiança.

O campo `gaps`, que lista o que o edital não informa, é obrigatório. No edital
de exemplo, a omissão mais cara é a ocupação: o documento não diz se o imóvel
está ocupado e atribui ao arrematante a imissão na posse. Um extrator que lesse
só os campos preenchidos não registraria esse risco.

Quando a ficha é reprovada no schema, os erros de validação voltam ao modelo
junto com o documento e um pedido de correção pontual. Há uma única tentativa
de correção.

#### 2.4 Conversa e escopo

Antes da extração, o assistente confirma qual imóvel a pessoa quer analisar.
Num edital de um imóvel, mostra o que encontrou e pede um "sim". Num edital de
poucos imóveis, lista todos. Nos catálogos da Caixa, que chegam a centenas de
imóveis em tabela, a lista não cabe numa mensagem, e a pessoa aponta o imóvel
pelo item, pela matrícula, pelo número do bem ou por parte do endereço; quando
a resposta casa com mais de um (há "Jardim Paulista" em São Paulo e em Paraíso
do Tocantins), o assistente lista só os candidatos. Imóvel marcado como
anulado no edital é recusado com aviso, sem gastar extração.

Num catálogo, a extração recebe as regras do leilão e só a linha do imóvel
escolhido. No de referência, com 482 imóveis, isso levou a entrada de 159 mil
para 32 mil tokens, o tempo de 355 para 126 s e o custo de US$ 0,018 para
US$ 0,005. Antes dessa mudança, o catálogo não tinha nenhum imóvel reconhecido
(a matrícula vinha como "Matrícula: N Ofício: M", fora do padrão esperado), e
o modelo escolhia sozinho um dos 482.

A pergunta passa primeiro por `services/extractors/scope.py`, um filtro
determinístico que recusa pedidos de aconselhamento, previsão de valor e
opinião sobre a compra. O prompt `prompts/qa-system.md` repete o limite como
segunda camada. O filtro só recusa formulações inequívocas, porque recusar uma
pergunta respondível é pior que deixar passar uma duvidosa: a duvidosa ainda
esbarra no prompt, e a respondível recusada fica sem resposta.

A ficha é apresentada em três blocos: "a favor" (deságio da segunda praça,
ausência de ônus, processo sem sinal de cancelamento), "pontos de atenção" e "o
que o edital não informa". O bloco "a favor" lista fatos favoráveis do edital,
sem recomendar a compra. Ele existe porque uma ficha só com alertas leva a
pessoa a concluir que o lote é ruim mesmo quando o documento não diz isso.

Cada fato desse bloco precisa de base. A ausência de ônus só aparece quando o
edital trata do assunto, e "processo sem sinal de cancelamento" só aparece
depois da consulta ao DataJud. Sem essa base, o bloco não diz nada sobre o
ponto.

#### 2.5 Entorno do imóvel

A ficha termina com o que há perto do imóvel a pé. A ideia vem do Walk Score,
mas a API dele cobre só Estados Unidos e Canadá, e um endereço brasileiro não
teria nota mesmo com chave. O projeto calcula um índice próprio, de 0 a 100, a
partir do OpenStreetMap: o Nominatim converte o endereço da ficha em
coordenadas, e o Overpass lista os serviços num raio de 1 km.

São oito categorias com peso: mercado (20), ponto de ônibus (15), farmácia ou
saúde (15), restaurante ou café (15), estação de metrô ou trem (10), escola
(10), parque ou academia (10) e banco (5). Cada ocorrência vale inteira até
400 m, que são cinco minutos a pé, e perde valor em linha reta até zerar em
1 km; a segunda ocorrência de uma categoria vale menos que a primeira. O método
está em `services/extractors/location.py`, e o nome "índice de entorno" evita a
marca do Walk Score, que usa outro cálculo.

A mensagem mostra o índice e, para cada categoria, a distância até o mais
próximo, porque "mercado a 217 m" dá para conferir e um número sozinho não.
Nota baixa nunca aparece como ponto de atenção. O OpenStreetMap é bem mapeado
nas capitais e irregular no interior, e um bairro sem mercado cadastrado pode
ter três; por isso a seção diz "nada mapeado a 1 km" em vez de "não há", e
declara a fonte e o limite dela.

O texto de endereço de edital atrapalha o geocoder. "28º Subdistrito – Jardim
Paulista" é a circunscrição do cartório, e o imóvel do edital de exemplo fica
de fato na Vila Olímpia; com esse trecho, o Nominatim não achava nenhum dos
dois endereços testados. A limpeza tira o vocabulário de cartório e a unidade
do condomínio, tenta com o número e, sem ele, só com a rua, e avisa quando o
ponto ficou no nível da rua.

#### 2.6 Camada de modelo trocável

Toda chamada de modelo passa pelo sub-fluxo `00-llm-gateway`. Os dois estágios
falam com a [OpenRouter](https://openrouter.ai), que dá acesso a centenas de
modelos com uma chave só; trocar de modelo é trocar uma variável de ambiente
por qualquer ID do catálogo. O gateway tem dois adaptadores, `openai`
(chat/completions, o protocolo da OpenRouter) e `anthropic` (Messages API, para
quem quiser falar com a Anthropic direto), e normaliza as diferenças entre
eles:

| Conceito | `anthropic` | `openai` |
|---|---|---|
| Prompt de sistema | campo `system` separado | primeira mensagem `role: "system"` |
| Saída estruturada | `output_config.format` | `response_format.json_schema` |
| Uso de tokens | `input_tokens` / `output_tokens` | `prompt_tokens` / `completion_tokens` |

Com o `usage` normalizado, o gateway grava o custo de cada chamada e recusa a
próxima quando o acumulado passa de `BUDGET_USD_LIMIT`. A recusa acontece antes
de a chamada ser feita.

O custo gravado é o que a OpenRouter informa ter cobrado, no campo
`usage.cost` da resposta. Uma tabela de preços fixa erraria aqui, porque a
OpenRouter escolhe entre vários provedores por trás de cada modelo e o preço
muda com a escolha: no catálogo, `deepseek/deepseek-v4-flash` aparecia com
saída a US$ 0,131 por milhão de tokens, e variantes datadas do mesmo modelo,
entre US$ 0,55 e 0,64. A tabela continua existindo só para a Anthropic
chamada direto, que não informa o custo.

O gateway também impede que o raciocínio do modelo chegue à pessoa, um problema
que só apareceu em produção. Os provedores costumam entregar o raciocínio num
campo separado, mas essa separação depende de o modelo gerar a marca `<think>`
de abertura, e essa marca é texto gerado como qualquer outro. Numa resposta, um
token espúrio saiu no lugar da abertura, e o raciocínio inteiro, em inglês, foi
entregue como se fosse a resposta. Desde então o gateway corta o texto pela
marca de fechamento, que apareceu mesmo nesse caso.

A configuração atual usa `google/gemma-4-26b-a4b-it` na extração e
`qwen/qwen3.7-flash` nas perguntas, os dois com `reasoning_effort=minimal`
(seção 3.7). As versões anteriores do projeto chamavam a API da DeepSeek direto
na extração e rodavam as perguntas num `qwen3:14b` local via Ollama; os
resultados da seção 3 indicam em qual configuração cada número foi medido.

#### 2.7 Privacidade

O CPF do executado consta do edital público, mas o sistema não o grava nem o
envia a provedor: o Markdown é mascarado na conversão, que é por onde o
documento entra. No edital de exemplo, o PDF tem dois CPFs e a ficha, nenhum. A
ficha também não tem campo para nome ou CPF de executado, e o
`additionalProperties: false` do schema rejeitaria a ficha se o modelo
tentasse incluí-los.

O Estágio 1 é o único que envia o documento a terceiro. O endereço do imóvel,
que é público no edital, vai também aos servidores do OpenStreetMap para o
cálculo do entorno. As perguntas seguintes
trafegam só a ficha, e o `chat_id` nunca vai a provedor de modelo. O projeto
não raspa portais de tribunal (e-SAJ, PJe) e usa o DataJud, a API pública do
CNJ. Quando o processo está fora da cobertura do DataJud, o assistente informa
isso.

#### 2.8 Implementação

| Componente | Papel |
|---|---|
| `n8n` | orquestração dos fluxos |
| `docling` | PDF → Markdown, com OCR opcional (`rapidocr`) |
| `postgres` | fichas, consultas processuais, custo por chamada |
| `cloudflared` | URL HTTPS pública para o webhook do Telegram |
| Nominatim e Overpass (externos) | endereço → coordenadas → serviços a 1 km |
| OpenRouter (externo) | os dois modelos, com uma chave só |

| Fluxo | Papel |
|---|---|
| `workflows/00-llm-gateway.json` | sub-fluxo: provedor trocável, custo, teto |
| `workflows/01-telegram-chat.json` | chat, roteamento, escopo, respostas |
| `workflows/02-edital-ingest.json` | PDF → ficha validada |
| `workflows/03-processo-lookup.json` | sub-fluxo: DataJud → sinais de risco |
| `workflows/04-edital-preparar.json` | PDF → texto + lotes, antes da confirmação |

Todos os fluxos são gerados por `scripts/build-workflows.py`, que roda
`node --check` em cada nó de código antes de escrever o JSON. Assim os fluxos
ficam versionados no git junto com o resto do código.

Documentação complementar:

- [`docs/architecture.md`](docs/architecture.md): decisões de desenho e o motivo de cada uma
- [`docs/domain.md`](docs/domain.md): pessoa, momento, evidência e critério de acerto
- [`docs/evaluation.md`](docs/evaluation.md): o que cada número mede e até onde vale
- [`docs/privacy.md`](docs/privacy.md): dado pessoal, LGPD e limites de coleta
- [`docs/operations.md`](docs/operations.md): endereços, logs, custo por conversa e troca de modelo
- [`docs/evidence/`](docs/evidence/): medições datadas, incluindo os erros

### 3. Resultados

#### 3.1 O assistente em uso

A pessoa envia o PDF do edital ao bot. Em segundos ele mostra o imóvel que
encontrou (ou a lista, quando o edital cobre vários) e pergunta qual analisar.
Depois da confirmação, a ficha fica pronta em cerca de dois minutos, e a pessoa
pode fazer perguntas:

> **você:** esse imóvel está ocupado?
> **bot:** O edital não informa a ocupação. Ele atribui a imissão na posse ao
> arrematante, o que significa que desocupar é problema de quem comprar.
> Verifique com uma visita ao imóvel antes de dar lance.

> **você:** vale a pena comprar?
> **bot:** Não consigo dizer se vale a pena — isso depende do seu objetivo e de
> valores que o edital não traz. O que dá para fazer é listar os custos que o
> edital menciona e o que ele deixa em aberto. Quer?

#### 3.2 Suíte de avaliação

Medidas sobre o edital de exemplo e a ficha gerada pelo pipeline, na
configuração anterior (extração pela DeepSeek direta, perguntas no `qwen3:14b`
local) (`python3 tests/run_eval.py --live`, relatório em
[`docs/evidence/2026-08-06-avaliacao.md`](docs/evidence/2026-08-06-avaliacao.md)):

| Medida | Resultado |
|---|---|
| Recusa correta (filtro determinístico) | 10/10 |
| Recusa indevida de pergunta respondível | 0/6 |
| Extração e validação de número CNJ | 3/3 |
| Acordo determinístico × modelo | 6/6 |
| Citação verificável na ficha gerada | 33/36 (92%) |
| Custo por edital | US$ 0,004 (hoje, ~US$ 0,003) |
| Custo por pergunta | US$ 0 no modelo local (hoje, ~US$ 0,0002) |

As duas medidas de recusa precisam ser lidas juntas, porque um filtro que
recusasse tudo tiraria 10/10 na primeira e deixaria o produto inútil.

#### 3.3 Corpus de editais reais

As medidas de citação e de acordo foram repetidas, na configuração anterior,
em todos os editais que o pipeline já processou, a partir do Markdown e da ficha gravados no banco, que
são o que a pessoa recebeu
([`docs/evidence/2026-08-07-avaliacao-corpus.md`](docs/evidence/2026-08-07-avaliacao-corpus.md)):

| Rito | Tamanho | Citação verificável | Determinístico × modelo |
|---|---|---|---|
| judicial | 5k | 26/26 (100%) | 5/5 (100%) |
| judicial | 10k | 32/34 (94%) | 6/6 (100%) |
| judicial | 17k | 30/32 (94%) | 5/6 (83%) |
| judicial | 13k | 31/35 (89%) | 5/5 (100%) |
| extrajudicial | 52k | 21/21 (100%) | 2/3 (67%) |
| agregado | | 140/148 (95%) | 23/25 (92%) |

As duas divergências são datas de primeira praça em que a regex e o modelo
leram valores diferentes. Em cada caso, uma das leituras está errada, e é para
encontrar esse tipo de erro que a conferência cruzada existe.

#### 3.4 Recusa sem o filtro de escopo

As mesmas dez perguntas fora do escopo foram enviadas direto ao modelo, sem o
filtro, e o prompt sozinho fez o modelo recusar só 1 delas. O `qa-system.md`
diz explicitamente que o assistente não opina sobre a compra. Mesmo assim,
perguntado "vale a pena comprar esse imóvel?", o modelo respondeu com uma
análise de investimento e inventou um bairro que não está na ficha.

Depois dessa medida, o limite do produto passou para
`services/extractors/scope.py`, que roda antes de qualquer chamada de modelo, e
o prompt ficou como segunda camada.

O modelo daquela medida era o `qwen3:14b` local. Repetida em setembro com os
modelos atuais, pela OpenRouter, a mesma bateria teve 9 de 10 recusas só com o
prompt. A segunda camada deixou de ser decorativa, mas uma pergunta ainda passa,
e o filtro continua sendo o que garante o limite.

#### 3.5 O efeito das âncoras determinísticas

Com valores, datas e matrícula entregues ao modelo como referência, a taxa de
citação verificável subiu de 71% para 92%. O modelo passou a copiar o texto do
edital nos trechos que contêm esses números. As três citações que ainda falham
são de texto corrido, sem número que sirva de âncora.

#### 3.6 Tempo e custo

| Etapa | Tempo | Custo |
|---|---|---|
| Conversão do PDF (Docling) | 3,9 s | — |
| Extração, `gemma-4-26b` via OpenRouter (atual) | 64 a 174 s | US$ 0,003 a 0,004 |
| Pergunta, `qwen3.7-flash` via OpenRouter (atual) | 13 a 26 s | ~US$ 0,0002 |
| Extração, DeepSeek direta, raciocínio padrão | ~147 s | US$ 0,005 |
| Extração, DeepSeek direta, `minimal` | ~110 s | US$ 0,004 |
| Pergunta, `qwen3:14b` local | 13 a 33 s | US$ 0 |

O custo gravado é o que a OpenRouter informa ter cobrado em cada chamada. A
chamada ao modelo de extração ocupa 97% do tempo total. Fora ela e a
conversão do Docling, os nós de código e as consultas ao banco somam menos de
100 ms. Com o raciocínio desligado, a extração ficou cinco vezes mais rápida,
mas só 1 de 3 execuções produziu ficha válida. Por isso a configuração adotada
é `minimal`
([`docs/evidence/2026-08-06-tempo-de-extracao.md`](docs/evidence/2026-08-06-tempo-de-extracao.md)).
Nas perguntas vale o mesmo: sem raciocínio a resposta sai de 2 a 5 vezes mais
rápida e erra um terço das perguntas sobre o edital.

#### 3.7 Escolha de modelo

Sete modelos do catálogo da OpenRouter passaram pela extração real, com o
mesmo prompt, schema, validação e passo de correção. Os três melhores rodaram
de novo em dois editais
([`docs/evidence/2026-09-23-comparacao-de-modelos.md`](docs/evidence/2026-09-23-comparacao-de-modelos.md)):

| Modelo | Válida de primeira | Citação | Tempo | Custo/edital |
|---|---|---|---|---|
| `google/gemma-4-26b-a4b-it` | 4/4 | 121/122 (99%) | 82–174 s | US$ 0,003–0,004 |
| `qwen/qwen3.7-flash` | 1/4 | 111/128 (87%) | 203–236 s | US$ 0,002–0,003 |
| `deepseek/deepseek-v4-flash` | 2/3 | 88/101 (87%) | 144–425 s | US$ 0,005–0,012 |

Outros quatro ficaram pelo caminho: `gpt-oss-20b` omitiu metade dos blocos da
ficha, `qwen3.5-flash` devolveu uma correção sem JSON, e `mimo-v2.6-flash` e
`nemotron-3-nano` ficaram abaixo de 70% de citação.

O Gemma 4 26B-A4B ativa cerca de 4B de parâmetros por token, e ficou com a
extração. Nas perguntas, o `qwen3.7-flash` acertou 6 de 6 sobre o edital e o
Gemma, 5 de 6; a diferença é de uma questão e está dentro do ruído.

O `deepseek-v4-flash`, que era o modelo do projeto, piorou ao passar pela
OpenRouter: na API direta extraía em ~110 s, e por ela variou entre 144 e
425 s. O catálogo mostra 15 provedores terceiros para ele, parte com
quantização FP8 ou FP4, sem a própria DeepSeek entre eles, e o roteamento
padrão prioriza preço.

A comparação também achou dois defeitos no projeto: a correção de ficha
derrubava a execução quando voltava sem JSON, e o extrator não lia datas de
praça dentro de tabela, que o Docling renderiza como `10 / 08 / 2026`.

### 4. Conclusões

Ler o documento uma vez e responder às perguntas sobre uma ficha curta deixou
cada pergunta em cerca de US$ 0,0002 e permitiu usar um modelo pequeno nelas.
Pela OpenRouter, trocar de modelo passou a ser trocar uma variável, e isso
tornou barato escolher por medição: o modelo que o projeto usava acabou em
terceiro. A citação obrigatória por campo tornou
verificável por máquina a principal promessa do projeto, a de que toda
afirmação mostra de onde veio.

O passe ao vivo da avaliação foi montado para confirmar que o prompt bastava
para recusar perguntas proibidas, e mostrou que não: o modelo recusou 1 de 10.
A conclusão prática é que um limite prometido ao usuário precisa de uma regra
que rode antes do modelo. O projeto aplica a mesma ideia em dois outros pontos:
os extratores determinísticos conferem a saída do modelo, e o gateway recusa a
chamada quando o teto de custo é atingido.

Limites conhecidos:

- O corpus tem cinco editais, quatro deles judiciais. Editais de outros
  tribunais, extrajudiciais ou digitalizados podem se comportar de outro jeito.
- PDF digitalizado sem camada de texto exige `ocr=true`, e a qualidade cai.
- O filtro de escopo é regex e pega formulações inequívocas. Uma pergunta
  rebuscada o suficiente passa por ele e encontra só o prompt.
- O DataJud traz só movimentos processuais, sem peças nem decisões. O
  assistente sinaliza risco procedimental e não conclui nada sobre o mérito.
- A conversão de um catálogo grande é lenta: o da Caixa, de 750 KB, levou
  mais de cinco minutos no Docling antes de a pergunta sobre o imóvel chegar.
- Cada conversa trabalha com um edital por vez, e a ficha carregada é sempre a
  mais recente.
- Os modelos dependem da OpenRouter e dos provedores que ela escolhe por trás,
  e o desempenho do mesmo modelo varia com essa escolha.
- A escolha de modelo usou dois editais e de 3 a 4 execuções por modelo, o
  bastante para descartar candidatos e pouco para afirmar taxas.
- Na ficha do edital de exemplo, três das 36 citações ainda são paráfrase. Elas
  estão listadas no relatório de avaliação.

Os próximos passos são ampliar o corpus para outros tribunais e para leilões
extrajudiciais, definir prazo automático de retenção dos dados (hoje a remoção
só acontece a pedido, pelo `/apagar`), permitir mais de um edital por conversa
e avaliar as respostas contra um gabarito, além da cobertura de termos.

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
16. OPENROUTER. Documentação oficial. Disponível em: <https://openrouter.ai/docs>.
17. GOOGLE DEEPMIND. Gemma. Disponível em: <https://ai.google.dev/gemma>.
18. OPENSTREETMAP CONTRIBUTORS. OpenStreetMap. Dados sob licença ODbL. Disponível em: <https://www.openstreetmap.org>.
19. NOMINATIM. Documentação e política de uso. Disponível em: <https://nominatim.org>.
20. OVERPASS API. Disponível em: <https://wiki.openstreetmap.org/wiki/Overpass_API>.
21. WALK SCORE. Walk Score API. Disponível em: <https://www.walkscore.com/professional/api.php>.

### 6. Instalação

Pré-requisitos: Docker com Compose, Python 3, Node.js, um bot criado no
[@BotFather](https://t.me/BotFather) e uma chave da
[OpenRouter](https://openrouter.ai/keys). Não é preciso GPU: nenhum modelo roda
na máquina.

```bash
git clone https://github.com/goulartt/puc-rj-projeto-final.git
cd puc-rj-projeto-final
cp .env.example .env      # token do Telegram, chave da OpenRouter e do DataJud
docker compose up -d
```

A chave do DataJud é pública e publicada pelo CNJ, mas pode mudar a qualquer
momento. A atual fica em <https://datajud-wiki.cnj.jus.br/api-publica/acesso/>.

O `.env` precisa de uma chave só para os modelos, `OPENROUTER_API_KEY`. Vale pôr
um limite de crédito nela no painel da OpenRouter, além do `BUDGET_USD_LIMIT`
que o gateway já aplica.

No n8n, abra `http://localhost:5678` pelo localhost e **crie a conta de dono
antes de publicar o túnel**. O túnel expõe o editor inteiro junto com o
webhook, e sem a conta qualquer pessoa com a URL acessa os fluxos e as
credenciais salvas. Crie as credenciais "Bot do Telegram" (token do bot) e
"Postgres do projeto" (host `postgres`, porta 5432, base e usuário do `.env`) e
importe os fluxos:

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
de ativação. Os detalhes estão em [`docs/operations.md`](docs/operations.md).

### 7. Como executar

```bash
./scripts/expose-bot.sh   # sobe o túnel, atualiza o .env e registra o webhook
```

O túnel rápido do Cloudflare sorteia um domínio a cada reinício, e rodar o
script de novo refaz o registro do webhook.

No Telegram, envie o PDF do edital ao bot (há um de exemplo em
`data/editais/edital-exemplo.pdf`), escolha o imóvel e faça perguntas. O
comando `/ajuda` explica o que o bot faz e o que ele faz com os dados, e o
`/apagar` remove os editais e fichas da conversa.

Para trocar o modelo de um estágio, basta pôr outro ID do catálogo da
OpenRouter no `.env` e recriar o contêiner com `docker compose up -d n8n`. O
`restart` mantém o ambiente antigo e não serve para isso:

```bash
OPENROUTER_API_KEY=sk-or-...
LLM_EXTRACTION_MODEL=deepseek/deepseek-v4-flash   # padrão, pode omitir
LLM_QA_MODEL=deepseek/deepseek-v4-flash           # padrão, pode omitir
LLM_EXTRACTION_REASONING=minimal
LLM_QA_REASONING=minimal
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
`n8n execute --id=<id>`. O smoke test do chat é gerado a partir do fluxo real:
troca o gatilho do Telegram por mensagens sintéticas e mantém roteamento,
escopo e gateway iguais aos de produção.

---

Matrícula: 252100217

Pontifícia Universidade Católica do Rio de Janeiro

Curso de Pós Graduação *Inteligência Artificial Generativa & Large Language Models*
