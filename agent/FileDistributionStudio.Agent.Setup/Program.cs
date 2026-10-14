using System.Diagnostics;
using System.Reflection;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;

const string ServiceName = "FileDistributionStudioAgent";
const string ServiceDisplayName = "FileDistributionStudio Agent";
const int DefaultPort = 8743;
const string RuntimeDirectoryName = ".fds-agent";
const string ServiceExecutableName = "FileDistributionStudio.Agent.Service.exe";

try
{
    var serviceCommand = GetServiceCommand(args);
    if (serviceCommand is not null)
    {
        RunServiceCommand(serviceCommand);
        Pause($"Agent {serviceCommand} completed.");
        return;
    }

    if (args.Any(arg => string.Equals(arg, "--uninstall", StringComparison.OrdinalIgnoreCase)))
    {
        Uninstall();
        Pause("Uninstall completed.");
        return;
    }

    var explicitAgentSource = GetOption(args, "--agent-exe");
    var temporaryAgent = string.IsNullOrWhiteSpace(explicitAgentSource);
    var agentSource = temporaryAgent
        ? ExtractEmbeddedAgent()
        : Path.GetFullPath(explicitAgentSource!);
    if (!File.Exists(agentSource))
        throw new FileNotFoundException("The embedded Agent payload could not be found.", agentSource);

    // By default install beside the launcher EXE. Runtime files are kept in a
    // hidden child directory so the user only needs one visible entry point.
    var installRoot = GetOption(args, "--install-root") ?? AppContext.BaseDirectory;
    var requestedRoot = GetOption(args, "--allowed-root");
    var requestedPort = GetOption(args, "--port");
    var currentConfigPath = Path.Combine(
        Path.GetFullPath(installRoot), RuntimeDirectoryName, "appsettings.json");
    var reuseConfig = File.Exists(currentConfigPath);
    try
    {
        Install(agentSource, installRoot, requestedRoot, requestedPort,
            GetOption(args, "--token"), reuseConfig);
        Pause("Installation completed. Press Enter to close.");
    }
    finally
    {
        if (temporaryAgent)
            TryDelete(agentSource);
    }
}
catch (Exception ex)
{
    Console.Error.WriteLine();
    Console.Error.WriteLine("FDS Agent failed:");
    Console.Error.WriteLine(ex.Message);
    Pause("Press Enter to close.");
    Environment.ExitCode = 1;
}

