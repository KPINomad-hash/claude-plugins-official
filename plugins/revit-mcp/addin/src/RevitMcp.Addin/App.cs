using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;
using Color = System.Windows.Media.Color;
using FormattedText = System.Windows.Media.FormattedText;
using Point = System.Windows.Point;
using System.Windows.Media.Imaging;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using RevitMcp.Bridge;

namespace RevitMcp.Addin
{
    public sealed class App : IExternalApplication
    {
        internal static BridgeSettings Settings = new BridgeSettings();
        internal static string PipeName = "";
        internal static string RevitVersion = "";
        internal static PushButton? WriteButton;

        private readonly RevitExecutor _executor = new RevitExecutor();
        private BridgeServer? _server;
        private InstanceFile? _instanceFile;

        internal static string AddinVersion =>
            Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "0.0.0";

        public Result OnStartup(UIControlledApplication application)
        {
            try
            {
                Settings = BridgeSettings.Load();
                RevitVersion = application.ControlledApplication.VersionNumber;

                _executor.Attach(ExternalEvent.Create(_executor));

                int pid = Process.GetCurrentProcess().Id;
                PipeName = BridgeProtocol.PipePrefix + pid;
                string token = BridgeServer.NewToken();

                _server = new BridgeServer(PipeName, token, Handle, Log.Write);
                _server.Start();
                _instanceFile = InstanceFile.Write(pid, PipeName, token, RevitVersion, AddinVersion);

                CreateRibbon(application);
                Log.Write("started, Revit " + RevitVersion + ", pipe " + PipeName);
                return Result.Succeeded;
            }
            catch (Exception ex)
            {
                // Never block Revit from starting because of this add-in.
                Log.Write("startup failed: " + ex);
                return Result.Succeeded;
            }
        }

        public Result OnShutdown(UIControlledApplication application)
        {
            _instanceFile?.Dispose();
            _server?.Dispose();
            return Result.Succeeded;
        }

        private Task<JsonNode?> Handle(BridgeRequest request, CancellationToken cancellation)
        {
            // Answered on the pipe thread, so it works even while Revit is busy.
            if (request.Method == "ping")
            {
                return Task.FromResult<JsonNode?>(new JsonObject
                {
                    ["pid"] = Process.GetCurrentProcess().Id,
                    ["revitVersion"] = RevitVersion,
                    ["addinVersion"] = AddinVersion,
                    ["writeEnabled"] = Settings.AllowWrite,
                });
            }

            if (!CommandRegistry.TryGet(request.Method, out CommandInfo? command))
                throw new BridgeException(ErrorCodes.UnknownMethod,
                    "The add-in has no command '" + request.Method + "'. The add-in may be older than the MCP server.");

            if (command!.Writes && !Settings.AllowWrite)
                throw new BridgeException(ErrorCodes.WriteDisabled,
                    "Model changes are switched off in Revit. Ask the user to turn on " +
                    "'Allow changes' on the Add-Ins > Revit MCP ribbon panel, then retry. Do not try to work around this.");

            return _executor.Run(
                app => command.Run(new CommandContext(app), request.Params),
                TimeSpan.FromSeconds(Settings.RevitTimeoutSeconds),
                cancellation);
        }

        private static void CreateRibbon(UIControlledApplication application)
        {
            RibbonPanel panel = application.CreateRibbonPanel("Revit MCP");
            string assembly = Assembly.GetExecutingAssembly().Location;

            var status = (PushButton)panel.AddItem(
                new PushButtonData("RevitMcpStatus", "Status", assembly, typeof(StatusCommand).FullName));
            status.ToolTip = "Shows whether AI assistants can reach this Revit session over MCP.";
            status.LargeImage = Icon("i", Color.FromRgb(0x2F, 0x6F, 0xB7));

            WriteButton = (PushButton)panel.AddItem(
                new PushButtonData("RevitMcpWrite", "Allow\nchanges", assembly, typeof(ToggleWriteCommand).FullName));
            WriteButton.ToolTip =
                "When off (default), AI assistants can only read the model. " +
                "When on, they can create, edit and delete elements. Every change is a normal undoable transaction.";
            UpdateWriteButton();
        }

