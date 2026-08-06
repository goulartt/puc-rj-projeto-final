"""Extração e validação de números de processo no padrão CNJ.

O número do processo é o campo mais valioso da ficha: é o único que pode ser
conferido contra uma fonte externa (DataJud). Por isso vale validar o dígito
verificador antes de confiar — a validação descarta sequências de dígitos que
apenas *parecem* um número de processo.

Formato (Resolução CNJ 65/2008):

    NNNNNNN-DD.AAAA.J.TR.OOOO
    |       |  |    | |  └── unidade de origem (4)
    |       |  |    | └───── tribunal (2)
    |       |  |    └─────── segmento do Judiciário (1)
    |       |  └──────────── ano de ajuizamento (4)
    |       └─────────────── dígito verificador (2)
    └─────────────────────── sequencial por origem/ano (7)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

# Aceita o número formatado e também a forma "crua" de 20 dígitos, que aparece
# em alguns editais copiados de sistemas processuais.
_FORMATTED = re.compile(r"\b(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})\b")
_BARE = re.compile(r"(?<!\d)(\d{7})(\d{2})(\d{4})(\d)(\d{2})(\d{4})(?!\d)")

SEGMENTOS = {
    "1": "Supremo Tribunal Federal",
    "2": "Conselho Nacional de Justiça",
    "3": "Superior Tribunal de Justiça",
    "4": "Justiça Federal",
    "5": "Justiça do Trabalho",
    "6": "Justiça Eleitoral",
    "7": "Justiça Militar da União",
    "8": "Justiça Estadual",
    "9": "Justiça Militar Estadual",
}

# Justiça Estadual (segmento 8): código do tribunal -> UF.
# É o segmento que interessa a leilão de imóvel; os demais raramente aparecem.
TRIBUNAIS_ESTADUAIS = {
    "01": "AC", "02": "AL", "03": "AP", "04": "AM", "05": "BA", "06": "CE",
    "07": "DF", "08": "ES", "09": "GO", "10": "MA", "11": "MT", "12": "MS",
    "13": "MG", "14": "PA", "15": "PB", "16": "PR", "17": "PE", "18": "PI",
    "19": "RJ", "20": "RN", "21": "RS", "22": "RO", "23": "RR", "24": "SC",
    "25": "SE", "26": "SP", "27": "TO",
}


# Um edital costuma citar jurisprudência no meio do juridiquês. Esses números
# são de processos alheios ao imóvel: consultá-los enriqueceria a ficha com a
# situação de outra causa, o que é pior do que não consultar nada.
_MARCADORES_CITACAO = re.compile(
    r"agravo|apela[çc][ãa]o|precedent|s[úu]mula|recurso\s+(especial|extraordin[áa]rio)"
    r"|jurisprud|ac[óo]rd[ãa]o|REsp|RE\s+\d",
    re.IGNORECASE,
)

# Contexto que indica o processo do próprio leilão.
_MARCADORES_PRINCIPAL = re.compile(
    r"processo\s*n|autos|execu[çc][ãa]o|a[çc][ãa]o\s+de|cumprimento\s+de\s+senten[çc]a",
    re.IGNORECASE,
)

# Quantos caracteres antes do número olhar para classificar.
_JANELA = 160


@dataclass(frozen=True)
class NumeroCNJ:
    numero: str          # sempre normalizado no formato com pontuação
    sequencial: str
    digito: str
    ano: str
    segmento: str
    tribunal: str
    origem: str
    valido: bool         # dígito verificador confere?
    segmento_nome: str
    uf: str | None       # só para a Justiça Estadual
    datajud_alias: str | None  # índice da API pública, ex.: api_publica_tjrj
    instancia: str       # 'primeiro_grau' | 'segundo_grau'
    papel: str           # 'principal' | 'citado' | 'indefinido'
    contexto: str        # trecho ao redor, para auditoria e para a ficha

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def digito_verificador(sequencial: str, ano: str, segmento: str, tribunal: str, origem: str) -> str:
    """Calcula o DV pelo módulo 97 base 10 (ISO 7064).

    Concatena os campos na ordem NNNNNNN AAAA J TR OOOO, multiplica por 100
    (o mesmo que anexar o campo DD zerado) e o dígito é 98 menos o resto.
    """
    base = int(f"{sequencial}{ano}{segmento}{tribunal}{origem}") * 100
    return f"{98 - (base % 97):02d}"


def _alias_datajud(segmento: str, tribunal: str) -> str | None:
    """Índice do DataJud correspondente ao tribunal, derivado do próprio número.

    Só cobrimos a Justiça Estadual: é onde ocorre a execução que leva o imóvel
    a leilão. Outros segmentos devolvem None e o fluxo trata como fora de
    cobertura, em vez de chutar um índice que não existe.
    """
    if segmento != "8":
        return None
    uf = TRIBUNAIS_ESTADUAIS.get(tribunal)
    return f"api_publica_tj{uf.lower()}" if uf else None


def _classificar(origem: str, contexto: str) -> tuple[str, str]:
    """Decide instância e papel a partir da origem e do texto que antecede.

    A origem `0000` significa que o processo corre no próprio tribunal, ou seja,
    segundo grau. A execução que leva um imóvel a leilão corre em primeiro grau,
    então um número de segundo grau num edital é quase sempre jurisprudência
    citada — e é exatamente esse o caso que precisamos descartar.
    """
    instancia = "segundo_grau" if origem == "0000" else "primeiro_grau"

    if instancia == "segundo_grau" or _MARCADORES_CITACAO.search(contexto):
        return instancia, "citado"
    if _MARCADORES_PRINCIPAL.search(contexto):
        return instancia, "principal"
    return instancia, "indefinido"


def _montar(seq: str, dv: str, ano: str, seg: str, trib: str, orig: str,
            contexto: str = "") -> NumeroCNJ:
    uf = TRIBUNAIS_ESTADUAIS.get(trib) if seg == "8" else None
    instancia, papel = _classificar(orig, contexto)
    return NumeroCNJ(
        numero=f"{seq}-{dv}.{ano}.{seg}.{trib}.{orig}",
        sequencial=seq, digito=dv, ano=ano, segmento=seg, tribunal=trib, origem=orig,
        valido=(dv == digito_verificador(seq, ano, seg, trib, orig)),
        segmento_nome=SEGMENTOS.get(seg, "desconhecido"),
        uf=uf,
        datajud_alias=_alias_datajud(seg, trib),
        instancia=instancia,
        papel=papel,
        contexto=contexto.strip(),
    )


def parse(texto: str) -> NumeroCNJ | None:
    """Interpreta uma única string como número CNJ. None se não casar o formato."""
    m = _FORMATTED.search(texto) or _BARE.search(texto)
    if not m:
        return None
    return _montar(*m.groups(), contexto=texto[max(0, m.start() - _JANELA):m.start()])


def extrair(texto: str, *, apenas_validos: bool = True) -> list[NumeroCNJ]:
    """Extrai todos os números CNJ do texto, sem repetição e na ordem de aparição.

    Por padrão devolve só os que passam no dígito verificador. Passe
    ``apenas_validos=False`` para inspecionar candidatos reprovados — útil ao
    diagnosticar um edital em que a conversão embaralhou dígitos.
    """
    # Espaços normalizados: o Markdown do Docling quebra linha no meio de
    # frases, e a janela de contexto ficaria truncada sem isso.
    texto = re.sub(r"\s+", " ", texto)

    achados: list[NumeroCNJ] = []
    vistos: set[str] = set()

    for regex in (_FORMATTED, _BARE):
        for m in regex.finditer(texto):
            item = _montar(*m.groups(), contexto=texto[max(0, m.start() - _JANELA):m.start()])
            if item.numero in vistos:
                continue
            if apenas_validos and not item.valido:
                continue
            vistos.add(item.numero)
            achados.append(item)

    return achados


def processo_principal(texto: str) -> NumeroCNJ | None:
    """Devolve o processo do leilão, descartando jurisprudência citada.

    Um edital cita precedentes no meio do texto jurídico; consultar esses
    números no DataJud traria a situação de causas alheias ao imóvel, o que é
    pior do que não consultar nada. Preferimos o candidato explicitamente
    marcado como principal; na falta dele, o primeiro de primeiro grau.

    Devolve None quando nada sobra — e aí o fluxo trata como leilão sem
    processo identificável, em vez de escolher um número no chute.
    """
    candidatos = [n for n in extrair(texto) if n.papel != "citado"]
    if not candidatos:
        return None
    for n in candidatos:
        if n.papel == "principal":
            return n
    return candidatos[0]
