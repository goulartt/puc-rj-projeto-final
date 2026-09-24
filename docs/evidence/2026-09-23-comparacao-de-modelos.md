# Comparação de modelos na OpenRouter — 23/09/2026

Ao trocar a API direta da DeepSeek e o `qwen3:14b` local pela OpenRouter, a
pergunta era se `deepseek/deepseek-v4-flash` continuava sendo a melhor escolha.
A resposta saiu de medição pelo pipeline real (mesmo prompt, mesmo schema,
mesma validação e mesmo passo de correção), com o modelo trocado só na
execução de teste.

## Candidatos

Filtro no catálogo: saída até US$ 1/MTok, contexto de pelo menos 128k tokens e
suporte a `response_format`. Dos 94 que passaram, sete foram testados, entre os
mais recentes e os com histórico de saída estruturada. Os de 3 a 8B ficaram de
fora: a ficha tem 12 blocos aninhados, com citação em cada folha.

## Primeira rodada: uma extração do edital de exemplo por modelo

| Modelo | Tempo | Ficha | Citação | Custo |
|---|---|---|---|---|
| `google/gemma-4-26b-a4b-it` | 174 s | válida de primeira | 29/29 | US$ 0,0042 |
| `qwen/qwen3.7-flash` | 213 s | após correção | 31/33 | US$ 0,0024 |
| `deepseek/deepseek-v4-flash` | 425 s | após correção | 25/33 | US$ 0,0118 |
| `xiaomi/mimo-v2.6-flash` | 489 s | após correção | 25/36 | US$ 0,0080 |
| `nvidia/nemotron-3-nano-30b-a3b` | 141 s | após correção | 21/30 | US$ 0,0049 |
| `qwen/qwen3.5-flash-02-23` | — | derrubou a execução | — | — |
| `openai/gpt-oss-20b` | 22 s | inválida: omitiu 6 blocos | — | US$ 0,0009 |

## Segunda rodada: os três primeiros, dois editais

Uma execução por modelo não basta para escolher; um documento só já levou este
projeto a uma recomendação que o primeiro edital diferente desmentiu. Os três
primeiros rodaram de novo no exemplo e num segundo edital real (12k
caracteres, datas de praça dentro de tabela), pelo smoke
`tests/workflows/94-ingest-corpus-smoke.json`.

Somando as duas rodadas:

| Modelo | Execuções | Válida de primeira | Citação | Tempo | Custo/edital |
|---|---|---|---|---|---|
| **`google/gemma-4-26b-a4b-it`** | 4 | **4/4** | **121/122 (99%)** | 82–174 s | US$ 0,0034–0,0044 |
| `qwen/qwen3.7-flash` | 4 | 1/4 | 111/128 (87%) | 203–236 s | US$ 0,0019–0,0027 |
| `deepseek/deepseek-v4-flash` | 3 | 2/3 | 88/101 (87%) | 144–425 s | US$ 0,0054–0,0118 |

O Gemma 4 26B-A4B é um *mixture of experts* com cerca de 4B de parâmetros
ativos por token, o que o põe na faixa de custo de um modelo pequeno.

## Perguntas

Ficha do edital de exemplo, as 20 perguntas de `tests/cases.yaml`, com
`tests/run_eval.py --live`. As de recusa vão **sem** o filtro de escopo, para
medir o prompt sozinho.

| Modelo | Raciocínio | Tempo médio | Glossário | Edital | Recusa sem filtro |
|---|---|---|---|---|---|
| `qwen/qwen3.7-flash` | minimal | 14 s | 4/4 | 6/6 | 9/10 |
| `google/gemma-4-26b-a4b-it` | minimal | 12 s | 4/4 | 5/6 | 9/10 |
| `deepseek/deepseek-v4-flash` | minimal | 21 s | 4/4 | 4/6 | 9/10 |
| os três | none | 3–8 s | 4/4 | 4/6 | 8–9/10 |

Desligar o raciocínio deixa a pergunta de 2 a 5 vezes mais rápida e erra um
terço das perguntas sobre o edital. `minimal` fica.

A vantagem do Qwen sobre o Gemma nas perguntas é de uma questão, numa rodada.
Está dentro do ruído, e a escolha pode mudar com mais medição.

## Decisão

- Extração: `google/gemma-4-26b-a4b-it`.
- Perguntas: `qwen/qwen3.7-flash`.

Medido depois, na conversa completa pelo Telegram simulado: extração em 64 s
por US$ 0,0032, perguntas entre 13 e 26 s por cerca de US$ 0,0002 cada.

## Por que o DeepSeek piorou

Na API direta, o `deepseek-v4-flash` extraía em ~110 s com 88–92% de citação.
Pela OpenRouter ele variou entre 144 e 425 s. O catálogo explica: o modelo é
servido por 15 provedores terceiros, parte com quantização FP8 ou FP4, e a
própria DeepSeek não está entre eles. O roteamento padrão da OpenRouter
prioriza preço. O `qwen3.7-flash` tem um provedor só, a Alibaba, que é quem o
treinou.

O Gemma também tem vários provedores e manteve 4 de 4, então o projeto não
fixa roteamento. Se a variação aparecer, a OpenRouter aceita, no corpo da
requisição, restringir provedores e níveis de quantização.

## O que a comparação achou no próprio projeto

- **A correção de ficha derrubava a execução** quando voltava sem JSON: o
  `/validate` respondia 400 e o fluxo parava antes do nó que sabe dizer isso à
  pessoa. Foi o `qwen3.5-flash` que expôs. `Validar correcao` passou a seguir
  em erro, como `Validar ficha` já fazia.
- **Datas dentro de tabela não eram lidas.** O Docling renderiza `10 / 08 /
  2026`, com espaços em volta das barras, e a regex exigia a data colada. Os
  três modelos leram as datas certas; o extrator determinístico, nenhuma.

## Limite desta medição

Dois editais, ambos judiciais do TJSP, e de 3 a 4 execuções por modelo. O
bastante para descartar candidatos e escolher com alguma segurança, pouco para
afirmar taxas. No Gemma, o edital de exemplo saiu uma vez com o lance mínimo
da 1ª praça igual à avaliação original (R$ 772.545,00), e não à atualizada
(R$ 800.933,79) que o edital manda usar. A suíte não pegou, porque os dois
valores existem no documento.
