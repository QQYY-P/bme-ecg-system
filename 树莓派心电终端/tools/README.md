# 树莓派工具索引

[终端导航](../README.md) · [快速开始](../docs/快速开始.md) · [故障排查](../docs/故障排查.md)

从 `树莓派心电终端/` 根目录运行下列命令，使用已安装模块依赖的 Python。Linux、图形会话、实体串口和参考数据的要求分别列出；不因文件名带 `verify` 就视为普通单元测试。

## 当前构建与设备配置

| 工具 | 用法或入口 | 执行影响 |
| --- | --- | --- |
| [package_cloud_workspace.py](package_cloud_workspace.py) | `python tools/package_cloud_workspace.py` | 按[白名单](cloud_workspace_files.json)生成当前完整工作台包到 `dist/`，不部署 |
| [configure_device_boot.py](configure_device_boot.py) | `sudo python3 tools/configure_device_boot.py status`；安装与撤销见[设备说明](../docs/设备待机与日常使用.md#启动与部署准备) | `status` 查看状态；`install/uninstall` 修改树莓派启动配置，仅在设备上按部署任务执行 |

## 采集、协议与显示诊断

| 工具 | 用途 / 示例 | 前置条件与影响 |
| --- | --- | --- |
| [capture_live_ecg.py](capture_live_ecg.py) | `python tools/capture_live_ecg.py --output evidence/capture --seconds 120` | Linux 图形环境、实际串口；会采集和保存真实输入 |
| [analyze_live_capture.py](analyze_live_capture.py)、[analyze_stage_capture.py](analyze_stage_capture.py) | `python tools/analyze_stage_capture.py <采集目录>` | 读取已有采集并写本地审计结果；不上传 |
| [verify_stm32_serial.py](verify_stm32_serial.py) | `python tools/verify_stm32_serial.py --help` | Linux PTY 软件串口验证，不打开实体串口；参数以脚本为准 |
| [verify_status_feedback.py](verify_status_feedback.py) | `python tools/verify_status_feedback.py --output-dir evidence/status` | Linux PTY 与 Tk；生成模拟验证结果 |
| [verify_waveform_runtime.py](verify_waveform_runtime.py) | 纯波形运行检查；查看脚本说明后运行 | 需要图形环境，产生验证输出 |
| [display_probe.py](display_probe.py)、[display_probe.sh](display_probe.sh) | `sh tools/display_probe.sh` | 在树莓派物理控制台显示测试窗口 |
| [display_probe_fixed.sh](display_probe_fixed.sh)、[display_probe_noglamor.sh](display_probe_noglamor.sh)、[ecg-probe-noglamor.conf](ecg-probe-noglamor.conf) | 历史 HDMI 诊断变体 | 启动诊断图形会话；先读[HDMI 排错](../docs/故障排查.md#hdmi-黑屏)，不作为日常启动入口 |

## 模型评估与性能

| 工具 | 用法 / 用途 | 前置条件与影响 |
| --- | --- | --- |
| [validate_cardioedge_stream.py](validate_cardioedge_stream.py) | `python tools/validate_cardioedge_stream.py --data <公开数据目录> --output <结果目录>` | 离线公开数据回放与评分，写结果 |
| [verify_edge_reference.py](verify_edge_reference.py) | `python tools/verify_edge_reference.py --model <原模型> --output <结果文件>` | 电脑端 LiteRT 参考环境；不是树莓派运行依赖 |
| [verify_cardioedge_board.py](verify_cardioedge_board.py) | `python tools/verify_cardioedge_board.py --port auto --output evidence/board` | Linux 图形环境；`--port` 打开实体串口，`--replay-npz` 为单独标注的 PTY 回放 |
| [edge_performance.py](edge_performance.py) | `python tools/edge_performance.py --output evidence/performance --kernel` | 运行性能测试、写报告；回放需另提供公开数据 |
| [audit_edge_demo.py](audit_edge_demo.py) | `python tools/audit_edge_demo.py <演示目录> --replay <回放CSV>` | 重算已保存演示数据 |
| [ui_latency_probe.py](ui_latency_probe.py) | `python tools/ui_latency_probe.py --source <源码目录> --csv <公开CSV> --output <结果目录>` | 图形环境，测量 Tk 回调延迟 |
| [run_edge_demo.sh](run_edge_demo.sh)、[run_stage_180s.sh](run_stage_180s.sh) | 阶段运行脚本，先核对脚本内路径与参数 | 启动相应实验并保存结果；不作为当前完整工作台部署入口 |

## 界面预览

[preview_device_ui.py](preview_device_ui.py)、[preview_classification_ui.py](preview_classification_ui.py)、[preview_cloud_ui.py](preview_cloud_ui.py)、[preview_storage_ui.py](preview_storage_ui.py)、[preview_interface.py](preview_interface.py) 用于相应界面的合成数据预览和截图。需要脚本要求的 Tk/显示环境；运行前检查输出目录，预览不能代替真实屏幕或采集验收。

## 旧阶段与联调

| 工具 | 适用范围 |
| --- | --- |
| [build_waveform_release.py](build_waveform_release.py) | 早期纯波形包及独立验证流程，默认历史版本标签；不作为当前完整工作台包 |
| [build_cardioedge_release.py](build_cardioedge_release.py) | 早期待机分类包，不包含后续完整测量与手机功能 |
| [install_cardioedge_release.py](install_cardioedge_release.py) | 固定早期版本哈希的安装器，`--apply` 会修改目标源码；不用于新工作台包 |
| [export_weights.py](export_weights.py) | 旧 `models/` 权重导出；依赖电脑端 PyTorch，可能写入模型目录 |
| [local_link_server.py](local_link_server.py) | `python tools/local_link_server.py --bind 127.0.0.1 --port 8765`；接收演示消息并写本机状态，见[电脑联调说明](电脑联调说明.txt) |
| [restore-direct-sharing.ps1](restore-direct-sharing.ps1)、[restore-sharing-service.ps1](restore-sharing-service.ps1) | 旧电脑网络共享恢复工具，会修改 Windows 网络/服务配置；只在对应网络维护任务中使用 |

新增工具需在本表登记用途、运行位置和写入影响。运行输出、进程号和真实记录不提交 Git；可公开的报告按[验证资料说明](../validation/README.md)登记来源和范围。
