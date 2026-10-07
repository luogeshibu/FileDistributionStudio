using System.Diagnostics;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using FileDistributionStudio.Agent;

var builder = WebApplication.CreateBuilder(args);
builder.Host.UseWindowsService(options => options.ServiceName = "FileDistributionStudio Agent");
builder.Configuration.AddEnvironmentVariables(prefix: "FDS_AGENT__");

var options = new AgentOptions();
builder.Configuration.GetSection("Agent").Bind(options);
builder.Services.AddSingleton(options);
builder.Services.AddSingleton<ProcessOperations>();

builder.WebHost.ConfigureKestrel((context, kestrel) =>
{
    var config = new AgentOptions();
    context.Configuration.GetSection("Agent").Bind(config);
    if (config.ListenPort is < 1 or > 65535)
        throw new InvalidOperationException("Agent.ListenPort 必须是 1 到 65535 之间的端口。");

    var certificate = FindCertificate(config.CertificateThumbprint);
    if (certificate is null)
    {
        throw new InvalidOperationException(
            "未找到 Agent HTTPS 证书。请由安装程序在 LocalMachine\\My 写入证书，"
            + "并在 Agent:CertificateThumbprint 中配置指纹。Agent 不默认启动明文 HTTP。");
    }

    kestrel.ListenAnyIP(config.ListenPort, listen => listen.UseHttps(certificate));
});

var app = builder.Build();
var agentOptions = app.Services.GetRequiredService<AgentOptions>();

app.Use(async (context, next) =>
{
    if (context.Request.Path.Equals("/api/v1/health", StringComparison.OrdinalIgnoreCase))
    {
        await next();
        return;
    }

    var supplied = context.Request.Headers["X-FDS-Agent-Token"].ToString();
    if (string.IsNullOrWhiteSpace(agentOptions.Token) || !FixedTimeEquals(supplied, agentOptions.Token))
    {
        context.Response.StatusCode = StatusCodes.Status401Unauthorized;
        await context.Response.WriteAsJsonAsync(new { error = "未提供有效的 Agent 配对令牌。" });
        return;
    }
    await next();
});

app.MapGet("/api/v1/health", () => Results.Ok(new
{
    status = "ok",
    service = agentOptions.AgentName,
    version = agentOptions.AgentVersion,
}));

app.MapGet("/api/v1/status", () => Results.Ok(new
{
    status = "online",
    machine = Environment.MachineName,
    agent = agentOptions.AgentName,
    version = agentOptions.AgentVersion,
    operatingSystem = Environment.OSVersion.VersionString,
    processId = Environment.ProcessId,
    utc = DateTimeOffset.UtcNow,
}));

app.MapPut("/api/v1/files", async (HttpRequest request, string path, AgentOptions config, CancellationToken cancellationToken) =>
{
    if (!TryResolveAllowedPath(config, path, out var destination, out var error))
        return Results.BadRequest(new { error });
    if (request.ContentLength is long contentLength && contentLength > config.MaxUploadBytes)
        return Results.BadRequest(new { error = "上传文件超过 Agent 允许的大小限制。" });

    Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
    var temporary = destination + $".fds-upload-{Guid.NewGuid():N}.tmp";
    long bytes = 0;
    try
    {
        await using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 128 * 1024, FileOptions.SequentialScan))
        {
            var buffer = new byte[128 * 1024];
            int read;
            while ((read = await request.Body.ReadAsync(buffer, cancellationToken)) > 0)
            {
                bytes += read;
                if (bytes > config.MaxUploadBytes)
                    throw new InvalidDataException("上传文件超过 Agent 允许的大小限制。");
                await output.WriteAsync(buffer.AsMemory(0, read), cancellationToken);
            }
            await output.FlushAsync(cancellationToken);
        }

        File.Move(temporary, destination, overwrite: true);
        return Results.Ok(new { path = destination, bytes });
    }
    catch (InvalidDataException ex)
    {
        TryDelete(temporary);
        return Results.BadRequest(new { error = ex.Message });
    }
    catch (OperationCanceledException)
    {
        TryDelete(temporary);
        return Results.StatusCode(499);
    }
    catch (Exception ex)
    {
        TryDelete(temporary);
        return Results.Problem($"文件写入失败：{ex.Message}", statusCode: StatusCodes.Status500InternalServerError);
    }
});

app.MapPost("/api/v1/files/inspect", (FileInspectRequest request, AgentOptions config) =>
{
    if (!TryResolveAllowedPath(config, request.Path, out var path, out var error))
        return Results.BadRequest(new { error });
    if (Directory.Exists(path))
        return Results.Ok(new { exists = true, isDir = true, size = 0L, sha256 = "" });
    if (!File.Exists(path))
        return Results.Ok(new { exists = false, isDir = false, size = 0L, sha256 = "" });
    var info = new FileInfo(path);
    var hash = request.IncludeSha256 ? ComputeSha256(path) : "";
    return Results.Ok(new { exists = true, isDir = false, size = info.Length, sha256 = hash });
});

