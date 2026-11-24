# 当前端侧分类模型

[算法导航](../../docs/算法与评估.md) · [接入约定](../docs/三分类实时接入说明.txt) · [云端副本](../../心电云端平台/cloud_model/README.md)

当前应用使用 CardioEdgeAI v1 INT8 的专用 NumPy 适配器，输出 N/S/V；不依赖树莓派上的 PyTorch 或通用 TFLite 运行时。

| 文件 | 用途 |
| --- | --- |
| [service.py](service.py) | 应用后台分类服务入口 |
| [stream.py](stream.py) | 连续段、预处理、R 点与上下文组织 |
| [numpy_candidate.py](numpy_candidate.py) | 固定模型的 NumPy 数值实现 |
| [portable_weights.npz](portable_weights.npz) | 当前适配器权重 |
| [pi_parity_vectors.npz](pi_parity_vectors.npz) | 用于数值一致性核对的公开来源测试向量 |
| [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt) | 上游提交、模型及测试向量来源 |
| [LICENSE.txt](LICENSE.txt)、[LICENSE-LiteRT.txt](LICENSE-LiteRT.txt) | 对应第三方许可 |

上游固定提交为 `ec36bbc5f96ea42a93274c499a51e3678742ffcf`。这是模型专用实验适配器，不能用任意 TFLite 模型替换权重。

更新时同步核对模型来源、权重摘要、数值一致性及流式输入测试；如更新云端副本，还需更新其 `SOURCE.json` 并分别测试两个 Python 工程。当前输入和质量门控见[接口约定](../../docs/接口约定.md)，评估工具见[工具索引](../tools/README.md#模型评估与性能)。
