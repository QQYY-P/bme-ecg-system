# Windows 免安装桌面入口

用途：将正式云端 https://62.234.150.104/ 打包为可解压运行的 Windows 启动入口，使用既有账号。无需安装 Python、Node.js；不包含离线服务、记录或凭据。1.1.0 对应已上线的宽屏记录/波形/结果布局，并支持专注查看、心搏筛选定位和连接重试。

下载：[Windows ZIP](https://62.234.150.104/downloads/ecg-workspace-windows.zip)、[SHA256](https://62.234.150.104/downloads/ecg-workspace-windows.zip.sha256)。也可从正式网页的“下载电脑端”打开说明。GitHub 仓库为私有，Release 下载需要仓库权限；普通使用者使用上述公网下载。

无需自行构建的使用者请从 [项目首页的应用下载](../../../README.md#应用下载) 获取免安装包、版本说明与校验文件。

实施与验收：

1. 编译无控制台 C# 启动程序，优先 Edge、其次 Chrome 独立窗口，再回退默认浏览器。固定 HTTPS 地址，不读本地密码、不变更浏览器安全策略。
2. 仅打包启动程序、备用 URL、说明、SHA256 清单；源码留在本目录，生成包进入 `deploy/dist/desktop/`。
3. 验证中文和带空格路径构建/解压、归档清单和摘要；从解压目录实际启动，核对可信 HTTPS 登录页和未登录接口限制。

构建（Windows 开发环境，Python 3.10+ / .NET Framework 4.x 编译器）：

```powershell
python deploy/package_desktop.py
python -m unittest discover -s tests -p test_desktop_package.py -v
```

此处 Python 仅用于开发打包，不是使用者运行条件。输出 EXE 为 AnyCPU，尚未进行代码签名；不使用安装器、管理员权限、计划任务或自启动。版本更新由服务器提供页面内容，启动地址变化则需重新构建。

用户操作见 [使用说明](使用说明.txt)：完整解压后双击“心电工作台.exe”，使用现有账号登录。遇到启动限制可使用包内浏览器备用入口。需联网；页面更新由云端提供，旧版启动入口也可以访问新版页面。

部署：源码包由 `deploy/package_cloud.py` 构建；生成的桌面 ZIP 与外置校验文件需单独放在服务器 `ecg-cloud/deploy/dist/desktop/`。公开下载路由只接受两个固定文件名，不公开目录、凭据或记录。`deploy/linux/update_desktop.py` 校验更新清单，停服备份数据，切换源码，检查模型/身份保护/下载/原始数据后启动；它用于既有 ubuntu 服务，不执行初始账号创建。内部虚拟环境随旧应用保留，外部环境沿用原路径。失败时恢复代码并保留当前数据，避免覆盖新的上传。

初版任务分支为 `feat/ecg-desktop-portable`，1.1.0 工作台后续分支为 `feat/ecg-desktop-workspace-110`。构建生成的 ZIP/EXE、记录和凭据不进入 Git；版本附件发布在 [Windows 1.1.0 Release](https://github.com/XiongBoT/bme-ecg-system/releases/tag/desktop-v1.1.0)。项目进度、验收证据、交付包摘要和后续事项统一见 [云端工作台迭代](../../云端工作台迭代.md) 的“Windows 免安装电脑端”小节。
