using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json.Nodes;
using Autodesk.Revit.DB;
using RevitMcp.Bridge;

namespace RevitMcp.Addin
{
    internal static class ModelHelpers
    {
        public static Element RequireElement(Document doc, ElementId id)
        {
            return doc.GetElement(id) ?? throw new BridgeException(ErrorCodes.NotFound,
                "No element with id " + id.Value + " in '" + doc.Title + "'.");
        }

        /// <summary>
        /// Accepts a BuiltInCategory name with or without the OST_ prefix ("OST_Walls", "Walls")
        /// or the category name as shown in the Revit UI in the current language.
        /// </summary>
        public static ElementId ResolveCategory(Document doc, string name)
        {
            string trimmed = name.Trim();
            string enumName = trimmed.StartsWith("OST_", StringComparison.OrdinalIgnoreCase) ? trimmed : "OST_" + trimmed;
            if (Enum.TryParse(enumName.Replace(" ", ""), true, out BuiltInCategory builtIn) &&
                Enum.IsDefined(typeof(BuiltInCategory), builtIn) && builtIn != BuiltInCategory.INVALID)
                return new ElementId(builtIn);

            foreach (Category category in doc.Settings.Categories)
            {
                if (string.Equals(category.Name, trimmed, StringComparison.CurrentCultureIgnoreCase))
                    return category.Id;
            }
            throw new BridgeException(ErrorCodes.NotFound,
                "Unknown category '" + name + "'. Use revit_list_categories to see the categories present in the model.");
        }

        public static string BuiltInCategoryName(Category? category)
        {
            if (category == null) return "";
            long value = category.Id.Value;
            if (value < 0)
            {
                object builtIn = Enum.ToObject(typeof(BuiltInCategory), value);
                if (Enum.IsDefined(typeof(BuiltInCategory), builtIn)) return builtIn.ToString() ?? "";
            }
            return "";
        }

        public static Level ResolveLevel(Document doc, JsonNode? node, string argName)
        {
            if (node is JsonValue v)
            {
                if (v.TryGetValue(out string? name))
                {
                    Level? byName = new FilteredElementCollector(doc).OfClass(typeof(Level)).Cast<Level>()
                        .FirstOrDefault(l => string.Equals(l.Name, name, StringComparison.CurrentCultureIgnoreCase));
                    if (byName != null) return byName;
                    if (!long.TryParse(name, NumberStyles.Integer, CultureInfo.InvariantCulture, out _))
                        throw new BridgeException(ErrorCodes.NotFound,
                            "No level named '" + name + "'. Use revit_list_levels_and_grids to see the levels.");
                }
                if (doc.GetElement(new ElementId(Args.ToId(node, argName))) is Level byId) return byId;
            }
            throw new BridgeException(ErrorCodes.NotFound,
                "'" + argName + "' must be a level name or level element id. Use revit_list_levels_and_grids to see the levels.");
        }

        public static ElementId LevelIdOf(Element e)
        {
            if (e.LevelId != null && e.LevelId != ElementId.InvalidElementId) return e.LevelId;
            foreach (BuiltInParameter bip in LevelParameters)
            {
                Parameter? p = e.get_Parameter(bip);
                if (p != null && p.StorageType == StorageType.ElementId)
                {
                    ElementId id = p.AsElementId();
                    if (id != null && id != ElementId.InvalidElementId) return id;
                }
            }
            return ElementId.InvalidElementId;
        }

        private static readonly BuiltInParameter[] LevelParameters =
        {
            BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM,
            BuiltInParameter.FAMILY_LEVEL_PARAM,
            BuiltInParameter.FAMILY_BASE_LEVEL_PARAM,
            BuiltInParameter.SCHEDULE_LEVEL_PARAM,
            BuiltInParameter.INSTANCE_SCHEDULE_ONLY_LEVEL_PARAM,
        };

        public static ElementType? TypeOf(Document doc, Element e)
        {
            ElementId typeId = e.GetTypeId();
            return typeId == null || typeId == ElementId.InvalidElementId ? null : doc.GetElement(typeId) as ElementType;
        }

