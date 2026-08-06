"""Testes do leitor de movimentos processuais.

O caso que motivou este módulo está aqui como teste: o processo do edital de
exemplo tem uma homologação de acordo de 2023, e o leilão é de 2026. Tratar
presença de acordo como risco alto, ignorando a data, produziria o alerta
errado — "leilão prestes a ser cancelado" num processo que seguiu ativo por
mais três anos.

    pytest tests/test_movements.py -q
"""

from __future__ import annotations

import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services"))

from extractors import movements  # noqa: E402

HOJE = date(2026, 8, 6)


def mv(nome: str, dia: str, codigo: int = 1) -> dict:
    return {"nome": nome, "dataHora": f"{dia}T12:00:00.000Z", "codigo": codigo}


def fonte(*movimentos: dict, **extra) -> dict:
    return {"movimentos": list(movimentos), **extra}


def kinds(result: dict, key: str = "signals") -> list[str]:
    return [s["kind"] for s in result[key]]


# ─── O caso real que motivou o módulo ───────────────────────────────────────

def test_acordo_antigo_com_processo_ativo_nao_e_risco_alto() -> None:
    """Acordo de 2023 seguido de anos de movimentação = acordo descumprido.

    Sinalizar isso como risco alto diria ao comprador que o leilão vai cair,
    quando o processo mostra o contrário.
    """
    result = movements.analyze(
        fonte(
            mv("Homologação de Acordo em Execução ou em Cumprimento de Sentença", "2023-04-05", 14099),
            mv("Petição", "2026-06-22", 85),
            mv("Conclusão", "2026-06-30", 51),
        ),
        today=HOJE,
    )
    acordo = next(s for s in result["signals"] if s["kind"] == "acordo")
    assert acordo["recent"] is False
    assert acordo["severity"] == movements.SEVERITY_LOW
    assert "acordo" not in kinds(result, "active_signals")


def test_acordo_recente_e_risco_alto() -> None:
    """O mesmo movimento, agora recente, muda de leitura por completo."""
    result = movements.analyze(
        fonte(mv("Homologação de Acordo em Execução", "2026-07-01", 14099)),
        today=HOJE,
    )
    acordo = next(s for s in result["signals"] if s["kind"] == "acordo")
    assert acordo["recent"] is True
    assert acordo["severity"] == movements.SEVERITY_HIGH
    assert "acordo" in kinds(result, "active_signals")


# ─── Sinais individuais ─────────────────────────────────────────────────────

def test_reconhece_embargos_recurso_e_suspensao() -> None:
    result = movements.analyze(
        fonte(
            mv("Embargos à Arrematação", "2026-07-10"),
            mv("Agravo de Instrumento", "2026-07-12"),
            mv("Suspensão do Processo", "2026-07-15"),
        ),
        today=HOJE,
    )
    assert set(kinds(result)) == {"embargos", "recurso", "suspensao"}
    assert all(s["recent"] for s in result["signals"])


def test_arrematacao_antiga_ainda_merece_atencao() -> None:
    """Edital publicado para bem ja arrematado e desatualizado, nao irrelevante."""
    result = movements.analyze(
        fonte(mv("Expedição de Carta de Arrematação", "2024-01-10"),
              mv("Petição", "2026-06-01")),
        today=HOJE,
    )
    arremat = next(s for s in result["signals"] if s["kind"] == "arrematacao")
    assert arremat["severity"] == movements.SEVERITY_MEDIUM
    assert "arrematacao" in kinds(result, "active_signals")


def test_movimento_de_rotina_nao_vira_sinal() -> None:
    result = movements.analyze(
        fonte(mv("Conclusão", "2026-06-30"), mv("Publicação", "2026-06-16"),
              mv("Documento", "2026-06-15")),
        today=HOJE,
    )
    assert result["signals"] == []


# ─── Inatividade ────────────────────────────────────────────────────────────

