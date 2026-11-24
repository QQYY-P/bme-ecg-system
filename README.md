# 生医工综合 · 心电采集与分析系统

STM32 心电采集、树莓派显示与分析、云端记录平台的共同开发仓库。

从下表选择要做的事；各模块的 `README.md` 提供导航，现行步骤在模块 `docs/`，`README.txt` 由这些文档自动生成供离线查阅。

| 你要做什么 | 入口 |
| --- | --- |
| 了解项目目标、实施阶段和验收条件 | [项目计划书](docs/项目计划书.md) |
| 下载客户端、打开网页 | [应用下载](#应用下载) |
| 接线、查看 STM32 固件 | [硬件导航](硬件/README.md) |
| 运行树莓派采集终端 | [终端导航](树莓派心电终端/README.md)、[日常使用](树莓派心电终端/docs/设备待机与日常使用.md) |
| 开发云端、网页或客户端 | [云端导航](心电云端平台/README.md) |
| 找算法、模型或评估结果 | [算法与评估](docs/算法与评估.md) |
| 根据功能定位源码 | [仓库导览](docs/仓库导览.md) |
| 参与开发、运行检查 | [成员入门](CONTRIBUTING.md)、[检查工具](tools/README.md) |
| 查接口、全部文档或版本变化 | [接口约定](docs/接口约定.md)、[文档中心](docs/README.md)、[更新日志](CHANGELOG.md) |

## 应用下载

**[下载 Windows 1.1.0 免安装版 ZIP](https://github.com/XiongBoT/bme-ecg-system/releases/download/desktop-v1.1.0/ecg-workspace-windows-1.1.0.zip)** · [版本说明与校验文件](https://github.com/XiongBoT/bme-ecg-system/releases/tag/desktop-v1.1.0)

适用于 Windows 10/11。下载后右键选择“全部解压”，双击文件夹中的 **心电工作台.exe**，使用已有的心电云端账号登录。无需安装 Python 或 Node.js，需要联网；详细操作见 [使用说明](心电云端平台/deploy/desktop/使用说明.txt)。

这是已上线云端工作台的桌面入口，功能随云端更新。手机或其他电脑可直接使用 [网页版](https://62.234.150.104/)。仓库当前为公开仓库；心电平台中的记录仍需使用各自账号登录后访问。

**[下载 Android APK（试用版）](https://github.com/XiongBoT/bme-ecg-system/releases/download/android-v1.0.0-preview.1/ecg-records-1.0.0.apk)** · [版本说明与校验文件](https://github.com/XiongBoT/bme-ecg-system/releases/tag/android-v1.0.0-preview.1)

安卓 8.0 及以上可安装，使用现有心电平台账号，需要联网。APK 内置“记录、结果、账号”手机界面，支持波形查看和 CSV 保存；设备管理会打开系统浏览器。当前为预发布，安卓实机安装、系统文件保存及覆盖升级仍待验收。源码与构建见 [安卓工程说明](心电云端平台/android/README.md)，验证范围见 [手机端与安卓安装包](docs/手机端与安卓安装包.md)。iPhone 继续使用网页版并按页面指引添加到主屏幕。

## 从这里开始

首次阅读建议按“[仓库导览](docs/仓库导览.md) → 所需模块 → [接口约定](docs/接口约定.md)”的顺序；早期设计与阶段资料见各模块的历史分类。

| 目录 | 内容 | 入口 |
| --- | --- | --- |
| [硬件/STM32心电采集](硬件/STM32心电采集/) | 固件、HAL/CMSIS、Keil 工程及接线资料 | [模块导航](硬件/STM32心电采集/README.md) |
| [树莓派心电终端](树莓派心电终端/) | 采集、屏幕显示、记录、预处理和端侧分类 | [模块导航](树莓派心电终端/README.md) |
| [心电云端平台](心电云端平台/) | 服务端、网页、账号/设备、电脑客户端与部署工具 | [模块导航](心电云端平台/README.md) |
| [docs](docs/) | 共同接口、协作与公开数据评估结果 | [文档导航](docs/README.md) |
| [tools](tools/) | 仓库级检查工具 | [工具导航](tools/README.md) |

各模块的 `docs/` 存使用及设计说明，`docs/archive/` 存明确标注的历史资料；`tests/` 存测试，部署和构建工具保留在各模块原有目录。模型和固件依赖随所属模块维护，避免跨目录导入或工具工程路径变化。新增资料按用途归位，完整约定见 [贡献指南](CONTRIBUTING.md#文件与数据)。

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

**所有协作者均可修改所有模块，没有按人员或目录划分的写入限制。** 每项任务单独建立分支，通过检查并解决审查讨论后，以 Pull Request 合并；其他成员复核为可选，流程见 [贡献指南](CONTRIBUTING.md) 和 [协作质量与发布](docs/协作质量与发布.md)。

`main` 为共同集成分支；每个任务从最新 `main` 建立 `feat/...`、`fix/...` 或 `docs/...` 分支。当前初始提交记录本机源码状态，**不代表所有功能已部署到树莓派或公网**。发布时记录提交编号、设备、验证结果和回退版本。

当前模型、第三方库和公开验证数据沿用各自许可证。保留其 LICENSE 与来源说明；仓库未为整个项目额外授予开源许可证。分类结果用于课程研究，不将软件测试结果表述为临床有效性验证。