app.MapGet("/api/v1/files/list", (string? path, AgentOptions config) =>
{
    var requested = path ?? "";
    if (string.IsNullOrWhiteSpace(requested))
    {
        var roots = config.AllowedRoots
            .Where(root => !string.IsNullOrWhiteSpace(root))
            .Select(root => Path.GetFullPath(root))
            .Where(Directory.Exists)
            .Select(root => new
            {
                name = Path.GetFileName(root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar))
                    ?? root,
                path = root,
                isDir = true,
                size = 0L,
                lastWriteUtc = Directory.GetLastWriteTimeUtc(root),
            });
        return Results.Ok(new { path = "", entries = roots });
    }

    if (!TryResolveAllowedPath(config, requested, out var directory, out var error))
        return Results.BadRequest(new { error });
    if (!Directory.Exists(directory))
        return Results.NotFound(new { error = "远程目录不存在。" });

    try
    {
        var entries = Directory.EnumerateFileSystemEntries(directory)
            .Select(entry =>
            {
                var isDir = Directory.Exists(entry);
                var info = isDir ? null : new FileInfo(entry);
                return new
                {
                    name = Path.GetFileName(entry),
                    path = Path.GetFullPath(entry),
                    isDir,
                    size = info?.Length ?? 0L,
                    lastWriteUtc = File.GetLastWriteTimeUtc(entry),
                };
            })
            .OrderByDescending(entry => entry.isDir)
            .ThenBy(entry => entry.name, StringComparer.OrdinalIgnoreCase)
            .ToArray();
        return Results.Ok(new { path = directory, entries });
    }
    catch (Exception ex)
    {
        return Results.Problem($"读取远程目录失败：{ex.Message}", statusCode: StatusCodes.Status500InternalServerError);
    }
});

app.MapGet("/api/v1/files/download", (string path, AgentOptions config) =>
{
    if (!TryResolveAllowedPath(config, path, out var file, out var error))
        return Results.BadRequest(new { error });
    if (!File.Exists(file))
        return Results.NotFound(new { error = "远程文件不存在。" });
    return Results.File(File.OpenRead(file), "application/octet-stream", Path.GetFileName(file), enableRangeProcessing: true);
});

app.MapPost("/api/v1/files/directory", (DirectoryRequest request, AgentOptions config) =>
{
    if (!TryResolveAllowedPath(config, request.Path, out var directory, out var error))
        return Results.BadRequest(new { error });
    try
    {
        Directory.CreateDirectory(directory);
        return Results.Ok(new { path = directory });
    }
    catch (Exception ex)
    {
        return Results.Problem($"创建远程目录失败：{ex.Message}", statusCode: StatusCodes.Status500InternalServerError);
    }
});

app.MapPost("/api/v1/files/rename", (RenameRequest request, AgentOptions config) =>
{
    if (!TryResolveAllowedPath(config, request.Path, out var source, out var sourceError))
        return Results.BadRequest(new { error = sourceError });
    if (!TryResolveAllowedPath(config, request.NewPath, out var destination, out var destinationError))
        return Results.BadRequest(new { error = destinationError });
    try
    {
        if (File.Exists(source)) File.Move(source, destination);
        else if (Directory.Exists(source)) Directory.Move(source, destination);
        else return Results.NotFound(new { error = "远程项目不存在。" });
        return Results.Ok(new { path = destination });
    }
    catch (Exception ex)
    {
        return Results.Problem($"重命名远程项目失败：{ex.Message}", statusCode: StatusCodes.Status500InternalServerError);
    }
});

app.MapDelete("/api/v1/files", (string path, AgentOptions config) =>
{
    if (!TryResolveAllowedPath(config, path, out var target, out var error))
        return Results.BadRequest(new { error });
    try
    {
        if (File.Exists(target)) File.Delete(target);
        else if (Directory.Exists(target)) Directory.Delete(target, recursive: true);
        else return Results.NotFound(new { error = "远程项目不存在。" });
        return Results.Ok(new { path = target });
    }
    catch (Exception ex)
    {
        return Results.Problem($"删除远程项目失败：{ex.Message}", statusCode: StatusCodes.Status500InternalServerError);
    }
});

