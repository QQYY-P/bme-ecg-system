心电数据工作台：电脑端与云端课程演示
更新日期：2026-11-23

多设备云端主平台：本地已加入独立设备凭据、授权用户、启停及凭据轮换、按设备筛选和手机/电脑设备管理页。管理员从工作台进入“设备管理”；树莓派可通过 ECG_CLOUD_URL 直连现有 HTTPS 云端。接入步骤、兼容策略、测试证据与包版本状态统一见 云端工作台迭代.md 的“云端主平台与多设备接入”小节。本机验收不代表已上线。

QRS/RR、HRV和性能评估：已分享的报告在仓库 docs/algorithm-validation/report.html，数值在同目录 report.json；本地重新运行默认输出到 outputs/qrs-hrv-evaluation/，该目录不加入Git。使用MIT-BIH 10条记录各前5分钟，不修改原始数据或模型；不是全库、树莓派或临床验证。NN统计额外要求明确窦性节律；手机工作台尚未新增HRV卡片。复跑命令与详细边界见 云端工作台迭代.md 对应小节。

克隆后的离线实验（PowerShell，先在仓库根目录按根README创建.venv）：
  .\.venv\Scripts\python.exe -m pip install -r "心电云端平台/requirements-research.txt"
从 https://physionet.org/content/mitdb/1.0.0/ 获取公开MIT-BIH记录，将对应.hea、.dat、.atr放在仓库外的数据目录，不提交到Git。
进入“心电云端平台”目录后执行，下例替换数据路径：
  ..\.venv\Scripts\python.exe -m research.evaluate_ecg --data "D:\datasets\mitdb" --records 100 101 103 105 200 201 207 208 231 232 --seconds 300
输出HTML与JSON包含配置、算法及输入文件摘要、匹配数、RR/NN统计和本机耗时。生成报告不会读取服务器账号或真实采集记录。只运行单元测试不需要WFDB或MIT-BIH数据。

手机桌面应用（PWA）：本地代码已增加安装入口、图标、独立窗口、离线连接提示和主动更新。沿用已有账号；无 APK，不离线保存心电记录。本轮是否上线以 云端工作台迭代.md 的 PWA 小节为准；手机安装方法见 deploy/linux/手机公网访问说明.txt。

本轮本地完成、尚未上线：采集引导与定时总结衔接、手机查看终端统计、本人修改密码、管理员重置及停用普通账号。部署包已更新，升级与验证进度统一见 云端工作台迭代.md。

已部署新版本：账号隔离、手机页面、设备心跳、记录管理及持久后台分析。
本次功能和验收入口：云端工作台迭代.md。
手机公网入口：https://62.234.150.104/ （可信HTTPS，不需要域名）。
真实云端部署、证书签发/自动续期和双账号隔离已验证。管理员登录信息：.cloud_private/mobile-admin-access.json。
原18条记录待管理员分配；树莓派仍需安装新版客户端与设备凭据。真实手机移动网络及树莓派实采联调尚待验证。
以下 2026-10-29 内容是既有课程实测记录。旧localhost HTTP网页入口不再用于新账号登录，请使用上面的HTTPS网址。
原“手动输入密码连接”流程已有后续自动通道替代，现有通道说明请看“自动连接说明.txt”。

一、现在可以做什么

已实现：现有终端记录上传、原始数据保存、会话列表、波形展示、历史回看、CSV下载、失败后补传和重复上传去重。
已跑通开发板 → 树莓派 → 电脑中转 → 腾讯云的本次新实采上传链路，也保留电脑本地演示。
腾讯云课程服务已运行，电脑通过加密通道访问云端存储、网页和模型分析。
云端已显式启用现有CardioEdgeAI v1 INT8模型，健康检查model_status=ready，按会话点击后分析。
当前云端4条记录共183712点、511块，清单、原始块校验值和下载CSV逐点核对通过。
其中43384点来自历史课程实采转存；新增64872点/181块为本次重新采集并上传的实采记录，时长180.2秒。
历史实采148搏及本次新实采210搏，合计358搏，云端与各自参考的R点、类别和N/S/V分数全部一致；
历史无效实采标记加板端模拟记录、以及本地合成记录均被模型拒绝分类。
新网页支持波形R点标记、分页心搏明细点击定位、跳过原因，以及来源和采集/上传时间区分。
本次还暂停本运行的上传通道约15秒，采集继续，恢复后补齐并核对；不是物理断网测试。
上述验证说明数据传输与模型复算可复现，不是临床准确率验证；未重新训练模型或接入大模型。
电脑本地服务默认不设置ECG_MODEL，仍保持模型空置；云端与电脑本地的开关分别生效。
电脑本地页面内两段30秒记录是本地合成；2026-10-28历史串口记录后半段为SW2切换后的板端模拟。
本次2026-10-29新串口记录全程mode=0，64872点均满足ADC与flags有效性检查。
云端使用方法见下一节；原电脑演示见“开发板上传演示说明.txt”和“连接开发板.cmd”。

