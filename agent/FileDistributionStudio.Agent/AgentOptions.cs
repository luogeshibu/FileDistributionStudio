namespace FileDistributionStudio.Agent;

public sealed class AgentOptions
{
    public string AgentName { get; set; } = "FileDistributionStudio Agent";
    public string AgentVersion { get; set; } = "0.1.0";
    public int ListenPort { get; set; } = 8743;
    public string CertificateThumbprint { get; set; } = "";
    public string Token { get; set; } = "";
    public string[] AllowedRoots { get; set; } = Array.Empty<string>();
    public long MaxUploadBytes { get; set; } = 20L * 1024 * 1024 * 1024;
}
