"""Extratores determinísticos de campos do edital.

Rodam sobre o Markdown antes do modelo e servem a dois fins: alimentam o prompt
como dica e **conferem** a resposta dele. Divergência vira `confidence: low` na
ficha, em vez de um desempate silencioso.

O motivo de existirem é concreto. Na primeira ficha gerada por modelo, 10 das
35 citações não existiam literalmente no edital — o modelo parafraseava onde
deveria copiar. Valor, data e número de matrícula são exatamente o tipo de dado
em que paráfrase é inaceitável e regex é melhor que qualquer modelo.

Cada extração devolve o texto cru como apareceu no documento, para que a ficha
possa citar o edital em vez de reescrevê-lo.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

CONTEXT_WINDOW = 120

# Distância máxima, em caracteres, entre o rótulo da praça e uma data para que
# elas sejam consideradas relacionadas. O edital escreve as duas datas de cada
# praça logo depois do rótulo; qualquer coisa muito além é outro assunto.
ROUND_PROXIMITY = 260


@dataclass(frozen=True)
class Found:
    value: Any
    raw: str
    context: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _context(text: str, start: int, end: int) -> str:
    """Trecho ao redor da ocorrência, com CPF sempre mascarado.

    A máscara vive aqui, e não em cada extrator, porque o contexto é o caminho
    por onde o dado pessoal escapa sem ninguém notar: o valor do CPF ia
    mascarado, mas o contexto de um valor monetário vizinho carregava o número
    inteiro para a ficha e para o banco.
    """
    window = text[max(0, start - CONTEXT_WINDOW):min(len(text), end + CONTEXT_WINDOW)]
    return redact_cpf(window).strip()


def _flatten(text: str) -> str:
    """O Docling quebra linha no meio de frases; contexto sem isso vira picado."""
    return re.sub(r"\s+", " ", text)


# ─── Dinheiro ───────────────────────────────────────────────────────────────

_MONEY = re.compile(r"R\$\s*((?:\d{1,3}(?:\.\d{3})*|\d+),\d{2})")


def money(text: str) -> list[Found]:
    """Valores em reais, no formato brasileiro.

    Devolve o número já convertido e o texto original. Ambos importam: o número
    para conferir contra o modelo, o original para a citação.
    """
    text = _flatten(text)
    out: list[Found] = []
    for match in _MONEY.finditer(text):
        amount = float(match.group(1).replace(".", "").replace(",", "."))
        out.append(Found(value=amount, raw=match.group(0), context=_context(text, *match.span())))
    return out


# ─── Datas ──────────────────────────────────────────────────────────────────

_MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}
_MONTH_NAMES = "|".join(_MONTHS)

# Três grafias, todas vistas em editais reais:
#   20/07/2026            numérica
#   03/setembro/2026      mês por extenso no meio da barra
#   21 de agosto de 2026  data inteira por extenso
#
# Cada uma das duas últimas custou as datas de praça de um edital inteiro: o
# extrator devolvia lista vazia e nada sinalizava que havia falhado.
# `\s*` ao redor das barras: quando a data está numa tabela, o Docling a
# renderiza como `10 / 08 / 2026`, e a versão colada não casava — um edital
# inteiro saía sem datas de praça, enquanto três modelos diferentes as liam
# certo.
_DATE = re.compile(
    rf"\b(\d{{1,2}})\s*/\s*(\d{{2}}|{_MONTH_NAMES})\s*/\s*(\d{{4}})\b"
    rf"|\b(\d{{1,2}})\s+de\s+({_MONTH_NAMES})\s+de\s+(\d{{4}})\b",
    re.IGNORECASE,
)


def _date_parts(match: re.Match) -> tuple[str, int, str] | None:
    """Normaliza os grupos das três grafias para (dia, mês, ano)."""
    day, month, year = match.group(1), match.group(2), match.group(3)
    if day is None:
        day, month, year = match.group(4), match.group(5), match.group(6)
    if month is None:
        return None
    number = _MONTHS.get(month.lower()) if not month.isdigit() else int(month)
    if number is None or not (1 <= number <= 12):
        return None
    if not (1 <= int(day) <= 31):
        return None
    return day, number, year
# Duas grafias, e a ordem importa: `10:11 horas` precisa ser tentada antes de
# `14h00`, senão o segundo padrão casa o "11 h" de "10:11 h*oras*" e devolve
# 11:00 para um leilão que começa às 10:11.
_TIME_NEARBY = re.compile(r"(\d{1,2})\s*:\s*(\d{2})|(\d{1,2})\s*h\s*(\d{2})?")


def dates(text: str) -> list[Found]:
    """Datas em DD/MM/AAAA, normalizadas para ISO."""
    text = _flatten(text)
    out: list[Found] = []
    for match in _DATE.finditer(text):
        parts = _date_parts(match)
        if parts is None:
            continue
        day, month, year = parts
        out.append(
            Found(
                value=f"{year}-{month:02d}-{int(day):02d}",
                raw=match.group(0),
                context=_context(text, *match.span()),
            )
        )
    return out


# Rótulo da praça e as formas que aparecem em edital. `1º Leilão`, `1ª praça` e
# `primeira praça` significam a mesma coisa.
#
# `°` (sinal de grau, U+00B0) entra junto de `º` (indicador ordinal, U+00BA)
# porque são visualmente idênticos e os editais usam os dois sem critério. Um
# edital real escrito com `1° leilão` fez a extração de praças devolver lista
# vazia — o extrator não achava nada e nada indicava que algo tinha falhado.
_ORDINAL = "[ºª°o]?"

_ROUND_LABELS = [
    ("first", re.compile(rf"(1{_ORDINAL}|primeir[ao])\s*(\(a\))?\s*"
                         rf"(leil[ãa]o|pra[çc]a|hasta)", re.IGNORECASE)),
    ("second", re.compile(rf"(2{_ORDINAL}|segund[ao])\s*(\(a\))?\s*"
                          rf"(leil[ãa]o|pra[çc]a|hasta)", re.IGNORECASE)),
]


def auction_rounds(text: str) -> dict[str, list[dict]]:
    """Associa datas ao rótulo de praça que as antecede.

    O edital diz "tendo o 1º Leilão início no dia 20/07/2026 às 14h00, e se
    encerrará dia 23/07/2026". Sem associar a data ao rótulo, sobra uma lista de
    datas soltas e o modelo tem de adivinhar qual é qual.

    A regra: uma data pertence ao último rótulo de praça anterior a ela, **desde
    que esteja perto dele**. Sem o limite de distância, tudo o que vem depois do
    último rótulo é absorvido — no edital de exemplo, a segunda praça engolia a
    data de uma resolução do CNJ de 2016 e dois vencimentos de IPTU, que estão
    milhares de caracteres adiante e não têm relação nenhuma.
    """
    text = _flatten(text)

    marks: list[tuple[int, str]] = []
    for label, regex in _ROUND_LABELS:
        marks.extend((m.start(), label) for m in regex.finditer(text))
    marks.sort()

    rounds: dict[str, list[dict]] = {"first": [], "second": []}
    for match in _DATE.finditer(text):
        previous = [(position, label) for position, label in marks if position < match.start()]
        if not previous:
            continue
        position, label = previous[-1]
        if match.start() - position > ROUND_PROXIMITY:
            continue
        parts = _date_parts(match)
        if parts is None:
            continue
        day, month, year = parts
        rounds[label].append(
            {
                "date": f"{year}-{month:02d}-{int(day):02d}",
                "raw": match.group(0),
                "time": _time_after(text, match.end()),
                "context": _context(text, *match.span()),
            }
        )
    return rounds


def _time_after(text: str, position: int) -> str | None:
    """Hora logo depois da data — `20/07/2026 às 14h00`."""
    window = text[position:position + 24]
    match = _TIME_NEARBY.search(window)
    if not match:
        return None
    if match.group(1) is not None:
        hour, minute = match.group(1), match.group(2)
    else:
        hour, minute = match.group(3), match.group(4) or "00"
    return f"{int(hour):02d}:{minute}"


# ─── Matrícula do imóvel ────────────────────────────────────────────────────

# Exige o substantivo `matrícula`, e não `matriculado`: o mesmo edital diz
# "matriculado na Junta Comercial ... sob o nº 798" a respeito do leiloeiro,
# e casar isso poria o registro do leiloeiro no campo do imóvel.
# O separador entre o substantivo e o número varia de edital para edital:
# `matrícula nº 83.276`, `matrícula 83.276` e `MATRÍCULA IMOBILIÁRIA: 83.276`
# são a mesma coisa. Sem aceitar os dois-pontos, o terceiro formato não casava
# e o edital inteiro ficava sem matrícula — o que deixava a conversa presa numa
# confirmação de lote que nunca podia ser aceita.
_REGISTRY = re.compile(
    r"matr[íi]cula\s*(?:imobili[áa]ria\s*)?n?[º°.]?\s*:?\s*([\d][\d.\-/]*\d)",
    re.IGNORECASE,
)
_REGISTRY_OFFICE = re.compile(
    r"(registro\s+de\s+im[óo]ve|cart[óo]rio|oficial\s+de\s+registro|\bCRI\b|serventia)",
    re.IGNORECASE,
)
# Termos que indicam registro de OUTRA coisa que não o imóvel.
_NOT_PROPERTY = re.compile(r"junta\s+comercial|jucesp|leiloeiro|OAB|CRECI", re.IGNORECASE)


def property_registry(text: str) -> list[Found]:
    """Matrícula do imóvel no Registro de Imóveis.

    A condição é o que vem **depois** do número: precisa mencionar cartório ou
    registro de imóveis, e não pode mencionar Junta Comercial ou leiloeiro.

    Olhar o texto anterior parecia prudente e era errado: o edital cita o
    leiloeiro antes do imóvel, e a frase anterior contaminava a matrícula
    correta, que passava a ser descartada. O que vem depois é o que qualifica
    o número.
    """
    text = _flatten(text)
    out: list[Found] = []
    for match in _REGISTRY.finditer(text):
        after = text[match.end():match.end() + 140]
        if _NOT_PROPERTY.search(after):
            continue
        if not _REGISTRY_OFFICE.search(after):
            continue
        out.append(
            Found(
                value=match.group(1),
                raw=match.group(0),
                context=_context(text, *match.span()),
            )
        )
    return out


# ─── Quantos imóveis o edital cobre ─────────────────────────────────────────

def count_properties(text: str) -> int:
    """Quantos imóveis distintos o documento descreve.

    Existe porque a ficha inteira — schema, chat, destaques — assume um edital,
    um imóvel, e essa premissa é falsa com frequência. Em cinco editais reais,
    **dois** cobriam vários: um extrajudicial com sete matrículas e um judicial
    com dois leilões em datas diferentes.

    O que acontecia sem esta contagem é pior que falhar: o modelo descrevia o
    primeiro imóvel e a ficha era apresentada como se fosse *do* edital. Nada
    indicava que havia outros seis. Alguém podia dar lance no lote errado
    achando que tinha lido o documento.

    A contagem é por matrícula, que é o identificador registral do imóvel. Um
    edital com uma matrícula só é o caso simples; mais de uma exige aviso.
    """
    registries = {found.value for found in property_registry(text)}
    return len(registries)


# Ordenados do mais específico para o mais ambíguo, e a ordem é a regra de
# desempate. Nem a primeira nem a última menção servem: `lote` e `vaga`
# aparecem como parte do endereço ("Lote nº 32 da Quadra 07") e como acessório
# ("apartamento com vaga de garagem"), então uma janela pode conter os três.
# Ganha o termo mais específico presente.
_LOT_KINDS = [
    "apartamento", "casa", "sobrado", "pr[ée]dio", "barrac[ãa]o",
    "sala comercial", "loja", "ch[áa]cara", "s[íi]tio", "fazenda",
    "gleba", "terreno", "sala", "lote", "vaga",
]
_LOT_KIND = re.compile(r"(?i)\b(" + "|".join(_LOT_KINDS) + r")\b")
_LOT_CITY = re.compile(r"(?i)Comarca de\s+([A-ZÁ-Ú][\wÀ-ú\s]{2,28}?/[A-Z]{2})")
# Janela antes da matrícula onde o edital descreve o imóvel. Depois dela vem o
# cartório e o próximo lote.
#
# 420 era curto: num edital real a descrição termina em "fração ideal no
# terreno … vaga na garagem", e o "Apartamento nº 144" que abre o parágrafo
# ficava de fora. O imóvel virava "Terreno" na pergunta de confirmação.
LOT_WINDOW = 900

# Ocorrências que parecem tipo de imóvel e não são. `fração ideal no terreno`
# descreve a cota do condomínio; `vaga na garagem` é acessório do apartamento.
# Sem isto, um apartamento em prédio é classificado pela descrição do prédio.
_NOT_A_KIND = re.compile(
    r"(?i)(fra[çc][ãa]o ideal[^.]{0,40}terreno"
    r"|[áa]rea[^.]{0,20}terreno"
    r"|vaga[^.]{0,40}garagem"
    r"|terreno e coisas de uso comum)"
)


# ─── Catálogo em tabela (Caixa e similares) ─────────────────────────────────
#
# Editais de venda em lote da Caixa não descrevem os imóveis em parágrafos: são
# catálogos com centenas de linhas de tabela, uma por imóvel, agrupadas por
# cidade. O de referência tinha 483 imóveis em 215 tabelas. Cada linha traz
# item, empreendimento, endereço, bairro, descrição (com `Matrícula: N
# Ofício: M`), número do bem, valor de venda e valor de avaliação.
#
# O leitor de parágrafo não via nenhum deles: exige "Registro de Imóveis" depois
# do número, e ali vem "Ofício". Com zero lotes, a conversa pedia um "sim"
# genérico e o modelo escolhia sozinho um imóvel entre os 483.

_CATALOG_REGISTRY = re.compile(r"Matr[íi]cula:\s*([\d][\d.\-/]*\d|\d)", re.IGNORECASE)
_HEADER_CELL = re.compile(r"\s*Estado:\s*([A-Z]{2})\s*-?\s*(?:Cidade:)?\s*(.*)")
_COLUMN_LABEL = re.compile(
    r"\s*-?\s*(Empreendimento|Endere[çc]o|Bairro|Descri[çc][ãa]o|N[úu]mero do bem|"
    r"Valor de Venda.*|Valor de Avalia[çc][ãa]o.*)\s*$", re.IGNORECASE)


def _header_city(linha: str) -> tuple[str, str] | None:
    """UF e cidade de um cabeçalho de tabela do catálogo.

    O Docling distribui o nome da cidade de um jeito diferente em cada tabela:
    "Estado: SP - Cidade: SAO PAULO - Endereço" numa, "Estado: MG - Cidade: |
    Estado: MG - UBERLANDIA Empreendimento" noutra, com o nome escorregando
    para a coluna seguinte. Um padrão fixo deixava 21 imóveis sem cidade. Aqui
    cada célula perde o rótulo da coluna e vence o nome que mais se repete.
    """
    uf = None
    candidatos: dict[str, int] = {}
    for celula in linha.strip().strip("|").split("|"):
        m = _HEADER_CELL.match(celula)
        if not m:
            continue
        uf = uf or m.group(1)
        resto = _COLUMN_LABEL.sub("", m.group(2)).strip(" -")
        if resto and re.fullmatch(r"[A-ZÀ-Ú][A-ZÀ-Ú' .-]+", resto):
            candidatos[resto] = candidatos.get(resto, 0) + 1
    if not uf or not candidatos:
        return None
    return uf, max(candidatos, key=candidatos.get)


def _money_cell(cell: str) -> float | None:
    m = re.fullmatch(r"\s*((?:\d{1,3}(?:\.\d{3})*|\d+),\d{2})\s*", cell or "")
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def catalog_lots(text: str) -> list[dict[str, Any]]:
    """Um lote por linha de tabela que contenha `Matrícula:`.

    As colunas são lidas pela posição relativa à descrição, que é a célula da
    matrícula: endereço e bairro vêm antes dela, número do bem e valores,
    depois. Posição relativa, e não absoluta, porque a quebra de página às
    vezes desloca a tabela e a primeira coluna some.

    A cidade vem do cabeçalho ("Estado: SP - Cidade: SAO PAULO - Endereço") e
    é herdada pelas linhas seguintes: no documento de referência, 22 linhas
    tinham perdido o cabeçalho numa quebra de página.
    """
    lotes: list[dict[str, Any]] = []
    cidade = uf = None
    # Linha de dados sem matrícula: pode ser a primeira metade de uma linha que
    # a quebra de página partiu. O fragmento seguinte traz só o fim da
    # descrição — "01082580052001 Matrícula: 57513 Ofício: 1." —, e o resto
    # do imóvel (item, endereço, valores) ficou aqui.
    pendente: list[str] | None = None
    for linha in (text or "").splitlines():
        if "Estado:" in linha:
            achado = _header_city(linha)
            if achado:
                uf, cidade = achado[0], achado[1].title()
        if not linha.lstrip().startswith("|"):
            continue
        celulas = [c.strip() for c in linha.strip().strip("|").split("|")]
        if not _CATALOG_REGISTRY.search(linha):
            if len(celulas) >= 6 and re.fullmatch(r"\d{1,5}", celulas[0]):
                pendente = celulas
            continue
        if len(celulas) < 4 and pendente:
            # Cola o fragmento na célula da descrição, que é a anterior ao
            # número do bem.
            j = next((i for i, c in enumerate(pendente) if re.fullmatch(r"\d{6,}", c)), None)
            if j is not None and j > 0:
                celulas = pendente[:j - 1] + [pendente[j - 1] + " " + " ".join(celulas)] + pendente[j:]
        pendente = None
        idx = next(i for i, c in enumerate(celulas) if _CATALOG_REGISTRY.search(c))
        descricao = celulas[idx]
        pega = lambda i: celulas[i] if 0 <= i < len(celulas) else ""  # noqa: E731
        item = celulas[0] if re.fullmatch(r"\d{1,5}", celulas[0]) else None
        numero_bem = pega(idx + 1) if re.fullmatch(r"\d{6,}", pega(idx + 1)) else None
        tipo = re.match(r"\s*([A-Za-zÀ-ú ]+?)\s*,", descricao)
        lotes.append({
            "registry": _CATALOG_REGISTRY.search(descricao).group(1),
            "item": item,
            "kind": tipo.group(1).strip().title() if tipo else None,
            "development": pega(idx - 3) or None,
            "address": pega(idx - 2) or None,
            "district": (pega(idx - 1) or "").title() or None,
            "city": f"{cidade}/{uf}" if cidade else None,
            "asset_id": numero_bem,
            "minimum_bid": _money_cell(pega(idx + 2)),
            "appraisal": _money_cell(pega(idx + 3)),
            # "ANULADO" no lugar do valor: o imóvel saiu do leilão. Não é dado
            # faltando, é o dado mais importante da linha.
            "annulled": "ANULADO" in " ".join(celulas).upper(),
        })
    return lotes


def focus_catalog(text: str, lot: dict) -> str:
    """O edital sem as linhas dos outros imóveis do catálogo.

    Mantém tudo que não é tabela — as regras do leilão, que valem para todos
    os lotes — e só a linha do imóvel escolhido, com um cabeçalho explícito.
    No catálogo de referência isso reduz 525 mil caracteres a cerca de 90 mil:
    os cabeçalhos das 215 tabelas, que repetem o nome da cidade em cada coluna,
    pesavam mais que as próprias regras.

    Mandar o catálogo inteiro custava 159 mil tokens por extração e deixava o
    modelo escolher entre 483 imóveis o que descrever.
    """
    alvo = str(lot.get("registry") or "")
    escolhida = None
    saida = []
    for linha in (text or "").splitlines():
        if linha.lstrip().startswith("|"):
            m = _CATALOG_REGISTRY.search(linha)
            if m and m.group(1) == alvo and escolhida is None:
                escolhida = linha
            continue
        saida.append(linha)
    if escolhida is None:
        return text
    local = " — ".join(x for x in (lot.get("city"),) if x)
    bloco = [
        "",
        f"## IMÓVEL ANALISADO{(' — ' + local) if local else ''}",
        "",
        "| Item | Empreendimento | Endereço | Bairro | Descrição | Número do bem "
        "| Valor de Venda (R$) | Valor de Avaliação (R$) |",
        "|---|---|---|---|---|---|---|---|",
        escolhida.strip(),
        "",
    ]
    return "\n".join(saida + bloco)


def lots(text: str) -> list[dict[str, Any]]:
    """Um registro por imóvel do edital, na ordem em que aparecem.

    Serve à pergunta "qual destes você quer analisar?". A descrição sai do
    trecho que **antecede** a matrícula, que é onde o edital descreve o bem —
    depois dela vem o cartório e o lote seguinte.

    Cada matrícula aparece uma vez só na lista, mesmo citada várias vezes ao
    longo do documento, e vence a ocorrência mais informativa.
    """
    flat = _flatten(text)
    by_registry: dict[str, dict[str, Any]] = {}

    # Onde cada matrícula aparece. A janela de um lote não pode passar da
    # matrícula anterior: com 900 caracteres ela sangrava para o lote de cima e
    # um terreno herdava o "apartamento" do vizinho na lista de escolha.
    encontradas = list(property_registry(text))
    posicoes = sorted({flat.find(f.raw) for f in encontradas} - {-1})

    for found in encontradas:
        position = flat.find(found.raw)
        if position < 0:
            window = ""
        else:
            anteriores = [p for p in posicoes if p < position]
            limite = max(position - LOT_WINDOW, anteriores[-1] if anteriores else 0)
            window = flat[limite:position]
        # Remove os trechos em que a palavra descreve outra coisa antes de
        # procurar o tipo.
        clean = _NOT_A_KIND.sub(" ", window)
        matches = {m.lower() for m in _LOT_KIND.findall(clean)}
        kind = next((k for k in _LOT_KINDS
                     if any(re.fullmatch(k, m, re.IGNORECASE) for m in matches)), None)
        city = _LOT_CITY.findall(window)
        amounts = sorted((m.value for m in money(window)), reverse=True)

        candidate = {
            "registry": found.value,
            "kind": _kind_label(kind, matches) if kind else None,
            "city": city[-1].strip() if city else None,
            # O maior valor da janela é a avaliação; o menor costuma ser o
            # lance mínimo da segunda praça.
            "appraisal": amounts[0] if amounts else None,
        }
        current = by_registry.get(found.value)
        if current is None or _score(candidate) > _score(current):
            by_registry[found.value] = candidate

    return list(by_registry.values())


def _kind_label(pattern: str, matches: set[str]) -> str:
    """Devolve como o edital escreveu, e não o padrão da lista."""
    for text in matches:
        if re.fullmatch(pattern, text, re.IGNORECASE):
            return text.title()
    return pattern.title()


def _score(lot: dict[str, Any]) -> int:
    """Quantos campos a ocorrência conseguiu preencher."""
    return sum(1 for key in ("kind", "city", "appraisal") if lot.get(key))


def multi_lot(text: str) -> dict[str, Any]:
    """Diz se o edital cobre mais de um imóvel.

    A evidência é a contagem de matrículas distintas, e só ela. Uma primeira
    versão também contava blocos de datas de praça, na ideia de pegar editais
    com dois leilões sob a mesma matrícula — e marcava como multi-lote um
    edital de um imóvel só, porque os rótulos `1º leilão` e `2º leilão` se
    repetem ao longo do texto.

    Aviso que aparece em todo edital deixa de ser aviso. Perder um caso é pior
    que nada, mas é melhor que treinar a pessoa a ignorar a linha.
    """
    # Catálogo em tabela primeiro: quando existe, é a fonte mais precisa, e o
    # leitor de parágrafo não vê nenhuma das linhas dele.
    found = catalog_lots(text) or lots(text)
    return {"properties": len(found), "multi": len(found) > 1, "lots": found,
            "catalog": bool(found) and "item" in found[0]}


# ─── CPF e CNPJ ─────────────────────────────────────────────────────────────

_CPF = re.compile(r"\b(\d{3})\.(\d{3})\.(\d{3})-(\d{2})\b")
_CNPJ = re.compile(r"\b(\d{2})\.(\d{3})\.(\d{3})/(\d{4})-(\d{2})\b")


def _valid_cpf(digits: str) -> bool:
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for size in (9, 10):
        total = sum(int(digits[i]) * (size + 1 - i) for i in range(size))
        check = (total * 10) % 11 % 10
        if check != int(digits[size]):
            return False
    return True


def _valid_cnpj(digits: str) -> bool:
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    for size, weights in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
                          (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        total = sum(int(digits[i]) * weights[i] for i in range(size))
        check = 0 if total % 11 < 2 else 11 - total % 11
        if check != int(digits[size]):
            return False
    return True


def documents(text: str) -> dict[str, list[dict]]:
    """Localiza CPF e CNPJ, **mascarados**.

    Existe para proteger, não para coletar: o CPF de pessoa física executada
    consta do edital e não pode ir para a ficha nem para o banco. Devolvemos a
    posição e a forma mascarada para que o texto possa ser higienizado antes de
    ser persistido ou enviado a um provedor.

    O CNPJ do exequente é pessoa jurídica e identifica a natureza da dívida —
    ele é útil e vem sem máscara.
    """
    text = _flatten(text)

    people: list[dict] = []
    for match in _CPF.finditer(text):
        digits = "".join(match.groups())
        people.append(
            {
                "masked": f"***.{match.group(2)}.{match.group(3)}-**",
                "valid": _valid_cpf(digits),
                "context": _context(text, *match.span()),
            }
        )

    companies: list[dict] = []
    for match in _CNPJ.finditer(text):
        digits = "".join(match.groups())
        companies.append(
            {
                "value": match.group(0),
                "valid": _valid_cnpj(digits),
                "context": _context(text, *match.span()),
            }
        )

    return {"cpf": people, "cnpj": companies}


def redact_cpf(text: str) -> str:
    """Substitui todo CPF pela forma mascarada.

    Aplicado ao Markdown antes de persistir e antes de enviar ao provedor: o
    edital é público, mas reunir dado pessoal num banco indexado e devolvê-lo
    num chat é outra coisa.
    """
    return _CPF.sub(lambda m: f"***.{m.group(2)}.{m.group(3)}-**", text)


# ─── Percentuais e áreas ────────────────────────────────────────────────────

_PERCENT = re.compile(r"(\d{1,3}(?:,\d+)?)\s*%")
_AREA = re.compile(r"([\d.]+,\d+|\d+)\s*m\s*[²2]", re.IGNORECASE)


def percentages(text: str) -> list[Found]:
    text = _flatten(text)
    return [
        Found(
            value=float(m.group(1).replace(",", ".")),
            raw=m.group(0),
            context=_context(text, *m.span()),
        )
        for m in _PERCENT.finditer(text)
    ]


def areas(text: str) -> list[Found]:
    text = _flatten(text)
    return [
        Found(
            value=float(m.group(1).replace(".", "").replace(",", "."))
            if "," in m.group(1) else float(m.group(1)),
            raw=m.group(0),
            context=_context(text, *m.span()),
        )
        for m in _AREA.finditer(text)
    ]


# ─── Composição ─────────────────────────────────────────────────────────────

def extract_all(text: str) -> dict[str, Any]:
    """Tudo o que dá para afirmar sobre o edital sem envolver um modelo."""
    docs = documents(text)
    return {
        "money": [f.to_dict() for f in money(text)],
        "dates": [f.to_dict() for f in dates(text)],
        "auction_rounds": auction_rounds(text),
        "property_registry": [f.to_dict() for f in property_registry(text)],
        "percentages": [f.to_dict() for f in percentages(text)],
        "areas": [f.to_dict() for f in areas(text)],
        # Só a contagem e a forma mascarada: o valor real não sai daqui.
        "cpf_count": len(docs["cpf"]),
        "cpf_masked": [d["masked"] for d in docs["cpf"]],
        "cnpj": [{"value": d["value"], "valid": d["valid"]} for d in docs["cnpj"]],
        "multi_lot": multi_lot(text),
    }