二、云端演示（当前电脑与云服务器已装好依赖）

服务器：腾讯云轻量应用服务器 ecg-course-demo，Ubuntu 24.04，2核2GB、40GB系统盘。
云服务器IP：62.234.150.104；登录用户名：ubuntu。
已支付35元购买1个月，到期时间为2026-11-29 17:11:32（北京时间），自动续费已关闭。
课程结束后如需保留数据，请在到期前下载CSV或另行备份；继续使用前在控制台核对续费金额。

访问网页：
1. 双击“连接云服务器.cmd”。
2. 在弹出的“连接云服务器 · 心电课程演示”窗口填写上述IP、用户名和云服务器密码，
   点击“连接云服务器”。密码仅在此窗口输入，程序不保存密码，不要写入脚本或说明文件。
3. 等待显示“云端服务正常 · 模型状态见网页”，保持这个连接窗口打开。
4. 浏览器打开 http://127.0.0.1:8767/ ，也可点击窗口里的“打开云端网页”。
   此地址通过当前电脑的加密通道访问云服务器。云端页面和本地8766页面分别保存各自的记录。

查看云端模型结果：
1. 在8767网页选择已上传完成的记录。本次新实采编号为pi_serial_20261029_183444_0c8f6d，
   来源标签为“本次采集”；历史实采23c0fb1241a84cdb842c25015c43c7b1标为“历史转存”。
2. 点击“开始云端分析”；已分析的记录点击“重新核对”可再次复算。
3. 新实采记录显示210个心搏（N=210、S=0、V=0）；历史实采记录仍为148搏（N=148、S=0、V=0）。
   N类包含束支阻滞，不能据此判断受测者完全正常；S类能力有限，模型分数不是诊断概率。
4. 模型只处理已完成会话中的有效实采片段；本地合成、板端模拟、未知模式、无效ADC与
   不满足信号质量或上下文要求的片段不会生成分类。被拒绝时网页会显示原因。
5. 查询已保存结果不会自动复算。原始采样不因分析而修改；结果独立保存，输入清单或模型变化后需重新分析。
6. 勾选“显示模型 R 点”可在原始波形上查看N/S/V标记；心搏明细每页10条，点击采样时间跳到对应波形窗口。
   明细中的N/S/V为模型分数，不是概率或准确率；复核标记为“未标记”也不等于临床正常。
   每2秒刷新不会重置同一结果的分页和选择；切换记录会清除另一会话的标记。
7. “有效实采点 / 来源、ADC跳过点”和拒绝原因用于说明输入筛选；有效点仍需通过信号质量与心搏上下文检查。

上传开发板数据：
1. 先按上面步骤连接云服务器，确认8767网页可打开。
2. 保持开发板连接树莓派USB串口、树莓派与电脑网线连接，电脑保持联网。
3. 双击“开发板上传云端.cmd”，在树莓派连接窗口填写树莓派pi2用户的密码，
   点击“连接并预检”。云端模式默认先预检，只检查时间、串口与原终端状态，尚不开始采集。
   此处需要树莓派密码，与ubuntu云服务器密码分别对应两台设备。
4. 查看预检结果，点击“保持时间继续”开始采集；若“校准时间并继续”可用且确需校时，可在开始前选择它。
   校时受串口占用、已有采集状态及权限检查约束，不会为了继续而中断原终端；校时不可用时可保留原时间。
5. 保持两个连接窗口运行，在8767网页选择新增记录。默认本次采集上传3分钟。
   如需验证板端模拟波形，可短按STM32的SW2切换模式，并以网页标注的实际模式为准。
