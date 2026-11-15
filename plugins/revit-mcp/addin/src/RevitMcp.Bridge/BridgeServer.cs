using System;
using System.IO;
using System.IO.Pipes;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

namespace RevitMcp.Bridge
{
    public delegate Task<JsonNode?> BridgeHandler(BridgeRequest request, CancellationToken cancellation);

    /// <summary>
    /// Named-pipe server. The pipe is created for the current user only, and every
    /// request must also carry the token from the instance discovery file.
    /// Nothing listens on the network.
    /// </summary>
    public sealed class BridgeServer : IDisposable
    {
        private const int MaxConnections = 8;

        private readonly string _pipeName;
        private readonly byte[] _token;
        private readonly BridgeHandler _handler;
        private readonly Action<string>? _log;
        private readonly CancellationTokenSource _stop = new CancellationTokenSource();
        private int _connections;

        public BridgeServer(string pipeName, string token, BridgeHandler handler, Action<string>? log = null)
        {
            _pipeName = pipeName;
            _token = Encoding.UTF8.GetBytes(token);
            _handler = handler;
            _log = log;
        }

        public static string NewToken()
        {
            return Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
        }

        public void Start()
        {
            // The first listener is created synchronously so that a client which
            // reads the discovery file right after startup can already connect.
            var first = CreatePipe();
            _ = Task.Run(() => AcceptLoop(first));
        }

        private NamedPipeServerStream CreatePipe()
        {
            return new NamedPipeServerStream(
                _pipeName,
                PipeDirection.InOut,
                NamedPipeServerStream.MaxAllowedServerInstances,
                PipeTransmissionMode.Byte,
                PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
        }

        private async Task AcceptLoop(NamedPipeServerStream pipe)
        {
            var next = pipe;
            while (!_stop.IsCancellationRequested)
            {
                try
                {
                    await next.WaitForConnectionAsync(_stop.Token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    next.Dispose();
                    return;
                }
                catch (Exception ex)
                {
                    _log?.Invoke("accept failed: " + ex.Message);
                    next.Dispose();
                    if (_stop.IsCancellationRequested) return;
                    try { await Task.Delay(500, _stop.Token).ConfigureAwait(false); }
                    catch (OperationCanceledException) { return; }
                    next = CreatePipe();
                    continue;
                }

                var connected = next;
                if (Interlocked.Increment(ref _connections) > MaxConnections)
                {
                    Interlocked.Decrement(ref _connections);
                    connected.Dispose();
                }
                else
                {
                    _ = Task.Run(() => ServeConnection(connected));
                }

                try
                {
                    next = CreatePipe();
                }
                catch (Exception ex)
                {
                    _log?.Invoke("cannot create pipe: " + ex.Message);
                    return;
                }
            }
            next.Dispose();
        }

        private async Task ServeConnection(NamedPipeServerStream pipe)
        {
            try
            {
                using (pipe)
                {
                    var reader = new LineReader(pipe, BridgeProtocol.MaxRequestBytes);
                    while (!_stop.IsCancellationRequested)
                    {
                        string? line = await reader.ReadLineAsync(_stop.Token).ConfigureAwait(false);
                        if (line == null) break;
                        if (line.Length == 0) continue;

                        JsonObject response = await HandleLine(line).ConfigureAwait(false);
                        byte[] bytes = Encoding.UTF8.GetBytes(response.ToJsonString() + "\n");
                        await pipe.WriteAsync(bytes, 0, bytes.Length, _stop.Token).ConfigureAwait(false);
                        await pipe.FlushAsync(_stop.Token).ConfigureAwait(false);
                    }
                }
            }
            catch (OperationCanceledException) { }
            catch (IOException) { }
            catch (Exception ex)
            {
                _log?.Invoke("connection failed: " + ex.Message);
            }
            finally
            {
                Interlocked.Decrement(ref _connections);
            }
        }

        private async Task<JsonObject> HandleLine(string line)
        {
            JsonNode? id = null;
            try
            {
                JsonObject? message;
                try
                {
                    message = JsonNode.Parse(line) as JsonObject;
                }
                catch (JsonException)
                {
                    message = null;
                }
                if (message == null)
                    throw new BridgeException(ErrorCodes.BadRequest, "Request is not a JSON object.");

                id = message["id"]?.DeepClone();

                string token = (message["token"] as JsonValue)?.TryGetValue(out string? t) == true ? t! : "";
                if (!CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(token), _token))
                    throw new BridgeException(ErrorCodes.Unauthorized, "Missing or wrong token.");

                string method = (message["method"] as JsonValue)?.TryGetValue(out string? m) == true ? m! : "";
                if (method.Length == 0)
                    throw new BridgeException(ErrorCodes.BadRequest, "Request has no method.");

                var request = new BridgeRequest
                {
                    Id = id,
                    Method = method,
                    Params = message["params"]?.DeepClone() as JsonObject ?? new JsonObject(),
                };

                JsonNode? result = await _handler(request, _stop.Token).ConfigureAwait(false);
                return new JsonObject
                {
                    ["id"] = id?.DeepClone(),
                    ["ok"] = true,
                    ["result"] = result,
                };
            }
            catch (BridgeException ex)
            {
                return Error(id, ex.Code, ex.Message);
            }
            catch (OperationCanceledException)
            {
                return Error(id, ErrorCodes.RevitBusy, "The add-in is shutting down.");
            }
            catch (Exception ex)
            {
                _log?.Invoke("handler failed: " + ex);
                return Error(id, ErrorCodes.Internal, ex.GetType().Name + ": " + ex.Message);
            }
        }

        private static JsonObject Error(JsonNode? id, string code, string message)
        {
            return new JsonObject
            {
                ["id"] = id?.DeepClone(),
                ["ok"] = false,
                ["error"] = new JsonObject { ["code"] = code, ["message"] = message },
            };
        }

        public void Dispose()
        {
            _stop.Cancel();
        }

        /// <summary>Reads '\n'-terminated UTF-8 lines with an upper bound on line size.</summary>
        private sealed class LineReader
        {
            private readonly Stream _stream;
            private readonly int _maxBytes;
            private readonly byte[] _buffer = new byte[16 * 1024];
            private readonly MemoryStream _line = new MemoryStream();
            private int _start;
            private int _end;

            public LineReader(Stream stream, int maxBytes)
            {
                _stream = stream;
                _maxBytes = maxBytes;
            }

            public async Task<string?> ReadLineAsync(CancellationToken cancellation)
            {
                while (true)
                {
                    for (int i = _start; i < _end; i++)
                    {
                        if (_buffer[i] != (byte)'\n') continue;
                        _line.Write(_buffer, _start, i - _start);
                        _start = i + 1;
                        string text = Encoding.UTF8.GetString(_line.GetBuffer(), 0, (int)_line.Length);
                        _line.SetLength(0);
                        return text.TrimEnd('\r');
                    }

                    _line.Write(_buffer, _start, _end - _start);
                    _start = _end = 0;
                    if (_line.Length > _maxBytes)
                        throw new IOException("Request line is too long.");

                    int read = await _stream.ReadAsync(_buffer, 0, _buffer.Length, cancellation).ConfigureAwait(false);
                    if (read == 0) return null;
                    _end = read;
                }
            }
        }
    }
}