static void Install(string sourceExe, string installRoot, string? requestedRoot,
    string? requestedPort, string? suppliedToken, bool reuseConfig)
{
    var fullInstallRoot = Path.GetFullPath(installRoot);
    var runtimeRoot = Path.Combine(fullInstallRoot, RuntimeDirectoryName);
    var installedAgent = Path.Combine(runtimeRoot, ServiceExecutableName);
    var configPath = Path.Combine(runtimeRoot, "appsettings.json");

    // Stop the previous service before replacing its executable. This makes
    // reinstall and upgrade safe when the Agent is already running.
    RemoveService();
    Directory.CreateDirectory(fullInstallRoot);
    Directory.CreateDirectory(runtimeRoot);
    HideRuntimeDirectory(runtimeRoot);
    PrepareConfigurationFile(configPath);

    var existing = reuseConfig ? ReadConfiguration(configPath) : null;
    var port = ResolvePort(requestedPort, existing?.Agent.ListenPort);
    var allowedRoots = ResolveAllowedRoots(requestedRoot, existing?.Agent.AllowedRoots, reuseConfig);
    foreach (var root in allowedRoots)
        Directory.CreateDirectory(root);
    CopyAgentWithRetry(sourceExe, installedAgent);

    var token = !string.IsNullOrWhiteSpace(suppliedToken)
        ? suppliedToken.Trim()
        : reuseConfig && !string.IsNullOrWhiteSpace(existing?.Agent.Token)
            ? existing!.Agent.Token.Trim()
            : Convert.ToBase64String(RandomNumberGenerator.GetBytes(32));
    if (string.IsNullOrWhiteSpace(token))
        throw new InvalidOperationException("Pairing token cannot be empty.");

    using var certificate = reuseConfig
        ? LoadExistingCertificate(existing?.Agent.CertificateThumbprint) ?? CreateAndInstallCertificate()
        : CreateAndInstallCertificate();
    var maxUploadBytes = existing?.Agent.MaxUploadBytes is > 0
        ? existing.Agent.MaxUploadBytes
        : 20L * 1024 * 1024 * 1024;
    var config = new
    {
        Agent = new
        {
            AgentName = "FileDistributionStudio Agent",
            AgentVersion = "0.1.0",
            ListenPort = port,
            CertificateThumbprint = certificate.Thumbprint,
            Token = token,
            AllowedRoots = allowedRoots,
            MaxUploadBytes = maxUploadBytes,
        },
    };
    BackupConfiguration(configPath);
    File.WriteAllText(configPath, JsonSerializer.Serialize(config, new JsonSerializerOptions { WriteIndented = true }));
    ProtectConfiguration(configPath);
    ConfigureFirewall(port);
    RegisterAndStartService(installedAgent);

    var certificateSha256 = Convert.ToHexString(SHA256.HashData(certificate.RawData));
    var dnsHostName = System.Net.Dns.GetHostName();
    var localIpv4Addresses = GetLocalIpv4Addresses();
    var addressFilePart = localIpv4Addresses.Length == 0
        ? "no-ip"
        : string.Join("-", localIpv4Addresses);
    var connectionInfoPath = Path.Combine(
        fullInstallRoot, $"FDS-Agent-Connection-{addressFilePart}.txt");
    // Remove the old fixed-name file created by earlier Agent versions so the
    // user sees the new IP-bearing file as the single current connection file.
    TryDelete(Path.Combine(fullInstallRoot, "FDS-Agent-Connection.txt"));
    WriteConnectionInfo(connectionInfoPath, dnsHostName, Environment.MachineName,
        localIpv4Addresses, certificateSha256, token, port, configPath);
    Console.WriteLine();
    Console.WriteLine("FDS Agent installation completed.");
    Console.WriteLine($"Target host: {dnsHostName}");
    Console.WriteLine($"Windows computer name: {Environment.MachineName}");
    Console.WriteLine($"Local IPv4 addresses: {FormatAddresses(localIpv4Addresses)}");
    Console.WriteLine($"HTTPS port: {port}");
    Console.WriteLine($"Allowed roots: {string.Join(", ", allowedRoots)}");
    Console.WriteLine($"Configuration: {(reuseConfig ? "reused" : "newly generated")}");
    Console.WriteLine($"Configuration path: {configPath}");
    Console.WriteLine($"Certificate SHA-256 fingerprint: {certificateSha256}");
    Console.WriteLine($"Pairing token: {token}");
    Console.WriteLine($"Connection information: {connectionInfoPath}");
    Console.WriteLine();
    Console.WriteLine("Enter the port, certificate fingerprint, and pairing token in the desktop app.");
}

