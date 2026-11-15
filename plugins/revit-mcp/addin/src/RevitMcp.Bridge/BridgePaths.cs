using System;
using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace RevitMcp.Bridge
{
    /// <summary>
    /// Per-user files shared with the MCP server:
    ///   %LOCALAPPDATA%\RevitMcp\instances\&lt;pid&gt;.json  - one per running Revit (pipe name + token)
    ///   %LOCALAPPDATA%\RevitMcp\settings.json           - add-in settings
    /// REVIT_MCP_HOME overrides the folder (used by tests).
    /// </summary>
    public static class BridgePaths
    {
        public static string Home
        {
            get
            {
                string? custom = Environment.GetEnvironmentVariable("REVIT_MCP_HOME");
                if (!string.IsNullOrWhiteSpace(custom)) return custom!;
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "RevitMcp");
            }
        }

        public static string InstancesDir => Path.Combine(Home, "instances");
        public static string SettingsFile => Path.Combine(Home, "settings.json");
        public static string LogFile => Path.Combine(Home, "addin.log");
    }

    public sealed class InstanceFile : IDisposable
    {
        private readonly string _path;

        private InstanceFile(string path)
        {
            _path = path;
        }

        public static InstanceFile Write(int pid, string pipeName, string token, string revitVersion, string addinVersion)
        {
            Directory.CreateDirectory(BridgePaths.InstancesDir);
            string path = Path.Combine(BridgePaths.InstancesDir, pid + ".json");
            var json = new JsonObject
            {
                ["protocol"] = BridgeProtocol.Version,
                ["pid"] = pid,
                ["pipe"] = pipeName,
                ["token"] = token,
                ["revitVersion"] = revitVersion,
                ["addinVersion"] = addinVersion,
                ["startedAt"] = DateTime.UtcNow.ToString("o"),
            };
            File.WriteAllText(path, json.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
            return new InstanceFile(path);
        }

        public void Dispose()
        {
            try { File.Delete(_path); } catch { /* best effort */ }
        }
    }

    public sealed class BridgeSettings
    {
        /// <summary>Model-changing commands are refused unless this is on. Off by default.</summary>
        public bool AllowWrite { get; set; }

        /// <summary>How long a request waits for Revit to become idle.</summary>
        public int RevitTimeoutSeconds { get; set; } = 120;

        public static BridgeSettings Load()
        {
            var settings = new BridgeSettings();
            try
            {
                if (File.Exists(BridgePaths.SettingsFile) &&
                    JsonNode.Parse(File.ReadAllText(BridgePaths.SettingsFile)) is JsonObject json)
                {
                    if (json["allowWrite"] is JsonValue w && w.TryGetValue(out bool allow)) settings.AllowWrite = allow;
                    if (json["revitTimeoutSeconds"] is JsonValue t && t.TryGetValue(out int seconds) && seconds > 0)
                        settings.RevitTimeoutSeconds = seconds;
                }
            }
            catch
            {
                // A broken settings file must not stop Revit from starting: fall back to defaults.
            }
            return settings;
        }

        public void Save()
        {
            Directory.CreateDirectory(BridgePaths.Home);
            var json = new JsonObject
            {
                ["allowWrite"] = AllowWrite,
                ["revitTimeoutSeconds"] = RevitTimeoutSeconds,
            };
            File.WriteAllText(BridgePaths.SettingsFile, json.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        }
    }
}
