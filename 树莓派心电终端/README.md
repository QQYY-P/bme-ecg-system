# 树莓派心电终端

接收 STM32 串口数据，在本地屏幕显示波形，完成预处理、质量判断、心搏分类、记录与云端上传。

[项目首页](../README.md) · [文档分类](docs/README.md) · [源码地图](../docs/仓库导览.md) · [算法入口](../docs/算法与评估.md)

## 使用与开发入口

- [安装依赖、运行与排错](README.txt) — 完整步骤及各阶段验证记录；仍随原有发布包提供。
- [待机与日常使用](docs/设备待机与日常使用.md)、[固定 IP 连接](docs/固定IP连接说明.txt)
- [STM32 七字节协议](docs/STM32串口对接说明.txt)、[实采预处理](docs/实采预处理说明.txt)、[状态与信号质量](docs/状态与信号质量说明.txt)
- [分类接入](docs/三分类实时接入说明.txt)、[分类呈现与复核](docs/分类呈现与复核说明.txt)、[记录与设置](docs/数据管理与设置说明.txt)
- [云端账号归属](docs/云端账号与记录归属.txt)；[旧版笔记本中转与分析](docs/自动连接与云端分析.txt)保留历史流程，现行账号登录使用 HTTPS。
- [统一开发进度](../心电云端平台/docs/云端工作台迭代.md)

## 目录与职责

| 位置 | 职责 |
| --- | --- |
| [app.py](app.py)、`start-*.sh` | 程序与启动入口 |
| [ui/](ui/) | 波形、历史记录及设置界面 |
| [acquisition.py](acquisition.py)、[protocol.py](protocol.py)、[preprocessing.py](preprocessing.py) | 采集、解析与信号预处理 |
| [cardioedge/](cardioedge/) | 当前模型、NumPy 推理适配及[第三方说明](cardioedge/THIRD_PARTY_NOTICES.txt) |
| [cloud/](cloud/) | 上传、设备状态与云端协议 |
| [tools/](tools/) | 部署、打包、性能检查与验证工具 |
| [tests/](tests/)、[validation/](validation/) | 软件回归及公开模型验证资料 |
| [docs/](docs/README.md) | 按日常使用、采集分析与阶段记录分类 |
| [models/](models/) | 保留的旧模型原型；与当前 `cardioedge/` 分开 |

早期设计、课程阶段要求与无模型框图统一放在 [历史资料](docs/archive/README.md)。原文中的“三字节协议”“尚未完成云端”等描述属于对应阶段；现行对接以 [共同接口约定](../docs/接口约定.md) 和统一开发进度为准。

启动、导入和依赖路径保持不变。测试方式见 [贡献指南](../CONTRIBUTING.md#检查与实物联调)；本地测试不代替串口、电极与 HDMI 实物验收。
