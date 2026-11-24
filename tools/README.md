# 仓库检查工具

[项目首页](../README.md) · [贡献指南](../CONTRIBUTING.md) · [CI 与发布约定](../docs/协作质量与发布.md)

| 文件 | 用途 |
| --- | --- |
| [run_checks.py](run_checks.py) | 分别启动终端 Python、云端 Python 和网页测试 |
| [check_repository.py](check_repository.py) | 检查已跟踪文件的常见私密/生成路径、私钥块及 Python 语法 |
| [test_repository_policy.py](test_repository_policy.py) | 仓库策略检查器的回归用例 |

按 [开发环境](../README.md#开发环境) 安装依赖后，从**仓库根目录**执行：

```powershell
.\.venv\Scripts\python.exe tools/check_repository.py
.\.venv\Scripts\python.exe -m unittest discover -s tools -p test_repository_policy.py -v
.\.venv\Scripts\python.exe tools/run_checks.py --suite pi
.\.venv\Scripts\python.exe tools/run_checks.py --suite cloud
.\.venv\Scripts\python.exe tools/run_checks.py --suite web
```

省略 `--suite` 会依次执行三个套件。两个 Python 工程由独立进程和工作目录运行，避免同名模块相互影响；网页测试需要 Node.js，Tk 界面测试需要显示环境。

发布前的 Windows/Android 打包检查与完整 CI 覆盖范围见 [协作质量与发布](../docs/协作质量与发布.md)。本机软件检查不替代 STM32 编译或实物联调。