def test_processo_parado_ha_muito_tempo_e_sinal() -> None:
    result = movements.analyze(fonte(mv("Petição", "2023-01-10")), today=HOJE)
    inativo = next(s for s in result["signals"] if s["kind"] == "inatividade")
    assert inativo["severity"] == movements.SEVERITY_MEDIUM
    assert "meses" in inativo["description"]


def test_processo_ativo_nao_gera_sinal_de_inatividade() -> None:
    result = movements.analyze(fonte(mv("Petição", "2026-07-30")), today=HOJE)
    assert "inatividade" not in kinds(result)


# ─── Agrupamento ────────────────────────────────────────────────────────────

def test_repeticoes_do_mesmo_tipo_viram_um_sinal_so() -> None:
    """Seis movimentos de embargos sao um problema, nao seis.

    Sem agrupar, um processo real gerou 11 sinais ativos que eram repeticoes de
    dois tipos — o resumo afogava, e a leitura sugeria seis problemas distintos.
    """
    result = movements.analyze(
        fonte(
            mv("Embargos à Execução", "2026-01-10"),
            mv("Embargos à Execução", "2026-03-15"),
            mv("Embargos à Execução", "2026-06-20"),
            mv("Suspensão do Processo", "2026-05-01"),
        ),
        today=HOJE,
    )
    assert kinds(result) == ["embargos", "suspensao"] or kinds(result) == ["suspensao", "embargos"]
    embargos = next(s for s in result["signals"] if s["kind"] == "embargos")
    assert embargos["occurrences"] == 3
    assert embargos["date"] == "2026-06-20"       # o mais recente
    assert embargos["first_date"] == "2026-01-10"  # e desde quando


def test_agrupamento_mantem_a_maior_severidade() -> None:
    """Um antigo e um recente do mesmo tipo: vale o recente, que e mais grave."""
    result = movements.analyze(
        fonte(mv("Homologação de Acordo", "2020-01-10"),
              mv("Homologação de Acordo", "2026-07-20")),
        today=HOJE,
    )
    acordo = next(s for s in result["signals"] if s["kind"] == "acordo")
    assert acordo["severity"] == movements.SEVERITY_HIGH
    assert acordo["occurrences"] == 2
    assert acordo["recent"] is True


def test_sinais_saem_ordenados_por_severidade() -> None:
    result = movements.analyze(
        fonte(mv("Penhora realizada", "2026-07-01"),
              mv("Suspensão do Processo", "2026-07-02")),
        today=HOJE,
    )
    assert kinds(result)[0] == "suspensao"


# ─── Metadados e robustez ───────────────────────────────────────────────────

def test_extrai_metadados_do_processo() -> None:
    result = movements.analyze(
        fonte(
            mv("Petição", "2026-06-22"),
            classe={"nome": "Execução de Título Extrajudicial"},
            orgaoJulgador={"nome": "15ª Vara Cível - Foro Central"},
            assuntos=[{"nome": "Condomínio em Edifício"}, {"nome": "Despesas Condominiais"}],
            dataAjuizamento="20230111151506",
        ),
        today=HOJE,
    )
    assert result["classe"] == "Execução de Título Extrajudicial"
    assert result["assuntos"] == ["Condomínio em Edifício", "Despesas Condominiais"]
    # dataAjuizamento vem em AAAAMMDDHHMMSS, não em ISO como os movimentos.
    assert result["data_ajuizamento"] == "2023-01-11"
    assert result["last_movement"] == {"name": "Petição", "date": "2026-06-22"}


def test_processo_sem_movimentos_nao_quebra() -> None:
    result = movements.analyze({}, today=HOJE)
    assert result["movement_count"] == 0
    assert result["signals"] == []
    assert result["last_movement"] is None


def test_movimento_sem_data_nao_quebra_a_ordenacao() -> None:
    result = movements.analyze(
        {"movimentos": [{"nome": "Embargos à Execução", "codigo": 1},
                        mv("Petição", "2026-06-01")]},
        today=HOJE,
    )
    embargos = next(s for s in result["signals"] if s["kind"] == "embargos")
    assert embargos["date"] is None
    # Sem data não dá para afirmar que é recente; o padrão prudente é não ser.
    assert embargos["recent"] is False
