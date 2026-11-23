# 成员入门与协作流程

## 权限

所有成员均可修改固件、终端、算法、云端、网页和文档。没有目录专属负责人或 CODEOWNERS 审批门槛。任务负责人仅用于协调进度，任何成员都能认领任何模块的任务。

仓库所有者在 GitHub 仓库 `Settings → Collaborators → Add people` 使用成员的 GitHub 用户名或注册邮箱发出邀请。成员接受后获得协作写入权限；每人使用自己的账号和本地克隆。

PR 必须通过 `Quality gate` 并由至少一位其他成员批准，新增代码后重新审查。所有成员可审查所有模块；不设置目录专属批准人。GitHub 强制规则以仓库设置为准，具体配置、AI 审查和发布流程见 [协作质量与发布](docs/协作质量与发布.md)。仓库管理权限与开发写入权限不同，所有者负责成员邀请和仓库设置。

## 首次加入

```powershell
git clone https://github.com/XiongBoT/bme-ecg-system.git
cd bme-ecg-system
git config user.name "你的姓名"
git config user.email "你的 GitHub 提交邮箱"
```

按 README 安装依赖。可以在 VS Code 中编辑；每人独立安装工具链，不共享虚拟环境。没有 STM32 或树莓派的成员可用现有模拟和回放工具开发，PR 中注明使用了哪种数据。

## 每个任务的操作

1. 在 Issues 建立任务，写明目标、涉及模块和验收条件，认领后开始。跨模块修改可放在同一任务中；一个 PR 聚焦一个问题。
2. 确认当前修改已提交或妥善暂存，再更新主分支并创建任务分支：

```powershell
git switch main
git pull --ff-only
git switch -c feat/history-filter
```

3. 编写代码与必要的测试。接口有变化时同步修改 `docs/接口约定.md` 和相关模块的说明。
4. 查看差异后，只暂存这次任务涉及的文件。例如修改云端网页：

```powershell
git status --short
git diff
git add "心电云端平台/web/app.js"
git diff --cached
git commit -m "feat: 增加历史记录筛选"
git push -u origin feat/history-filter
```

5. 在 GitHub 创建 PR，目标分支为 `main`，按模板填写效果、检查结果、兼容性和是否需要部署。等待 GitHub Actions 检查，处理 AI/人工意见，请任意另一位成员批准最新改动；审查者不局限于某个模块，作者不能自批。
6. 如有冲突，在任务分支执行 `git fetch origin`、`git merge origin/main`，逐处解决后重新测试、提交、推送。不要强制推送覆盖公共分支。
7. 审查与检查完成后合并 PR；其他成员更新 `main`。一个任务结束后，下个任务重新建分支。

不要多人同时编辑同一个共享磁盘工作目录。同一电脑需要并行任务时使用独立克隆或 Git worktree。对同一个文件的重叠修改先在任务中协调。

## 检查与实物联调

```powershell
.\.venv\Scripts\python.exe tools/run_checks.py --suite pi
.\.venv\Scripts\python.exe tools/run_checks.py --suite cloud
.\.venv\Scripts\python.exe tools/run_checks.py --suite web
```

不指定 `--suite` 则依次运行三个模块的检查，任一失败返回非零退出码。两个 Python 工程分开运行，防止它们的同名 `protocol.py` 相互干扰。

固件改动还应提供 Keil 编译结果；串口、显示、采集或部署相关修改还应提供实物验证记录。记录明确区分本机模拟、公开数据库回放、板端模拟与真实采集。

正式设备和服务器的更新需要另行安排，合并 PR 不自动部署。部署前记录当前版本和回退方法，共用板卡时先协调使用时间。

## 文件与数据

提交源码、依赖清单、文档、公开来源的必要模型及测试向量。真实心电记录、账号数据库、设备凭据和服务器密钥通过另行授权的渠道管理，不放进仓库或 Issue/PR 附件。

本地历史报告不自动进入仓库；要新增可共享文档时放到 `docs/`。新增顶层模块需要同步调整根目录 `.gitignore` 的纳入规则。

文件归位：共同说明放根 `docs/`；模块操作与设计放各模块 `docs/`，历史方案与旧图放 `docs/archive/` 并标注适用阶段。可复用脚本放原有 `tools/` 或 `deploy/`；软件测试放模块 `tests/`。模块 `README.md` 提供导航，完整运行步骤继续在已有使用说明中维护。新增重要入口时更新 [文档导航](docs/README.md)，移动文件时一并修正链接和打包引用。Keil、Python 导入及启动脚本要求的目录保持原样。
