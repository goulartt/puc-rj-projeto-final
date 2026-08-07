# Por que a extração leva 147 segundos — 06/08/2026

## Onde o tempo está

| Etapa | Tempo | Fatia |
|---|---|---|
| Aviso de recebimento | 0,9 s | — |
| Conversão do PDF (Docling) | 3,9 s | 2,6% |
| **Chamada ao modelo** | **147,1 s** | **97,4%** |
| Consulta processual, tradução, envio | < 0,1 s | ~0% |

Nós de código, Postgres e serviço de documentos somam menos de 100 ms juntos.
Otimizar qualquer coisa que não seja a chamada ao modelo não muda nada.

## E dentro da chamada?

```
prompt_tokens        12.291   (12.288 vindos de cache — entrada é quase de graça)
completion_tokens    18.684
  ├─ reasoning       14.366   ← 77%
  └─ ficha            4.318
                      127 tokens de saída por segundo
```

**Três quartos do tempo o modelo passa pensando, não escrevendo a ficha.** A
ficha em si sai em ~34 s; os outros ~113 s são raciocínio.

## Desligar o raciocínio funciona — e quebra a ficha

A DeepSeek respeita `reasoning_effort` na escala inteira. Prompt de controle,
`max_tokens=4000`:

| `reasoning_effort` | Tempo | Tokens de raciocínio |
|---|---|---|
| `none` | 4,0 s | 0 |
| `minimal` | 22,9 s | 1.604 |
| `low` | 26,9 s | 2.143 |
| `medium` | 48,1 s | ≥ 4.000 (teto) |
| `high` | 52,7 s | ≥ 4.000 (teto) |
| ausente | 53,9 s | ≥ 4.000 (teto) |

**Correção de uma medição anterior.** A primeira rodada deste teste usou
`max_tokens=2000` e concluiu que `minimal` era ignorado, porque ele bateu no
teto igual ao padrão. O teto é que escondia a diferença. Um limite baixo demais
faz duas configurações distintas parecerem idênticas — vale para qualquer
medição de modelo com raciocínio.

Três execuções de cada configuração, mesmo edital:

| Configuração | Tempo | Fichas válidas | Custo | Citação |
|---|---|---|---|---|
| `flash`, raciocínio padrão | ~147 s | 3/3 | US$ 0,0053 | 33/36 (92%) |
| **`flash`, `minimal`** | **~110 s** | **3/3** | **US$ 0,0040** | **30/34 (88%)** |
| `flash`, `none` | ~30 s | **1/3** | US$ 0,0027 | — |
| `pro`, `none` | ~44 s | **2/3** | US$ 0,0087 | — |

Sem raciocínio nenhum: cinco vezes mais rápido pela metade do preço, e uma
ficha em três. Com `minimal`: 25% mais rápido, 25% mais barato, três em três.

## O que exatamente se perde

Não é compreensão — é conformidade com o schema, e sempre nos mesmos pontos.

A falha recorrente do `flash` é o campo `debts.buyer_liability.quote`, que
guarda a contradição do IPTU: o edital atribui o tributo ao arrematante e, no
mesmo texto, cita o Tema 1134 do STJ invalidando a atribuição. O prompt manda
registrar os dois lados.

Com raciocínio, o modelo une os dois trechos com `[...]` e fica em **483
caracteres**. Sem raciocínio, ele copia a cláusula inteira e estoura o limite de
600. Acrescentar o limite explicitamente ao prompt não resolveu.

O `pro` sem raciocínio falhou de outro jeito — `debts.condo_fees: None is not
of type 'object'` —, o que confirma que o problema é a disciplina de formato, e
não a capacidade do modelo.

O limite de 600 não é arbitrário: numa ficha boa, o maior de 36 trechos tem 483
caracteres e a mediana é 118. Afrouxá-lo trocaria citação por transcrição.

## Decisão

**`LLM_EXTRACTION_REASONING=minimal`.** É mais rápido e mais barato que o padrão
sem perda observada de validade, e a diferença de citação — 3 falhas contra 4,
em ~35 trechos — está dentro da variação natural: a mesma configuração produziu
12.207, 16.376 e 13.888 tokens de saída em três execuções seguidas.

Três execuções por configuração não provam confiabilidade de 100%; provam que
`none` falha com frequência e que `minimal` não falhou nesta amostra. A
distinção importa, e o número de execuções está aqui para que ninguém leia
mais do que ele diz.

`none` fica descartado: 1 ficha válida em 3 não é uma opção, por mais rápido
que seja.

## A correção, agora implementada

Antes era estimativa. Foi implementada e medida.

Ficha reprovada não é mais descartada: os erros de validação voltam ao modelo
com o documento inteiro e um pedido de correção pontual. Uma tentativa só —
duas falhas seguidas no mesmo documento indicam problema no edital ou no
schema, não algo que insistir resolva.

Seis execuções com `reasoning_effort=none`, que falha com frequência e por isso
serve para exercitar o caminho:

| Resultado | Execuções | Tempo |
|---|---|---|
| válida de primeira | 3 | ~34 s |
| **resgatada pela correção** | **1** | 64 s |
| falhou mesmo após corrigir | 2 | ~59 s |

O mecanismo funciona. O que ele não conserta é um modelo que também não
consegue seguir a instrução na segunda tentativa: sem raciocínio, a correção
falha pelo mesmo motivo que a extração.

Com `minimal`, sete execuções sobre o edital de exemplo passaram de primeira.
A falha observada em produção foi em **outro documento** — um edital de direitos
fiduciários, cuja cláusula de IPTU é redigida de forma mais longa. É essa a
classe de caso que a correção passa a resgatar, e é ela que justifica manter o
caminho mesmo com o exemplo passando sempre.

Custo: a correção acrescenta cerca de US$ 0,002 quando dispara, e o campo
`cost_usd` da ficha já soma as duas chamadas — a tentativa que falhou também
foi paga.

## Uma amostra que era pequena demais

`minimal` foi adotado com base em três execuções, e este documento afirmou
3/3. A primeira ficha real que passou por ele, num edital diferente, falhou.
Três execuções sobre um documento não medem confiabilidade — medem que aquele
documento passou três vezes.
