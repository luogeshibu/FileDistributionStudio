using System.Diagnostics;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using FileDistributionStudio.Agent;

const long UploadChunkMaxBytes = 16L * 1024 * 1024;

var builder = WebApplication.CreateBuilder(args);
// Windows 服务默认工作目录通常是 C:\Windows\System32。固定使用 EXE 所在目录，
// 确保程序文件和运行时依赖始终从 Agent 安装目录读取。
builder.Host.UseContentRoot(AppContext.BaseDirectory);
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
    // File size is enforced by AgentOptions inside the upload endpoint. Do not
    // let Kestrel's much smaller default request-body limit reject large files
    // before the Agent can apply its configured limit.
    kestrel.Limits.MaxRequestBodySize = null;
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
    machine = System.Net.Dns.GetHostName(),
    computerName = Environment.MachineName,
    agent = agentOptions.AgentName,
    version = agentOptions.AgentVersion,
    operatingSystem = Environment.OSVersion.VersionString,
    processId = Environment.ProcessId,
    utc = DateTimeOffset.UtcNow,
}));

// Large uploads use a small resumable protocol.  Each chunk is committed to a
// temporary file before the next one is sent, so a broken connection resumes
// from the last acknowledged offset instead of restarting the whole file.
app.MapPost("/api/v1/files/upload/init", (UploadInitRequest request, AgentOptions config) =>
{
    if (!TryResolveAllowedPath(config, request.Path, out var destination, out var error))
        return Results.BadRequest(new { error });
    if (request.Size < 0 || request.Size > config.MaxUploadBytes)
        return Results.BadRequest(new { error = "上传文件超过 Agent 允许的大小限制。" });

    try
    {
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        var temporary = UploadTemporaryPath(destination);
        var statePath = UploadStatePath(destination);
        var expectedHash = NormalizeSha256(request.Sha256);
        var state = ReadUploadState(statePath);
        if (state is not null
            && File.Exists(temporary)
            && state.Destination.Equals(destination, StringComparison.OrdinalIgnoreCase)
            && state.Size == request.Size
            && state.Sha256.Equals(expectedHash, StringComparison.OrdinalIgnoreCase))
        {
            var offset = new FileInfo(temporary).Length;
            return Results.Ok(new { uploadId = state.UploadId, offset, size = state.Size });
        }

        TryDelete(temporary);
        TryDelete(statePath);
        using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1024 * 1024, FileOptions.SequentialScan))
            output.SetLength(0);

        state = new UploadState(Guid.NewGuid().ToString("N"), destination, request.Size, expectedHash, 0);
        WriteUploadState(statePath, state);
        return Results.Ok(new { uploadId = state.UploadId, offset = 0L, size = state.Size });
    }
    catch (Exception ex)
    {
        return Results.Problem($"初始化文件上传失败：{ex.Message}", statusCode: StatusCodes.Status500InternalServerError);
    }
});

app.MapPut("/api/v1/files/upload/chunk", async (HttpRequest request, string path, string uploadId, long offset, AgentOptions config, CancellationToken cancellationToken) =>
{
    if (!TryResolveAllowedPath(config, path, out var destination, out var error))
        return Results.BadRequest(new { error });
    if (string.IsNullOrWhiteSpace(uploadId) || offset < 0)
        return Results.BadRequest(new { error = "上传分块参数无效。" });
    if (request.ContentLength is long contentLength && contentLength > UploadChunkMaxBytes)
        return Results.BadRequest(new { error = "上传分块过大。" });

    var temporary = UploadTemporaryPath(destination);
    var statePath = UploadStatePath(destination);
    var state = ReadUploadState(statePath);
    if (state is null || !state.UploadId.Equals(uploadId, StringComparison.OrdinalIgnoreCase))
        return Results.NotFound(new { error = "上传会话不存在或已过期。" });

    var currentOffset = File.Exists(temporary) ? new FileInfo(temporary).Length : 0L;
    if (currentOffset != offset)
        return Results.Conflict(new { error = "上传偏移量不匹配，请重新获取当前偏移量。", offset = currentOffset });
    if (currentOffset >= state.Size)
        return Results.BadRequest(new { error = "文件已经达到声明大小。", offset = currentOffset });

    try
    {
        long received = 0;
        await using (var output = new FileStream(temporary, FileMode.Open, FileAccess.Write, FileShare.None, 1024 * 1024, FileOptions.Asynchronous | FileOptions.SequentialScan))
        {
            output.Seek(offset, SeekOrigin.Begin);
            var buffer = new byte[1024 * 1024];
            int read;
            while ((read = await request.Body.ReadAsync(buffer, cancellationToken)) > 0)
            {
                received += read;
                if (currentOffset + received > state.Size || received > UploadChunkMaxBytes)
                    throw new InvalidDataException("上传分块超过声明的文件大小。" );
                await output.WriteAsync(buffer.AsMemory(0, read), cancellationToken);
            }
            await output.FlushAsync(cancellationToken);
        }

        var newOffset = currentOffset + received;
        WriteUploadState(statePath, state with { UploadedBytes = newOffset });
        return Results.Ok(new { offset = newOffset, bytes = received });
    }
    catch (InvalidDataException ex)
    {
        return Results.BadRequest(new { error = ex.Message });
    }
    catch (OperationCanceledException)
    {
        return Results.StatusCode(499);
    }
    catch (Exception ex)
    {
        return Results.Problem($"上传分块写入失败：{ex.Message}", statusCode: StatusCodes.Status500InternalServerError);
    }
});

