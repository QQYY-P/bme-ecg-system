# 心电云端与工作台

接收树莓派记录，按账号与设备管理数据，提供波形回看及后台分析。手机和电脑共用网页；Windows 启动器在 `deploy/desktop/`。

[项目首页](../README.md) · [文档分类](docs/README.md) · [源码地图](../docs/仓库导览.md) · [算法入口](../docs/算法与评估.md)

## 使用与开发入口

- [下载电脑端与打开网页](../README.md#应用下载)
- [快速开始](docs/快速开始.md)、[使用说明](docs/使用说明.md)、[故障排查](docs/故障排查.md)；[离线文本](README.txt) 自动生成，原文快照在历史目录。
- [当前进度及部署验证](docs/云端工作台迭代.md) — 功能、版本、备份与待验收项的统一入口。
- [Windows 客户端构建](deploy/desktop/README.md)、[Linux 部署](deploy/linux/部署说明.txt)、[手机 HTTPS 访问](deploy/linux/手机公网访问说明.txt)
- [Android 试用版下载与验收边界](../docs/手机端与安卓安装包.md)、[Android 工程与构建](android/README.md)
- 历史流程复现：[2026-10-29 笔记本中转](docs/自动连接说明.txt)、[2026-10-28 开发板演示](docs/开发板上传演示说明.txt)。新账号登录使用根 README 的 HTTPS 入口。

## 目录与职责

| 位置 | 职责 |
| --- | --- |
| [server.py](server.py)、[run_server.py](run_server.py) | 服务端与启动入口 |
| `*_store.py`、[device_routes.py](device_routes.py)、[analysis_tasks.py](analysis_tasks.py) | 账号、设备、记录与持久分析任务 |
| [web/](web/) | 手机/电脑网页、PWA、波形与操作界面 |
| [android/](android/README.md) | Android 试用客户端、资源与构建工具 |
| [cloud_model/](cloud_model/README.md) | 云端模型适配器及固定版本依赖；[来源](cloud_model/SOURCE.json) |
| [physiology_analysis.py](physiology_analysis.py) | 独立 QRS/RR 后台分析、结果版本与输入门控 |
| [research/](research/README.md) | 离线评估；其中检测与统计两个纯函数模块供云端复用 |
| [deploy/](deploy/README.md) | 按目标查找云端、Windows 打包和升级工具 |
| [tests/](tests/) | Python、网页脚本与 Windows 启动器检查 |
| [docs/](docs/README.md) | 使用、部署进度与历史流程分类 |
| [docs/archive/](docs/archive/README.md) | 历史技术方案、阶段验证 |

根目录 `.cmd`、`.ps1`、连接及验证脚本按[辅助工具索引](tools/README.md)分类查阅，保留原运行路径。`requirements*.txt` 仍放在模块根目录。测试方式统一见 [贡献指南](../CONTRIBUTING.md#检查与实物联调)。

`work/`、`outputs/`、`deploy/dist/`、`.cloud_private/` 和采集数据属于本地运行内容，不加入 Git；下载包通过 Release 或正式网页分发。

## QRS/RR 分析

安装 `requirements-model.txt` 后即可运行 QRS/RR，不要求启用分类模型。记录上传完成后，在手机或电脑的“QRS / RR 分析”面板点击分析；后台排队并保存结果，退出页面不影响已接受的任务。页面显示 R 峰数、平均 RR、换算心率、质量门控比例、有效时长/点数、排除原因及 RR 变异性。没有可靠正常窦性间期标记时，HRV 明确显示不可用。

单记录上限 100 万采样点（360 Hz 时约 46 分钟）；模拟、模式不明或质量不足的记录可能没有可用结果。原始记录和分类结果分别保留。接口及升级/回退边界见 [接口约定](../docs/接口约定.md#独立-qrsrr-分析2026-11-24待部署)。本轮仅提交源码与软件验证，公网和已发布 APK 需后续更新才会出现此功能。
