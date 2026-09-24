"""Testes do índice de entorno — sem rede.

O cálculo e a limpeza de endereço rodam sobre dados fixos; a parte que fala com
Nominatim e Overpass fica fora da suíte, porque depende de serviço público
externo e de horário.

    pytest tests/test_location.py -q
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services"))

from extractors import location  # noqa: E402


# ─── Endereço de edital ─────────────────────────────────────────────────────

def test_subdistrito_do_cartorio_sai_do_endereco() -> None:
    """"28º Subdistrito – Jardim Paulista" é a circunscrição do registro.

    O imóvel do edital de exemplo fica na Vila Olímpia. Com o subdistrito no
    texto, o Nominatim não achava nenhum dos dois endereços testados.
    """
    p = location.address_parts(
        "Rua Ponta Delgada, nº 76, 28º Subdistrito - Jardim Paulista, São Paulo")
    assert p == {"street": "Rua Ponta Delgada", "number": "76",
                 "city": "São Paulo", "state": None}


def test_uf_depois_da_barra() -> None:
    p = location.address_parts("Av. Dr. Cardoso de Melo, nº 146, Jardim Paulista, São Paulo/SP")
    assert p["street"] == "Av. Dr. Cardoso de Melo"
    assert p["number"] == "146"
    assert p["city"] == "São Paulo"
    assert p["state"] == "SP"


def test_unidade_do_condominio_nao_confunde_o_numero() -> None:
    """"apartamento 144, 14º andar" não é o número do prédio."""
    p = location.address_parts(
        "sito à Rua das Flores, 100, apartamento 144, 14º andar, Londrina/PR")
    assert p["street"] == "Rua das Flores"
    assert p["number"] == "100"
    assert p["city"] == "Londrina"
    assert p["state"] == "PR"


def test_consultas_do_mais_preciso_para_o_menos() -> None:
    consultas = location.geocode_queries("Rua A, nº 10, Campinas/SP")
    assert consultas == [("numero", "Rua A, 10, Campinas, SP"),
                         ("rua", "Rua A, Campinas, SP")]


def test_sem_cidade_nao_arrisca_consulta() -> None:
    """Rua sem cidade acharia uma homônima em qualquer lugar do país."""
    assert location.geocode_queries("Rua A, 10") == []


# ─── Distância ──────────────────────────────────────────────────────────────

def test_ate_400_metros_nao_desconta() -> None:
    assert location.decay(0) == 1.0
    assert location.decay(400) == 1.0


def test_desconto_linear_ate_zerar_em_1_km() -> None:
    assert location.decay(700) == 0.5
    assert location.decay(1000) == 0.0
    assert location.decay(1500) == 0.0


# ─── Índice ─────────────────────────────────────────────────────────────────

def lugar(categoria: str, metros: float, nome: str = "") -> dict:
    return {"category": categoria, "distance_m": metros, "name": nome}


def test_tudo_na_porta_da_100() -> None:
    lugares = [lugar(c["key"], 50) for c in location.CATEGORIES for _ in c["take"]]
    assert location.score(lugares)["score"] == 100


def test_nada_por_perto_da_zero() -> None:
    resultado = location.score([])
    assert resultado["score"] == 0
    assert resultado["band"] == "quase tudo exige carro"


def test_o_segundo_mercado_vale_menos_que_o_primeiro() -> None:
    um = location.score([lugar("mercado", 100)])["score"]
    dois = location.score([lugar("mercado", 100), lugar("mercado", 100)])["score"]
    tres = location.score([lugar("mercado", 100)] * 3)["score"]
    dez = location.score([lugar("mercado", 100)] * 10)["score"]
    assert um < dois < tres == dez == 20


def test_distancia_pesa_no_indice() -> None:
    perto = location.score([lugar("banco", 100)])["score"]
    longe = location.score([lugar("banco", 900)])["score"]
    assert perto > longe


def test_lista_o_mais_proximo_de_cada_categoria() -> None:
    """A lista importa mais que o número: "mercado a 217 m" dá para conferir."""
    r = location.score([lugar("mercado", 500, "Longe"), lugar("mercado", 217, "Dia")])
    mercado = next(c for c in r["categories"] if c["key"] == "mercado")
    assert mercado == {"key": "mercado", "label": "Mercado", "count": 2,
                       "nearest_m": 217, "nearest_name": "Dia"}


def test_pesos_somam_100() -> None:
    assert sum(c["weight"] for c in location.CATEGORIES) == 100


def test_categoria_pelas_tags_do_osm() -> None:
    assert location.category_of({"shop": "supermarket"}) == "mercado"
    assert location.category_of({"highway": "bus_stop"}) == "onibus"
    assert location.category_of({"railway": "subway_entrance"}) == "trilhos"
    assert location.category_of({"amenity": "parking"}) is None
