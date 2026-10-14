using System.Diagnostics;

namespace FileDistributionStudio.Agent;

public sealed class ProcessOperations
{
    private static readonly HashSet<string> ProtectedNames = new(StringComparer.OrdinalIgnoreCase)
    {
        "System", "Idle", "Registry", "smss", "csrss", "wininit", "services", "lsass",
        "winlogon", "svchost", "SystemSettings", "FileDistributionStudio.Agent"
    };

    public KillProcessResult KillByImageName(string imageName)
    {
        var rawName = (imageName ?? "").Trim();
        if (string.IsNullOrWhiteSpace(rawName)
            || rawName.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0
            || rawName.Contains(Path.DirectorySeparatorChar)
            || rawName.Contains(Path.AltDirectorySeparatorChar))
            return KillProcessResult.Invalid("进程名称只能是 EXE 镜像名称，不能包含路径或参数。");

        var normalized = Path.GetFileName(rawName);
        if (normalized.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
            normalized = normalized[..^4];

        if (string.IsNullOrWhiteSpace(normalized))
            return KillProcessResult.Invalid("必须提供进程 EXE 名称。");
        if (ProtectedNames.Contains(normalized))
            return KillProcessResult.Invalid($"系统保护进程不允许由 Agent 结束：{normalized}。");

        var processes = Process.GetProcessesByName(normalized);
        var failures = new List<string>();
        var killed = 0;
        foreach (var process in processes)
        {
            try
            {
                process.Kill(entireProcessTree: true);
                process.WaitForExit(5000);
                killed++;
            }
            catch (Exception ex)
            {
                failures.Add($"PID {process.Id}: {ex.Message}");
            }
            finally
            {
                process.Dispose();
            }
        }

        return new KillProcessResult(true, normalized, processes.Length, killed, failures);
    }

    public IReadOnlyList<ProcessInfo> List()
    {
        var result = new List<ProcessInfo>();
        foreach (var process in Process.GetProcesses())
        {
            try
            {
                var image = process.ProcessName + ".exe";
                var path = "";
                var description = "";
                try
                {
                    path = process.MainModule?.FileName ?? "";
                    if (!string.IsNullOrWhiteSpace(path) && File.Exists(path))
                        description = FileVersionInfo.GetVersionInfo(path).FileDescription ?? "";
                }
                catch
                {
                    // Protected/system processes may deny module inspection; the
                    // image name and PID are still useful to the desktop UI.
                }
                result.Add(new ProcessInfo(process.Id, image, description, path));
            }
            catch
            {
                // A process can exit between enumeration and inspection.
            }
            finally
            {
                process.Dispose();
            }
        }
        return result.OrderBy(x => x.ImageName, StringComparer.OrdinalIgnoreCase)
            .ThenBy(x => x.Pid)
            .ToArray();
    }
}

public sealed record ProcessInfo(int Pid, string ImageName, string Description, string Path);

public sealed record KillProcessResult(
    bool Accepted,
    string ImageName,
    int Matched,
    int Killed,
    IReadOnlyList<string> Failures,
    string? Error = null)
{
    public static KillProcessResult Invalid(string message) =>
        new(false, "", 0, 0, Array.Empty<string>(), message);
}
