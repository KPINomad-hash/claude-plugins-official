using System;
using System.Diagnostics;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using RevitMcp.Bridge;

// Fake Revit: publishes an instance file and answers a few commands.
int pid = Process.GetCurrentProcess().Id;
string pipe = BridgeProtocol.PipePrefix + pid;
string token = BridgeServer.NewToken();
var settings = BridgeSettings.Load();

Task<JsonNode?> Handle(BridgeRequest request, CancellationToken cancellation)
{
    switch (request.Method)
    {
        case "ping":
        case "status":
            return Task.FromResult<JsonNode?>(new JsonObject
            {
                ["pid"] = pid,
                ["revitVersion"] = "2026",
                ["addinVersion"] = "test",
                ["writeEnabled"] = settings.AllowWrite,
                ["activeDocument"] = "Fake.rvt",
            });
        case "query_elements":
            return Task.FromResult<JsonNode?>(new JsonObject
            {
                ["total"] = 1,
                ["echo"] = request.Params.DeepClone(),
            });
        case "delete_elements":
            if (!settings.AllowWrite)
                throw new BridgeException(ErrorCodes.WriteDisabled, "Model changes are switched off in Revit.");
            return Task.FromResult<JsonNode?>(new JsonObject { ["totalDeleted"] = 0 });
        case "crash":
            throw new InvalidOperationException("boom");
        default:
            throw new BridgeException(ErrorCodes.UnknownMethod, "The add-in has no command '" + request.Method + "'.");
    }
}

using var server = new BridgeServer(pipe, token, Handle, Console.Error.WriteLine);
server.Start();
using var file = InstanceFile.Write(pid, pipe, token, "2026", "test");
Console.WriteLine("ready " + pid);
Console.In.ReadLine(); // stop when the parent closes stdin