app.MapPost("/api/v1/files/upload/complete", (UploadCompleteRequest request, AgentOptions config) =>
{
    if (!TryResolveAllowedPath(config, request.Path, out var destination, out var error))
        return Results.BadRequest(new { error });
    var temporary = UploadTemporaryPath(destination);
    var statePath = UploadStatePath(destination);
    var state = ReadUploadState(statePath);
    if (state is null || !state.UploadId.Equals(request.UploadId ?? "", StringComparison.OrdinalIgnoreCase))
        return Results.NotFound(new { error = "上传会话不存在或已过期。" });

    try
    {
        var info = new FileInfo(temporary);
        if (!info.Exists || info.Length != state.Size)
            return Results.BadRequest(new { error = "上传文件尚未完整接收。", offset = info.Exists ? info.Length : 0L });
        if (!string.IsNullOrWhiteSpace(state.Sha256)
            && !ComputeSha256(temporary).Equals(state.Sha256, StringComparison.OrdinalIgnoreCase))
            return Results.BadRequest(new { error = "上传完成后 SHA256 校验失败。" });

        File.Move(temporary, destination, overwrite: true);
        TryDelete(statePath);
        return Results.Ok(new { path = destination, bytes = info.Length, sha256 = string.IsNullOrWhiteSpace(state.Sha256) ? "" : state.Sha256 });
    }
    catch (Exception ex)
    {
        return Results.Problem($"完成文件上传失败：{ex.Message}", statusCode: StatusCodes.Status500InternalServerError);
    }
});

app.MapDelete("/api/v1/files/upload", (string path, string uploadId, AgentOptions config) =>
{
    if (!TryResolveAllowedPath(config, path, out var destination, out var error))
        return Results.BadRequest(new { error });
    var statePath = UploadStatePath(destination);
    var state = ReadUploadState(statePath);
    if (state is null || !state.UploadId.Equals(uploadId ?? "", StringComparison.OrdinalIgnoreCase))
        return Results.NotFound(new { error = "上传会话不存在或已过期。" });
    TryDelete(UploadTemporaryPath(destination));
    TryDelete(statePath);
    return Results.Ok(new { cancelled = true });
});

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
                // Path.GetFileName("C:\\") is empty on Windows.  Always return a
                // visible drive/root name so the desktop browser does not render
                // blank rows for AllowedRoots.
                name = DisplayPathName(root),
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

app.MapPost("/api/v1/files/backup-tree", (TreeBackupRequest request, AgentOptions config) =>
{
    if (!TryResolveAllowedPath(config, request.SourcePath, out var source, out var sourceError))
        return Results.BadRequest(new { error = sourceError });
    if (!TryResolveAllowedPath(config, request.BackupPath, out var backup, out var backupError))
        return Results.BadRequest(new { error = backupError });
    if (!File.Exists(source) && !Directory.Exists(source))
        return Results.NotFound(new { error = "待备份文件或目录不存在。" });

    try
    {
        var result = CopyTree(source, backup);
        return Results.Ok(new { path = backup, files = result.Files, bytes = result.Bytes });
    }
    catch (Exception ex)
    {
        return Results.Problem($"递归备份失败：{ex.Message}", statusCode: StatusCodes.Status500InternalServerError);
    }
});

