# 生医工综合 · 心电采集与分析系统

STM32 心电采集、树莓派显示与分析、云端记录平台的共同开发仓库。

仓库：https://github.com/XiongBoT/bme-ecg-system

**所有协作者均可修改所有模块。没有按人员或目录划分的写入限制。** 每项任务单独建立分支，完成测试后通过 Pull Request 合并。

## 应用下载

**[下载 Windows 免安装版 ZIP](https://github.com/XiongBoT/bme-ecg-system/releases/download/desktop-v1.0.0/ecg-workbench-windows-portable.zip)** · [版本说明与校验文件](https://github.com/XiongBoT/bme-ecg-system/releases/tag/desktop-v1.0.0)

适用于 Windows 10/11。下载后右键选择“全部解压”，双击文件夹中的 **心电工作台.exe**，使用已有的心电云端账号登录。无需安装 Python 或 Node.js，需要联网；详细操作见 [使用说明](心电云端平台/deploy/desktop/使用说明.txt)。

这是已上线云端工作台的桌面入口，功能随云端更新。手机或其他电脑可直接使用 [网页版](https://62.234.150.104/)。仓库当前为公开仓库；心电平台中的记录仍需使用各自账号登录后访问。

## 从这里开始

- [成员入门与协作流程](CONTRIBUTING.md)
- [自动检查、AI 审查与版本发布](docs/协作质量与发布.md)
- [接口约定与联调](docs/接口约定.md)
- [任务与发布记录](docs/团队协作.md)
- [初始版本与验证边界](docs/初始版本说明.md)
- [Windows 免安装电脑端：使用与构建](心电云端平台/deploy/desktop/README.md)
- [QRS/RR、HRV 公开数据评估报告](docs/algorithm-validation/report.html)（下载后用浏览器打开；[数值结果](docs/algorithm-validation/report.json)）

| 目录 | 内容 | 入口 |
| --- | --- | --- |
| `硬件/STM32心电采集/` | STM32F103C8 固件、HAL/CMSIS 与接线文档 | `Project/my_bme_work.uvprojx` |
| `树莓派心电终端/` | 采集、显示、原始记录、预处理和端侧分类 | `app.py`、`README.txt` |
| `树莓派心电终端/cardioedge/` | 当前模型及 NumPy 推理适配器 | `THIRD_PARTY_NOTICES.txt` |
| `心电云端平台/` | FastAPI、网页、账号、设备与记录管理 | `server.py`、`README.txt` |
| `docs/` | 共同接口、协作说明和版本说明 | 本目录文档 |
| `tools/` | 仓库级检查入口 | `run_checks.py` |

现有日期目录、演示视频、阶段报告、运行环境、个人配置、实采记录和密钥不纳入 Git。它们保留在原电脑；新成员克隆仓库后不会得到这些文件。需要共享的接线图或报告经确认后可放入 `docs/`。

## 开发环境

桌面开发建议 Python 3.12、Node.js 22 与 Git。树莓派 ARM32 的安装方式单独遵循终端 README，不复制 Windows 虚拟环境。

在 PowerShell 中，从仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r "树莓派心电终端/requirements-pi.txt" -r "心电云端平台/requirements-test.txt" -r "心电云端平台/requirements-model.txt"
.\.venv\Scripts\python.exe tools/run_checks.py
```

不需要激活虚拟环境，因此不依赖 PowerShell 脚本执行策略。Node.js 仅用于网页脚本测试。Python Tk 界面测试需要可用的显示环境，缺少显示时部分测试会跳过；应在 PR 中记录跳过原因。

STM32 使用现有 Keil 工程：ARM Compiler 5.06 update 7（build 960）、Keil.STM32F1xx_DFP 2.3.0。具体参数以工程文件为准。固件编译与板上串口联调单独验收，不由 Python 测试代替。

## 版本与部署

`main` 为共同集成分支；每个任务从最新 `main` 建立 `feat/...`、`fix/...` 或 `docs/...` 分支。当前初始提交记录本机源码状态，**不代表所有功能已部署到树莓派或公网**。发布时记录提交编号、设备、验证结果和回退版本。

当前模型、第三方库和公开验证数据沿用各自许可证。保留其 LICENSE 与来源说明；仓库未为整个项目额外授予开源许可证。分类结果用于课程研究，不将软件测试结果表述为临床有效性验证。

