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

## 独立 QRS/RR 分析（待部署）

当前源码另提供独立 QRS/RR 后台分析，不覆盖原始记录或分类结果。安装 `requirements-model.txt` 后即可使用，无需启用分类模型；记录上传完成后在“QRS / RR 分析”卡片提交任务，退出页面不影响已接受的任务。

结果显示 R 峰数、平均 RR、换算心率、RR 变异性、有效时长/点数及排除原因。单记录上限 100 万采样点；模拟、模式不明或质量不足的输入可能没有可用结果。没有可信正常窦性间期标记时，HRV 显示不可用，不能用模型 N 类代替 NN。

本轮源码尚未部署公网，也未更新已发布 APK。后台任务与结果版本、权限及升级回退边界见接口约定（https://github.com/XiongBoT/bme-ecg-system/blob/main/docs/接口约定.md#独立-qrsrr-分析2026-11-24待部署）。公开数据的离线评估与在线结果分别查阅算法与评估（https://github.com/XiongBoT/bme-ecg-system/blob/main/docs/算法与评估.md）。

## 设备和账号

管理员在设备管理中维护设备、授权用户及凭据；普通用户按分配权限访问。树莓派客户端需要匹配的云端地址、设备授权和记录归属。既有未分配记录由管理员处理。

手机、电脑和终端使用相同的记录归属约定，见共同接口（https://github.com/XiongBoT/bme-ecg-system/blob/main/docs/接口约定.md）。凭据、账号数据库和原始心电记录不纳入源码仓库。

## 数据库管理（源码已实现，待部署）

管理员登录后，在工作台账号区选择“数据库管理”；电脑和手机使用同一个入口。普通账号继续查看自己的记录，不显示管理入口。当前公网和已发布安装包尚未更新本功能。

1. 查看记录数量、数据库体积及磁盘剩余空间，点击“统计占用”获取原始数据、分析缓存、备份和临时文件占用。文件占用标注最近统计时间，并非实时精确值；读取失败显示未知或统计不完整。
2. 按所属用户、采集设备、采集日期、状态和名称/编号查找记录，支持分页。管理员只查看元信息；波形仍按本人账号访问。
3. 完成上传和分析后可移入回收站，随后选择“回收站”筛选并恢复。回收站仍占磁盘空间；第一版不提供永久删除。
4. 点击“检查完整性”，后台核对数据库结构、外键、已登记原始块及点数。完成后查看问题数量和具体提示，不会自动删除或修复坏数据。检查通过不表示信号或诊断正确。
5. 点击“创建服务器备份”并确认。后台会生成数据库快照、核对该快照引用的原始块并校验归档；离开页面任务继续。仅显示备份状态、文件名、大小及摘要，网页不提供下载。

维护任务同一时间只执行一个，最近任务和操作记录保留在后台。任务失败/中断可在排除原因后重新创建；已完成备份列表表示创建当时成功，不保证后来没有被服务器人员移动或删除。备份和临时空间均计入占用，第一版不自动淘汰旧备份。

### 服务器上的备份与恢复

备份路径为实际 `ECG_DATA_DIR` 下的 `backups/ecg-backup-<任务编号>.zip`，权限限制为服务账号目录。管理员网页登录权限不授予该文件的下载权限；服务器运维人员应按原有服务器权限管理。

备份包含 `cloud.sqlite`、快照引用的原始波形块和 SHA256 清单，**不包含模型/QRS分析缓存、程序、模型文件或服务器配置**。它用于保护账号、归属和原始采集数据，不能代替部署前完整运行备份。

在服务器或隔离环境的模块根目录执行：

```sh
python tools/restore_database.py /实际数据目录/backups/ecg-backup-任务编号.zip --destination /实际恢复父目录/新目录
```

父目录须已存在，目标目录必须尚不存在；已有目标会拒绝，不覆盖线上数据。命令验证成功后才公布新目录，不自动切换服务。恢复后原浏览器会话和设备密钥全部失效，需重新登录、重新发放设备凭据；分析结果需重新计算。先以新目录启动隔离服务核对账号和记录，再单独安排正式切换。

任务异常终止后，下次维护进程启动只清理本模块标记且对应任务存在的已知临时文件。未知文件保留，显示在临时占用中，由服务器人员核实；不自动递归删除不明目录。ZIP公布后、任务状态提交前若断电，服务器可能保留未列入成功记录的归档，需在服务器验证后处理。

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
| 看不到数据库管理入口 | 确认新版服务和网页已部署，并登录有效管理员账号；普通账号和设备凭据不具备后台管理权限 |
| 占用显示未统计或不完整 | 点击“统计占用”；核对扫描时间及目录读取权限，未知不能当成零；上传期间目录仍会增长 |
| 整理记录提示409 | 等待上传、排队或运行中的分析完成；维护任务409表示同一目录已有检查/扫描/备份任务 |
| 备份失败或显示空间不足 | 查看任务错误；需留出数据库快照、归档及至少256 MiB余量；第一版不会自动删除历史备份或原始数据 |
| 完整性检查发现问题 | 根据记录和块编号核对原始来源及服务器文件；不要通过删库或删记录掩盖问题，坏块不会自动修复 |
| 任务中断或一直运行 | 服务重启或管理页再次查询会启动恢复检查；中断后重新提交。未知临时文件保留，需服务器核实；无权限或损坏数据库应先修复运行环境 |
| 恢复提示目标已存在 | 选择不存在的新目录，保留原目录；恢复后需重新登录、重发设备凭据及重新分析，不会自动替换正式服务 |

## 验证工具的边界

`verify_live_capture.py --offline` 只检查本地证据，不证明线上验收通过。`verify_live_model.py` 读取已有云端结果并在本地复算；`verify_cloud_model.py` 会请求云端分析。运行前查看工具分类（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/tools/README.md），不要把所有 `verify_` 脚本当作无副作用单元测试。

## 报告问题

提供提交编号、入口、操作步骤、错误消息以及是否本地/模拟/实体环境。使用脱敏的合成示例复现；不要上传账号数据库、访问令牌、真实记录或私钥。正式服务的升级和回退按部署说明（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/deploy/README.md）执行。
