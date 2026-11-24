由 tools/sync_docs.py 从本模块 docs/ 下的快速开始、使用说明、故障排查生成。
请修改 Markdown 原文后重新生成，不单独编辑本文件。
本文说明完整仓库当前代码；早期精简发布包不一定包含全部功能。

# 云端快速开始

云端导航（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/README.md） · 使用说明（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/使用说明.md） · 故障排查（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/故障排查.md）

## 使用现有平台

打开项目首页的网页及客户端下载入口（https://github.com/XiongBoT/bme-ecg-system/blob/main/README.md#应用下载），使用已有心电平台账号登录。Windows 客户端和 Android 试用 APK 都连接现有云端；iPhone 可使用网页并添加到主屏幕。

Android 的实机安装、保存与覆盖升级验收范围见手机端说明（https://github.com/XiongBoT/bme-ecg-system/blob/main/docs/手机端与安卓安装包.md）。现行账号登录使用 HTTPS；早期 localhost 中转流程仅供历史复现。

最新源码已包含管理员账号管理（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/使用说明.md#管理员账号管理待部署）和独立 QRS/RR 分析（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/使用说明.md#独立-qrsrr-分析待部署）。这两项尚未部署到公网，也未进入已发布 APK；仅更新本地仓库不会使现有手机或电脑入口出现新功能。各版本状态见更新日志（https://github.com/XiongBoT/bme-ecg-system/blob/main/CHANGELOG.md#当前交付状态）。

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

## 管理员账号管理（待部署）

适用版本：PR #29 已合并的源码。当前公网及 Android 1.0.0 试用包尚未更新本节界面；普通用户不能进入管理员管理窗口。

管理员登录后打开“账号与待分配记录”（手机先进入“账号”页），可按账号名称搜索，按可登录/已停用或角色筛选，每页显示 10 个账号。点击“刷新”更新概况；创建时间与最近成功登录缺少历史数据时显示“未记录”。“有效登录”是尚未过期的登录凭证数，不是实时在线人数。

- 展开“创建普通账号”，填写账号、初始密码及确认密码。账号以字母或数字开头，支持 3–32 位字母、数字、横线、下划线，大写会转为小写；密码须为 10–128 个字符。创建成功后通过可信渠道交付密码，并在设备管理中按需要授权采集设备。
- “重置密码”使该普通账号原有登录失效；“撤销登录”保留密码，要求已有登录重新验证，用户仍可用原密码再次登录。
- “停用”阻止账号登录并撤销原有登录，也使其不再成为设备可选接收人；“恢复”后可重新登录。已有记录、归属与设备授权仍保留。管理员账号本身不能在这里停用、重置或撤销登录。
- “分配历史记录”只列尚无归属的记录。明确选择使用者后确认，已归属记录不能在此改给别人。

操作成功后若刷新失败，成功提示仍保留；点击“刷新”核对最新状态，避免重复创建。关闭管理或密码窗口会清空密码输入。微信登录暂不实现。当前是源码功能，需另行更新云端及内置网页的 APK 后使用。

### 三种操作如何选择

| 操作 | 原密码还能登录吗 | 已有登录 | 记录、归属及设备授权 |
| --- | --- | --- | --- |
| 重置密码 | 否，改用新密码 | 全部撤销 | 保留 |
| 撤销登录 | 可以重新登录 | 全部撤销 | 保留 |
| 停用账号 | 停用期间不能登录；恢复后可用现有密码 | 全部撤销，恢复不会复活旧登录 | 保留；停用期间不列为设备可选接收人 |

如果要阻止账号继续登录，应选择“停用”；单独撤销登录不禁止再次登录。操作不取消已经获准执行中的业务请求。管理员不会因此获得该账号的波形读取权限。

### 新功能如何更新到各端

| 入口 | 更新方式 |
| --- | --- |
| 手机/电脑浏览器、PWA、Windows 网页入口 | 服务器部署新版后重新打开或刷新页面；Windows 启动器自身版本与网页功能分别维护 |
| Android 内置网页应用 | 服务器更新后，还需构建并发布包含新版页面的 APK，沿用原签名并提高版本号；仅刷新 APK 不会替换内置页面 |

部署与安装包发布另行安排，操作前核对备份及回退方案。原始记录和正式账号不通过重新建库来更新。常见问题见账号管理排查（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/故障排查.md#账号管理排查）。

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

## 账号管理排查

以下针对已合并的新版源码，部署状态先查更新日志（https://github.com/XiongBoT/bme-ecg-system/blob/main/CHANGELOG.md#当前交付状态）。详细操作见账号管理说明（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/docs/使用说明.md#管理员账号管理待部署）。

| 现象 | 检查与处理 |
| --- | --- |
| 找不到搜索、筛选或撤销登录 | 先核对是否为管理员，以及服务器是否已部署 PR #29；Android 还需更新内置页面的 APK。仓库已合并不等于当前页面已更新 |
| 创建时提示账号格式不正确或已存在 | 账号为 3–32 位字母、数字、横线、下划线，以字母或数字开头；首尾空白去除、大写转小写后仍须唯一。搜索现有账号再判断，勿反复提交 |
| 初始密码不能提交 | 检查密码是否为 10–128 个字符，两次输入是否一致；不要把真实密码写入反馈或截图 |
| 已提示创建成功，但列表未更新 | 点击“刷新”再搜索账号；刷新失败不代表创建失败，不要重复创建 |
| 新账号可以登录，却不在树莓派接收人列表中 | 在设备管理中核对该设备的用户授权，并确认账号已启用；创建账号不会自动授予所有设备采集权限 |
| 最近登录显示“未记录” | 旧系统没有保存历史时间，或账号尚无新版记录的成功登录；不代表该账号从未使用过。失败登录不会更新该时间 |
| 有效登录数与实际人数不同 | 该数统计尚未过期的登录凭证，不是在线人数或实体设备数；同一人可以有多个登录，关闭浏览器也不等于凭证立即失效 |
| 撤销后仍能再次登录 | 属于预期行为，原密码保留；若需阻止再次登录，应停用账号。若密码不应继续使用，重置密码后再核对状态 |
| 恢复账号后旧窗口仍提示登录失效 | 恢复不会复活已撤销的登录，使用当前密码重新登录 |
| 列表加载失败或登录校验失效 | 先刷新或重新登录，再核对账号/目标和当前状态；确认操作结果前不要反复重置密码或分配记录 |

## 验证工具的边界

`verify_live_capture.py --offline` 只检查本地证据，不证明线上验收通过。`verify_live_model.py` 读取已有云端结果并在本地复算；`verify_cloud_model.py` 会请求云端分析。运行前查看工具分类（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/tools/README.md），不要把所有 `verify_` 脚本当作无副作用单元测试。

## 报告问题

提供提交编号、入口、操作步骤、错误消息以及是否本地/模拟/实体环境。使用脱敏的合成示例复现；不要上传账号数据库、访问令牌、真实记录或私钥。正式服务的升级和回退按部署说明（https://github.com/XiongBoT/bme-ecg-system/blob/main/心电云端平台/deploy/README.md）执行。
