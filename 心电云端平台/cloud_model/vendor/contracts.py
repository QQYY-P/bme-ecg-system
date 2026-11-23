"""Shared, immutable acquisition messages. Times are receive-count based, not UTC sampling."""
from dataclasses import dataclass

SAMPLE_RATE = 360
INVALID = 1
SATURATED = 2
DISCONTINUITY = 4
MODE_CHANGE = 8
STREAM_RESYNC = 16


@dataclass(frozen=True)
class SamplePoint:
    sample_id: int
    segment_id: int
    segment_index: int
    sequence: int
    raw_adc: int
    flags: int = 0
    mode: int | None = None

    @property
    def time_s(self):
        return self.sample_id / SAMPLE_RATE

    @property
    def valid(self):
        return 0 < self.raw_adc < 4095 and not self.flags & (INVALID | SATURATED)


@dataclass(frozen=True)
class SampleBatch:
    session_id: str
    source: str
    points: tuple
    received_monotonic: float
    received_utc: str
    wire: bytes = b""
