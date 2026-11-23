"""Bounded Canvas plotting with peak-preserving display-only reduction."""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from numbers import Real

import numpy as np
from preprocessing import adc_to_volts
from ui.render_cache import Canvas


MAX_CLASSIFICATION_MARKERS = 100
MAX_MARKER_CANDIDATES = 2000
MARKER_COLORS = {"N": "#56d8d0", "S": "#efb66b", "V": "#c79af5"}


def _sample_integer(value):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        return None
    if not np.isfinite(value) or value < 0 or int(value) != value:
        return None
    return int(value)


def classification_marker_positions(x, y, segment_ids, markers, begin, end):
    """Find exact sample/segment matches without interpolating across gaps.

    x is absolute acquisition sample time (sample_id / 360), never the time at
    which a delayed classification arrived. The caller filters session IDs.
    Input/visible-event limits bound work and the number of Canvas marker slots.
    """
    if not isinstance(markers, (list, tuple)) or not markers or segment_ids is None:
        return []
    x, y, segments = np.asarray(x, dtype=float), np.asarray(y, dtype=float), np.asarray(segment_ids)
    if x.ndim != 1 or y.shape != x.shape or segments.shape != x.shape:
        return []
    if not np.isfinite([begin, end]).all() or end <= begin:
        return []
    candidates = {}
    for event in markers[-MAX_MARKER_CANDIDATES:]:
        if not isinstance(event, dict):
            continue
        label = event.get("label")
        if not isinstance(label, str) or label not in MARKER_COLORS:
            continue
        sample = _sample_integer(event.get("r_sample"))
        segment = _sample_integer(event.get("segment_id"))
        if sample is None or segment is None or not begin <= sample / 360.0 <= end:
            continue
        candidates[(sample, segment)] = event
    if not candidates:
        return []
    visible = np.flatnonzero(np.isfinite(x) & np.isfinite(y) & (x >= begin) & (x <= end))
    # Filter in NumPy before Python-side matching: normal 5-second windows have
    # about six classified beats, rather than thousands of samples to inspect.
    samples = np.rint(x[visible] * 360)
    exact = np.abs(x[visible] - samples / 360.0) <= 1e-9
    wanted = np.isin(samples, [key[0] for key in candidates])
    matching = visible[exact & wanted]
    lookup = {}
    ambiguous = set()
    for index in matching:
        sample = _sample_integer(round(float(x[index]) * 360))
        segment = _sample_integer(segments[index])
        if sample is None or segment is None:
            continue
        # Tolerance accommodates only floating-point representation error. A
        # missing/neighboring sample is never accepted as the requested peak.
        if abs(float(x[index]) - sample / 360.0) > 1e-9:
            continue
        key = (sample, segment)
        if key in lookup:
            ambiguous.add(key)
        else:
            lookup[key] = int(index)
    events = {}
    for key, event in candidates.items():
        sample, segment = key
        index = lookup.get(key)
        if index is None or key in ambiguous:
            continue
        target = sample / 360.0
        if not begin <= target <= end:
            continue
        events[key] = {"event": dict(event), "x": target,
                       "y": float(y[index]), "sample_index": index}
    return [events[key] for key in sorted(events)[-MAX_CLASSIFICATION_MARKERS:]]


