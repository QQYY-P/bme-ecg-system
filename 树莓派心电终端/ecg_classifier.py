"""三分类推理：固定窗口检测、180 点心拍归一化和仅使用历史的 RR 特征。"""
from collections import deque
from numpy_predictor import Predictor

import numpy as np
from scipy.signal import butter, filtfilt, find_peaks
from notch_filter import NotchFilter

FS = 360  # 模型训练采样率，不接受直接修改时间轴来冒充重采样。
HALF = 90  # 心拍切片：检测峰前 90 点、从峰起后 90 点。
LABELS = ("N", "PVC", "PAC")  # 必须与权重的输出神经元顺序一致。
NAMES = ("正常心拍", "室性早搏", "房性早搏")


def preprocess_windows(windows):
    """逐拍减去首尾连线，再做零均值、单位标准差；与训练代码相同。"""
    output = np.zeros_like(windows, dtype=float)
    for index, window in enumerate(windows):
        corrected = window - np.linspace(window[0], window[-1], len(window))
        centered = corrected - np.mean(corrected)
        std = np.std(corrected)
        output[index] = centered / std if std != 0 else centered
    return output.astype(np.float32)


def detect(signal):
    """复用训练数据生成时的 5～40 Hz 检测支路，不改变形态支路。"""
    if len(signal) < 32:
        return np.empty(0, dtype=int)
    b, a = butter(2, [5 / (FS / 2), 40 / (FS / 2)], btype="band")
    filtered = filtfilt(b, a, signal - np.mean(signal))
    energy = filtered * filtered
    peaks, _ = find_peaks(energy, distance=90,
                         prominence=max(float(np.std(energy)) * 0.35, 1e-12))
    return peaks


class HistoryRR:
    """维护最近 8 个历史间期；不读取下一次心跳或整段记录的中位数。"""

    def __init__(self):
        self.last = None  # 上一个检测峰的绝对采样下标。
        self.intervals = deque(maxlen=8)

    def step(self, peak):
        """输出当前间期/历史中位数、上一间期/历史中位数。"""
        if self.last is None:
            self.last = int(peak)
            return np.ones(2, dtype=np.float32)
        interval = int(peak) - self.last
        if interval <= 0:
            raise ValueError("检测峰必须严格递增")
        median = float(np.median(self.intervals)) if self.intervals else float(interval)
        previous = self.intervals[-1] if self.intervals else interval
        result = np.clip([interval / median, previous / median], 0.25, 4).astype(np.float32)
        self.intervals.append(interval)
        self.last = int(peak)
        return result


