# Extração com modelo local — medição de 06/08/2026

Registro do que aconteceu ao rodar o Estágio 1 com `qwen3:14b` local, em vez de
um modelo de API. Serve de justificativa medida para a escolha de modelo, e não
de suposição.

## Montagem

| | |
|---|---|
| Modelo | `qwen3-edital` (`qwen3:14b` com `num_ctx 32768`) |
| Hardware | RTX 4070 Ti Super 16 GB, Ollama nativo no WSL2 |
| Documento | `data/editais/edital-exemplo.pdf`, 4 páginas, 10.835 caracteres |
| Schema | `ficha.schema.json`, 12 campos de topo, ~25 mil caracteres desreferenciado |
| Gabarito | `docs/evidence/ficha-edital-exemplo.json` |

## Resultados

| Tentativa | Configuração | Erros de schema | Tempo |
|---|---|---|---|
| 1 | decodificação restrita ligada | — | falhou antes de gerar |
| 2 | restrita desligada, schema **fora** do prompt | 23 | ~1min38 |
| 3 | restrita desligada, schema **no** prompt | 5 | ~1min33 |

Nenhuma produziu ficha válida. Nada foi persistido em nenhuma das três.

## O que cada falha ensinou

**Decodificação restrita não funciona com este schema no Ollama.** O
llama.cpp compila o JSON Schema numa gramática GBNF e responde
`Failed to initialize samplers: failed to parse grammar`. A bisseção mostrou
que não é uma construção específica — tipos união, `enum`, `pattern`, `format`,
`maxLength` e arrays de objetos passam todos isoladamente, e o schema inteiro
passa com `max_tokens: 32`. O limite está entre 32 e 256 tokens de geração:
grande demais para qualquer uso real.

Isso não afeta a correção do pipeline, e vale registrar por quê: **a garantia
do formato nunca foi o provedor.** Quem reprova ficha malformada é o
`POST /validate`, que roda depois e independe de quem gerou. Decodificação
restrita é otimização; por isso ela virou opcional (`LLM_EXTRACTION_STRUCTURED`)
em vez de requisito.

**Sem decodificação restrita, o schema precisa ir no prompt.** A primeira
versão do fluxo simplesmente omitia o schema quando a restrição estava
desligada, e o modelo inventava a estrutura — 23 erros, com campos como
`commissions`, `conflicts` e `lease_terms` que não existem no contrato. Passar
o schema como texto derrubou para 5 erros.

**Os 5 erros restantes são de capacidade, não de configuração.** O modelo
acerta os 12 campos de topo mas aninha errado (coloca `court_case` dentro de
`procedure`) e omite `quote` e `confidence` obrigatórios. É exatamente o tipo
de rigor estrutural em que um 14B fica atrás num documento jurídico denso.

## Conclusão

O modelo local serve ao Estágio 3 (perguntas sobre a ficha, contexto de ~3 mil
tokens) e **não** serve ao Estágio 1. Isso confirma a premissa da arquitetura
de dois estágios: vale pagar por um modelo forte uma vez por edital e deixar o
barato responder as perguntas.

O pipeline se comportou como projetado nas três tentativas — a ficha inválida
foi rejeitada com erro específico e nada entrou no banco. A separação entre
"o modelo errou" e "o sistema aceitou o erro" é o que essa medição mostra.

## O caminho de sucesso

Não foi percorrido com o modelo local — nenhuma das três tentativas produziu
ficha válida. Ele foi verificado logo em seguida com a DeepSeek; ver a segunda
parte deste documento.

---

# Extração com DeepSeek — mesma medição, 06/08/2026

Repetição do exercício acima com `deepseek-v4-flash` via API, para comparar.

| | |
|---|---|
| Modelo | `deepseek-v4-flash` (protocolo OpenAI, `https://api.deepseek.com`) |
| Preço | US$ 0,14 / 0,28 por milhão (entrada / saída); cache a US$ 0,0028 |
| Resultado | **ficha válida e persistida** |
| Custo | US$ 0,0068 no processamento do edital |

## Comparação com o gabarito

| Campo | DeepSeek | Gabarito |
|---|---|---|
| Processo | `1002465-53.2023.8.26.0100` | igual |
| Rito | judicial | igual |
| Ocupação | `not_informed` | igual |
| Matrícula | 106.233 | igual |
| Avaliação atualizada | R$ 800.933,79 | igual |
| Riscos / lacunas | 5 / 4 | 6 / 5 |