6. 上传结束后可回看记录、下载完整CSV，再点击模型分析。提前结束请先在树莓派连接窗口点击“停止 / 关闭”，
   等待记录保存完成，再断开云服务器连接。

来源与时间：页面分别显示设备记录的采集开始、云端上传登记和云端最后收块，均按本机时区显示。
“本次采集”（live）与“历史转存”（historical）只说明数据来路；是否正在接收仍以接收时间和上传状态判断。
没有来源注记的旧记录显示待确认（unknown），不能因为刚上传就当作刚采集。
时钟检查只新增说明，不改写原始日期。本次采集前树莓派较电脑慢约3.799秒，NTP当时未同步，
实际选择“保持时间继续”，没有执行校时；页面“相差不超过5秒”仅表示这次与电脑的检查结果。

实际链路：开发板 → 树莓派 → 电脑中转 → 腾讯云存储与网页服务。
网页由电脑上的127.0.0.1:8767通道访问；当前没有开放可供他人直接访问的公网网页入口，
也没有配置开发板或树莓派绕过电脑直连云服务器。演示时电脑与云服务器连接窗口需保持运行。
关闭云服务器连接窗口会关闭电脑访问通道，不删除云端记录，也不会停止云端后台服务。
当前云端model_status=ready；会话分析结果通过analysis接口查询。
/health中的model_result仍为null，因为健康检查不返回某条记录的分析结果，不能据此判断模型未部署。

云端代码目录：/home/ubuntu/ecg-cloud/。
云端数据目录：/home/ubuntu/ecg-cloud-data/，含cloud.sqlite与sessions下的原始采样块。
云端模型结果：/home/ubuntu/ecg-cloud-data/analysis/<会话编号>.json。
电脑连接日志：logs/cloud-link.log；项目保存的服务器公钥：.cloud_private/known_hosts。
每次开发板上传云端的连接日志、采集清单与下载核对记录，分别保存在
evidence/cloud/pi-link/<本次运行编号>/，不会覆盖2026-10-28的evidence/pi-link历史证据。
上传流程结束后，程序通过同一次树莓派连接下载源索引、session.json及原始块到
cloud_sources/<本次运行编号>/<会话编号>/，并逐文件核对SHA-256；单次自动下载最多300块。
index.sqlite由源数据库的只读连接生成一致性备份，保留完整索引表和原状态；
正在录制的源只保留截至快照的已提交记录，不会被标为采集完成。
下载进度和结果见该次证据目录的source-copy.json；失败时清单和已有文件保留供检查。
只有源状态为complete且下载核对成功后，才可用以下命令另行验证云端逐点一致性：
  .\.venv\Scripts\python.exe verify_cloud.py --session-dir "cloud_sources\本次运行编号\会话编号" --expected-model ready
源文件下载成功不等于云端逐点验证通过；后者以verify_cloud.py的实际结果为准。
主机密钥若与保存记录不一致，程序会拒绝连接；应先在云控制台核对，不要删除记录绕过检查。
只访问云端时无需再启动电脑上的“启动服务.cmd”。

模型开关与回退（服务器管理时使用）：
当前云端用户服务ecg-cloud.service通过
  /home/ubuntu/.config/systemd/user/ecg-cloud.service.d/model.conf
设置[Service]下的Environment=ECG_MODEL=cardioedge，依赖见requirements-model.txt。
如需恢复只上传、存储和展示，以ubuntu用户编辑该文件，将这一行改为Environment=ECG_MODEL=none，
然后运行systemctl --user daemon-reload和systemctl --user restart ecg-cloud.service。
恢复模型时改回cardioedge，并执行相同的重新加载与重启；以/health实际返回ready为成功依据。
关闭后状态为not_configured、model_result=null；已有原始记录和分析文件保留，不需要删除数据库或重新上传。
电脑本地默认未设置ECG_MODEL，也可显式设为none；仅安装模型文件不会自动启用推理。

三、电脑日常演示（当前电脑已装好依赖）

1. 双击“启动服务.cmd”，保持服务窗口运行。
   若服务已经在后台运行，直接打开下面网页即可，无需重复启动。
