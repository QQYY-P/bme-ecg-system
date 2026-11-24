# 云端辅助工具索引

[云端导航](../README.md) · [快速开始](../docs/快速开始.md) · [部署工具](../deploy/README.md)

下表按用途整理现有根目录脚本。运行目录仍为 `心电云端平台/`，现有 `.cmd` 启动器、Python 导入和历史命令路径保持兼容。

| 分类 | 文件 | 作用与运行影响 |
| --- | --- | --- |
| 服务入口 | [run_server.py](../run_server.py)、[启动服务.cmd](../启动服务.cmd)、[停止服务.ps1](../停止服务.ps1) | 启动/停止本地服务；当前开发步骤见快速开始 |
| 账号管理 | [manage_accounts.py](../manage_accounts.py) | 修改指定数据目录的账号或设备密钥；`python manage_accounts.py --help` 查看命令，不在公开日志记录凭据 |
| 数据恢复 | [restore_database.py](restore_database.py) | `python tools/restore_database.py <服务器备份ZIP> --destination <不存在的新目录>`；逐文件验证后写新目录，撤销恢复库中的旧登录会话与设备密钥，不切换正式服务；见[使用说明](../docs/使用说明.md#服务器上的备份与恢复) |
| 合成演示 | [demo.py](../demo.py)、[运行演示.cmd](../运行演示.cmd) | 默认会生成并上传模拟记录；`python demo.py --generate-only` 只生成本地合成记录 |
| 记录上传 | [uploader.py](../uploader.py) | `python uploader.py --session-dir <目录> --url <地址>`；上传已有记录并保存重试状态，先核对授权及目标 |
| 本机链路验证 | [verify_local.py](../verify_local.py)、[verify_board.py](../verify_board.py) | 依赖相应服务、演示或板端参考资料；先读脚本说明；不是无需前置数据的单元测试 |
| 云端逐点核对 | [verify_cloud.py](../verify_cloud.py) | `python verify_cloud.py --session-dir <目录> --expected-model ready`；核对云端与源记录 |
| 远程模型验证 | [verify_cloud_model.py](../verify_cloud_model.py) | 需要既有参考及服务；会触发云端复算并写报告 |
| 采集证据验收 | [verify_live_capture.py](../verify_live_capture.py) | `python verify_live_capture.py --run-dir <目录>`；远程 GET；`--offline` 仅检查本地证据 |
| 已有结果复核 | [verify_live_model.py](../verify_live_model.py) | `python verify_live_model.py --session-dir <目录>`；读取已有云端结果、本地独立复算，不请求远程分析 |
| 历史 SSH 中转 | [cloud_link.py](../cloud_link.py)、[pi_link.py](../pi_link.py)、[board_stream.py](../board_stream.py)、[auto_link.py](../auto_link.py) | 建立连接、采集或上传；旧入口见[历史流程](../docs/README.md#历史流程)，正式登录使用 HTTPS |
| 历史连接准备 | [prepare_auto_link.py](../prepare_auto_link.py)、[setup-ecg-relay.sh](../setup-ecg-relay.sh)、[setup-ecg-tunnel.sh](../setup-ecg-tunnel.sh) | 可能创建连接身份或修改服务配置，仅用于明确的环境配置任务 |

原根目录的“连接开发板”“连接云服务器”“手动连接云服务器”“开发板上传云端”启动器仍是历史流程入口；不将其中的地址、记录数量和部署状态视为当前配置。

日常软件检查统一执行仓库级 [run_checks.py](../../tools/README.md)，不会要求提供私有实采资料。构建、部署与研究脚本分别在 [deploy/](../deploy/README.md)、[research/](../research/README.md)，避免与以上现场联调工具混用。