## Três obstáculos, e o que cada um exigiu

**`json_schema` é recusado.** A DeepSeek responde
`This response_format type is unavailable now`. Isso transformou o antigo flag
booleano de saída estruturada em três modos — `schema`, `json` e `none` —, com
o schema indo no texto do prompt quando o provedor não o impõe.

**Modelo de raciocínio consome o orçamento de saída.** Com `max_tokens: 16000`
ele gastou os 16 mil inteiros pensando (`reasoning_tokens: 16000`) e devolveu
conteúdo vazio com `finish_reason: length`. Com 64 mil: 24 mil de raciocínio e
~3,7 mil de resposta. Daí `LLM_EXTRACTION_MAX_TOKENS` existir.

**Aninhamento errado.** A primeira ficha válida em JSON pôs `auctioneer_fee` e
`payment` dentro de `debts`. Enumerar as chaves de primeiro nível no prompt
resolveu — custa poucos tokens e o `additionalProperties: false` pegaria o erro
de qualquer forma.

## Cache

Na segunda execução do mesmo edital, 11.520 dos 11.522 tokens de entrada vieram
do cache do provedor. É o que justifica a tabela de preços ter `cachedInput`
por modelo: a 10% da Anthropic, esse custo seria contabilizado cinco vezes
maior do que o real.

## O que ainda não está bom

**10 das 35 citações não existem literalmente no edital.** O modelo parafraseia
onde deveria copiar. Isso passa no schema e falha em
`tests/check_citations.py`, que é justamente a diferença entre "tem o formato
certo" e "diz a verdade". Fica como métrica de qualidade para a Fase 7 e como
alvo de ajuste de prompt — não de código.

## Conclusão

O `deepseek-v4-flash` dá conta do Estágio 1 a um custo que muda a economia do
projeto: ~US$ 0,007 por edital contra ~US$ 0,25 estimados com Opus 5. Os US$ 5
do orçamento cobrem centenas de editais. O gasto acumulado destas medições foi
de US$ 0,0205.

---

# Efeito dos extratores determinísticos — 06/08/2026

Terceira medição sobre o mesmo edital, agora com valores, datas de praça,
matrícula, percentuais e áreas extraídos por expressão regular e entregues ao
modelo como conferência.

| | Sem extratores | Com extratores |
|---|---|---|
| Citações localizadas | 25 de 35 (71%) | 30 de 36 (83%) |
| Processo, matrícula, avaliação | corretos | corretos |
| Custo | US$ 0,0068 | US$ 0,0109 |

O ganho está onde se esperava: o modelo passou a copiar em vez de parafrasear
os campos em que havia âncora factual no prompt. As seis citações restantes são
texto corrido — cláusulas de responsabilidade e condições de pagamento —, onde
não há número a ancorar. Alvo de ajuste de prompt na Fase 7.

## Duas armadilhas encontradas no edital real

**Matrícula do imóvel × do leiloeiro.** O documento traz "matriculado na Junta
Comercial ... sob o nº 798" (o leiloeiro) e "matrícula nº 106.233 do 4º
Cartório de Registro de Imóveis" (o imóvel). O extrator exige que o texto
**depois** do número mencione registro de imóveis.

Olhar o texto **anterior** parecia prudente e estava errado: como o edital cita
o leiloeiro antes do imóvel, a frase anterior contaminava a matrícula correta,
que passava a ser descartada. O teste que pega isso põe as duas no mesmo texto.

**Datas de praça absorvendo o resto do edital.** Associar cada data ao último
rótulo de praça anterior fazia a segunda praça engolir a data de uma resolução
do CNJ de 2016 e dois vencimentos de IPTU, milhares de caracteres adiante. Um
limite de proximidade resolve.

## Um vazamento de dado pessoal, e onde ele estava

Os extratores mascaram o CPF no valor, mas o **contexto** de cada ocorrência —
que também vai para o prompt e para o banco — carregava o número inteiro. O CPF
de um executado escapava pelo contexto de um valor monetário vizinho.

A máscara passou para `_context()`, ponto único por onde todo contexto sai, e o
Markdown é higienizado já na conversão, que é onde o documento entra no
sistema. Verificado contra o edital real: dois CPFs presentes no PDF, nenhum na
saída dos extratores nem na ficha persistida.

O teste que pegou isso afirma o que importa e não o mecanismo — "o CPF completo
não aparece em lugar nenhum da saída" —, e por isso continuaria válido se a
implementação mudasse.
