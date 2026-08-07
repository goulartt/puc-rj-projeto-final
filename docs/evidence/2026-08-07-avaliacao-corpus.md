# Avaliação do corpus — 2026-08-07

5 edital(is) processado(s) pelo pipeline, medidos pelo
Markdown e pela ficha que ficaram no banco — a mesma coisa que o
usuário recebeu.

| Edital | Rito | Tamanho | Citação verificável | Determinístico × modelo |
|---|---|---|---|---|
| `4c7c47afbf5c` sl-bem-18629-6a51259750067-6 | judicial | 5k | 26/26 (100%) | 5/5 (100%) |
| `2fc2408665b3` edital-exemplo.pdf | judicial | 10k | 32/34 (94%) | 6/6 (100%) |
| `52a4dbc924cf` 068968d3-92d7-4919-ace8-5bba | judicial | 17k | 30/32 (94%) | 5/6 (83%) |
| `1b429e481fa1` 20260709115019_60561_94066 ( | judicial | 13k | 31/35 (89%) | 5/5 (100%) |
| `59354fd9b396` edital extrajudicial.pdf | extrajudicial | 52k | 21/21 (100%) | 2/3 (67%) |

**Agregado:** citação 140/148 (95%) · determinístico × modelo 23/25 (92%)

## `2fc2408665b3` edital-exemplo.pdf

**Citações não localizadas no edital:**
- `debts.buyer_liability` — "Eventuais ônus sobre o imóvel correrão por conta do arrematante, exceto débitos de IPTU e demais taxas e impos…"
- `property.address` — "sito à Av. Dr. Cardoso de Melo, nº 146, no 28º Subdistrito - Jardim Paulista…"

## `52a4dbc924cf` 068968d3-92d7-4919-ace8-5bba64540849.pdf

**Divergência entre regex e modelo** — cada uma merece olhar, porque uma das duas está errada:
- `primeira_praca`: regex `['2026-11-02', '2026-11-05']`, modelo `2026-08-03`

**Citações não localizadas no edital:**
- `payment.installment_terms` — "proposta de pagamento de pelo menos vinte e cinco por cento do valor do lance à vista e o restante parcelado e…"
- `encumbrances[3]` — "Av.07 - indisponibilidade processo nº 1514200-27.2006.5.09.0009…"

## `1b429e481fa1` 20260709115019_60561_94066 (1).pdf

**Citações não localizadas no edital:**
- `risks[2]` — "O arrematante deverá pagar o preço no ato ... oferta de pagamento de pelo menos 25% do lance a vista e o resta…"
- `auction.first_round` — "PRIMEIRO(A) LEILÃO/PRAÇA: Dia 21 de agosto de 2026 às 09:30 ... para a venda a quem mais der, desde que não se…"
- `auction.second_round` — "SEGUNDO(A) LEILÃO/PRAÇA: Dia 28 de agosto de 2026 às 09:30 ... para a venda a quem mais der, desde que não se …"
- `payment.installment_terms` — "oferta de pagamento de pelo menos 25% do lance a vista e o restante parcelado em até 30 meses…"

## `59354fd9b396` edital extrajudicial.pdf

**Divergência entre regex e modelo** — cada uma merece olhar, porque uma das duas está errada:
- `primeira_praca`: regex `['2026-06-16']`, modelo `2026-08-25`

