using System;
using System.Text.Json.Nodes;

namespace RevitMcp.Bridge
{
    /// <summary>
    /// Wire protocol between the MCP server process and the Revit add-in.
    /// One JSON object per line, UTF-8, over a per-user named pipe.
    ///   request:  {"id":1,"token":"...","method":"query_elements","params":{...}}
    ///   response: {"id":1,"ok":true,"result":{...}}
    ///             {"id":1,"ok":false,"error":{"code":"...","message":"..."}}
    /// </summary>
    public static class BridgeProtocol
    {
        public const int Version = 1;
        public const string PipePrefix = "RevitMcp-";
        /// <summary>Largest request line the add-in accepts.</summary>
        public const int MaxRequestBytes = 4 * 1024 * 1024;
    }

    public sealed class BridgeRequest
    {
        public JsonNode? Id { get; init; }
        public string Method { get; init; } = "";
        public JsonObject Params { get; init; } = new JsonObject();
    }

    /// <summary>An error that is reported to the MCP client as-is.</summary>
    public sealed class BridgeException : Exception
    {
        public string Code { get; }

        public BridgeException(string code, string message) : base(message)
        {
            Code = code;
        }
    }

    public static class ErrorCodes
    {
        public const string Unauthorized = "unauthorized";
        public const string BadRequest = "bad_request";
        public const string UnknownMethod = "unknown_method";
        public const string InvalidParams = "invalid_params";
        public const string NoDocument = "no_document";
        public const string NotFound = "not_found";
        public const string WriteDisabled = "write_disabled";
        public const string RevitBusy = "revit_busy";
        public const string RevitError = "revit_error";
        public const string Internal = "internal_error";
    }
}
