using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("心电工作台")]
[assembly: AssemblyDescription("心电云端平台 HTTPS 桌面入口")]
[assembly: AssemblyProduct("心电工作台")]
[assembly: AssemblyVersion("1.2.0.0")]

internal static class Launcher
{
    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        Application.Run(new ConnectionWindow(Array.IndexOf(args, "--connection") < 0));
        return 0;
    }

    internal static bool OpenWorkspace(Rectangle area)
    {
        string[] roots = {
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData)
        };
        string[] browsers = { @"Microsoft\Edge\Application\msedge.exe", @"Google\Chrome\Application\chrome.exe" };
        foreach (string browser in browsers)
        {
            foreach (string root in roots)
            {
                if (String.IsNullOrEmpty(root)) continue;
                string path = Path.Combine(root, browser);
                if (!File.Exists(path)) continue;
                if (TryStart(path, LaunchPolicy.BrowserArguments(area), false)) return true;
            }
        }
        // Default browser remains available when Edge/Chrome is not installed.
        return OpenBrowser();
    }

    internal static bool OpenBrowser() { return TryStart(LaunchPolicy.CloudUrl, "", true); }

    private static bool TryStart(string file, string arguments, bool shell)
    {
        try
        {
            using (Process process = Process.Start(new ProcessStartInfo {
                FileName = file,
                Arguments = arguments,
                UseShellExecute = shell,
                WorkingDirectory = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile)
            })) { }
            return true;
        }
        catch (System.ComponentModel.Win32Exception) { return false; }
        catch (InvalidOperationException) { return false; }
        catch (System.Security.SecurityException) { return false; }
    }
}

internal sealed class ConnectionWindow : Form
{
    private readonly ConnectionGate gate = new ConnectionGate();
    private readonly CancellationTokenSource lifetime = new CancellationTokenSource();
    private readonly bool autoOpen;
    private readonly Label status = new Label();
    private readonly Label detail = new Label();
    private readonly Button open = new Button();
    private readonly Button retry = new Button();

