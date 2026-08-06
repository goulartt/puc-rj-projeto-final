# Domínio

> Registro exigido pelo slide 20: pessoa, momento, evidência e critério de
> acerto. É o que amarra o resto das decisões do projeto.

## Pessoa

Comprador pessoa física, primeiro ou segundo leilão, sem formação jurídica.
Encontrou um lote num portal de leiloeiro e tem em mãos um edital de 4 a 10
páginas escrito para advogados.

Não é investidor profissional: não tem equipe para ler matrícula, não sabe
distinguir um ônus que se extingue de um que acompanha o imóvel, e não tem
como avaliar sozinho se o preço mínimo é bom.

## Momento

À noite, com o edital aberto, antes do prazo do leilão — e com uma dúvida
pontual **que não sabe formular por escrito**. Ouviu "imissão na posse" e não
sabe se é "emissão". Quer perguntar sobre a dívida que gruda no imóvel e não
sabe que isso se chama *propter rem*.

É aí que uma interface conversacional ganha do formulário: a pessoa descreve o
problema com as palavras que tem, e o assistente traduz.

## Evidência

Editais de leilão judicial são documentos públicos, publicados no portal do
leiloeiro por exigência do art. 887, § 2º do CPC. Um deles está em
`data/editais/edital-exemplo.pdf` e serve de caso de teste.

Não usamos dado de pessoa usuária real. Os CPFs dos executados constam do
edital e são deliberadamente omitidos da ficha — ver `docs/privacy.md`.

## Acerto

Dois critérios observáveis, ambos mensuráveis pela suíte da Fase 7:

**1. Toda afirmação sobre o edital cita o trecho que a sustenta.**
Verificável por máquina: `tests/check_citations.py` confere que cada citação
existe mesmo no documento convertido. Uma ficha em que o modelo parafraseou
passa no schema e falha aqui.

**2. O assistente recusa o que está fora do escopo.**
Aconselhamento jurídico, "vale a pena comprar?" e previsão de valor não são
respondidos. Recusar bem é resultado, não limitação.

> Meu assistente ajudará **um comprador iniciante de imóveis em leilão** a
> **entender termos, prazos e riscos do edital antes de dar um lance** usando
> **o PDF do edital e perguntas em linguagem natural**, e vou avaliar isso por
> **taxa de citação verificável e taxa de recusa correta**.

## O que o edital de exemplo ensinou

Três coisas que só apareceram ao processar um documento real, e que mudaram o
desenho:

**Nem todo número de processo no edital é o processo do imóvel.** Dos cinco
números CNJ do documento, quatro são precedentes jurisprudenciais citados no
texto — causas alheias ao lote. Consultá-los traria a situação processual de
outra pessoa para dentro da ficha.

**A omissão é o achado mais valioso.** O edital não diz se o imóvel está
ocupado, e ao mesmo tempo transfere ao arrematante o custo da imissão na
posse. Um assistente que só extrai campos preenchidos perderia exatamente o
risco mais caro. Daí o campo `gaps` ser obrigatório no schema.

**O edital se contradiz e resolve a própria contradição.** Ele atribui o IPTU
ao arrematante pelo art. 130 do CTN e, no parágrafo seguinte, cita o Tema 1134
do STJ dizendo que essa atribuição é inválida. Um leigo lê a primeira frase e
desiste. Traduzir esse tipo de coisa é o valor do produto.

## Limite

O assistente organiza e explica o conteúdo do edital. **Não substitui análise
jurídica profissional**, não recomenda arrematar, e não estima retorno. Quando
a resposta depende de documento que ele não tem — matrícula atualizada,
certidão de débitos, autos do processo —, ele diz isso e aponta onde apurar.
