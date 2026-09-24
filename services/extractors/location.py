"""Entorno do imóvel: o que dá para fazer a pé, a partir do OpenStreetMap.

Existe no lugar do Walk Score, que não serve aqui: a API dele cobre só Estados
Unidos e Canadá, então um endereço em São Paulo não teria nota mesmo com chave.

O índice é nosso e o método está todo neste arquivo. Não se chama "Walk Score"
porque não é: é uma conta própria, parecida no espírito — serviços do dia a
dia, pesados pela distância a pé — e diferente nos detalhes.

**O número vem com a lista do que há perto**, e a lista importa mais. "Mercado
a 217 m" é verificável; "índice 84" sozinho não é.

**Nota baixa não vira alerta.** O OpenStreetMap é bem mapeado em capitais e
irregular no interior: um bairro sem mercado cadastrado pode ter três. A
ausência no mapa não prova ausência na rua, e por isso este módulo só descreve
— nunca classifica o entorno como ponto de atenção.

A parte de rede (Nominatim e Overpass) fica em `fetch_*`, separada do cálculo,
para o cálculo ser testado sem internet.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request
from typing import Any

# A política de uso do Nominatim exige um User-Agent que identifique a
# aplicação, e no máximo uma requisição por segundo.
USER_AGENT = "ArremataAI/1.0 (projeto academico PUC-Rio; github.com/goulartt/puc-rj-projeto-final)"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Mais de uma instância pública: elas ficam sobrecarregadas com frequência, e
# a principal respondeu 504 na primeira consulta de teste.
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Raio da consulta. 1 km cobre uns 12 minutos a pé. Com 1,6 km, uma consulta em
# São Paulo trazia milhares de restaurantes e estourava o tempo do servidor.
RADIUS_M = 1000
# Até aqui a distância não desconta nada: é a caminhada de cinco minutos.
FULL_SCORE_M = 400

UFS = ("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR "
       "SC SP SE TO").split()


# ─── Categorias ─────────────────────────────────────────────────────────────
#
# Peso de cada categoria no índice (soma 100) e quantas ocorrências contam: o
# segundo mercado ajuda menos que o primeiro, e o décimo restaurante, nada.
# `decay` é quanto vale cada ocorrência seguinte.

CATEGORIES: list[dict[str, Any]] = [
    {"key": "mercado", "label": "Mercado", "weight": 20, "take": [0.6, 0.25, 0.15],
     "tags": {"shop": {"supermarket", "convenience", "greengrocer", "bakery", "butcher"}}},
    {"key": "onibus", "label": "Ponto de ônibus", "weight": 15, "take": [0.6, 0.25, 0.15],
     "tags": {"highway": {"bus_stop"}}},
    {"key": "trilhos", "label": "Estação de metrô ou trem", "weight": 10, "take": [1.0],
     "tags": {"railway": {"station", "subway_entrance"}}},
    {"key": "saude", "label": "Farmácia ou saúde", "weight": 15, "take": [0.7, 0.3],
     "tags": {"amenity": {"pharmacy", "hospital", "clinic", "doctors"}}},
    {"key": "escola", "label": "Escola", "weight": 10, "take": [0.7, 0.3],
     "tags": {"amenity": {"school", "kindergarten"}}},
    {"key": "alimentacao", "label": "Restaurante ou café", "weight": 15,
     "take": [0.35, 0.25, 0.2, 0.12, 0.08],
     "tags": {"amenity": {"restaurant", "cafe", "fast_food"}}},
    {"key": "lazer", "label": "Parque ou academia", "weight": 10, "take": [0.7, 0.3],
     "tags": {"leisure": {"park", "playground", "fitness_centre"}}},
    {"key": "banco", "label": "Banco", "weight": 5, "take": [1.0],
     "tags": {"amenity": {"bank"}}},
]

BANDS = [
    (90, "muito bem servido — dá para resolver quase tudo a pé"),
    (70, "bem servido — a maior parte do dia a dia fica a pé"),
    (50, "parcialmente servido — parte do dia a dia exige transporte"),
    (25, "pouco servido — a maior parte exige carro ou ônibus"),
    (0, "quase tudo exige carro"),
]


def category_of(tags: dict) -> str | None:
    for cat in CATEGORIES:
        for key, values in cat["tags"].items():
            if tags.get(key) in values:
                return cat["key"]
    return None


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em linha reta. A caminhada real é maior, e o índice aceita
    isso: comparar dois imóveis pela mesma régua importa mais que o metro exato."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


def decay(meters: float) -> float:
    """Peso pela distância: 1 até 400 m, cai em linha reta até 0 em 1 km."""
    if meters <= FULL_SCORE_M:
        return 1.0
    if meters >= RADIUS_M:
        return 0.0
    return 1 - (meters - FULL_SCORE_M) / (RADIUS_M - FULL_SCORE_M)


def band(score: int) -> str:
    return next(texto for limite, texto in BANDS if score >= limite)


def score(places: list[dict]) -> dict[str, Any]:
    """Índice de 0 a 100 e o mais próximo de cada categoria.

    `places` é uma lista de `{category, distance_m, name}`.
    """
    by_cat: dict[str, list[dict]] = {}
    for place in places:
        if place.get("category"):
            by_cat.setdefault(place["category"], []).append(place)

    total = 0.0
    categorias = []
    for cat in CATEGORIES:
        found = sorted(by_cat.get(cat["key"], []), key=lambda p: p["distance_m"])
        pontos = sum(peso * decay(p["distance_m"]) for peso, p in zip(cat["take"], found))
        total += cat["weight"] * min(pontos, 1.0)
        nearest = found[0] if found else None
        categorias.append({
            "key": cat["key"],
            "label": cat["label"],
            "count": len(found),
            "nearest_m": round(nearest["distance_m"]) if nearest else None,
            "nearest_name": (nearest.get("name") or None) if nearest else None,
        })

    valor = round(total)
    return {"score": valor, "band": band(valor), "categories": categorias}


# ─── Endereço ───────────────────────────────────────────────────────────────

def address_parts(address: str) -> dict[str, str | None]:
    """Separa rua, número, cidade e UF do endereço como o edital escreve.

    O texto de edital tem vocabulário de cartório que atrapalha o geocoder:
    "sito à", "nº", "apartamento 144, 14º andar", e principalmente "28º
    Subdistrito – Jardim Paulista", que é a circunscrição do registro e não o
    bairro — no edital de exemplo o imóvel fica de fato na Vila Olímpia. Com
    esse texto, o Nominatim não achava nenhum dos dois endereços testados.
    """
    s = re.sub(r"(?i)^\s*(sito|situad[oa]|localizad[oa])\s+(à|a|na|no|em)\s+", "", address or "")
    s = re.sub(r"(?i)\bn[º°o.]\s*(?=\d)", "", s)
    s = re.sub(r"(?i)\b\d+\s*[º°ª]?\s*subdistrito\b[^,]*", "", s)
    s = re.sub(r"(?i)\b(apto\.?|apartamento|unidade|sala|conj\.?|conjunto|bloco|"
               r"andar|box|vaga)\b[^,]*", "", s)
    uf = next((u for u in UFS if re.search(rf"(?:/|,|-|\s){u}\b", s)), None)
    s = re.sub(r"\s*[/-]\s*[A-Z]{2}\b", "", s)
    s = re.sub(r"\bCEP\b[^,]*", "", s, flags=re.IGNORECASE)

    # O catálogo da Caixa põe um código antes do logradouro — "2 HIS 2 ALAMEDA
    # CASA BRANCA N. 438" — que o geocoder não entende. Tudo o que vem antes do
    # tipo de logradouro sai.
    tipo = re.search(r"(?i)\b(rua|avenida|av\.|alameda|travessa|estrada|rodovia|"
                     r"pra[çc]a|largo|viela|servid[ãa]o)\b", s)
    if tipo and tipo.start() > 0:
        s = s[tipo.start():]

    pedacos = [p.strip(" -–") for p in s.split(",") if p.strip(" -–")]
    rua = pedacos[0] if pedacos else ""
    numero = None
    # Número colado na rua ("ALAMEDA CASA BRANCA 438", depois de o "N." sair)
    # vale mais que uma parte solta: "11 ANDAR" vira "11" depois da limpeza e
    # seria lido como número do prédio.
    colado = re.match(r"(.*?\D)\s+(\d+[A-Za-z]?)\s*$", rua)
    if colado:
        rua, numero = colado.group(1).strip(), colado.group(2)
    else:
        numero = next((p for p in pedacos[1:] if re.fullmatch(r"\d+[A-Za-z]?", p)), None)
    # Partes que são só número (restos de andar, bloco, vaga) não são cidade.
    resto = [p for p in pedacos[1:] if p != numero and not re.fullmatch(r"[\d\s]+", p)]
    cidade = resto[-1] if resto else None
    return {"street": rua or None, "number": numero, "city": cidade, "state": uf}


def geocode_queries(address: str) -> list[tuple[str, str]]:
    """Consultas em ordem de precisão: com número, depois só a rua.

    Com a rua sozinha o ponto cai no meio dela, o que ainda serve para o
    entorno de uma rua curta e engana numa avenida de 5 km — por isso a
    precisão obtida volta no resultado e aparece na mensagem.
    """
    p = address_parts(address)
    if not p["street"] or not p["city"]:
        return []
    fim = ", ".join(x for x in (p["city"], p["state"]) if x)
    consultas = []
    if p["number"]:
        consultas.append(("numero", f"{p['street']}, {p['number']}, {fim}"))
    consultas.append(("rua", f"{p['street']}, {fim}"))
    return consultas


# ─── Rede ───────────────────────────────────────────────────────────────────

def _get_json(url: str, *, data: bytes | None = None, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def fetch_geocode(address: str) -> dict[str, Any] | None:
    for precisao, consulta in geocode_queries(address):
        url = NOMINATIM + "?" + urllib.parse.urlencode(
            {"q": consulta, "format": "jsonv2", "countrycodes": "br", "limit": 1})
        resultado = _get_json(url)
        time.sleep(1.1)  # política do Nominatim: uma requisição por segundo
        if resultado:
            r = resultado[0]
            return {"lat": float(r["lat"]), "lon": float(r["lon"]), "precision": precisao,
                    "display_name": r.get("display_name")}
    return None


def _overpass_query(lat: float, lon: float) -> str:
    filtros = []
    for cat in CATEGORIES:
        for key, values in cat["tags"].items():
            filtros.append(f'nwr(around:{RADIUS_M},{lat},{lon})[{key}~"^({"|".join(sorted(values))})$"];')
    return "[out:json][timeout:25];(" + "".join(filtros) + ");out center tags;"


def fetch_places(lat: float, lon: float) -> list[dict]:
    corpo = urllib.parse.urlencode({"data": _overpass_query(lat, lon)}).encode()
    ultimo_erro: Exception | None = None
    for servidor in OVERPASS:
        try:
            elementos = _get_json(servidor, data=corpo, timeout=60)["elements"]
            break
        except Exception as erro:  # noqa: BLE001 — tenta a próxima instância
            ultimo_erro = erro
    else:
        raise RuntimeError(f"nenhuma instância do Overpass respondeu: {ultimo_erro}")

    lugares = []
    for e in elementos:
        tags = e.get("tags", {})
        la = e.get("lat", (e.get("center") or {}).get("lat"))
        lo = e.get("lon", (e.get("center") or {}).get("lon"))
        if la is None or lo is None:
            continue
        lugares.append({"category": category_of(tags),
                        "distance_m": distance_m(lat, lon, la, lo),
                        "name": tags.get("name")})
    return lugares


def evaluate(address: str) -> dict[str, Any]:
    """Endereço → índice, com a precisão do ponto e a fonte dos dados."""
    ponto = fetch_geocode(address)
    if not ponto:
        return {"found": False, "reason": "endereço não localizado no mapa",
                "address": address}
    resultado = score(fetch_places(ponto["lat"], ponto["lon"]))
    return {"found": True, "address": address, **ponto, **resultado,
            "radius_m": RADIUS_M, "source": "OpenStreetMap"}