def decimate_segments(x, y, pixels, xmin, xmax, segment_ids=None, sample_rate=360.0):
    """Return ordered finite polylines; never join a missing point or segment.

    Each occupied pixel column retains its minimum and maximum, in time order.
    This is only for drawing: it must never feed recording or classification.
    """
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.ndim != 1 or y.shape != x.shape:
        raise ValueError("波形时间和数据必须是等长的一维数组")
    if not np.isfinite([xmin, xmax]).all() or xmax <= xmin:
        raise ValueError("无效的波形显示范围")
    pixels = max(1, int(pixels))
    if segment_ids is not None:
        segment_ids = np.asarray(segment_ids)
        if segment_ids.shape != x.shape:
            raise ValueError("分段编号与波形长度不一致")
    if not len(x):
        return []
    valid = np.isfinite(x) & np.isfinite(y) & (x >= xmin) & (x <= xmax)
    breaks = np.zeros(len(x), dtype=bool)
    breaks[0] = True
    if len(x) > 1:
        delta = np.diff(x)
        breaks[1:] = (~valid[:-1]) | (delta <= 0)
        if sample_rate is not None:
            breaks[1:] |= delta > 1.5 / sample_rate
        if segment_ids is not None:
            breaks[1:] |= segment_ids[1:] != segment_ids[:-1]
    starts = np.flatnonzero(valid & breaks)
    # A run also begins at the first visible point after the left boundary.
    starts = np.union1d(starts, np.flatnonzero(valid & ~np.r_[False, valid[:-1]]))
    result = []
    stops = np.flatnonzero(valid & np.r_[~valid[1:] | breaks[1:], True]) + 1
    for start, stop in zip(starts, stops):
        xx, yy = x[start:stop], y[start:stop]
        if len(xx) <= 2 * pixels:
            result.append(np.column_stack((xx, yy)))
            continue
        bins = np.clip(((xx - xmin) * pixels / (xmax - xmin)).astype(int), 0, pixels - 1)
        edges = np.r_[0, np.flatnonzero(np.diff(bins)) + 1, len(bins)]
        # Grouped reductions preserve exactly the same first min/max index in
        # each column, avoiding hundreds of small NumPy calls on the Pi 2.
        groups = np.repeat(np.arange(len(edges)-1), np.diff(edges))
        minimum = np.minimum.reduceat(yy, edges[:-1])
        maximum = np.maximum.reduceat(yy, edges[:-1])
        indices = np.arange(len(yy))
        minima = np.minimum.reduceat(np.where(yy == minimum[groups], indices, len(yy)), edges[:-1])
        maxima = np.minimum.reduceat(np.where(yy == maximum[groups], indices, len(yy)), edges[:-1])
        keep = np.unique(np.r_[0, len(xx)-1, minima, maxima])
        result.append(np.column_stack((xx[keep], yy[keep])))
    return result


