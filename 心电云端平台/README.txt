由 tools/sync_docs.py 从本模块 docs/ 下的快速开始、使用说明、故障排查生成。
请修改 Markdown 原文后重新生成，不单独编辑本文件。
本文说明完整仓库当前代码；早期精简发布包不一定包含全部功能。

# 云端快速开始

云端导航（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/README.md） · 使用说明（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/使用说明.md） · 故障排查（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/故障排查.md）

## 使用现有平台

打开项目首页的网页及客户端下载入口（https://github.com/XiongBoT/bme-ecg-system/blob/main/README.md#应用下载），使用已有心电平台账号登录。Windows 客户端和 Android 试用 APK 都连接现有云端；iPhone 可使用网页并添加到主屏幕。

Android 的实机安装、保存与覆盖升级验收范围见手机端说明（https://github.com/XiongBoT/bme-ecg-system/blob/main/docs/手机端与安卓安装包.md）。现行账号登录使用 HTTPS；早期 localhost 中转流程仅供历史复现。

## 本地开发

从仓库根目录按开发环境（https://github.com/XiongBoT/bme-ecg-system/blob/main/README.md#开发环境）创建 Python 环境，再进入 `心电云端平台/`：

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-test.txt
..\.venv\Scripts\python.exe run_server.py --data-dir work/local-server
```

默认仅监听 `127.0.0.1:8766`。本地数据目录与正式服务器分开；当前账号、令牌及 HTTPS 配置见Linux 部署说明（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/deploy/linux/部署说明.txt）和公网访问说明（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/deploy/linux/手机公网访问说明.txt）。不要把历史无账号演示流程作为正式登录配置。

需要在本地启用现有模型时，在启动服务的同一 PowerShell 会话中执行：

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements-model.txt
$env:ECG_MODEL = 'cardioedge'
```

然后运行上面的启动命令。仅安装权重不会启用模型；`ECG_MODEL=none` 为不启用模型。模型与输入约束见云端模型说明（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/cloud_model/README.md）。

## 检查与离线研究

从仓库根目录分别执行：

```powershell
.\.venv\Scripts\python.exe tools/run_checks.py --suite cloud
.\.venv\Scripts\python.exe tools/run_checks.py --suite web
```

QRS/RR 与 HRV 的公开数据复跑步骤集中在研究工具（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/research/README.md），结果可直接阅读Markdown 摘要（https://github.com/XiongBoT/bme-ecg-system/blob/main/docs/algorithm-validation/结果摘要.md）。研究数据放仓库外，生成结果留本机。

## 构建与部署

服务器、Windows、Android 的构建入口见部署导航（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/deploy/README.md）。合并代码或构建安装包不自动更新服务器或设备；实际部署与待验收项按阶段记录（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/云端工作台迭代.md）核对。

# 云端使用说明

快速开始（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/快速开始.md） · 故障排查（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/故障排查.md） · 文档分类（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/README.md）

## 登录与找到记录

1. 从项目首页打开 HTTPS 网页，或启动 Windows/Android 客户端。
2. 使用本人账号登录；普通用户只查看归属于自己的记录。
3. 按设备和记录选择所需会话，核对采集时间、来源及上传状态。
4. 查看波形、历史位置与测量总结；按需要导出 CSV。

采集时间、云端登记时间和最后收块时间含义不同。刚上传的记录可能是历史转存；来源未知的记录不能当作新实采。记录仍在上传时，导出仅代表已经接收的部分。

## 分析与结果复核

当前模型为 CardioEdgeAI v1 INT8，使用 N/S/V 分类。仅已完成记录中满足来源、ADC、质量及上下文要求的有效实采片段进入分类；本地合成、板端模拟或未知模式可被拒绝。

在记录中启动分析后等待任务完成；查询已保存结果不会自动重新计算。通过心搏明细定位波形，结合拒绝原因和质量提示复核。模型分数不是诊断概率，N 类包含束支阻滞，S 类需特别复核。

分析结果与原始采样独立保存。模型或输入变化后是否需要重新分析，按界面状态判断；不要为重算删除原始数据。离线 HRV 研究报告与当前工作台功能是不同入口，见算法与评估（https://github.com/XiongBoT/bme-ecg-system/blob/main/docs/算法与评估.md）。

## 设备和账号

管理员在设备管理中维护设备、授权用户及凭据；普通用户按分配权限访问。树莓派客户端需要匹配的云端地址、设备授权和记录归属。既有未分配记录由管理员处理。

手机、电脑和终端使用相同的记录归属约定，见共同接口（https://github.com/XiongBoT/bme-ecg-system/blob/main/docs/接口约定.md）。凭据、账号数据库和原始心电记录不纳入源码仓库。

## 查阅功能与部署状态

当前下载版本从首页（https://github.com/XiongBoT/bme-ecg-system/blob/main/README.md#应用下载）进入；交付变化见更新日志（https://github.com/XiongBoT/bme-ecg-system/blob/main/CHANGELOG.md）。云端工作台迭代（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/云端工作台迭代.md）保存设计、版本与验收过程，其中“本地完成”“已部署”“待实机验收”应分别理解。

早期中转、固定记录数量及当时验证结果保留在 README 历史快照（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/archive/README_20261123.txt），不作为当前服务状态。

# 云端故障排查

快速开始（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/快速开始.md） · 使用说明（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/使用说明.md） · 辅助工具（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/tools/README.md）

| 现象 | 检查顺序 |
| --- | --- |
| 网页或客户端无法连接 | 从首页核对当前 HTTPS 地址，检查网络及服务可用性；客户端连接问题不代表本地记录丢失 |
| 本地服务启动失败 | 从模块根目录运行，确认 Python 环境及 `requirements.txt`；检查端口是否占用、数据目录是否可写 |
| 账号登录失败 | 核对账号状态和平台地址；账号由管理员管理，服务器 SSH 密码不是平台登录密码 |
| 登录后没有记录 | 核对记录归属、账号、设备筛选及上传完成状态；未分配记录需管理员处理 |
| 模型显示 `not_configured` | 检查实际运行进程的 `ECG_MODEL`，模型依赖是否安装；本地与服务器的开关各自生效 |
| 健康检查 `model_result=null` | 健康检查不返回某条记录的结果；同时查看模型状态和目标会话的分析任务 |
| 模型拒绝或没有心搏结果 | 核对会话是否完整、输入来源、ADC、信号质量及上下文；模拟链路可验证上传，但不一定生成分类 |
| 手机看不到终端最新测量 | 先确认终端保存，再确认上传、设备授权和记录所属账号；终端总结不等于云端分析完成 |
| 旧脚本要求中转通道或找不到参考记录 | 这些属于历史联调工具；先看工具索引及归档说明，新克隆不包含原电脑的记录与凭据 |

## 验证工具的边界

`verify_live_capture.py --offline` 只检查本地证据，不证明线上验收通过。`verify_live_model.py` 读取已有云端结果并在本地复算；`verify_cloud_model.py` 会请求云端分析。运行前查看工具分类（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/tools/README.md），不要把所有 `verify_` 脚本当作无副作用单元测试。

## 报告问题

提供提交编号、入口、操作步骤、错误消息以及是否本地/模拟/实体环境。使用脱敏的合成示例复现；不要上传账号数据库、访问令牌、真实记录或私钥。正式服务的升级和回退按部署说明（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/deploy/README.md）执行。