app.MapGet("/api/v1/system/info", () =>
{
    var drives = DriveInfo.GetDrives()
        .Where(drive => drive.IsReady)
        .Select(drive => new
        {
            name = drive.Name,
            volumeLabel = drive.VolumeLabel,
            totalBytes = drive.TotalSize,
            freeBytes = drive.AvailableFreeSpace,
        })
        .ToArray();
    return Results.Ok(new
    {
        machine = System.Net.Dns.GetHostName(),
        computerName = Environment.MachineName,
        operatingSystem = Environment.OSVersion.VersionString,
        user = Environment.UserName,
        utc = DateTimeOffset.UtcNow,
        timeZone = TimeZoneInfo.Local.Id,
        drives,
    });
});

app.MapGet("/api/v1/operations/process/list", (ProcessOperations processes) =>
    Results.Ok(processes.List()));

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

static string DisplayPathName(string path)
{
    var full = Path.GetFullPath(path);
    var root = Path.GetPathRoot(full);
    if (!string.IsNullOrWhiteSpace(root)
        && full.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
            .Equals(root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar), StringComparison.OrdinalIgnoreCase))
        return root;

    return Path.GetFileName(full.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar))
        ?? full;
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
        // 不要在 LINQ lambda 中直接捕获 out 参数；C# 编译器会拒绝这种写法。
        var candidate = Path.GetFullPath(requested);
        var allowed = options.AllowedRoots
            .Where(root => !string.IsNullOrWhiteSpace(root))
            .Select(Path.GetFullPath)
            .Any(root => candidate.Equals(root, StringComparison.OrdinalIgnoreCase)
                || candidate.StartsWith(root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase));
        if (!allowed)
        {
            error = "目标路径不在 Agent 允许的目录范围内。";
            return false;
        }
        resolved = candidate;
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

static string UploadTemporaryPath(string destination) => destination + ".fds-upload.tmp";

static string UploadStatePath(string destination) => destination + ".fds-upload.state.json";

static string NormalizeSha256(string? value) => string.Concat((value ?? "").Where(char.IsAsciiHexDigit)).ToLowerInvariant();

static UploadState? ReadUploadState(string path)
{
    try
    {
        if (!File.Exists(path))
            return null;
        return System.Text.Json.JsonSerializer.Deserialize<UploadState>(File.ReadAllText(path));
    }
    catch
    {
        return null;
    }
}

static void WriteUploadState(string path, UploadState state)
{
    var temporary = path + ".tmp";
    var json = System.Text.Json.JsonSerializer.Serialize(state);
    File.WriteAllText(temporary, json, Encoding.UTF8);
    File.Move(temporary, path, overwrite: true);
}

static string ComputeSha256(string path)
{
    using var stream = File.OpenRead(path);
    return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
}

static (int Files, long Bytes) CopyTree(string source, string destination)
{
    if (File.Exists(source))
    {
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        if (!File.Exists(destination))
            File.Copy(source, destination, overwrite: false);
        return (1, new FileInfo(source).Length);
    }

    var destinationRoot = Path.GetFullPath(destination).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
    var sourceDirectories = Directory.EnumerateDirectories(source, "*", SearchOption.AllDirectories).ToArray();
    var sourceFiles = Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories).ToArray();
    Directory.CreateDirectory(destination);
    var files = 0;
    long bytes = 0;
    foreach (var directory in sourceDirectories)
    {
        if (directory.StartsWith(destinationRoot, StringComparison.OrdinalIgnoreCase))
            continue;
        var relative = Path.GetRelativePath(source, directory);
        Directory.CreateDirectory(Path.Combine(destination, relative));
    }
    foreach (var file in sourceFiles)
    {
        if (file.StartsWith(destinationRoot, StringComparison.OrdinalIgnoreCase))
            continue;
        var relative = Path.GetRelativePath(source, file);
        var target = Path.Combine(destination, relative);
        Directory.CreateDirectory(Path.GetDirectoryName(target)!);
        if (!File.Exists(target))
            File.Copy(file, target, overwrite: false);
        files++;
        bytes += new FileInfo(file).Length;
    }
    return (files, bytes);
}

public sealed record KillProcessRequest(string ImageName);
public sealed record RestartRequest(bool Force);
public sealed record FileInspectRequest(string Path, bool IncludeSha256 = true);
public sealed record FileBackupRequest(string SourcePath, string BackupPath);
public sealed record TreeBackupRequest(string SourcePath, string BackupPath);
public sealed record DirectoryRequest(string Path);
public sealed record RenameRequest(string Path, string NewPath);
public sealed record UploadInitRequest(string Path, long Size, string Sha256 = "");
public sealed record UploadCompleteRequest(string Path, string UploadId);
public sealed record UploadState(string UploadId, string Destination, long Size, string Sha256, long UploadedBytes);
