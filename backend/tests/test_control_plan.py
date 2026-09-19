"""Порядок визитов в плане диспетчеров: в выгрузке его нет, и мы выбираем его сами."""

from app.domain.enums import Priority, Skill
from app.ingest.beeline_csv import RawFile, RawRequestRow
from app.synth.control import build_control_plan
from tests.helpers import eng, problem_of, req


def row(row_index, request_id, crew=""):
    return RawRequestRow(
        row_index=row_index,
        request_id=request_id,
        type_bk="Локальная заявка",
        type_hd="",
        window_start=0,
        window_end=0,
        district="",
        address="",
        crew=crew,
    )


def files(pairs):
    """Контрольный и синтетический файлы одной формы: номер заявки и бригада, которой её отдал диспетчер."""
    control = RawFile(
        rows=[row(k, rid, crew) for k, (rid, crew) in enumerate(pairs)],
        office_address=None,
        is_control=True,
    )
    synthetic = RawFile(
        rows=[row(k, rid) for k, (rid, _) in enumerate(pairs)], office_address=None, is_control=False
    )
    return control, synthetic


def test_the_brigade_goes_to_the_request_whose_window_closes_first():
    """Авария с окном на весь день не забирает утро: сначала подключение, у которого окно закрывается в 12:00.

    Сортировка по началу окна ставила бы аварию первой (её окно начинается в 00:01) и роняла бы подключение.
    """
    emergency = req("A", 1, 0, "00:01", "23:59", skill=Skill.EMERGENCY, priority=Priority.URGENT, duration=80)
    connection = req("C", 1, 0, "10:00", "12:00", duration=70)
    problem = problem_of([emergency, connection], [eng("E1")])
    control, synthetic = files([("A", "Бригада 1"), ("C", "Бригада 1")])

    plan = build_control_plan(problem, control, synthetic, {"Бригада 1": "E1"})

    assert [visit.request_id for visit in plan.routes[0].visits] == ["C", "A"]
    assert plan.violations == []


def test_a_request_without_a_brigade_stays_unassigned_with_a_reason():
    problem = problem_of([req("R1", 1, 0, "10:00", "12:00")], [eng("E1")])
    control, synthetic = files([("R1", "")])

    plan = build_control_plan(problem, control, synthetic, {"Бригада 1": "E1"})

    assert [item.request_id for item in plan.unassigned] == ["R1"]
    assert plan.unassigned[0].reason_text == "Диспетчер не назначил бригаду."