2. 浏览器打开 http://127.0.0.1:8766/
3. 双击“运行演示.cmd”。约30秒生成一段合成波形，独立上传线程每约2秒发送新块。
4. 网页每2秒更新列表；点击新出现的记录，即可观察点数与波形变化。
5. 演示结束显示“上传完成”，点击“下载完整CSV”取得全部10800点。
6. 可用10/30/60秒窗口和历史位置滑块回看；点击“查看历史”按钮可恢复跟随最新。

每次运行演示都会新增一个会话，不覆盖已有记录。
显示“正在接收”表示最近有上传；超过15秒没有新块且会话未结束时显示“等待上传”。
源会话结束且清单、点数核对一致后显示“上传完成”，不能把接收确认理解为模型复核。
采集中下载的是当前已接收数据，按钮相应显示“下载已接收CSV”。

前台服务可按Ctrl+C停止。此次交付时已在后台启动服务，日志位于logs目录。
如需停止后台服务，在本目录PowerShell中运行：
  .\停止服务.ps1
脚本只停止8766端口上、命令行属于本项目run_server.py的进程，不删除记录。
若本机脚本执行策略不允许运行，可在任务管理器中按logs/server-error.log内的服务PID查找结束。
关闭网页不会自动停止后台服务。

四、上传已有的树莓派终端记录

在本目录的PowerShell中运行：
  .\.venv\Scripts\python.exe uploader.py --session-dir "完整会话目录" --url http://127.0.0.1:8766

完整会话目录示例：
  D:\课程\1.生物医学工程综合\树莓派心电终端\data\sessions\某个会话编号

目录需要包含index.sqlite和chunks下的原始块；不是选择一个CSV文件。
当前采用现有二进制记录直接上传，网页下载的CSV可用于检查和回放。
上传到云端时，先连接云服务器，再把上述--url改为http://127.0.0.1:8767。
持续采集期间追加 --watch，上传器每5秒检查已提交的新块并尝试补传：
  .\.venv\Scripts\python.exe uploader.py --session-dir "完整会话目录" --watch

Ctrl+C只停止上传器，不会停止另一个进程中的采集。
网络失败会明确报错并保留源文件；再次运行同一条命令会向服务器核对并补齐缺块。
即使本地确认记录已存在，服务器数据被清空后仍会重新上传，不会因旧进度而漏传。
同编号同内容返回重复确认；同编号不同内容报冲突，禁止覆盖原记录。

五、电脑本地保存位置和数据含义

