"""Детерминированный солвер и события для тестов таймлайна.

OR-Tools с лимитом по времени на каждом запуске даёт разные маршруты, поэтому тесты повторных применений решают
задачу FCFS: одинаковые входы дают одинаковый план, а число решений считается по времени задачи.
"""

import threading

from app.domain.enums import EventType
from app.domain.models import Event
from app.domain.timeutil import fmt_hhmm
from app.planning import session as session_module
from app.planning.timeline import Timeline, replay_step
from app.planning.variants import VARIANTS, is_choosable
from app.solvers.fcfs import FcfsSolver


class FcfsSolves(list):
    """Время задачи (HH:MM) каждого решения по порядку. gate задерживает решение на заданное время."""

    def __init__(self):
        super().__init__()
        self.gates: dict[str, tuple[threading.Event, threading.Event]] = {}
        # Стратегия каждого решения по порядку: optimal или stable.
        self.variants: list[str] = []

    def clear(self) -> None:
        """Забывает и время, и стратегии решений."""
        super().clear()
        self.variants.clear()

    def hold(self, time: str) -> tuple[threading.Event, threading.Event]:
        """Первое решение задачи на time ждёт release; entered сообщает, что решение началось."""
        entered, release = threading.Event(), threading.Event()
        self.gates[time] = (entered, release)
        return entered, release

    def solve(self, problem, workload_level, time_limit_s, variant="optimal"):
        time = fmt_hhmm(problem.now)
        self.append(time)
        self.variants.append(variant)
        gate = self.gates.pop(time, None)
        if gate is not None:
            entered, release = gate
            entered.set()
            assert release.wait(timeout=30), "решение не отпустили"
        plan = FcfsSolver().solve(problem)
        return plan, plan


def fcfs_solves(monkeypatch) -> FcfsSolves:
    solves = FcfsSolves()
    monkeypatch.setattr(session_module, "_solve", solves.solve)
    return solves


def cancel(request_id, time):
    return Event(type=EventType.CANCEL, time=time, request_id=request_id)


def restore(request_id, time):
    return Event(type=EventType.RESTORE, time=time, request_id=request_id)


def replay_all(timeline: Timeline, base, ctx):
    """Считает все недостающие шаги по порядку, как фоновый предподсчёт, и возвращает полный проход.

    На «ломающем» событии без выбора считает шаги всех стратегий и возвращает проход, остановленный на выборе.
    """
    version = max([base.version, *(step.session.version for step in timeline.steps.values())])
    while True:
        walk = timeline.walk(base)
        if walk.awaiting is not None:
            return walk
        if walk.done == len(timeline.entries):
            return walk
        entry = timeline.entries[walk.done]
        variants = VARIANTS if is_choosable(entry.event) and entry.variant is None else (None,)
        for variant in variants:
            step = replay_step(walk.session, entry, ctx, version + 1, variant)
            timeline.store(walk, entry, step, variant)
        if step.applied is not None:
            version += 1
