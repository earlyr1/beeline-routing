"""Отмены дня: заявки, которые клиент отменил уже в течение дня.

Заявка со статусом отмены в контрольном файле — это не «заявки не было», а факт дня: утром её планируют, и
только потом клиент звонит и отказывается. Поэтому отмены не убирают заявку из дня, а ложатся на шкалу времени,
каждая в своё время, и приходят одна за другой, пока идут часы дня.
"""

from __future__ import annotations

import random

from app.domain.models import Cancellation, Request
from app.ingest.beeline_csv import RawFile
from app.synth.config import SynthConfig

# Раньше 09:00 отмены не приходят: день на шкале начинается с этого часа.
CANCELLATION_EARLIEST = 9 * 60
# Отмена приходит за 30–240 минут до начала окна: клиент успевает отказаться до выезда инженера.
CANCELLATION_MIN_AHEAD = 30
CANCELLATION_MAX_AHEAD = 240


def cancellation_time(cfg: SynthConfig, request_id: str, window_start: int) -> int:
    """Время отмены: начало окна минус 30–240 минут, но не раньше 09:00. Детерминировано по seed и номеру заявки.

    Смены начинаются не раньше 10:00, а визит не начинается раньше своего окна, поэтому отмена всегда приходит
    до того, как по заявке начнут работу. У окна аварии 0:01–23:59 время отмены — ровно 09:00.
    """
    rng = random.Random(f"{cfg.seed}:cancellation:{request_id}")
    ahead = rng.randint(CANCELLATION_MIN_AHEAD, CANCELLATION_MAX_AHEAD)
    return max(CANCELLATION_EARLIEST, window_start - ahead)


def build_cancellations(
    cfg: SynthConfig, requests: list[Request], control: RawFile, synthetic: RawFile
) -> list[Cancellation]:
    """По одной отмене на заявку дня со статусом отмены в контрольном файле, по времени."""
    by_id = {request.id: request for request in requests}
    cancellations = [
        Cancellation(
            request_id=s_row.request_id,
            time=cancellation_time(cfg, s_row.request_id, by_id[s_row.request_id].window_start),
        )
        for c_row, s_row in zip(control.rows, synthetic.rows, strict=True)
        if c_row.status_bk in cfg.cancelled_control_statuses and s_row.request_id in by_id
    ]
    return sorted(cancellations, key=lambda item: (item.time, item.request_id))