class FixedFrameStream:
    """10 秒核心窗口、左右各 2 秒上下文；在线结果约延后 2～12 秒确认。

    处理边界和研究代码一致；断流必须 reset，不能删除缺失点后拼接心拍。
    """

    def __init__(self, predictor, start=0, minimum_range=0):
        self.predictor = predictor
        self.minimum_range = minimum_range  # ADC 场景拒绝近乎平直的心拍，非完整质量评估。
        self.reset(start)

    def reset(self, start=0):
        """新连续段清空波形、峰检测和 RR 历史。"""
        self.buf = np.empty(0)
        self.origin = self.received = self.core = self.segment_start = int(start)
        self.last_peak = None
        self.rr = HistoryRR()
        self.closed = False

    def push(self, samples, start=None):
        """输入连续采样块，返回已确认的逐拍结果。"""
        samples = np.asarray(samples, dtype=float)
        if self.closed or samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError("必须输入未关闭数据流的有限一维采样")
        if start is not None and start != self.received:
            raise ValueError("采样缺口必须重置检测器")
        rows = []
        for offset in range(0, len(samples), 4096):
            part = samples[offset:offset + 4096]
            self.buf = np.concatenate([self.buf, part])
            self.received += len(part)
            rows.extend(self._drain(False))
        return rows

    def flush(self):
        """停止采集后处理尾段；不补造未来数据，结果标记为尾段。"""
        if self.closed:
            return []
        rows = self._drain(True)
        self.closed = True
        return rows

    def _drain(self, final):
        """固定核心窗口只输出一次，避免滑窗重复计算同一心拍。"""
        rows = []
        while self.core < self.received:
            end = self.core + 3600
            if not final and self.received < end + 720:
                break
            core_end = min(end, self.received)
            left = max(self.segment_start, self.core - 720)
            right = min(end + 720, self.received)
            signal = self.buf[left - self.origin:right - self.origin]
            accepted = []
            for peak in detect(signal) + left:
                peak = int(peak)
                if not self.core <= peak < core_end or peak - HALF < self.segment_start or peak + HALF > right:
                    continue
                if self.last_peak is not None and peak - self.last_peak < 90:
                    continue
                self.last_peak = peak
                accepted.append(peak)
            if accepted:
                windows = np.stack([self.buf[p - HALF - self.origin:p + HALF - self.origin] for p in accepted])
                rr = np.stack([self.rr.step(p) for p in accepted])
                valid = np.ptp(windows, axis=1) >= self.minimum_range
                if valid.any():
                    probabilities = self.predictor(preprocess_windows(windows[valid]), rr[valid])
                    if (probabilities.shape != (int(valid.sum()), 3)
                            or not np.isfinite(probabilities).all()):
                        raise ValueError("模型输出不是有效的三分类概率")
                    for peak, features, prob in zip(np.asarray(accepted)[valid], rr[valid], probabilities):
                        rows.append({"peak": int(peak), "time_s": int(peak) / FS,
                                     "rr": features.tolist(), "proba": prob.tolist(),
                                     "label": int(np.argmax(prob)),
                                     "latency_s": (self.received - int(peak)) / FS,
                                     "tail": bool(final)})
            self.core = end
            retain = max(self.segment_start, self.core - 720)
            drop = max(0, min(retain - self.origin, len(self.buf)))
            self.buf = self.buf[drop:].copy()
            self.origin += drop
        return rows


class AcquisitionClassifier:
    """将串口 ADC 采样转换为连续模型输入；保留原始时间和缺口。"""

    def __init__(self, predictor, use_notch=True):
        self.predictor = predictor
        self.use_notch = use_notch
        self.reset()

    def reset(self):
        """断流或导联无效后重新积累，不复用此前的 RR 间期。"""
        self.notch = NotchFilter() if self.use_notch else None
        self.stream = None
        self.last_index = None
        self.status = "等待连续数据：分类前陷波预热 2 秒，再积累 12 秒" if self.use_notch else "等待连续 12 秒数据"

    def process(self, times, raw):
        """按连续有效段处理；ADC 两端饱和、NaN、时间跳变均切断上下文。"""
        times, raw = np.asarray(times), np.asarray(raw)
        if times.ndim != 1 or raw.shape != times.shape:
            raise ValueError("采样时间与 ADC 数据必须等长")
        rows = []
        start = 0
        while start < len(raw):
            if not np.isfinite(times[start]) or not np.isfinite(raw[start]) or not 0 < raw[start] < 4095:
                self.reset()
                self.status = "无效/饱和采样：已重置，等待新的连续心电"
                start += 1
                continue
            index = int(round(times[start] * FS))
            if self.last_index is not None and index != self.last_index + 1:
                self.reset()
            stop = start + 1
            while stop < len(raw):
                if (not np.isfinite(raw[stop]) or not 0 < raw[stop] < 4095
                        or not np.isfinite(times[stop])
                        or int(round(times[stop] * FS)) != index + stop - start):
                    break
                stop += 1
            values = raw[start:stop]
            self.last_index = index + len(values) - 1
            if self.notch:
                values, warmup = self.notch.process(values)
                skip = int(np.count_nonzero(warmup))
                values, index = values[skip:], index + skip
            if len(values):
                if self.stream is None or self.stream.closed:
                    self.stream = FixedFrameStream(self.predictor, start=index, minimum_range=4)
                rows.extend(self.stream.push(values, start=index))
                duration = (self.stream.received - self.stream.segment_start) / FS
                if duration < 12:
                    self.status = f"正在积累心拍上下文 {duration:.1f} / 12 秒"
                else:
                    self.status = "后台分类中 · 每 10 秒更新 · 约延后 2～12 秒；无结果时检查信号"
            start = stop
        return rows

    def flush(self):
        """正常停止采集时输出可用尾段；缺少半拍的边界点仍不分类。"""
        return self.stream.flush() if self.stream else []
