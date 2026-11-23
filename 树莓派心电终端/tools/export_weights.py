"""仅在已有 PyTorch 的电脑上执行：导出融合 BN 的 NumPy 权重。"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch


def export(source, destination):
    original = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    config = json.loads((source / "model_config.json").read_text(encoding="utf-8"))
    expected = dict(in_channels=1, samples_per_lead=180, rr_features=2,
                    channels=[32, 64, 128], blocks_per_stage=2, n_classes=3)
    if config != expected or original["labels"] != ["N", "PVC", "PAC"]:
        raise ValueError("仅允许已验证的三分类 conv/no-SE 架构")
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    for entry in original["weights"]:
        path = source / entry["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("原权重校验失败：" + str(path))
        state = {k: v.detach().cpu().numpy() for k, v in
                 torch.load(path, map_location="cpu", weights_only=True).items()}
        arrays = {}

        def norm(name):
            scale = state[name + ".weight"] / np.sqrt(state[name + ".running_var"] + 1e-5)
            offset = state[name + ".bias"] - state[name + ".running_mean"] * scale
            return scale, offset

        def fused(conv_name, bn_name, target):
            scale, offset = norm(bn_name)
            arrays[target + ".weight"] = state[conv_name + ".weight"] * scale[:, None, None]
            arrays[target + ".bias"] = offset

        fused("stem.0", "stem.1", "stem")
        for i in range(6):
            name = "stages." + str(i)
            for index in (1, 2):
                fused(name + ".conv" + str(index), name + ".bn" + str(index), name + ".conv" + str(index))
            if i in (2, 4):
                fused(name + ".shortcut.0", name + ".shortcut.1", name + ".shortcut")
        arrays["rr.scale"], arrays["rr.offset"] = norm("rr_norm")
        for layer in (0, 3):
            for suffix in ("weight", "bias"):
                key = "head." + str(layer) + "." + suffix
                arrays[key] = state[key]
        output = destination / (path.stem + ".npz")
        np.savez_compressed(output, **arrays)
        entries.append(dict(file=output.name, sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                            source_sha256=entry["sha256"]))
    manifest = dict(format="ecg-resnet1d-fused-numpy-v1", model_id=original["model_id"],
                    labels=original["labels"], sample_rate=360, weights=entries,
                    note=original["note"], reference_validation=original["reference_validation"])
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已导出三个模型；请运行测试核对数值。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--destination", type=Path, default=Path(__file__).resolve().parents[1] / "models")
    args = parser.parse_args()
    export(args.source, args.destination)