data/cloud.sqlite                  电脑服务的会话、块索引及保存状态
data/sessions/<id>/chunks/*.bin     收到的原始采样块
demo_data/sessions/<id>/            由现有终端Recorder生成的合成源记录
upload_state/*.json                 上传确认快照，恢复时以服务器清单重新核对
evidence/local_http_verification.json  本机HTTP验证结果
evidence/verified_export.csv        已逐点核对的10800点下载结果
evidence/web-preview.png            实际网页截图
evidence/browser-verification.json  网页操作验证记录
evidence/test-results.txt           自动测试输出
logs/                              后台服务运行日志

支持现有ECGRAW01的v1/v2记录，保留原始ADC、采样编号、连续段、序号、flags和mode。
二进制数据和SHA-256校验通过、索引提交后才返回接收确认。
网页和导出读取时也检查原始块校验值，损坏或缺失会报错，不展示为正常数据。
只读取已提交索引，未索引的临时文件或中断写入不会成为可见记录。

波形纵轴为ADC原始值，下载CSV另含按Vref换算的电压V，不是直接测得的皮肤心电mV。
无效点、采样编号跳变、段边界、模式切换或重同步不跨段连线。
有效采样占比只按ADC范围和flags计算，不替代完整信号质量或诊断判断。
时间轴来自采样编号/360，跨中断不等于精确硬件采样时间。
串口会话可同时包含实采和板端模拟，应结合逐点mode判断。
当前未上传串口wire原始字节、完整事件日志或全部quality.json指标，仍保留在源会话中。
当前未接入心率/RR快照，所以网页不展示未经上传或计算的心率值。

六、接口

GET  /health
PUT  /api/sessions/{id}
PUT  /api/sessions/{id}/provenance
PUT  /api/sessions/{id}/chunks/{chunk_id}
POST /api/sessions/{id}/complete
GET  /api/sessions
GET  /api/sessions/{id}
GET  /api/sessions/{id}/waveform?seconds=10
GET  /api/sessions/{id}/export
GET  /api/sessions/{id}/analysis
POST /api/sessions/{id}/analysis

接口结构说明：http://127.0.0.1:8766/docs
连接云服务器后，云端接口说明：http://127.0.0.1:8767/docs。
块请求使用JSON，原始二进制用Base64编码，保留其原始SHA-256。
provenance接口独立保存来源与时钟检查说明，不改变原始采集时间、采样块或既有模型结果的输入校验值。
GET analysis返回模型状态及已保存结果，POST analysis对已完成会话按需复算。
云端当前model_status=ready；模型关闭时为not_configured/null，被拒绝的会话为ineligible/null。
复算最多100万点，同一服务进程一次执行一个分析任务；忙碌时返回409，不同时堆积多个任务。
单次请求限制2MiB，单块最多36000点，波形单次最多60秒。
单次CSV导出最多100万点；列表最多显示最近100个会话。
这是单机、单设备课程版本，设备标识默认pi2-demo，可在首次启动前通过ECG_DEVICE_ID设置。
已有数据库不允许改为另一设备，避免数据被重新归属。

七、树莓派与电脑联调（2026-10-28已完成实机验证）

当前双击“连接开发板.cmd”，在本机窗口输入树莓派密码并连接。
使用已知主机密钥检查和反向SSH转发；树莓派通过加密通道访问电脑127.0.0.1:8766。
默认每次采集上传3分钟，结束后释放串口并保留记录。密码不保存。
若已有正在增长的串口会话，仅读取并上传，不启动第二个采集器、不停止原采集程序。
连接程序在树莓派创建独立ecg-cloud-upload-时间目录，不改写现有终端程序。
上传器uploader.py和protocol.py只依赖Python标准库；电脑连接程序另需requirements-board.txt。
树莓派上的protocol.py重命名为cloud_protocol.py，避免与串口协议模块重名。
本机连接地址192.168.137.59、用户名pi2；变更时修改pi_link.py中的连接配置。

可选局域网直连方式：先设置随机ECG_API_TOKEN（至少16位），再使用：
  .\.venv\Scripts\python.exe run_server.py --host 0.0.0.0
上传器从同名环境变量读取凭证；网页可在“设置”中填写，凭证只保存在当前浏览器会话中。
服务器地址需改成电脑的实际局域网IP，网络和防火墙放行应另行核实。
以上只适用于明确的局域网联调；公网部署应采用方案中的SSH隧道或HTTPS入口。
2026-10-28的电脑联调没有改变电脑防火墙或购买云资源；2026-10-29新增云端部署见第二节。

八、验证与复现

自动测试：
  .\.venv\Scripts\python.exe -m unittest discover -s tests -v

真实本机HTTP测试（需先启动服务，会新增一段30秒合成记录）：
  .\.venv\Scripts\python.exe verify_local.py

2026-10-28历史电脑联调验证（当时模型按要求空置）：
- 12项自动测试通过，覆盖校验拒绝、去重、冲突、乱序/重叠、缺块完成拒绝、持久化、
  无效点与断段、v1导出、存储损坏、访问凭证、模型空置和源记录补传。
- 30秒、360Hz、10800点、30个块通过真实HTTP上传，下载逐点匹配原记录。
- 向不可达地址上传失败后，源数据保留，恢复到有效地址后完成补传。
  这是连接失败与恢复测试，不是实际拔网线或树莓派无线断网测试。
- 同一块重复请求返回duplicate=true，数据库点数不增加。
- 后台服务实际重启后，旧会话仍可查询和下载。
- 浏览器已观察到接收中点数动态增长、会话切换、历史滑块及窗口切换、CSV下载。
- 当时模型状态始终未接入，未导入torch或numpy_predictor；这不是2026-10-29云端模型的当前状态。

2026-10-28历史实机验证：64656点、180个块、179.6秒；树莓派清单与电脑清单一致，
所有块SHA-256匹配，CSV全部采样字段逐点一致，重复上传不增加点数。
实采模式前段持续发送无效标记；用户短按SW2后进入板端模拟，收到25056个有效模拟点。
该历史测试不代表有效人体实采验证；当时模型导入列表为空，分析接口返回not_configured/null。
详细证据位于evidence/pi-link/verification.json，网页截图web-board.png，下载数据board-export.csv。
复核本次记录：.\.venv\Scripts\python.exe verify_board.py
2026-10-29第一轮云端验证（历史记录转存）：
- 64656点/180块历史串口记录、10800点/30块本地合成记录、43384点/120块课程实采记录，
  合计118840点/330块。三个会话均清单匹配、CSV逐点相等，重复上传未发送新块。
- 43384点实采数据来自树莓派心电终端已保存的课程记录转存，保留source=serial、mode=0和原始校验值。
  这一轮使用已有记录；后续新实采验收见下一段。系统一直通过电脑中转访问云端。
- 云端已启用CardioEdgeAI v1 INT8。verify_cloud_model.py通过远程接口触发真实计算，
  比对148搏R点、类别、所有类别分数、所选分数、段号、复核标记与时间，分数最大绝对误差为0。
- POST结果与随后GET持久化结果完全一致；无效实采加板端模拟记录、本地合成记录均返回ineligible/null。
- 模型启用之前，服务实际重启后已核对2条记录/210块与数据库完整性；证据restart-result.txt记录的是该历史检查。
- 模型启用后再次实际重启服务，进程PID由16636变为18214；148搏分析缓存JSON保持完全一致，
  3条记录118840点、330块校验全部通过，SQLite quick_check结果为ok。
- 浏览器实际点击“重新核对”后完成云端复算，结果仍为148搏；操作后的网页截图见web-model-ready.png。
演示使用现有终端的Recorder和SampleBatch生成兼容记录，不改动原终端程序。

2026-10-29新实采与优化验收（当前）：
- 云端优化包c3607258bdd9更新成功，新增来源/时钟说明和模型明细界面；原148搏分析缓存保持不变。
- 运行编号20261029_182522_579678，会话pi_serial_20261029_183444_0c8f6d，
  新实采180.2秒、64872点、181块，全部mode=0、有效ADC；源会话正常完成并完整下载核对。
- 本次仅暂停这一运行的上传转发通道15.016465秒，出现7次实际上传失败，采集与本地保存继续增长。
  通道恢复后自动补齐。未断开物理网络、SSH或原终端，因此不宣称完成物理断网恢复测试。
- 采集前preflight-before-capture.json记录设备较电脑慢3.799秒，检查不确定度约0.317秒。
  当时NTP未同步、校时未执行，采集期间也未改时钟；原始采集时间保留，来源注记为live。
- verify_cloud.py对真实云端完成清单核对、CSV全部行逐点比较，重复上传没有发送新块。
- verify_live_model.py只GET读取已有云端分析，并在电脑独立复算下载的原始记录；210搏的全部R点、
  类别、N/S/V分数、段号、复核标记、分类采样点和质量门控结果一致，分数最大绝对误差为0。
  该比对脚本不上传数据、不触发云端分析、不修改源记录；它验证复现一致性，不验证临床准确率。
- verify_live_capture.py真实云端GET只读验收通过，同时核对源记录、暂停恢复证据、采集前时间检查、
  云端清单、完整CSV、provenance及原始采集时间。没有启动采集、上传、复算或修改云端数据。
- 当前云端总计4会话、183712点、511块；历史148搏加本次210搏共358搏，两条实采记录分别核对通过。
当前未验证项：物理断网恢复、长期稳定性、外部临床标注准确率；树莓派绕过电脑直连云端不属于当前方案。

云端证据（相对本项目目录）：
  evidence/cloud/pi_serial_20261027_015950_e3d04b-verification.json  64656点逐点核对
  evidence/cloud/demo_20261027_161048_be8a0d-verification.json       10800点逐点核对
  evidence/cloud/23c0fb1241a84cdb842c25015c43c7b1-verification.json  43384点逐点核对
  evidence/cloud/model-verification.json                          远程148搏比对与拒绝分类验证总报告
  evidence/cloud/model-health.json                                当前模型ready健康检查
  evidence/cloud/model-analysis-real.json                          云端实采复算完整结果
  evidence/cloud/model-analysis-real-get.json                      随后GET读取的持久化结果
  evidence/cloud/model-analysis-historical-invalid-simulated.json  历史无效/板端模拟拒绝结果
  evidence/cloud/model-analysis-demo.json                          本地合成拒绝结果
  evidence/cloud/restart-result.txt                               模型启用前的2会话/210块重启验证
  evidence/cloud/model-restart-verification.json                  模型启用后重启、148搏缓存及330块持久化验证
  evidence/cloud/web-model-ready.png                              网页点击复算后显示148搏的实际截图
  evidence/cloud/app-update-result.txt                             本次云端优化包更新与原148搏缓存保留
  evidence/cloud/pi_serial_20261029_183444_0c8f6d-verification.json   新实采64872点云端逐点核对
  evidence/live-model/pi_serial_20261029_183444_0c8f6d/verification.json  新实采210搏独立复算与云端GET比对
  evidence/cloud/web-live-complete.png                             本次新实采完整网页与模型结果截图
  evidence/cloud/web-live-waveform.png                             新实采波形与模型R点标记截图
  evidence/cloud/web-live-results.png                              新实采模型结果与心搏明细截图
  evidence/cloud/live-summary.json                                当前4会话、183712点、511块汇总
本次交付的完整验收说明见“云端优化与链路验证报告_20261029.txt”。
逐点核对报告中的not_configured/null是模型部署之前的测试快照，当前模型状态以model-health.json为准。
原始实采与148搏参考位于：
  ..\树莓派心电终端\evidence\19_live_electrodes_20261029\ecg-live-electrodes-20261029\
其中offline-predictions.json是148搏参考，data/sessions/23c0fb1241a84cdb842c25015c43c7b1是原始会话。
原始参考和来源文件均保持不变；cloud_model/SOURCE.json记录正式模型来源、权重及代码哈希。
连接8767通道后，可再次运行云端模型验证：
  .\.venv\Scripts\python.exe verify_cloud_model.py
该脚本读取既有参考并请求云端复算，不上传新采样，不在电脑本地加载模型；报告写入evidence/cloud。

本次新实采证据目录：evidence/cloud/pi-link/20261029_182522_579678/。
  preflight-before-capture.json  采集开始前的实际时钟、串口与终端检查
  run-provenance.json            live来源及本次时钟检查说明
  outage-test.json               仅本运行上传通道暂停、7次失败及恢复证据
  board-result.json              新采集结果、模式、点数、块清单和补传观测
  source-copy.json               源记录下载完成与逐文件哈希核对
  live-verification.json         已通过的真实云端GET只读验收报告
  cloud-readonly-session.json    GET取得的会话、清单与来源说明
  cloud-readonly-export.csv      GET下载并逐点核对的完整CSV
下载的原始源记录：cloud_sources/20261029_182522_579678/pi_serial_20261029_183444_0c8f6d/。

连接8767通道后，可再次执行本次采集的只读验收（只向云端发GET，本地写出验证报告）：
  .\.venv\Scripts\python.exe verify_live_capture.py --run-dir "evidence\cloud\pi-link\20261029_182522_579678"
追加--offline只验证已有本地证据，结果为offline_passed，不等于真实云端验收通过。
再次核对新实采模型结果（先确保网页已完成该会话分析；云端只GET，本地独立复算）：
  .\.venv\Scripts\python.exe verify_live_model.py --session-dir "cloud_sources\20261029_182522_579678\pi_serial_20261029_183444_0c8f6d"
报告默认写入evidence/live-model/<会话编号>/verification.json，验证脚本不会改写原始源记录。

九、迁移到另一台电脑

服务端建议使用Python 3.12；普通Python安装可运行：
  python -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt

运行测试再安装requirements-test.txt；连接开发板或云服务器另安装requirements-board.txt。
requirements-lock.txt记录原有服务端及测试依赖版本。
此电脑的项目虚拟环境已安装好，无需再次安装。
demo.py依赖上一级“树莓派心电终端”中的recording.py和contracts.py；复制演示工程时保留这一相对目录，
或直接使用上传器发送已有记录。未启用模型的服务端和上传器不需要NumPy、SciPy或PyTorch。
显式启用CardioEdge模型时需另安装requirements-model.txt中的NumPy和SciPy，仍无需PyTorch或GPU。

网页按Sites技能的本地流程完成，未注册或发布任何托管网站。