static void WriteConnectionInfo(
    string path, string dnsHostName, string computerName, IReadOnlyList<string> localIpv4Addresses,
    string certificateSha256,
    string token, int port, string configPath)
{
    var addressText = localIpv4Addresses.Count == 0
        ? "(no non-loopback IPv4 address found)"
        : string.Join(Environment.NewLine, localIpv4Addresses.Select(address => $"- {address}"));
    var content = $"""
FileDistributionStudio Agent connection information
===================================================

Target DNS host name: {dnsHostName}
Windows computer name: {computerName}
Local IPv4 addresses (excluding loopback):
{addressText}
HTTPS port: {port}
Certificate SHA-256 fingerprint: {certificateSha256}
Pairing token: {token}

Desktop application setup:
1. Add or select this Windows host.
2. Select the FDS Agent (HTTPS) channel.
3. Enter the HTTPS port and certificate SHA-256 fingerprint above.
4. Use the Pairing token when setting the Agent token.

Agent configuration file: {configPath}
Generated at (UTC): {DateTime.UtcNow:O}

WARNING: This file contains the Agent pairing token. Keep it private.
""";
    File.WriteAllText(path, content, new System.Text.UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
}

static string[] GetLocalIpv4Addresses()
{
    try
    {
        return NetworkInterface.GetAllNetworkInterfaces()
            .Where(network => network.OperationalStatus == OperationalStatus.Up)
            .SelectMany(network => network.GetIPProperties().UnicastAddresses)
            .Select(unicast => unicast.Address)
            .Where(address => address.AddressFamily == AddressFamily.InterNetwork
                && !IPAddress.IsLoopback(address))
            .Select(address => address.ToString())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(address => address, StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }
    catch
    {
        // Network enumeration is informational only; installation must still
        // complete when a network adapter is temporarily unavailable.
        return Array.Empty<string>();
    }
}

static string FormatAddresses(IReadOnlyList<string> addresses) =>
    addresses.Count == 0 ? "(none)" : string.Join(", ", addresses);

static string? GetServiceCommand(string[] args)
{
    foreach (var arg in args)
    {
        var command = arg.Trim().TrimStart('-').ToLowerInvariant();
        if (command is "start" or "stop" or "restart" or "status")
            return command;
    }

    return null;
}

static void RunServiceCommand(string command)
{
    switch (command)
    {
        case "start":
            RunTool("sc.exe", "start", ServiceName);
            Console.WriteLine("FDS Agent service started.");
            break;
        case "stop":
            RunTool("sc.exe", "stop", ServiceName);
            Console.WriteLine("FDS Agent service stopped.");
            break;
        case "restart":
            RunToolAllowFailure("sc.exe", "stop", ServiceName);
            Thread.Sleep(1000);
            RunTool("sc.exe", "start", ServiceName);
            Console.WriteLine("FDS Agent service restarted.");
            break;
        case "status":
            Console.WriteLine(QueryServiceState() ?? "FDS Agent service is not installed.");
            break;
        default:
            throw new ArgumentOutOfRangeException(nameof(command));
    }
}

static string[] ResolveAllowedRoots(string? requestedRoot, string[]? existingRoots, bool reuseConfig)
{
    if (!string.IsNullOrWhiteSpace(requestedRoot))
        return new[] { Path.GetFullPath(requestedRoot.Trim()) };
    if (reuseConfig && existingRoots is { Length: > 0 })
        return existingRoots.Where(root => !string.IsNullOrWhiteSpace(root))
            .Select(Path.GetFullPath)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
    return GetDefaultAllowedRoots();
}

static int ResolvePort(string? requestedPort, int? existingPort)
{
    if (!string.IsNullOrWhiteSpace(requestedPort))
        return ParsePort(requestedPort);
    if (existingPort is >= 1 and <= 65535)
        return existingPort.Value;
    return DefaultPort;
}

static AgentConfigurationFile? ReadConfiguration(string configPath)
{
    if (!File.Exists(configPath))
        return null;
    try
    {
        return JsonSerializer.Deserialize<AgentConfigurationFile>(
            File.ReadAllText(configPath),
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
    }
    catch (Exception ex)
    {
        throw new InvalidOperationException($"Existing Agent configuration is invalid: {configPath}. {ex.Message}", ex);
    }
}

static void BackupConfiguration(string configPath)
{
    if (!File.Exists(configPath))
        return;
    var backupPath = configPath + ".backup-" + DateTime.UtcNow.ToString("yyyyMMddHHmmss");
    File.Copy(configPath, backupPath, overwrite: false);
}

static X509Certificate2? LoadExistingCertificate(string? thumbprint)
{
    var normalized = new string((thumbprint ?? string.Empty)
        .Where(char.IsAsciiHexDigit).ToArray());
    if (string.IsNullOrWhiteSpace(normalized))
        return null;

    using var store = new X509Store(StoreName.My, StoreLocation.LocalMachine);
    store.Open(OpenFlags.ReadOnly);
    var matches = store.Certificates.Find(X509FindType.FindByThumbprint, normalized, validOnly: false);
    foreach (var certificate in matches)
    {
        if (certificate.HasPrivateKey && certificate.NotAfter.ToUniversalTime() > DateTime.UtcNow.AddMinutes(5))
            return new X509Certificate2(certificate);
    }
    return null;
}

static string[] GetDefaultAllowedRoots()
{
    var roots = DriveInfo.GetDrives()
        .Where(drive => drive.DriveType is DriveType.Fixed or DriveType.Removable)
        .Select(drive => drive.RootDirectory.FullName)
        .Distinct(StringComparer.OrdinalIgnoreCase)
        .ToArray();
    if (roots.Length > 0)
        return roots;

    var systemDrive = Environment.GetEnvironmentVariable("SystemDrive");
    return new[] { string.IsNullOrWhiteSpace(systemDrive) ? @"C:\" : systemDrive + @"\" };
}

static string ExtractEmbeddedAgent()
{
    const string resourceName = "FDS.Agent.Payload.exe";
    var resource = Assembly.GetExecutingAssembly().GetManifestResourceStream(resourceName)
        ?? throw new InvalidOperationException("The setup program does not contain an Agent payload.");
    var temporaryPath = Path.Combine(Path.GetTempPath(), $"fds-agent-{Guid.NewGuid():N}.exe");
    using (resource)
    using (var output = new FileStream(temporaryPath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
    {
        resource.CopyTo(output);
    }
    return temporaryPath;
}

static void CopyAgentWithRetry(string sourceExe, string destinationExe)
{
    Exception? lastError = null;
    for (var attempt = 0; attempt < 12; attempt++)
    {
        try
        {
            File.Copy(sourceExe, destinationExe, overwrite: true);
            return;
        }
        catch (IOException ex)
        {
            lastError = ex;
            Thread.Sleep(500);
        }
    }

    throw new IOException(
        $"The installed Agent executable is still locked: {destinationExe}",
        lastError);
}

static X509Certificate2 CreateAndInstallCertificate()
{
    var machineName = Environment.MachineName;
    var dnsHostName = System.Net.Dns.GetHostName();
    using var rsa = RSA.Create(2048);
    var request = new CertificateRequest(
        $"CN={machineName}",
        rsa,
        HashAlgorithmName.SHA256,
        RSASignaturePadding.Pkcs1);
    request.CertificateExtensions.Add(new X509BasicConstraintsExtension(false, false, 0, false));
    request.CertificateExtensions.Add(new X509KeyUsageExtension(
        X509KeyUsageFlags.DigitalSignature | X509KeyUsageFlags.KeyEncipherment,
        critical: true));
    request.CertificateExtensions.Add(new X509SubjectKeyIdentifierExtension(request.PublicKey, false));
    var san = new SubjectAlternativeNameBuilder();
    san.AddDnsName(machineName);
    if (!dnsHostName.Equals(machineName, StringComparison.OrdinalIgnoreCase))
        san.AddDnsName(dnsHostName);
    san.AddDnsName("localhost");
    request.CertificateExtensions.Add(san.Build());

    using var generated = request.CreateSelfSigned(
        DateTimeOffset.UtcNow.AddMinutes(-5),
        DateTimeOffset.UtcNow.AddYears(3));
    var pfx = generated.Export(X509ContentType.Pfx);
    var certificate = new X509Certificate2(
        pfx,
        (string?)null,
        X509KeyStorageFlags.MachineKeySet | X509KeyStorageFlags.PersistKeySet);

    using var store = new X509Store(StoreName.My, StoreLocation.LocalMachine);
    store.Open(OpenFlags.ReadWrite);
    store.Add(certificate);
    return certificate;
}

static void ProtectConfiguration(string configPath)
{
    RunTool("icacls.exe", configPath, "/inheritance:r");
    RunTool("icacls.exe", configPath, "/grant:r", "*S-1-5-18:R", "*S-1-5-32-544:R");
}

static void PrepareConfigurationFile(string configPath)
{
    if (!File.Exists(configPath))
        return;

    // A previous installation intentionally made this file read-only for
    // normal users. Restore inherited admin write access before upgrading.
    RunTool("icacls.exe", configPath, "/reset");
    File.SetAttributes(configPath, FileAttributes.Normal);
}

static void HideRuntimeDirectory(string runtimeRoot)
{
    var directory = new DirectoryInfo(runtimeRoot);
    directory.Attributes |= FileAttributes.Hidden;
}

static void ConfigureFirewall(int port)
{
    RunToolAllowFailure("netsh.exe", "advfirewall", "firewall", "delete", "rule", $"name={ServiceDisplayName}");
    RunTool(
        "netsh.exe", "advfirewall", "firewall", "add", "rule",
        $"name={ServiceDisplayName}", "dir=in", "action=allow", "protocol=TCP",
        $"localport={port}", "profile=Domain,Private");
}

static void RemoveService()
{
    RunToolAllowFailure("sc.exe", "stop", ServiceName);
    RunToolAllowFailure("sc.exe", "delete", ServiceName);
    // Windows may keep the service process alive briefly after STOP returns.
    // Give it time to release the executable before copying the new payload.
    for (var attempt = 0; attempt < 10; attempt++)
    {
        Thread.Sleep(500);
        var serviceState = QueryServiceState();
        if (serviceState is null || serviceState.Contains("1060", StringComparison.OrdinalIgnoreCase))
            break;
    }
}

static void RegisterAndStartService(string installedAgent)
{
    RunTool(
        "sc.exe", "create", ServiceName,
        "binPath=", $"\"{installedAgent}\"",
        "start=", "auto", "DisplayName=", ServiceDisplayName);
    // Start after the network stack is available and recover automatically
    // if the Agent process exits unexpectedly.
    RunTool("sc.exe", "config", ServiceName, "start=", "delayed-auto");
    RunTool("sc.exe", "description", ServiceName, "FileDistributionStudio HTTPS target agent.");
    RunTool(
        "sc.exe", "failure", ServiceName,
        "reset=", "86400",
        "actions=", "restart/5000/restart/10000/restart/30000");
    RunTool("sc.exe", "failureflag", ServiceName, "1");
    RunTool("sc.exe", "start", ServiceName);
}

static string? QueryServiceState()
{
    try
    {
        return RunToolCapture("sc.exe", "query", ServiceName);
    }
    catch
    {
        return null;
    }
}

static void Uninstall()
{
    RunToolAllowFailure("sc.exe", "stop", ServiceName);
    RunToolAllowFailure("sc.exe", "delete", ServiceName);
    RunToolAllowFailure("netsh.exe", "advfirewall", "firewall", "delete", "rule", $"name={ServiceDisplayName}");
    Console.WriteLine("The FDS Agent service and firewall rule were removed.");
}

static void RunTool(string fileName, params string[] arguments)
{
    RunToolInternal(fileName, arguments, allowFailure: false);
}

static void RunToolAllowFailure(string fileName, params string[] arguments)
{
    RunToolInternal(fileName, arguments, allowFailure: true);
}

static void RunToolInternal(string fileName, string[] arguments, bool allowFailure)
{
    using var process = new Process
    {
        StartInfo = new ProcessStartInfo
        {
            FileName = fileName,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        },
    };
    foreach (var argument in arguments)
        process.StartInfo.ArgumentList.Add(argument);
    process.Start();
    var output = process.StandardOutput.ReadToEnd();
    var error = process.StandardError.ReadToEnd();
    process.WaitForExit();
    if (process.ExitCode != 0 && !allowFailure)
    {
        var detail = string.IsNullOrWhiteSpace(error) ? output : error;
        throw new InvalidOperationException($"{fileName} failed ({process.ExitCode}): {detail.Trim()}");
    }
}

static string RunToolCapture(string fileName, params string[] arguments)
{
    using var process = new Process
    {
        StartInfo = new ProcessStartInfo
        {
            FileName = fileName,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        },
    };
    foreach (var argument in arguments)
        process.StartInfo.ArgumentList.Add(argument);
    process.Start();
    var output = process.StandardOutput.ReadToEnd();
    var error = process.StandardError.ReadToEnd();
    process.WaitForExit();
    if (process.ExitCode != 0)
        return string.IsNullOrWhiteSpace(error) ? output : error;
    return output;
}

static int ParsePort(string? value)
{
    if (string.IsNullOrWhiteSpace(value)) return DefaultPort;
    if (!int.TryParse(value, out var port) || port is < 1 or > 65535)
        throw new ArgumentException("Port must be between 1 and 65535.");
    return port;
}

static string? GetOption(string[] args, string name)
{
    for (var index = 0; index < args.Length - 1; index++)
    {
        if (string.Equals(args[index], name, StringComparison.OrdinalIgnoreCase))
            return args[index + 1];
    }
    return null;
}

static void Pause(string message)
{
    if (!Console.IsInputRedirected)
    {
        Console.WriteLine(message);
        Console.ReadLine();
    }
}

static void TryDelete(string path)
{
    try
    {
        if (File.Exists(path)) File.Delete(path);
    }
    catch
    {
        // The temporary payload is best-effort cleanup only.
    }
}

sealed class AgentConfigurationFile
{
    public AgentConfiguration Agent { get; set; } = new();
}

sealed class AgentConfiguration
{
    public string AgentName { get; set; } = "FileDistributionStudio Agent";
    public string AgentVersion { get; set; } = "0.1.0";
    public int ListenPort { get; set; } = 8743;
    public string CertificateThumbprint { get; set; } = "";
    public string Token { get; set; } = "";
    public string[] AllowedRoots { get; set; } = Array.Empty<string>();
    public long MaxUploadBytes { get; set; } = 20L * 1024 * 1024 * 1024;
}
