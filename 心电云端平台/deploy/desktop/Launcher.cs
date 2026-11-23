using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("心电工作台")]
[assembly: AssemblyDescription("心电云端平台 HTTPS 桌面入口")]
[assembly: AssemblyProduct("心电工作台")]
[assembly: AssemblyVersion("1.0.0.0")]

internal static class Launcher
{
    // Fixed public origin: never read account passwords or device credentials here.
    private const string CloudUrl = "https://62.234.150.104/";

    [STAThread]
    private static int Main()
    {
        Application.EnableVisualStyles();
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
                if (TryStart(path, "--app=" + CloudUrl + " --window-size=1440,940", false)) return 0;
            }
        }
        // Default browser remains available when Edge/Chrome is not installed.
        if (TryStart(CloudUrl, "", true)) return 0;
        MessageBox.Show(
            "无法打开浏览器。请使用 Microsoft Edge 或 Chrome 打开：\n\n" + CloudUrl +
            "\n\n也可以双击文件夹中的“浏览器备用入口”。",
            "心电工作台", MessageBoxButtons.OK, MessageBoxIcon.Information);
        return 1;
    }

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
