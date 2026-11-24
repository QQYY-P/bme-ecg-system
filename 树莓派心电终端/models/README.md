# 旧模型原型

[当前端侧模型](../cardioedge/README.md) · [算法导航](../../docs/算法与评估.md)

本目录保留历史 N/PVC/PAC 三分类集成原型及回归依据。当前应用入口使用 `cardioedge/`，不加载本目录模型。

| 文件 | 用途 |
| --- | --- |
| [manifest.json](manifest.json) | 格式、类别顺序、360 Hz 采样率、权重摘要及历史参考指标 |
| [seed42.npz](seed42.npz)、[seed43.npz](seed43.npz)、[seed44.npz](seed44.npz) | 三个历史模型权重 |
| [LICENSE.txt](LICENSE.txt) | 原模型许可 |

相关实现为 [numpy_predictor.py](../numpy_predictor.py)、[ecg_classifier.py](../ecg_classifier.py)、[inference_worker.py](../inference_worker.py)。不要将这里的类别顺序、评估指标或权重与当前 N/S/V 模型混用。

历史格式和测试仍可能引用这些文件，因此保留原路径。重新导出工具是 [export_weights.py](../tools/export_weights.py)，仅在明确维护此原型时使用，并更新清单摘要和对应回归结果。
