# 仓库检查工具

[项目首页](../README.md) · [贡献指南](../CONTRIBUTING.md) · [CI 与发布约定](../docs/协作质量与发布.md)

| 文件 | 用途 |
| --- | --- |
| [run_checks.py](run_checks.py) | 分别启动终端 Python、云端 Python 和网页测试 |
| [check_repository.py](check_repository.py) | 检查已跟踪文件的常见私密/生成路径、私钥块及 Python 语法 |
| [check_docs.py](check_docs.py) | 离线检查已跟踪 Markdown 链接、章节锚点和四份终端/云端打包白名单 |
| [sync_docs.py](sync_docs.py) | 从 Markdown 生成两个离线 README，并从报告 JSON 生成结果摘要；`--check` 检查同步 |
| [test_documentation.py](test_documentation.py) | 文档检查器与生成同步的回归用例 |
| [test_repository_policy.py](test_repository_policy.py) | 仓库策略检查器的回归用例 |
| [local_verification.py](local_verification.py) | 在受限工作站上跑三个套件并写出 JSON 证据；网页逐个文件运行，可选 Windows 沙箱适配 |
| [test_local_verification.py](test_local_verification.py) | 本机验证工具的解析与汇总回归用例 |

按 [开发环境](../README.md#开发环境) 安装依赖后，从**仓库根目录**执行：

```powershell
.\.venv\Scripts\python.exe tools/check_repository.py
.\.venv\Scripts\python.exe -m unittest discover -s tools -p "test_*.py" -v
.\.venv\Scripts\python.exe tools/check_docs.py
.\.venv\Scripts\python.exe tools/sync_docs.py --check
.\.venv\Scripts\python.exe tools/run_checks.py --suite pi
.\.venv\Scripts\python.exe tools/run_checks.py --suite cloud
.\.venv\Scripts\python.exe tools/run_checks.py --suite web
```

省略 `--suite` 会依次执行三个套件。两个 Python 工程由独立进程和工作目录运行，避免同名模块相互影响；网页测试需要 Node.js，Tk 界面测试需要显示环境。

受限工作站（例如禁止创建私有临时目录、禁止进程派生的环境）可用：

```powershell
.\.venv\Scripts\python.exe tools/local_verification.py --suite all `
    --adapt-windows-sandbox --json "docs/中期检查/local-verification-20261125.json"
```

它与 `run_checks.py` 的区别只有三点：网页测试按文件逐个运行而不是 `node --test`；`--adapt-windows-sandbox` 在**当前进程内**放宽私有临时目录权限位并注入子进程，不改仓库文件；结果写成 `local-verification-v1` JSON，供文档引用。退出码 `0` 全通过、`1` 有真实失败、`2` 仅环境阻塞。

发布前的 Windows/Android 打包检查与完整 CI 覆盖范围见 [协作质量与发布](../docs/协作质量与发布.md)。本机软件检查不替代 STM32 编译或实物联调。

算法研究另有 `Algorithm evaluation tests`：安装云端 `requirements-research.txt` 后运行 `tests/*benchmark*.py`，检查同输入、匹配边界、分类分母、断流及报告一致性，不下载公开心电数据库。全量数据实验按[研究工具](../心电云端平台/research/README.md)独立执行，不在每次 CI 中重复长时评测。

修改现行三份说明或公开报告 JSON 后，执行 `python tools/sync_docs.py --write`。历史 `docs/archive/` 原文不参与生成。链接检查支持行内/引用链接、Markdown 标题和 HTML 锚点，忽略代码示例中的链接，不访问外网；文件名大小写按 Git 跟踪清单核对。生成文本中的在线链接用于精简离线包查阅，文档原文仍采用仓库相对链接。