app.MapPost("/api/v1/files/backup", (FileBackupRequest request, AgentOptions config) =>
{
    if (!TryResolveAllowedPath(config, request.SourcePath, out var source, out var sourceError))
        return Results.BadRequest(new { error = sourceError });
    if (!TryResolveAllowedPath(config, request.BackupPath, out var backup, out var backupError))
        return Results.BadRequest(new { error = backupError });
    if (!File.Exists(source))
        return Results.NotFound(new { error = "待备份文件不存在。" });
    try
    {
        Directory.CreateDirectory(Path.GetDirectoryName(backup)!);
        if (!File.Exists(backup))
            File.Copy(source, backup, overwrite: false);
        return Results.Ok(new { path = backup, bytes = new FileInfo(backup).Length, sha256 = ComputeSha256(backup) });
    }
    catch (Exception ex)
    {
        return Results.Problem($"文件备份失败：{ex.Message}", statusCode: StatusCodes.Status500InternalServerError);
    }
});

app.MapPost("/api/v1/operations/process/kill", (KillProcessRequest request, ProcessOperations processes) =>
{
    var result = processes.KillByImageName(request.ImageName);
    return result.Accepted
        ? Results.Ok(result)
        : Results.BadRequest(result);
});

app.MapPost("/api/v1/operations/restart", (RestartRequest request, ILoggerFactory loggerFactory) =>
{
    if (!request.Force)
        return Results.BadRequest(new { error = "强制重启必须明确发送 force=true。" });

    var logger = loggerFactory.CreateLogger("RestartOperation");
    try
    {
        logger.LogWarning("收到强制重启请求，目标主机将在立即关闭程序后重启。");
        using var shutdown = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = Path.Combine(Environment.SystemDirectory, "shutdown.exe"),
                UseShellExecute = false,
                CreateNoWindow = true,
            }
        };
        shutdown.StartInfo.ArgumentList.Add("/r");
        shutdown.StartInfo.ArgumentList.Add("/f");
        shutdown.StartInfo.ArgumentList.Add("/t");
        shutdown.StartInfo.ArgumentList.Add("0");
        shutdown.Start();
        return Results.Accepted(value: new { status = "accepted", message = "重启命令已接受，主机随后离线属于正常现象。" });
    }
    catch (Exception ex)
    {
        logger.LogError(ex, "强制重启请求失败。");
        return Results.Problem($"强制重启失败：{ex.Message}", statusCode: StatusCodes.Status500InternalServerError);
    }
});

app.Run();

static X509Certificate2? FindCertificate(string thumbprint)
{
    var normalized = (thumbprint ?? "").Replace(" ", "", StringComparison.Ordinal).Trim();
    if (string.IsNullOrWhiteSpace(normalized))
        return null;

    using var store = new X509Store(StoreName.My, StoreLocation.LocalMachine);
    store.Open(OpenFlags.ReadOnly);
    return store.Certificates.Find(X509FindType.FindByThumbprint, normalized, validOnly: false)
        .OfType<X509Certificate2>()
        .FirstOrDefault();
}

static bool FixedTimeEquals(string left, string right)
{
    var leftBytes = Encoding.UTF8.GetBytes(left ?? "");
    var rightBytes = Encoding.UTF8.GetBytes(right ?? "");
    return leftBytes.Length == rightBytes.Length
        && CryptographicOperations.FixedTimeEquals(leftBytes, rightBytes);
}

static bool TryResolveAllowedPath(AgentOptions options, string requested, out string resolved, out string error)
{
    resolved = "";
    error = "";
    if (string.IsNullOrWhiteSpace(requested) || !Path.IsPathFullyQualified(requested))
    {
        error = "目标文件路径必须是本机绝对路径。";
        return false;
    }

    try
    {
        resolved = Path.GetFullPath(requested);
        var allowed = options.AllowedRoots
            .Where(root => !string.IsNullOrWhiteSpace(root))
            .Select(Path.GetFullPath)
            .Any(root => resolved.Equals(root, StringComparison.OrdinalIgnoreCase)
                || resolved.StartsWith(root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase));
        if (!allowed)
        {
            error = "目标路径不在 Agent 允许的目录范围内。";
            resolved = "";
            return false;
        }
        return true;
    }
    catch (Exception ex)
    {
        error = $"目标路径无效：{ex.Message}";
        return false;
    }
}

static void TryDelete(string path)
{
    try { if (File.Exists(path)) File.Delete(path); } catch { /* cleanup is best effort */ }
}

static string ComputeSha256(string path)
{
    using var stream = File.OpenRead(path);
    return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
}

public sealed record KillProcessRequest(string ImageName);
public sealed record RestartRequest(bool Force);
public sealed record FileInspectRequest(string Path, bool IncludeSha256 = true);
public sealed record FileBackupRequest(string SourcePath, string BackupPath);
public sealed record DirectoryRequest(string Path);
public sealed record RenameRequest(string Path, string NewPath);