        /// <summary>One compact row describing an element.</summary>
        public static JsonObject Summary(Document doc, Element e)
        {
            var row = new JsonObject { ["id"] = e.Id.Value };
            if (e.Category != null) row["category"] = e.Category.Name;

            if (e is ElementType selfType)
            {
                row["family"] = selfType.FamilyName;
                row["type"] = selfType.Name;
                row["isType"] = true;
            }
            else
            {
                ElementType? type = TypeOf(doc, e);
                if (type != null)
                {
                    row["family"] = type.FamilyName;
                    row["type"] = type.Name;
                    row["typeId"] = type.Id.Value;
                }
                else if (!string.IsNullOrEmpty(e.Name))
                {
                    row["name"] = e.Name;
                }
            }

            ElementId levelId = LevelIdOf(e);
            if (levelId != ElementId.InvalidElementId && doc.GetElement(levelId) is Level level)
                row["level"] = level.Name;
            return row;
        }

        public static Parameter? FindParameter(Document doc, Element e, string name, bool includeType = true)
        {
            Parameter? p = e.LookupParameter(name);
            if (p == null && Enum.TryParse(name, true, out BuiltInParameter bip) &&
                Enum.IsDefined(typeof(BuiltInParameter), bip))
                p = e.get_Parameter(bip);
            if (p == null && includeType && !(e is ElementType))
            {
                ElementType? type = TypeOf(doc, e);
                if (type != null) return FindParameter(doc, type, name, false);
            }
            return p;
        }

        /// <summary>The value as Revit shows it to the user (with units), or null when empty.</summary>
        public static string? DisplayValue(Document doc, Parameter p)
        {
            if (!p.HasValue) return null;
            switch (p.StorageType)
            {
                case StorageType.String:
                    return p.AsString();
                case StorageType.ElementId:
                    ElementId id = p.AsElementId();
                    if (id == null || id == ElementId.InvalidElementId) return null;
                    string? shown = p.AsValueString();
                    if (!string.IsNullOrEmpty(shown)) return shown;
                    return doc.GetElement(id)?.Name ?? id.Value.ToString(CultureInfo.InvariantCulture);
                default:
                    return p.AsValueString() ?? (p.StorageType == StorageType.Integer
                        ? p.AsInteger().ToString(CultureInfo.InvariantCulture)
                        : p.AsDouble().ToString("R", CultureInfo.InvariantCulture));
            }
        }

        /// <summary>
        /// The numeric value in the unit the project displays for this parameter
        /// (e.g. mm for lengths in a metric project), or null for non-numeric parameters.
        /// </summary>
        public static double? NumericValue(Parameter p)
        {
            if (!p.HasValue) return null;
            if (p.StorageType == StorageType.Integer) return p.AsInteger();
            if (p.StorageType != StorageType.Double) return null;
            double raw = p.AsDouble();
            try
            {
                return Math.Round(UnitUtils.ConvertFromInternalUnits(raw, p.GetUnitTypeId()), 6);
            }
            catch
            {
                return Math.Round(raw, 6); // unitless number
            }
        }

        public static JsonObject ParameterJson(Document doc, Parameter p, string scope)
        {
            var json = new JsonObject
            {
                ["name"] = p.Definition.Name,
                ["value"] = DisplayValue(doc, p),
            };
            double? number = NumericValue(p);
            if (number.HasValue) json["number"] = number.Value;
            if (p.StorageType == StorageType.ElementId && p.HasValue)
            {
                ElementId id = p.AsElementId();
                if (id != null && id != ElementId.InvalidElementId) json["elementId"] = id.Value;
            }
            json["scope"] = scope;
            if (p.IsReadOnly) json["readOnly"] = true;
            return json;
        }

        public static JsonArray ParametersJson(Document doc, Element e, string scope)
        {
            var result = new JsonArray();
            foreach (Parameter p in e.Parameters.Cast<Parameter>()
                         .Where(p => p.Definition != null)
                         .OrderBy(p => p.Definition.Name, StringComparer.CurrentCulture))
            {
                if (!p.HasValue) continue;
                result.Add(ParameterJson(doc, p, scope));
            }
            return result;
        }

