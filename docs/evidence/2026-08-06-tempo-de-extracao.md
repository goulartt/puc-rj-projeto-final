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

A DeepSeek aceita `reasoning_effort: "none"`, e ele desliga de verdade: num
prompt de controle, 27,5 s → 3,1 s, sem tokens de raciocínio. `"minimal"` é
ignorado em silêncio, que é o que uma API compatível com OpenAI faz diante de
um valor desconhecido.

Três execuções de cada configuração, mesmo edital:

| Configuração | Tempo | Fichas válidas | Custo |
|---|---|---|---|
| `deepseek-v4-flash` + raciocínio | ~147 s | 3/3 | US$ 0,0053 |
| `deepseek-v4-flash` sem raciocínio | ~30 s | **1/3** | US$ 0,0027 |
| `deepseek-v4-pro` sem raciocínio | ~44 s | **2/3** | US$ 0,0087 |

**Cinco vezes mais rápido pela metade do preço, e uma ficha em três.**

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

Mantido `deepseek-v4-flash` **com** raciocínio. Uma ficha errada custa mais que
dois minutos de espera, e o aviso de recebimento já resolveu o pior do
problema, que era a pessoa ficar sem sinal nenhum.

`LLM_EXTRACTION_REASONING` fica no `.env` para que a escolha seja explícita e
reversível, e para que quem trocar de provedor no futuro encontre a alavanca
documentada em vez de redescobri-la.

## O caminho não percorrido

O padrão que resolveria: em vez de aceitar ou rejeitar a ficha, **devolver os
erros de validação ao modelo e pedir a correção**. Os erros observados são
todos triviais de corrigir com o defeito apontado — "seu trecho tem 812
caracteres, o limite é 600, use `[...]`".

Se a correção acertasse na segunda tentativa, seriam ~56 s pela metade do custo
atual. Não implementado, e por isso não medido — o número acima é estimativa, e
está registrado como tal.
