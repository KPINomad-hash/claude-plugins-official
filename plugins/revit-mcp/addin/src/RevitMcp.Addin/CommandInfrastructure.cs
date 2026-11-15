using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json.Nodes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using RevitMcp.Bridge;

namespace RevitMcp.Addin
{
    public sealed class CommandContext
    {
        public UIApplication UiApp { get; }

        public CommandContext(UIApplication uiApp)
        {
            UiApp = uiApp;
        }

        public UIDocument? UiDocOrNull => UiApp.ActiveUIDocument;

        public UIDocument UiDoc =>
            UiApp.ActiveUIDocument ?? throw new BridgeException(ErrorCodes.NoDocument,
                "No document is open in Revit. Ask the user to open a project.");

        public Document Doc => UiDoc.Document;
    }

    public sealed class CommandInfo
    {
        public string Name { get; }
        public bool Writes { get; }
        public Func<CommandContext, JsonObject, JsonNode?> Run { get; }

        public CommandInfo(string name, bool writes, Func<CommandContext, JsonObject, JsonNode?> run)
        {
            Name = name;
            Writes = writes;
            Run = run;
        }
    }

    /// <summary>
    /// Command names here must match the "method" of each tool in server/lib/tools.mjs
    /// (checked by server/test/catalog.test.mjs).
    /// </summary>
    public static class CommandRegistry
    {
        private static readonly Dictionary<string, CommandInfo> Commands = Build();

        public static bool TryGet(string name, out CommandInfo? command)
        {
            return Commands.TryGetValue(name, out command);
        }

        private static Dictionary<string, CommandInfo> Build()
        {
            var list = new List<CommandInfo>
            {
                Read("status", ReadCommands.Status),
                Read("get_document_info", ReadCommands.GetDocumentInfo),
                Read("list_categories", ReadCommands.ListCategories),
                Read("query_elements", ReadCommands.QueryElements),
                Read("get_elements", ReadCommands.GetElements),
                Read("get_selection", ReadCommands.GetSelection),
                Read("list_levels_and_grids", ReadCommands.ListLevelsAndGrids),
                Read("list_views", ReadCommands.ListViews),
                Read("list_sheets", ReadCommands.ListSheets),
                Read("list_family_types", ReadCommands.ListFamilyTypes),
                Read("get_schedule_data", ReadCommands.GetScheduleData),
                Read("list_warnings", ReadCommands.ListWarnings),
                Read("export_view_image", ReadCommands.ExportViewImage),
                Read("select_elements", UiCommands.SelectElements),
                Read("set_active_view", UiCommands.SetActiveView),
                Write("set_parameters", WriteCommands.SetParameters),
                Write("create_levels", WriteCommands.CreateLevels),
                Write("create_grids", WriteCommands.CreateGrids),
                Write("create_walls", WriteCommands.CreateWalls),
                Write("place_family_instances", WriteCommands.PlaceFamilyInstances),
                Write("transform_elements", WriteCommands.TransformElements),
                Write("delete_elements", WriteCommands.DeleteElements),
            };
            return list.ToDictionary(c => c.Name, StringComparer.Ordinal);
        }

        private static CommandInfo Read(string name, Func<CommandContext, JsonObject, JsonNode?> run)
        {
            return new CommandInfo(name, false, run);
        }

        private static CommandInfo Write(string name, Func<CommandContext, JsonObject, JsonNode?> run)
        {
            return new CommandInfo(name, true, run);
        }
    }

    /// <summary>Typed access to command arguments with clear error messages.</summary>
    internal static class Args
    {
        public static BridgeException Invalid(string message)
        {
            return new BridgeException(ErrorCodes.InvalidParams, message);
        }

        public static string? String(JsonObject o, string name)
        {
            JsonNode? node = o[name];
            if (node == null) return null;
            if (node is JsonValue v && v.TryGetValue(out string? s)) return string.IsNullOrWhiteSpace(s) ? null : s;
            throw Invalid("'" + name + "' must be a string.");
        }

        public static string RequiredString(JsonObject o, string name)
        {
            return String(o, name) ?? throw Invalid("'" + name + "' is required.");
        }

        public static bool Bool(JsonObject o, string name, bool fallback)
        {
            JsonNode? node = o[name];
            if (node == null) return fallback;
            if (node is JsonValue v && v.TryGetValue(out bool b)) return b;
            throw Invalid("'" + name + "' must be true or false.");
        }