class WaveformCanvas(Canvas):
    """A reusable-item ECG plot; the Tk thread owns all methods."""

    COLORS = {"raw": "#7cb5a8", "filtered": "#44f083"}

    def __init__(self, master, **kwargs):
        kwargs.setdefault("background", "#050807")
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(master, **kwargs)
        family = tkfont.nametofont("TkDefaultFont", root=self).actual("family")
        self._axis_font = tkfont.Font(root=self, family=family, size=9)
        self._grid = [self.create_line(0, 0, 0, 0, fill="#12241b") for _ in range(12)]
        self._ticks = [self.create_text(0, 0, fill="#62766a", font=self._axis_font)
                       for _ in range(12)]
        self._title = self.create_text(0, 0, anchor="nw", fill="#8ba996", font=self._axis_font)
        self._time_title = self.create_text(0, 0, anchor="se", fill="#62766a", font=self._axis_font)
        self._legend_raw = self.create_text(0, 0, text="原始", anchor="ne", fill=self.COLORS["raw"], font=self._axis_font)
        self._legend_filtered = self.create_text(0, 0, text="处理后", anchor="ne", fill=self.COLORS["filtered"], font=self._axis_font)
        self._empty = self.create_text(0, 0, text="等待采集", fill="#62766a",
                                      font=(family, 13))
        self._lines = {"raw": [], "filtered": []}
        self._marker_pool = []
        self._marker_data = []
        self.on_marker_click = None
        self._line_height = self._axis_font.metrics("linespace")
        self._axis_left = float(max(52, self._axis_font.measure("-0.00") + 16))
        self._legend_gap = self._axis_font.measure("处理后") + 22
        self._label_widths = {label: self._axis_font.measure(label) for label in MARKER_COLORS}
        self._layer_signature = None

    def render(self, wave, window_s=10.0, mode="filtered", auto_scale=True, vref=3.3,
               markers=None, bounds=None):
        x = np.asarray(wave.get("x", []), dtype=float)
        raw = np.asarray(wave.get("raw", []), dtype=float)
        filtered = np.asarray(wave.get("filtered", []), dtype=float)
        if raw.shape != x.shape or filtered.shape != x.shape or x.ndim != 1:
            raise ValueError("显示快照的数组长度不一致")
        if not np.isfinite(vref) or vref <= 0:
            raise ValueError("参考电压必须大于 0")
        if not np.isfinite(window_s) or window_s <= 0:
            raise ValueError("显示时长必须大于 0")
        width, height = max(self.winfo_width(), 100), max(self.winfo_height(), 100)
        # Two separate text rows: numeric ticks below the plot, then the title.
        # Font metrics also keep the bottom margin valid under display scaling.
        line_height = self._line_height
        left = self._axis_left
        top, right = float(line_height + 18), width - 18.0
        bottom = height - (2 * line_height + 24.0)
        plot_w, plot_h = max(1.0, right - left), max(1.0, bottom - top)
        finite_x = x[np.isfinite(x)]
        if bounds is None:
            end = max(float(window_s), float(finite_x[-1]) if len(finite_x) else 0.0)
            begin = end - float(window_s)
        else:
            if len(bounds) != 2:
                raise ValueError("历史波形范围必须包含起始和结束时间")
            begin, end = map(float, bounds)
            if not np.isfinite([begin, end]).all() or end <= begin:
                raise ValueError("无效的历史波形显示范围")
        span_s = end - begin
        series = {"raw": adc_to_volts(raw, vref), "filtered": adc_to_volts(filtered, vref)}
        active = ("raw", "filtered") if mode == "both" else (mode,)
        if any(key not in series for key in active):
            raise ValueError("未知波形显示模式")
        selected = [values[(x >= begin) & (x <= end) & np.isfinite(values)]
                    for key, values in series.items() if key in active]
        visible = np.concatenate(selected) if selected else np.empty(0)
        if len(visible):
            lo, hi = float(visible.min()), float(visible.max())
            if auto_scale:
                span = max(hi - lo, vref * 0.02)
                middle = (lo + hi) / 2
                lo, hi = middle - span * 0.62, middle + span * 0.62
            else:
                lo, hi = min(0.0, lo), max(vref, hi)
                if lo < 0:
                    lo -= (hi - lo) * 0.02
                if hi > vref:
                    hi += (hi - lo) * 0.02
        else:
            lo, hi = 0.0, vref
        for index in range(6):
            fraction = index / 5
            xx, yy = left + plot_w * fraction, top + plot_h * fraction
            self.coords(self._grid[index], xx, top, xx, bottom)
            self.coords(self._grid[index + 6], left, yy, right, yy)
            self.coords(self._ticks[index], xx, bottom + 7)
            anchor = "nw" if index == 0 else "ne" if index == 5 else "n"
            self.itemconfigure(self._ticks[index], text=f"{begin + span_s * fraction:.1f}", anchor=anchor)
            self.coords(self._ticks[index + 6], left - 7, yy)
            self.itemconfigure(self._ticks[index + 6], text=f"{hi - (hi - lo) * fraction:.2f}", anchor="e")
        self.coords(self._title, left, 8)
        self.itemconfigure(self._title, text="ADC 输入电压 / V · 未校准")
        legend_gap = self._legend_gap
        self.coords(self._legend_raw, right - legend_gap if mode == "both" else right, 8)
        self.coords(self._legend_filtered, right, 8)
        self.itemconfigure(self._legend_raw, state="normal" if "raw" in active else "hidden")
        self.itemconfigure(self._legend_filtered, state="normal" if "filtered" in active else "hidden")
        self.coords(self._time_title, right, height - 7)
        self.itemconfigure(self._time_title, text="采样相对时间 / s")
        self.coords(self._empty, (left + right) / 2, (top + bottom) / 2)
        self.itemconfigure(self._empty, state="hidden" if len(visible) else "normal")
        segment_ids = wave.get("segment_ids", wave.get("segment_id"))
        for key, values in series.items():
            segments = decimate_segments(x, values, int(plot_w), begin, end, segment_ids) if key in active else []
            pool = self._lines[key]
            while len(pool) < len(segments):
                pool.append(self.create_line(0, 0, 0, 0, fill=self.COLORS[key],
                                             width=1.0, capstyle=tk.BUTT, joinstyle=tk.MITER))
            for index, item in enumerate(pool):
                if index >= len(segments):
                    self.itemconfigure(item, state="hidden")
                    continue
                segment = segments[index]
                coordinates = np.column_stack((left + (segment[:, 0] - begin) / span_s * plot_w,
                                                bottom - (segment[:, 1] - lo) / (hi - lo) * plot_h))
                if len(coordinates) == 1:
                    coordinates = np.repeat(coordinates, 2, axis=0)
                    coordinates[1, 0] += 0.5
                self.coords(item, *coordinates.ravel().tolist())
                self.itemconfigure(item, state="normal")
        marker_series = series["filtered"] if "filtered" in active else series["raw"]
        positions = classification_marker_positions(x, marker_series, segment_ids, markers, begin, end)
        self._render_markers(positions, begin, span_s, left, top, right, bottom, lo, hi, line_height)
        layers = (sum(map(len, self._lines.values())), len(self._marker_pool))
        if layers != self._layer_signature:
            for pool in self._lines.values():
                for item in pool:
                    self.tag_raise(item)
            self.tag_raise("classification_marker")
            self.tag_raise(self._empty)
            self._layer_signature = layers

    def _marker_clicked(self, slot):
        if callable(self.on_marker_click) and 0 <= slot < len(self._marker_data):
            self.on_marker_click(dict(self._marker_data[slot]))

    def _render_markers(self, positions, begin, span_s, left, top, right, bottom, lo, hi, line_height):
        """Reuse at most 100 marker slots (dot, short leader, optional label)."""
        positions = positions[:MAX_CLASSIFICATION_MARKERS]
        while len(self._marker_pool) < len(positions):
            slot = len(self._marker_pool)
            tag = f"classification_marker_{slot}"
            tags = ("classification_marker", tag)
            items = {
                "dot": self.create_oval(0, 0, 0, 0, width=1, tags=tags),
                "leader": self.create_line(0, 0, 0, 0, width=1, tags=tags),
                "label": self.create_text(0, 0, font=self._axis_font, anchor="n", tags=tags),
            }
            self.tag_bind(tag, "<Button-1>", lambda event, index=slot: self._marker_clicked(index))
            self._marker_pool.append(items)
        self._marker_data = [position["event"] for position in positions]
        row_right = [-float("inf"), -float("inf")]
        plot_w, plot_h = max(1.0, right - left), max(1.0, bottom - top)
        for slot, items in enumerate(self._marker_pool):
            if slot >= len(positions):
                for item in items.values():
                    self.itemconfigure(item, state="hidden")
                continue
            position = positions[slot]
            label = position["event"]["label"]
            color = MARKER_COLORS[label]
            xx = left + (position["x"] - begin) / span_s * plot_w
            yy = bottom - (position["y"] - lo) / (hi - lo) * plot_h
            self.coords(items["dot"], xx - 3, yy - 3, xx + 3, yy + 3)
            self.itemconfigure(items["dot"], state="normal", fill=color, outline="#050807")
            self.coords(items["leader"], xx, max(top, yy - 12), xx, max(top, yy - 5))
            self.itemconfigure(items["leader"], state="normal", fill=color)
            half_text = (self._label_widths[label] + 8) / 2
            text_x = min(right - half_text, max(left + half_text, xx))
            row = next((index for index, previous in enumerate(row_right)
                        if text_x - half_text >= previous + 3), None)
            if row is None:
                self.itemconfigure(items["label"], state="hidden")
            else:
                row_right[row] = text_x + half_text
                self.coords(items["label"], text_x, top + 3 + row * (line_height + 2))
                self.itemconfigure(items["label"], state="normal", fill=color, text=label)