        internal static void UpdateWriteButton()
        {
            if (WriteButton == null) return;
            bool on = Settings.AllowWrite;
            WriteButton.ItemText = on ? "Changes\nON" : "Changes\nOFF";
            WriteButton.LargeImage = Icon(on ? "W" : "R",
                on ? Color.FromRgb(0xC6, 0x4B, 0x1E) : Color.FromRgb(0x3C, 0x8A, 0x4E));
        }

        /// <summary>Draws a simple 32x32 icon so the add-in needs no image files.</summary>
        private static ImageSource Icon(string letter, Color color)
        {
            var visual = new DrawingVisual();
            using (DrawingContext dc = visual.RenderOpen())
            {
                dc.DrawRoundedRectangle(new SolidColorBrush(color), null, new Rect(2, 2, 28, 28), 6, 6);
                var text = new FormattedText(
                    letter,
                    System.Globalization.CultureInfo.InvariantCulture,
                    FlowDirection.LeftToRight,
                    new Typeface(new FontFamily("Segoe UI"), FontStyles.Normal, FontWeights.Bold, FontStretches.Normal),
                    18,
                    Brushes.White,
                    1.0);
                dc.DrawText(text, new Point(16 - text.Width / 2, 16 - text.Height / 2));
            }
            var bitmap = new RenderTargetBitmap(32, 32, 96, 96, PixelFormats.Pbgra32);
            bitmap.Render(visual);
            bitmap.Freeze();
            return bitmap;
        }
    }

    [Transaction(TransactionMode.Manual)]
    public sealed class StatusCommand : IExternalCommand
    {
        public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
        {
            var dialog = new TaskDialog("Revit MCP")
            {
                MainInstruction = "Revit MCP bridge is running",
                MainContent =
                    "Add-in version: " + App.AddinVersion + "\n" +
                    "Revit: " + App.RevitVersion + "\n" +
                    "Pipe: " + App.PipeName + " (this Windows user only)\n" +
                    "Model changes: " + (App.Settings.AllowWrite ? "ALLOWED" : "off (read-only)") + "\n" +
                    "Wait for idle Revit: " + App.Settings.RevitTimeoutSeconds + " s\n\n" +
                    "Settings and log: " + BridgePaths.Home,
            };
            dialog.Show();
            return Result.Succeeded;
        }
    }

    [Transaction(TransactionMode.Manual)]
    public sealed class ToggleWriteCommand : IExternalCommand
    {
        public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
        {
            if (!App.Settings.AllowWrite)
            {
                var dialog = new TaskDialog("Revit MCP")
                {
                    MainInstruction = "Allow AI assistants to change models?",
                    MainContent =
                        "Connected assistants will be able to create, edit and delete elements in any open document. " +
                        "Each change is one undoable transaction named 'MCP: ...'.\n\n" +
                        "The setting stays on for later Revit sessions until you turn it off here.",
                    CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No,
                    DefaultButton = TaskDialogResult.No,
                };
                if (dialog.Show() != TaskDialogResult.Yes) return Result.Cancelled;
            }

            App.Settings.AllowWrite = !App.Settings.AllowWrite;
            try { App.Settings.Save(); }
            catch (Exception ex) { Log.Write("cannot save settings: " + ex.Message); }
            App.UpdateWriteButton();
            return Result.Succeeded;
        }
    }

    internal static class Log
    {
        private static readonly object Gate = new object();

        public static void Write(string message)
        {
            try
            {
                lock (Gate)
                {
                    Directory.CreateDirectory(BridgePaths.Home);
                    var file = new FileInfo(BridgePaths.LogFile);
                    if (file.Exists && file.Length > 2 * 1024 * 1024) file.Delete();
                    File.AppendAllText(BridgePaths.LogFile,
                        DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + message + Environment.NewLine);
                }
            }
            catch
            {
                // Logging must never break a command.
            }
        }
    }
}