        public static int Int(JsonObject o, string name, int fallback, int min, int max)
        {
            JsonNode? node = o[name];
            if (node == null) return fallback;
            double value = ToDouble(node, name);
            if (value != Math.Floor(value) || value < min || value > max)
                throw Invalid("'" + name + "' must be an integer from " + min + " to " + max + ".");
            return (int)value;
        }

        public static double? Double(JsonObject o, string name)
        {
            JsonNode? node = o[name];
            return node == null ? (double?)null : ToDouble(node, name);
        }

        public static double RequiredDouble(JsonObject o, string name)
        {
            return Double(o, name) ?? throw Invalid("'" + name + "' is required.");
        }

        public static double ToDouble(JsonNode node, string name)
        {
            if (node is JsonValue v)
            {
                if (v.TryGetValue(out double d) && !double.IsNaN(d) && !double.IsInfinity(d)) return d;
                if (v.TryGetValue(out long l)) return l;
            }
            throw Invalid("'" + name + "' must be a number.");
        }

        public static long ToId(JsonNode? node, string name)
        {
            if (node is JsonValue v)
            {
                if (v.TryGetValue(out long l)) return l;
                if (v.TryGetValue(out double d) && d == Math.Floor(d)) return (long)d;
                if (v.TryGetValue(out string? s) &&
                    long.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out long parsed)) return parsed;
            }
            throw Invalid("'" + name + "' must be an element id (integer).");
        }

        public static long? Id(JsonObject o, string name)
        {
            return o[name] == null ? (long?)null : ToId(o[name], name);
        }

        public static List<ElementId> Ids(JsonObject o, string name, int max)
        {
            if (!(o[name] is JsonArray array) || array.Count == 0)
                throw Invalid("'" + name + "' must be a non-empty array of element ids.");
            if (array.Count > max)
                throw Invalid("'" + name + "' has " + array.Count + " ids; the limit is " + max + " per call.");
            return array.Select(n => new ElementId(ToId(n, name))).ToList();
        }

        public static List<JsonObject> Objects(JsonObject o, string name, int max)
        {
            if (!(o[name] is JsonArray array) || array.Count == 0)
                throw Invalid("'" + name + "' must be a non-empty array.");
            if (array.Count > max)
                throw Invalid("'" + name + "' has " + array.Count + " items; the limit is " + max + " per call.");
            var result = new List<JsonObject>();
            foreach (JsonNode? item in array)
                result.Add(item as JsonObject ?? throw Invalid("Every item of '" + name + "' must be an object."));
            return result;
        }

        public static List<string> Strings(JsonObject o, string name, int max)
        {
            var result = new List<string>();
            if (o[name] == null) return result;
            if (!(o[name] is JsonArray array)) throw Invalid("'" + name + "' must be an array of strings.");
            foreach (JsonNode? item in array)
            {
                if (item is JsonValue v && v.TryGetValue(out string? s) && !string.IsNullOrWhiteSpace(s)) result.Add(s!);
                else throw Invalid("'" + name + "' must be an array of strings.");
            }
            if (result.Count > max) throw Invalid("'" + name + "' allows at most " + max + " items.");
            return result;
        }

        /// <summary>Reads [x, y] or [x, y, z] in millimetres and returns a point in internal units (feet).</summary>
        public static XYZ PointMm(JsonObject o, string name, bool required = true)
        {
            JsonNode? node = o[name];
            if (node == null)
            {
                if (required) throw Invalid("'" + name + "' is required: [x, y, z] in millimetres.");
                return XYZ.Zero;
            }
            if (!(node is JsonArray a) || a.Count < 2 || a.Count > 3)
                throw Invalid("'" + name + "' must be [x, y] or [x, y, z] in millimetres.");
            double x = ToDouble(a[0]!, name), y = ToDouble(a[1]!, name), z = a.Count == 3 ? ToDouble(a[2]!, name) : 0;
            return new XYZ(Units.MmToFeet(x), Units.MmToFeet(y), Units.MmToFeet(z));
        }
    }

    internal static class Units
    {
        public static double MmToFeet(double mm)
        {
            return UnitUtils.ConvertToInternalUnits(mm, UnitTypeId.Millimeters);
        }

        public static double FeetToMm(double feet)
        {
            return Math.Round(UnitUtils.ConvertFromInternalUnits(feet, UnitTypeId.Millimeters), 3);
        }

        public static JsonArray PointMm(XYZ p)
        {
            return new JsonArray(FeetToMm(p.X), FeetToMm(p.Y), FeetToMm(p.Z));
        }
    }
}
