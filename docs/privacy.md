# Privacidade e dados

> O bloco de dados do PDF da Aula 01 (slide 41) pede quatro decisões:
> classificar, minimizar, posicionar e explicar. Este documento responde às
> quatro para este projeto.

## Classificar — que dados circulam aqui

| Dado | Classificação | Onde aparece |
|---|---|---|
| Texto do edital | público | documento publicado por exigência do art. 887, § 2º do CPC |
| CPF dos executados | **pessoal** | consta do edital de exemplo |
| Nome dos executados | **pessoal** | idem |
| Metadados processuais | público, mas sensível por contexto | DataJud |
| `chat_id` do Telegram | **pessoal** (identificador) | banco |
| PDF enviado pela pessoa | depende do que ela enviar | banco |

Um edital ser público não torna neutro reunir seus dados pessoais num banco
indexado e devolvê-los num chat. Publicidade processual serve à fiscalização
dos atos do Judiciário, não à criação de dossiês.

## Minimizar — o que fica guardado

**Não persistimos dado pessoal de terceiro.** A ficha não tem campo para nome
ou CPF de executado, e isso é decisão de esquema, não de prompt: mesmo que o
modelo tentasse preencher, `additionalProperties: false` rejeitaria.

O `exequente` é guardado porque em execução de condomínio ou tributária é
pessoa jurídica, e o dado identifica a natureza da dívida — que é justamente o
risco que interessa ao comprador.

O que fica no banco:

- Markdown do edital — necessário para conferir citação
- ficha estruturada
- `chat_id`, para devolver a resposta a quem perguntou
- `sha256` do PDF, para não reprocessar o mesmo arquivo
- custo por chamada de LLM, sem conteúdo

Do DataJud guardamos movimentos e o resumo de risco. O prompt do Estágio 2
instrui explicitamente a resumir **risco procedimental, não pessoas**.

## Posicionar — onde cada coisa é processada

| Etapa | Onde roda | O que sai da máquina |
|---|---|---|
| Conversão do PDF | container local (Docling) | nada |
| Extratores determinísticos | local | nada |
| Extração da ficha (Estágio 1) | **OpenRouter** | o texto do edital |
| Consulta processual | API pública do CNJ | só o número do processo |
| Q&A (Estágio 3) | **OpenRouter** | ficha e pergunta, nunca o edital inteiro |

O Estágio 1 é o único que envia o documento a terceiro. Isso é consequência
direta do desenho de dois estágios: o edital sai **uma vez**, e as perguntas
seguintes trafegam só a ficha.

A OpenRouter é intermediária: o texto passa por ela e segue para o provedor que
ela escolher para o modelo pedido. Quem opera o sistema herda as políticas de
retenção dos dois, e a OpenRouter permite, na configuração da conta, excluir
provedores que treinam com os dados recebidos.

O `chat_id` nunca vai para provedor de LLM.

## Explicar — o que a pessoa sabe

O bot informa, no primeiro contato e no `/ajuda`:

- que é assistente automatizado, não advogado;
- que o PDF enviado é processado e o texto guardado para responder perguntas;
- que a extração usa um modelo de terceiro, quando for o caso;
- como apagar os dados (`/apagar` remove editais e fichas daquele `chat_id`).

## Retenção

Editais e fichas ficam enquanto a conversa fizer sentido. `/apagar` remove
tudo daquele chat. Não há prazo automático definido — e essa é uma lacuna
consciente deste estágio do projeto, registrada aqui em vez de omitida.

## Scrape (iteração 2)

Antes de raspar qualquer portal: verificar `robots.txt` e termos de uso,
preferir feed ou API quando existir, e usar intervalo conservador. Se o portal
proibir, não raspamos — a alternativa é pedir à pessoa que cole o link.

## O que decidimos não fazer

- **Não raspar portal de tribunal** (e-SAJ, PJe). Têm captcha e termos
  restritivos, e quebram a cada mudança de layout. DataJud é a rota pública
  legítima; onde ela não cobre, a resposta certa é dizer que não cobre.
- **Não montar base consultável de executados.** O produto responde sobre *um*
  edital que *a pessoa* trouxe. Índice cruzado de nomes seria outro produto,
  com outro dever legal.
- **Não guardar CPF**, mesmo constando de documento público.