        public static JsonObject? LocationJson(Element e)
        {
            switch (e.Location)
            {
                case LocationPoint lp:
                    var point = new JsonObject { ["pointMm"] = Units.PointMm(lp.Point) };
                    try { point["rotationDeg"] = Math.Round(lp.Rotation * 180.0 / Math.PI, 4); }
                    catch { /* some point-located elements have no rotation */ }
                    return point;
                case LocationCurve lc when lc.Curve != null && lc.Curve.IsBound:
                    return new JsonObject
                    {
                        ["startMm"] = Units.PointMm(lc.Curve.GetEndPoint(0)),
                        ["endMm"] = Units.PointMm(lc.Curve.GetEndPoint(1)),
                        ["lengthMm"] = Units.FeetToMm(lc.Curve.Length),
                        ["isStraight"] = lc.Curve is Line,
                    };
                default:
                    return null;
            }
        }

        public static JsonObject? BoundingBoxJson(Element e)
        {
            BoundingBoxXYZ? box = e.get_BoundingBox(null);
            if (box == null) return null;
            return new JsonObject
            {
                ["minMm"] = Units.PointMm(box.Min),
                ["maxMm"] = Units.PointMm(box.Max),
            };
        }
    }

    /// <summary>
    /// Runs a model change as one undoable transaction. Warnings are dismissed and
    /// reported back instead of opening dialogs; errors roll the transaction back.
    /// </summary>
    internal static class Tx
    {
        public static JsonObject Run(Document doc, string name, bool dryRun, Func<JsonObject> work)
        {
            if (doc.IsReadOnly)
                throw new BridgeException(ErrorCodes.RevitError, "'" + doc.Title + "' is read-only.");
            if (doc.IsModifiable)
                throw new BridgeException(ErrorCodes.RevitBusy,
                    "Another transaction is open in Revit. Ask the user to finish the current command, then retry.");

            var failures = new FailureCollector();
            using (var tx = new Transaction(doc, "MCP: " + name))
            {
                FailureHandlingOptions options = tx.GetFailureHandlingOptions();
                options.SetFailuresPreprocessor(failures);
                options.SetClearAfterRollback(true);
                tx.SetFailureHandlingOptions(options);

                if (tx.Start() != TransactionStatus.Started)
                    throw new BridgeException(ErrorCodes.RevitError, "Revit refused to start a transaction.");

                JsonObject result;
                try
                {
                    result = work();
                    if (dryRun)
                    {
                        doc.Regenerate();
                        tx.RollBack();
                        result["dryRun"] = true;
                        result["committed"] = false;
                        return result;
                    }
                }
                catch
                {
                    if (tx.GetStatus() == TransactionStatus.Started) tx.RollBack();
                    throw;
                }

                TransactionStatus status = tx.Commit();
                if (status != TransactionStatus.Committed)
                {
                    string details = failures.Errors.Count > 0 ? string.Join(" | ", failures.Errors) : status.ToString();
                    throw new BridgeException(ErrorCodes.RevitError,
                        "Revit rolled the change back, nothing was modified. Reason: " + details);
                }

                result["committed"] = true;
                if (failures.Warnings.Count > 0)
                    result["revitWarnings"] = new JsonArray(failures.Warnings.Distinct().Take(20)
                        .Select(w => (JsonNode?)JsonValue.Create(w)).ToArray());
                return result;
            }
        }

        private sealed class FailureCollector : IFailuresPreprocessor
        {
            public List<string> Warnings { get; } = new List<string>();
            public List<string> Errors { get; } = new List<string>();

            public FailureProcessingResult PreprocessFailures(FailuresAccessor accessor)
            {
                bool hasError = false;
                foreach (FailureMessageAccessor failure in accessor.GetFailureMessages())
                {
                    string text;
                    try { text = failure.GetDescriptionText(); }
                    catch { text = "(no description)"; }

                    if (failure.GetSeverity() == FailureSeverity.Warning)
                    {
                        Warnings.Add(text);
                        accessor.DeleteWarning(failure);
                    }
                    else
                    {
                        Errors.Add(text);
                        hasError = true;
                    }
                }
                return hasError ? FailureProcessingResult.ProceedWithRollBack : FailureProcessingResult.Continue;
            }
        }
    }
}
