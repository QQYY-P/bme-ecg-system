"""固定 ResNet-1D 的 float32 CPU 前向计算，树莓派运行时不需要 PyTorch。

卷积和 BatchNorm 在电脑导出时融合；保持三个 softmax 概率平均。
仅支持随包模型架构，禁止把不同架构的权重静默当作本模型。
"""
import hashlib
import json
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

MODEL_DIR = Path(__file__).resolve().parent / "models"


def conv(x, weights, bias, stride=1):
    kernel = weights.shape[2]
    padded = np.pad(x, ((0, 0), (0, 0), (kernel // 2, kernel // 2)))
    windows = sliding_window_view(padded, kernel, axis=2)[:, :, ::stride, :]
    batch, channels, length, _ = windows.shape
    matrix = np.ascontiguousarray(windows.transpose(0, 2, 1, 3)).reshape(batch * length, -1)
    result = matrix @ weights.reshape(weights.shape[0], -1).T + bias
    return result.reshape(batch, length, -1).transpose(0, 2, 1)


class Predictor:
    def __init__(self, directory=MODEL_DIR):
        directory = Path(directory)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if (manifest["format"] != "ecg-resnet1d-fused-numpy-v1"
                or manifest["labels"] != ["N", "PVC", "PAC"]
                or len(manifest["weights"]) != 3):
            raise ValueError("不支持的模型格式或类别顺序")
        self.model_id = manifest["model_id"]
        self.models = []
        for entry in manifest["weights"]:
            path = directory / entry["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError("模型校验失败：" + path.name)
            with np.load(path, allow_pickle=False) as archive:
                self.models.append({key: archive[key] for key in archive.files})

    @staticmethod
    def _forward(weights, morph, rr):
        def layer(x, name, stride=1):
            return conv(x, weights[name + ".weight"], weights[name + ".bias"], stride)

        x = np.maximum(layer(morph[:, None, :], "stem", 2), 0)
        padded = np.pad(x, ((0, 0), (0, 0), (1, 1)), constant_values=-np.inf)
        x = sliding_window_view(padded, 3, axis=2)[:, :, ::2, :].max(axis=-1)
        for i in range(6):
            name = "stages." + str(i)
            stride = 2 if i in (2, 4) else 1
            identity = layer(x, name + ".shortcut", stride) if stride == 2 else x
            out = np.maximum(layer(x, name + ".conv1", stride), 0)
            x = np.maximum(layer(out, name + ".conv2") + identity, 0)
        rr = rr * weights["rr.scale"] + weights["rr.offset"]
        features = np.concatenate((x.mean(axis=2), rr), axis=1)
        hidden = np.maximum(features @ weights["head.0.weight"].T + weights["head.0.bias"], 0)
        logits = hidden @ weights["head.3.weight"].T + weights["head.3.bias"]
        scores = np.exp(logits - logits.max(axis=1, keepdims=True))
        return scores / scores.sum(axis=1, keepdims=True)

    def __call__(self, morph, rr):
        morph = np.asarray(morph, dtype=np.float32)
        rr = np.asarray(rr, dtype=np.float32)
        if (morph.ndim != 2 or morph.shape[1] != 180 or rr.shape != (len(morph), 2)
                or not np.isfinite(morph).all() or not np.isfinite(rr).all()):
            raise ValueError("需要有限的 [N,180] 心拍和 [N,2] RR 特征")
        result = np.empty((len(morph), 3), dtype=np.float32)
        # 限制卷积临时矩阵，避免在 1 GB 板上一次展开大量心拍。
        for start in range(0, len(morph), 4):
            wave, intervals = morph[start:start + 4], rr[start:start + 4]
            result[start:start + 4] = np.mean(
                [self._forward(w, wave, intervals) for w in self.models], axis=0)
        return result
