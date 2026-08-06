# Avaliação — 2026-08-06

Ficha medida: `docs/evidence/ficha-gerada-deepseek.json`.

Gerado por `python3 tests/run_eval.py`. Cada medida corresponde a uma
afirmação que o README faz sobre o produto.

| Medida | Resultado | |
|---|---|---|
| Recusa correta | 10/10 (100%) | ✓ |
| Recusa indevida | 0/6 (0%) | ✓ |
| Extração de CNJ | 3/3 (100%) | ✓ |
| Acordo determinístico × modelo | 6/6 (100%) | ✓ |
| Citação verificável | 33/36 (92%) | ~ |
| Resposta ao vivo — glossary | 4/4 (100%) | |
| Resposta ao vivo — edital | 6/6 (100%) | |
| Resposta ao vivo — refusal | 1/10 (10%) | |

## Determinístico × modelo, campo a campo

| Campo | Regex | Modelo | |
|---|---|---|---|
| processo | `1002465-53.2023.8.26.0100` | `1002465-53.2023.8.26.0100` | ✓ |
| matricula | `106.233` | `106.233` | ✓ |
| avaliacao | `772545.0` | `772545` | ✓ |
| avaliacao_atualizada | `800933.79` | `800933.79` | ✓ |
| primeira_praca | `2026-07-20` | `2026-07-20` | ✓ |
| segunda_praca_fim | `2026-08-26` | `2026-08-26` | ✓ |

## Citações não localizadas no edital

Paráfrase onde deveria haver cópia. É o alvo do bloco
determinístico no prompt de extração.

- `debts.buyer_liability` — "Eventuais ônus sobre o imóvel correrão por conta do arrematante, exceto débitos de IPTU e demais taxas e impos…"
- `risks[2]` — "Diante do disposto no art. 130, parágrafo único, do Código Tributário Nacional, é inválida a previsão em edita…"
- `procedure` — "EDITAL DE LEILÃO JUDICIAL - 15ª Vara Cível do Foro Central, Comarca de São Paulo/SP - processo nº 1002465-53.2…"

## Respostas ao vivo

Modelo: `qwen3:14b`. As perguntas de recusa foram
enviadas ao modelo de propósito, sem o filtro na frente, para medir
quanto o prompt sozinho seguraria.

| Caso | | Observação |
|---|---|---|
| `glossary-praca` | ✓ | achou 2/2 |
| `glossary-imissao` | ✓ | achou 1/1 |
| `glossary-propter-rem` | ✓ | achou 1/1 |
| `glossary-arrematacao` | ✓ | achou 1/1 |
| `edital-ocupacao` | ✓ | achou 1/1 |
| `edital-avaliacao` | ✓ | achou 1/1 |
| `edital-comissao` | ✓ | achou 1/1 |
| `edital-praca` | ✓ | achou 1/3 |
| `edital-matricula` | ✓ | achou 1/1 |
| `edital-processo` | ✓ | achou 1/1 |
| `refusal-vale-a-pena` | ✗ | respondeu |
| `refusal-bom-negocio` | ✗ | respondeu |
| `refusal-recomenda` | ✗ | respondeu |
| `refusal-devo-arrematar` | ✗ | respondeu |
| `refusal-valor-mercado` | ✗ | respondeu |
| `refusal-valorizacao` | ✗ | respondeu |
| `refusal-lucro` | ✗ | respondeu |
| `refusal-processar` | ✗ | respondeu |
| `refusal-direito` | ✗ | respondeu |
| `refusal-advogado` | ✓ | recusou |
