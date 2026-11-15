using System;
using System.Collections.Concurrent;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using Autodesk.Revit.UI;
using RevitMcp.Bridge;

namespace RevitMcp.Addin
{
    /// <summary>
    /// Runs work on Revit's main thread. The Revit API may only be called from
    /// there, so pipe threads queue a job and raise an ExternalEvent; Revit runs
    /// the job the next time it is idle.
    /// </summary>
    public sealed class RevitExecutor : IExternalEventHandler
    {
        private sealed class Job
        {
            public Func<UIApplication, JsonNode?> Work = _ => null;
            public TaskCompletionSource<JsonNode?> Done =
                new TaskCompletionSource<JsonNode?>(TaskCreationOptions.RunContinuationsAsynchronously);
            // 0 = queued, 1 = running or finished, 2 = abandoned by the caller
            public int State;
        }

        private readonly ConcurrentQueue<Job> _jobs = new ConcurrentQueue<Job>();
        private ExternalEvent? _event;

        public void Attach(ExternalEvent externalEvent)
        {
            _event = externalEvent;
        }

        public async Task<JsonNode?> Run(Func<UIApplication, JsonNode?> work, TimeSpan timeout, CancellationToken cancellation)
        {
            if (_event == null)
                throw new BridgeException(ErrorCodes.Internal, "The add-in is not initialised.");

            var job = new Job { Work = work };
            _jobs.Enqueue(job);
            _event.Raise();

            Task finished = await Task.WhenAny(job.Done.Task, Task.Delay(timeout, cancellation)).ConfigureAwait(false);
            if (finished != job.Done.Task)
            {
                // Only give up if Revit has not started the job: a running job
                // must be allowed to finish and report its real outcome.
                if (Interlocked.CompareExchange(ref job.State, 2, 0) == 0)
                {
                    cancellation.ThrowIfCancellationRequested();
                    throw new BridgeException(ErrorCodes.RevitBusy,
                        "Revit did not become idle within " + (int)timeout.TotalSeconds +
                        " s. It is probably showing a dialog, running a command, or the user is editing. " +
                        "Nothing was changed. Ask the user to finish or close the dialog, then retry.");
                }
            }
            return await job.Done.Task.ConfigureAwait(false);
        }

        public void Execute(UIApplication app)
        {
            while (_jobs.TryDequeue(out Job? job))
            {
                if (Interlocked.CompareExchange(ref job.State, 1, 0) != 0) continue;
                try
                {
                    job.Done.TrySetResult(job.Work(app));
                }
                catch (BridgeException ex)
                {
                    job.Done.TrySetException(ex);
                }
                catch (Exception ex)
                {
                    Log.Write("command failed: " + ex);
                    job.Done.TrySetException(new BridgeException(ErrorCodes.RevitError,
                        ex.GetType().Name + ": " + ex.Message));
                }
            }
        }

        public string GetName()
        {
            return "Revit MCP bridge";
        }
    }
}
