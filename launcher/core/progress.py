from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional


ProgressListener = Callable[["ProgressState"], None]


@dataclass
class ProgressState:
    percent: float
    phase: str
    detail: str
    eta_seconds: Optional[float]
    current: int = 0
    total: int = 0

    @property
    def eta_text(self) -> str:
        if self.eta_seconds is None:
            return "calculando…"
        secs = max(0, int(self.eta_seconds))
        if secs < 60:
            return f"{secs}s"
        mins, rem = divmod(secs, 60)
        if mins < 60:
            return f"{mins}m {rem:02d}s"
        hours, rem_m = divmod(mins, 60)
        return f"{hours}h {rem_m:02d}m"


class ProgressTracker:
    """Weighted multi-phase progress with ETA."""

    def __init__(
        self,
        phases: dict[str, float],
        on_update: Optional[ProgressListener] = None,
    ) -> None:
        total_w = sum(phases.values()) or 1.0
        self.phases = {k: v / total_w for k, v in phases.items()}
        self.order = list(phases.keys())
        self.on_update = on_update
        self._completed: set[str] = set()
        self._phase = self.order[0] if self.order else ""
        self._phase_progress = 0.0
        self._detail = ""
        self._current = 0
        self._total = 0
        self._started = time.monotonic()
        self._last_emit = 0.0

    def set_phase(self, phase: str, detail: str = "") -> None:
        if phase not in self.phases:
            self.phases[phase] = 0.0
            if phase not in self.order:
                self.order.append(phase)
        self._phase = phase
        self._phase_progress = 0.0
        self._current = 0
        self._total = 0
        if detail:
            self._detail = detail
        self.emit(force=True)

    def set_detail(self, detail: str) -> None:
        self._detail = detail
        self.emit()

    def set_phase_fraction(self, fraction: float, detail: str = "") -> None:
        self._phase_progress = max(0.0, min(1.0, fraction))
        if detail:
            self._detail = detail
        self.emit()

    def set_counts(self, current: int, total: int, detail: str = "") -> None:
        self._current = max(0, current)
        self._total = max(0, total)
        if total > 0:
            self._phase_progress = min(1.0, current / total)
        if detail:
            self._detail = detail
        self.emit()

    def complete_phase(self, detail: str = "") -> None:
        self._phase_progress = 1.0
        self._completed.add(self._phase)
        if detail:
            self._detail = detail
        self.emit(force=True)

    def overall_percent(self) -> float:
        done = 0.0
        for name, weight in self.phases.items():
            if name in self._completed and name != self._phase:
                done += weight
            elif name == self._phase:
                done += weight * self._phase_progress
        return max(0.0, min(100.0, done * 100.0))

    def _eta(self, percent: float) -> Optional[float]:
        if percent <= 1.0:
            return None
        elapsed = time.monotonic() - self._started
        if elapsed < 1.5:
            return None
        rate = percent / elapsed
        if rate <= 0:
            return None
        return (100.0 - percent) / rate

    def emit(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_emit) < 0.08:
            return
        self._last_emit = now
        percent = self.overall_percent()
        state = ProgressState(
            percent=percent,
            phase=self._phase,
            detail=self._detail or self._phase,
            eta_seconds=self._eta(percent),
            current=self._current,
            total=self._total,
        )
        if self.on_update:
            self.on_update(state)

    def as_mll_callback(self, phase: str):
        max_val = {"v": 0}

        def set_status(status: str) -> None:
            if self._phase != phase:
                self.set_phase(phase, status)
            else:
                self.set_detail(status)

        def set_progress(value: int) -> None:
            self.set_counts(int(value), max(max_val["v"], 1))

        def set_max(value: int) -> None:
            max_val["v"] = max(0, int(value))
            self.set_counts(self._current, max_val["v"])

        return {
            "setStatus": set_status,
            "setProgress": set_progress,
            "setMax": set_max,
        }