    internal ConnectionWindow(bool autoOpen)
    {
        this.autoOpen = autoOpen;
        Text = "心电工作台 · 连接助手";
        Font = new Font("Microsoft YaHei UI", 10F);
        AutoScaleMode = AutoScaleMode.Font;
        ClientSize = new Size(570, 385);
        AutoScroll = true;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(246, 249, 247);
        MaximizeBox = false;
        Icon = Icon.ExtractAssociatedIcon(Assembly.GetExecutingAssembly().Location);

        var layout = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize=true, MinimumSize=new Size(500,0), Padding = new Padding(28), ColumnCount = 1, RowCount = 6 };
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        Controls.Add(layout);
        layout.Controls.Add(new Label {Text="心电工作台",AutoSize=true,Font=new Font(Font.FontFamily,23F,FontStyle.Bold),ForeColor=Color.FromArgb(8,127,113),Margin=new Padding(0,0,0,8)});
        layout.Controls.Add(new Label {Text="记录与分析保存在云端，换一台电脑也能继续查看。",AutoSize=true,ForeColor=Color.FromArgb(95,117,109),Margin=new Padding(0,0,0,22)});
        var panel = new TableLayoutPanel {Dock=DockStyle.Fill,AutoSize=true,MinimumSize=new Size(0,115),ColumnCount=1,RowCount=2,BackColor=Color.White,Padding=new Padding(18),Margin=new Padding(0,0,0,18)};
        panel.RowStyles.Add(new RowStyle(SizeType.AutoSize));panel.RowStyles.Add(new RowStyle(SizeType.Percent,100));
        status.Text="正在连接云端…";status.AutoSize=true;status.Font=new Font(Font,FontStyle.Bold);status.Margin=new Padding(0,0,0,9);
        detail.Text="连接成功后自动打开工作台。";detail.Dock=DockStyle.Fill;detail.AutoSize=true;detail.ForeColor=Color.FromArgb(95,117,109);detail.MaximumSize=new Size(455,0);
        panel.Controls.Add(status);panel.Controls.Add(detail);layout.Controls.Add(panel);
        var actions = new FlowLayoutPanel {AutoSize=true,Dock=DockStyle.Fill,Margin=new Padding(0),WrapContents=true};
        Configure(open,"打开工作台",true);open.Enabled=false;open.Click+=delegate {OpenWorkspace();};
        Configure(retry,"重新连接",false);retry.Click+=delegate {CheckConnection();};
        var browser=new Button();Configure(browser,"浏览器打开",false);browser.Click+=delegate {if(!Launcher.OpenBrowser()) ShowLaunchError();};
        actions.Controls.Add(open);actions.Controls.Add(retry);actions.Controls.Add(browser);layout.Controls.Add(actions);
        var links=new FlowLayoutPanel {AutoSize=true,Dock=DockStyle.Fill,Margin=new Padding(0,12,0,8)};
        var shortcut=new LinkLabel {Text="创建桌面快捷方式",AutoSize=true,Margin=new Padding(0,5,20,5),LinkColor=Color.FromArgb(8,127,113)};
        shortcut.LinkClicked+=delegate {CreateShortcut();};
        var address=new LinkLabel {Text="复制云端地址",AutoSize=true,Margin=new Padding(0,5,0,5),LinkColor=Color.FromArgb(8,127,113)};
        address.LinkClicked+=delegate {try {Clipboard.SetText(LaunchPolicy.CloudUrl);detail.Text="云端地址已复制，可粘贴到浏览器打开。";} catch(ExternalException) {detail.Text="剪贴板暂不可用。云端地址："+LaunchPolicy.CloudUrl;}};
        links.Controls.Add(shortcut);links.Controls.Add(address);layout.Controls.Add(links);
        layout.Controls.Add(new Label {Text="Windows 版 1.2 · 需要联网 · 沿用已有心电平台账号",AutoSize=true,ForeColor=Color.FromArgb(117,135,127),Font=new Font(Font.FontFamily,9F),Margin=new Padding(0,8,0,0)});
        Shown+=delegate {CheckConnection();};
        FormClosing+=delegate {gate.Close();lifetime.Cancel();};
        FormClosed+=delegate {lifetime.Dispose();};
    }

    protected override void OnLoad(EventArgs e)
    {
        base.OnLoad(e);
        Rectangle area=Screen.FromControl(this).WorkingArea;
        MaximumSize=area.Size;
        Bounds=LaunchPolicy.ConnectionBounds(area,Size);
    }

    private void Configure(Button button,string caption,bool primary)
    {
        button.Text=caption;button.AutoSize=true;button.MinimumSize=new Size(132,42);button.Margin=new Padding(0,0,10,0);
        button.FlatStyle=FlatStyle.Flat;button.FlatAppearance.BorderColor=Color.FromArgb(211,227,218);
        button.BackColor=primary?Color.FromArgb(8,127,113):Color.White;button.ForeColor=primary?Color.White:Color.FromArgb(44,74,62);
    }

    private async void CheckConnection()
    {
        if(!gate.Begin()) return;
        retry.Enabled=false;open.Enabled=false;status.Text="正在连接云端…";
        detail.Text=autoOpen?"连接成功后自动打开工作台。也可以直接用浏览器打开。":"正在检查公开入口，不读取账号或采集记录。";
        ConnectionResult result;
        try
        {
            using(var handler=new HttpClientHandler {AllowAutoRedirect=false,UseCookies=false})
            using(var client=new HttpClient(handler) {Timeout=TimeSpan.FromSeconds(6)})
                result=await ConnectionProbe.CheckAsync(client,lifetime.Token);
        }
        catch(OperationCanceledException) {return;}
        if(!gate.Finish() || IsDisposed) return;
        retry.Enabled=true;open.Enabled=true;
        status.Text=result.Kind==ConnectionKind.Ready?"已连接云端":"暂时无法连接";
        detail.Text=result.Message;
        if(result.Kind==ConnectionKind.Ready && autoOpen) OpenWorkspace();
    }

    private void OpenWorkspace()
    {
        if(Launcher.OpenWorkspace(Screen.FromControl(this).WorkingArea)) Close();else ShowLaunchError();
    }
    private void ShowLaunchError()
    {
        status.Text="浏览器未能启动";
        detail.Text="请尝试“浏览器打开”，或复制云端地址到 Edge / Chrome："+LaunchPolicy.CloudUrl;
    }
    private void CreateShortcut()
    {
        string path=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),"心电工作台.lnk");
        if(File.Exists(path)) {detail.Text="桌面上已有同名快捷方式，未覆盖。需要更换时请先移走旧快捷方式。";return;}
        object shell=null,shortcut=null;
        try
        {
            Type shellType=Type.GetTypeFromProgID("WScript.Shell");
            if(shellType==null) throw new InvalidOperationException();
            shell=Activator.CreateInstance(shellType);
            shortcut=shellType.InvokeMember("CreateShortcut",BindingFlags.InvokeMethod,null,shell,new object[]{path});
            Type type=shortcut.GetType();string executable=Assembly.GetExecutingAssembly().Location;
            type.InvokeMember("TargetPath",BindingFlags.SetProperty,null,shortcut,new object[]{executable});
            type.InvokeMember("WorkingDirectory",BindingFlags.SetProperty,null,shortcut,new object[]{Path.GetDirectoryName(executable)});
            type.InvokeMember("Description",BindingFlags.SetProperty,null,shortcut,new object[]{"心电云端工作台"});
            type.InvokeMember("Save",BindingFlags.InvokeMethod,null,shortcut,null);
            detail.Text="桌面快捷方式已创建。请保留当前解压文件夹，移动后需重新创建。";
        }
        catch(Exception error)
        {
            if(!(error is COMException || error is TargetInvocationException || error is UnauthorizedAccessException || error is IOException || error is InvalidOperationException)) throw;
            detail.Text="未能创建快捷方式。可以在资源管理器中右键“心电工作台.exe”手动创建。";
        }
        finally {if(shortcut!=null && Marshal.IsComObject(shortcut)) Marshal.ReleaseComObject(shortcut);if(shell!=null && Marshal.IsComObject(shell)) Marshal.ReleaseComObject(shell);}
    }
}
