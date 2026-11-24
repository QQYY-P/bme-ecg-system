# 离线 ECG 研究工具

[算法与评估](../../docs/算法与评估.md) · [已有报告](../../docs/algorithm-validation/README.md) · [云端首页](../README.md)

本目录用于公开数据回放与离线评估。`streaming_qrs.py` 与 `physiology.py` 同时供云端独立 QRS/RR 分析复用，随源码包交付；评估脚本、基准工具、WFDB 及公开数据不进入生产部署包。

| 文件 | 职责 |
| --- | --- |
| [evaluate_ecg.py](evaluate_ecg.py) | 读取 MIT-BIH 记录、匹配 QRS、计算误差并生成 HTML/JSON |
| [streaming_qrs.py](streaming_qrs.py) | 流式 QRS 检测路径 |
| [physiology.py](physiology.py) | RR/NN 与时域 HRV 研究统计 |
| [研究依赖](../requirements-research.txt) | 离线评估使用的依赖 |
| [研究测试](../tests/test_ecg_research.py) | 软件回归测试 |
| [benchmark_cloud_physiology.py](benchmark_cloud_physiology.py) | 临时合成数据的 3 设备、2 账号本地并发检查 |

## 复跑入口

先按 [根 README](../../README.md#开发环境) 创建环境，再按 [完整运行说明](../README.txt) 的“离线实验”步骤准备公开数据。数据放在仓库外；从 `心电云端平台/` 执行：

```powershell
..\.venv\Scripts\python.exe -m research.evaluate_ecg --data "D:\datasets\mitdb" --records 100 101 103 105 200 201 207 208 231 232 --seconds 300
```

示例数据路径需要替换为本机位置。输出中的配置、代码及输入摘要用于复核结果。单元测试不需要下载 MIT-BIH 数据；测试入口见 [检查工具](../../tools/README.md)。当前交付及验证状态见 [更新日志](../../CHANGELOG.md)。

从云端模块执行 `python research/benchmark_cloud_physiology.py` 可复跑本地并发基准。需要当前云端测试依赖及 NumPy/SciPy；脚本建立临时账号和三条 60 秒合成记录，完成后清除。它使用本地 TestClient，不包含公网传输、TLS、实体设备或真实心电数据，不能推导服务器并发容量。实际测量结果记录在 [云端工作台迭代](../docs/云端工作台迭代.md)。
