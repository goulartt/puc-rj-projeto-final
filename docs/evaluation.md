# Avaliação — o que é medido e o que a medida não alcança

`python3 tests/run_eval.py --live --out docs/evidence/`
Relatórios datados em [`evidence/`](evidence/).

Cada medida existe porque o README faz uma afirmação, e afirmação sem medida é
propaganda. O que segue é o que cada número significa e — mais importante —
onde ele para.

## As medidas

**Recusa correta.** Dez perguntas que pedem exatamente o que o produto declara
não fazer. É o critério de aceite da suíte: falhar aqui reprova a execução com
código de saída 1, porque é o limite jurídico do produto.

**Recusa indevida.** Seis perguntas que *parecem* as de cima e têm resposta
legítima — "quanto vale a comissão do leiloeiro?" é uma delas. Existe como
contramedida: um filtro que recusa tudo tira nota máxima na medida anterior e
destrói o produto. As duas só significam alguma coisa juntas.

**Extração de CNJ.** Número válido reconhecido, dígito verificador inválido
rejeitado, número de segunda instância classificado como precedente citado em
vez de processo do leilão. O terceiro caso é o que impede a ficha de puxar
jurisprudência alheia do DataJud.

**Acordo determinístico × modelo.** Seis campos que a regex e o modelo leem de
forma independente. Concordância é evidência fraca de acerto e divergência é
evidência forte de problema — por isso a assimetria: divergência vira
`confidence: low` na ficha em vez de desempate silencioso.

**Citação verificável.** Toda `quote` da ficha tem de existir no edital. É a
promessa central do projeto medida diretamente.

**Passe ao vivo (`--live`).** Pergunta ao modelo de Q&A real. Mede a segunda
camada: as perguntas de recusa vão sem o filtro na frente, de propósito, para
saber quanto o prompt sozinho seguraria.

## O que os números não dizem

**Um edital.** Todas as medidas de extração e citação vêm de um único documento
(TJSP, execução de condomínio). Um edital de alienação fiduciária, um de outro
tribunal ou um digitalizado podem se comportar de outro jeito. O número é
honesto sobre esse edital e não deve ser lido como taxa geral.

**A ficha de referência foi conferida por mim.** Ela tira 100% em citação por
construção — foi escrita lendo o documento. Serve para validar o *instrumento*,
não o produto. A medida que diz algo é sempre contra
`ficha-gerada-deepseek.json`, produzida pelo pipeline.

**`must_mention` é cobertura, não gabarito.** Uma resposta pode conter o termo
certo e ainda estar errada. A medida detecta omissão, não incorreção. O caso
`edital-ocupacao` compensa isso em parte com `must_not_mention`, que é o teste
mais severo da suíte: afirmar "desocupado" onde o edital cala é o erro caro.

**O filtro de escopo é regex.** Ele pega as formulações inequívocas. Uma
pergunta rebuscada o suficiente passa — e aí encontra o prompt, que segurou 1
de 10. A cobertura real de recusa está entre 100% e algo abaixo disso, e a
suíte não sabe medir onde.

**Custo é do provedor atual.** US$ 0,011 por edital é `deepseek-v4-flash`.
Trocar `LLM_EXTRACTION_MODEL` muda o número; o gateway o recalcula, mas a
tabela de preços em `gateway.js` precisa conhecer o modelo — se não conhecer,
ele bloqueia a chamada em vez de gastar às cegas.

## O achado que mudou o desenho

O passe ao vivo foi construído para confirmar que o prompt bastava. Ele mostrou
o contrário: **1 recusa em 10**. O `prompts/qa-system.md` diz, em texto claro,
que o assistente não opina se vale a pena arrematar; perguntado exatamente
isso, o modelo respondeu com análise de investimento e citou um bairro que não
está na ficha.

Foi essa medida que transformou o limite em `services/extractors/scope.py`. Sem
ela, o produto teria sido entregue com uma promessa que o README fazia e o
sistema não cumpria — e ninguém notaria até um usuário perguntar.

Registro completo em
[`evidence/2026-08-06-chat-telegram.md`](evidence/2026-08-06-chat-telegram.md).
