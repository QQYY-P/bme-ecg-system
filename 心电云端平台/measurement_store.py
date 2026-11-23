"""Terminal measurement summaries, separate from immutable model input metadata."""
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SummaryModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class HeartRate(SummaryModel):
    mean: float = Field(gt=0, strict=True)
    min: float = Field(gt=0, strict=True)
    max: float = Field(gt=0, strict=True)
    window_samples: int = Field(gt=0, strict=True)

    @model_validator(mode='after')
    def ordered(self):
        if not self.min <= self.mean <= self.max:
            raise ValueError('心率均值必须位于最小值与最大值之间')
        return self


class AnalysisStats(SummaryModel):
    schema_version: Literal[1]
    analyzed_real_samples: int = Field(ge=0, strict=True)
    usable_real_samples: int = Field(ge=0, strict=True)
    hr: HeartRate | None = None

    @model_validator(mode='after')
    def consistent(self):
        if self.usable_real_samples > self.analyzed_real_samples:
            raise ValueError('可分析点数不能超过已分析实采点数')
        if self.hr and self.hr.window_samples > self.usable_real_samples:
            raise ValueError('心率统计窗口不能超过可分析点数')
        return self


class ClassCounts(SummaryModel):
    N: int = Field(ge=0, strict=True)
    S: int = Field(ge=0, strict=True)
    V: int = Field(ge=0, strict=True)


class MeasurementSummary(SummaryModel):
    schema_version: Literal[1]
    requested_duration_s: float | None = Field(default=None, gt=0, strict=True)
    end_reason: str | None = Field(default=None, max_length=64)
    analysis_stats: AnalysisStats | None = None
    classification_counts: ClassCounts | None = None
    classification_enabled: bool | None = Field(default=None, strict=True)


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS measurement_summaries ('
               'session_id TEXT PRIMARY KEY REFERENCES sessions(id), details TEXT NOT NULL)')


def read(db, session_id):
    row = db.execute('SELECT details FROM measurement_summaries WHERE session_id=?', (session_id,)).fetchone()
    return json.loads(row[0]) if row else None


def save(db, session_id, summary, *, sample_count, real_count, valid_real_count):
    if summary is None:
        return  # Legacy upload retries cannot erase a previously received summary.
    stats = summary.analysis_stats
    if stats and (stats.analyzed_real_samples > real_count or stats.usable_real_samples > valid_real_count):
        raise HTTPException(409, '测量总结的实采点数与已收到的原始数据不一致')
    if summary.classification_counts and sum(summary.classification_counts.model_dump().values()) > sample_count:
        raise HTTPException(409, '分类计数超过已收到的采样点数')
    value = summary.model_dump(mode='json')
    previous = read(db, session_id)
    if previous is not None and previous != value:
        raise HTTPException(409, '已保存的测量总结不能由补传覆盖')
    db.execute('INSERT OR IGNORE INTO measurement_summaries VALUES(?,?)',
               (session_id, json.dumps(value, ensure_ascii=False, allow_nan=False)))
