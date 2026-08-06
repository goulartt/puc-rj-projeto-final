# Estágio 3 — o chat, e um limite que o prompt não sustentou

06/08/2026. Bot **Arremata AI** (`@LeilaoImovelAnaliseBot`), fluxo
`01-telegram-chat.json`, 27 nós.

## O que aconteceu no primeiro teste

O `prompts/qa-system.md` diz, em texto claro:

> Você **não**: (…) opina se vale a pena arrematar, se o preço está bom, ou
> quanto o imóvel vale;

Perguntado *"Vale a pena comprar esse imóvel?"*, o qwen3:14b respondeu com
análise de investimento — "Pontos positivos", "Localização estratégica",
"valorização imobiliária historicamente sólida" — e citou o bairro **Jardim
Paulista** como argumento. Duas falhas de uma vez: opinou sobre a compra e
inventou contexto de mercado que não está na ficha.

Instrução em prompt é um pedido. Num modelo pequeno, um pedido que ele às
vezes atende. O limite que o produto promete não pode depender disso.

## A correção

`services/extractors/scope.py` classifica a pergunta **antes** de qualquer
chamada de modelo. Fora de escopo não chega ao modelo: a recusa é uma resposta
fixa, e a instrução do prompt vira segunda camada em vez de única.

Três categorias, com a resposta pronta em cada uma: conselho de investimento,
estimativa de valor de mercado e orientação jurídica. Cada recusa nomeia o
limite e oferece o que dá para fazer — porta fechada sem saída não ajuda.

**O filtro é conservador de propósito.** Recusar uma pergunta respondível é um
defeito pior do que deixar passar uma duvidosa: a segunda ainda encontra o
prompt pela frente, a primeira não tem resgate. `quanto vale` ficou de fora do
padrão de avaliação porque *"quanto vale a comissão do leiloeiro?"* é pergunta
sobre o edital e tem resposta na ficha. Metade dos testes de
`tests/test_scope.py` guarda exatamente isso: as perguntas que **precisam
passar**.

## Medição

Cinco mensagens sintéticas pelo fluxo real (`96-chat-smoke`), uma por caminho:

| Mensagem | Rota | Chamou o modelo? |
|---|---|---|
| "Esse imóvel está ocupado?" | `question` | sim |
| "Vale a pena comprar esse imóvel?" | `out_of_scope` | **não** |
| "O que é comissão do leiloeiro?" | `question` | sim |
| "Posso processar o antigo dono?" | `out_of_scope` | **não** |
| `/ajuda` | `help` | não |
| foto em vez de PDF | `unsupported_file` | não |

Duas chamadas em vez de quatro. A recusa também é a resposta mais barata.

Custo do lote: **US$ 0** — o Q&A roda no qwen3:14b local. O modelo pago é usado
só na extração, uma vez por edital.

## O smoke test deriva do fluxo real

`build_chat_smoke()` não reimplementa o chat: pega `build_chat()`, troca o
gatilho do Telegram por mensagens sintéticas e os envios por passagem direta.
Roteamento, escopo, carga da ficha, montagem do prompt e gateway são o mesmo
código que roda em produção — não uma cópia que envelhece em silêncio.

## Três defeitos que o smoke test pegou

**O binário sumia no roteamento.** Um nó de código que devolve só `json`
descarta o anexo, e o PDF nunca chegava ao Docling.

**O nó de contexto ficou órfão.** Estava definido e nunca foi ligado ao fluxo,
então o preparador lia a linha crua do Postgres e a pergunta chegava vazia ao
modelo — que respondia com erro 400 em vez de silêncio, o que ao menos foi
honesto.

**`.first()` num nó antes do Switch pega o primeiro item de todos os ramos**,
não o do ramo em execução. Em produção há uma mensagem por execução e isso
nunca apareceria; com cinco mensagens, apareceu. A seleção passou a ser pela
rota (`filter(i => i.json.route === 'question')`), que diz o que se quer e não
depende da ordem das saídas.

## Publicar o bot

`scripts/expose-bot.sh`. O túnel rápido do Cloudflare sorteia um domínio a cada
início e o n8n só lê `N8N_WEBHOOK_URL` no boot, o que cria uma ordem não óbvia:
túnel → esperar responder de fato → gravar no `.env` → **recriar** o n8n
(`restart` mantém o ambiente antigo) → apagar o webhook velho → reiniciar →
conferir no Telegram.

Feita fora de ordem, o Telegram fica apontando para um domínio morto e não há
erro visível em lugar nenhum: a mensagem simplesmente não chega. Foi o que
aconteceu aqui antes de o script existir.
