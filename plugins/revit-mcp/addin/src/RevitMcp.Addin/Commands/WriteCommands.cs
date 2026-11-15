using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json.Nodes;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Structure;
using RevitMcp.Bridge;

namespace RevitMcp.Addin
{
    internal static class WriteCommands
    {
        public static JsonNode? SetParameters(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            List<JsonObject> changes = Args.Objects(args, "changes", 500);
            bool dryRun = Args.Bool(args, "dryRun", false);

            return Tx.Run(doc, "set parameters", dryRun, () =>
            {
                var applied = new JsonArray();
                var failed = new JsonArray();
                foreach (JsonObject change in changes)
                {
                    long id = Args.Id(change, "elementId") ?? throw Args.Invalid("Every change needs 'elementId'.");
                    string name = Args.RequiredString(change, "parameter");
                    JsonNode? value = change["value"];
                    try
                    {
                        Element element = ModelHelpers.RequireElement(doc, new ElementId(id));
                        // Instance parameters only: changing a type parameter affects every
                        // element of that type, so the caller must address the type id itself.
                        Parameter? p = ModelHelpers.FindParameter(doc, element, name, includeType: false);
                        if (p == null)
                        {
                            bool onType = !(element is ElementType) &&
                                          ModelHelpers.FindParameter(doc, element, name) != null;
                            throw new BridgeException(ErrorCodes.NotFound, onType
                                ? "'" + name + "' is a type parameter. To change it for all elements of the type, use the typeId as elementId."
                                : "Element has no parameter '" + name + "'. Use revit_get_elements to see its parameters.");
                        }
                        if (p.IsReadOnly)
                            throw new BridgeException(ErrorCodes.RevitError, "Parameter '" + name + "' is read-only.");

                        string? before = ModelHelpers.DisplayValue(doc, p);
                        SetValue(p, value, name);
                        applied.Add(new JsonObject
                        {
                            ["elementId"] = id,
                            ["parameter"] = p.Definition.Name,
                            ["before"] = before,
                            ["after"] = ModelHelpers.DisplayValue(doc, p),
                        });
                    }
                    catch (Exception ex) when (!(ex is OutOfMemoryException))
                    {
                        failed.Add(new JsonObject { ["elementId"] = id, ["parameter"] = name, ["error"] = ex.Message });
                    }
                }

                var result = new JsonObject { ["appliedCount"] = applied.Count, ["failedCount"] = failed.Count };
                // Keep the reply small for bulk edits.
                result["applied"] = new JsonArray(applied.Take(100).Select(n => n!.DeepClone()).ToArray());
                if (applied.Count > 100) result["appliedTruncated"] = true;
                if (failed.Count > 0) result["failed"] = failed;
                return result;
            });
        }

        private static void SetValue(Parameter p, JsonNode? value, string name)
        {
            if (value == null)
            {
                if (p.StorageType == StorageType.String) { p.Set(""); return; }
                if (p.StorageType == StorageType.ElementId) { p.Set(ElementId.InvalidElementId); return; }
                throw Args.Invalid("'value' is required for parameter '" + name + "'.");
            }
            if (!(value is JsonValue v)) throw Args.Invalid("'value' must be a string, number or boolean.");

            bool ok;
            if (v.TryGetValue(out string? text))
            {
                text ??= "";
                switch (p.StorageType)
                {
                    case StorageType.String:
                        ok = p.Set(text);
                        break;
                    case StorageType.ElementId:
                        if (!long.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out long refId))
                            throw Args.Invalid("Parameter '" + name + "' refers to an element: give its element id as a number.");
                        ok = p.Set(new ElementId(refId));
                        break;
                    case StorageType.Integer:
                        if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out int i)) ok = p.Set(i);
                        else ok = p.SetValueString(text);
                        break;
                    default:
                        // A string for a measurable parameter is parsed like user input, e.g. "2500" or "2.5 m".
                        ok = p.SetValueString(text);
                        break;
                }
            }
            else if (v.TryGetValue(out bool flag))
            {
                if (p.StorageType != StorageType.Integer)
                    throw Args.Invalid("Parameter '" + name + "' is not a yes/no parameter.");
                ok = p.Set(flag ? 1 : 0);
            }
            else
            {
                double number = Args.ToDouble(value, "value");
                switch (p.StorageType)
                {
                    case StorageType.Double:
                        double internalValue;
                        try { internalValue = UnitUtils.ConvertToInternalUnits(number, p.GetUnitTypeId()); }
                        catch { internalValue = number; }
                        ok = p.Set(internalValue);
                        break;
                    case StorageType.Integer:
                        if (number != Math.Floor(number)) throw Args.Invalid("Parameter '" + name + "' needs an integer.");
                        ok = p.Set((int)number);
                        break;
                    case StorageType.ElementId:
                        ok = p.Set(new ElementId((long)number));
                        break;
                    default:
                        ok = p.Set(number.ToString(CultureInfo.InvariantCulture));
                        break;
                }
            }
            if (!ok) throw new BridgeException(ErrorCodes.RevitError, "Revit rejected the value for parameter '" + name + "'.");
        }

        public static JsonNode? CreateLevels(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            List<JsonObject> items = Args.Objects(args, "levels", 100);
            return Tx.Run(doc, "create levels", false, () =>
            {
                var created = new JsonArray();
                foreach (JsonObject item in items)
                {
                    double elevation = Args.RequiredDouble(item, "elevationMm");
                    Level level = Level.Create(doc, Units.MmToFeet(elevation));
                    string? name = Args.String(item, "name");
                    if (name != null) level.Name = name;
                    created.Add(new JsonObject { ["id"] = level.Id.Value, ["name"] = level.Name, ["elevationMm"] = elevation });
                }
                return new JsonObject
                {
                    ["created"] = created,
                    ["note"] = "Levels do not get plan views automatically.",
                };
            });
        }

        public static JsonNode? CreateGrids(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            List<JsonObject> items = Args.Objects(args, "grids", 200);
            return Tx.Run(doc, "create grids", false, () =>
            {
                var created = new JsonArray();
                foreach (JsonObject item in items)
                {
                    Line line = BoundLine(Args.PointMm(item, "startMm"), Args.PointMm(item, "endMm"), "grid");
                    Grid grid = Grid.Create(doc, line);
                    string? name = Args.String(item, "name");
                    if (name != null) grid.Name = name;
                    created.Add(new JsonObject { ["id"] = grid.Id.Value, ["name"] = grid.Name });
                }
                return new JsonObject { ["created"] = created };
            });
        }

        public static JsonNode? CreateWalls(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            List<JsonObject> items = Args.Objects(args, "walls", 200);
            return Tx.Run(doc, "create walls", false, () =>
            {
                var created = new JsonArray();
                foreach (JsonObject item in items)
                {
                    Level level = ModelHelpers.ResolveLevel(doc, item["level"], "level");
                    double height = Args.RequiredDouble(item, "heightMm");
                    if (height <= 0) throw Args.Invalid("'heightMm' must be positive.");

                    ElementId typeId;
                    long? requestedType = Args.Id(item, "wallTypeId");
                    if (requestedType.HasValue)
                    {
                        if (!(doc.GetElement(new ElementId(requestedType.Value)) is WallType))
                            throw new BridgeException(ErrorCodes.NotFound,
                                "wallTypeId " + requestedType + " is not a wall type. Use revit_list_family_types with category 'Walls'.");
                        typeId = new ElementId(requestedType.Value);
                    }
                    else
                    {
                        typeId = doc.GetDefaultElementTypeId(ElementTypeGroup.WallType);
                        if (typeId == ElementId.InvalidElementId)
                            throw new BridgeException(ErrorCodes.NotFound,
                                "The project has no default wall type: pass 'wallTypeId'.");
                    }

                    // Walls are drawn on the level plane: z of the points is ignored.
                    XYZ start = Flatten(Args.PointMm(item, "startMm"));
                    XYZ end = Flatten(Args.PointMm(item, "endMm"));
                    Wall wall = Wall.Create(doc, BoundLine(start, end, "wall"), typeId, level.Id,
                        Units.MmToFeet(height), Units.MmToFeet(Args.Double(item, "baseOffsetMm") ?? 0), false,
                        Args.Bool(item, "structural", false));
                    created.Add(new JsonObject { ["id"] = wall.Id.Value, ["type"] = wall.WallType.Name, ["level"] = level.Name });
                }
                return new JsonObject { ["created"] = created };
            });
        }

        public static JsonNode? PlaceFamilyInstances(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            List<JsonObject> items = Args.Objects(args, "instances", 200);
            return Tx.Run(doc, "place family instances", false, () =>
            {
                var created = new JsonArray();
                foreach (JsonObject item in items)
                {
                    long typeId = Args.Id(item, "typeId") ?? throw Args.Invalid("Every instance needs 'typeId'.");
                    if (!(doc.GetElement(new ElementId(typeId)) is FamilySymbol symbol))
                        throw new BridgeException(ErrorCodes.NotFound,
                            "typeId " + typeId + " is not a loadable family type. Use revit_list_family_types and pick a row with placeable=true.");
                    if (!symbol.IsActive)
                    {
                        symbol.Activate();
                        doc.Regenerate();
                    }

                    Level? level = item["level"] == null ? null : ModelHelpers.ResolveLevel(doc, item["level"], "level");
                    StructuralType structuralType = ParseStructuralType(Args.String(item, "structuralType"), symbol, item["endPointMm"] != null);
                    XYZ point = Args.PointMm(item, "pointMm");

                    FamilyInstance instance;
                    if (item["endPointMm"] != null)
                    {
                        // Line-based placement: beams, braces and other curve-driven families.
                        Line line = BoundLine(point, Args.PointMm(item, "endPointMm"), "instance");
                        if (level == null) throw Args.Invalid("Line-based instances (endPointMm) need 'level'.");
                        instance = doc.Create.NewFamilyInstance(line, symbol, level, structuralType);
                    }
                    else
                    {
                        instance = level != null
                            ? doc.Create.NewFamilyInstance(point, symbol, level, structuralType)
                            : doc.Create.NewFamilyInstance(point, symbol, structuralType);

                        double? rotation = Args.Double(item, "rotationDeg");
                        if (rotation.HasValue && Math.Abs(rotation.Value) > 1e-9)
                        {
                            Line axis = Line.CreateBound(point, point + XYZ.BasisZ);
                            ElementTransformUtils.RotateElement(doc, instance.Id, axis, rotation.Value * Math.PI / 180.0);
                        }
                    }

                    var row = new JsonObject
                    {
                        ["id"] = instance.Id.Value,
                        ["family"] = symbol.FamilyName,
                        ["type"] = symbol.Name,
                    };
                    if (level != null) row["level"] = level.Name;
                    created.Add(row);
                }
                return new JsonObject { ["created"] = created };
            });
        }

        private static StructuralType ParseStructuralType(string? text, FamilySymbol symbol, bool lineBased)
        {
            if (text != null)
            {
                switch (text.ToLowerInvariant())
                {
                    case "nonstructural": return StructuralType.NonStructural;
                    case "column": return StructuralType.Column;
                    case "beam": return StructuralType.Beam;
                    case "brace": return StructuralType.Brace;
                    case "footing": return StructuralType.Footing;
                    default:
                        throw Args.Invalid("'structuralType' must be one of: nonstructural, column, beam, brace, footing.");
                }
            }

            // Default from the family's category so structural families behave as expected.
            long category = symbol.Category?.Id.Value ?? 0;
            if (category == (long)(int)BuiltInCategory.OST_StructuralColumns) return StructuralType.Column;
            if (category == (long)(int)BuiltInCategory.OST_StructuralFraming) return lineBased ? StructuralType.Beam : StructuralType.NonStructural;
            if (category == (long)(int)BuiltInCategory.OST_StructuralFoundation) return StructuralType.Footing;
            return StructuralType.NonStructural;
        }

        public static JsonNode? TransformElements(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            List<ElementId> ids = Args.Ids(args, "ids", 2000);
            bool copy = Args.Bool(args, "copy", false);
            bool hasMove = args["moveMm"] != null;
            double? rotate = Args.Double(args, "rotateDeg");
            if (!hasMove && !rotate.HasValue)
                throw Args.Invalid("Give 'moveMm' and/or 'rotateDeg'.");
            if (rotate.HasValue && args["rotateOriginMm"] == null)
                throw Args.Invalid("'rotateOriginMm' is required with 'rotateDeg': the point the vertical rotation axis passes through.");

            foreach (ElementId id in ids) ModelHelpers.RequireElement(doc, id);
            XYZ move = hasMove ? Args.PointMm(args, "moveMm") : XYZ.Zero;

            return Tx.Run(doc, copy ? "copy elements" : "transform elements", false, () =>
            {
                ICollection<ElementId> targets = ids;
                if (copy)
                    targets = ElementTransformUtils.CopyElements(doc, ids, move);
                else if (hasMove)
                    ElementTransformUtils.MoveElements(doc, ids, move);

                if (rotate.HasValue)
                {
                    XYZ origin = Args.PointMm(args, "rotateOriginMm");
                    Line axis = Line.CreateBound(origin, origin + XYZ.BasisZ);
                    ElementTransformUtils.RotateElements(doc, targets, axis, rotate.Value * Math.PI / 180.0);
                }

                var result = new JsonObject { ["count"] = targets.Count };
                if (copy)
                    result["newIds"] = new JsonArray(targets.Select(i => (JsonNode?)JsonValue.Create(i.Value)).ToArray());
                return result;
            });
        }

        public static JsonNode? DeleteElements(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            List<ElementId> ids = Args.Ids(args, "ids", 2000);
            // Dry run by default: deletion cascades to hosted and dependent elements.
            bool dryRun = Args.Bool(args, "dryRun", true);
            foreach (ElementId id in ids) ModelHelpers.RequireElement(doc, id);

            return Tx.Run(doc, "delete elements", dryRun, () =>
            {
                var requested = new HashSet<long>(ids.Select(i => i.Value));
                // Revit also deletes hosted and dependent elements and returns all ids.
                ICollection<ElementId> deleted = doc.Delete(ids);
                int dependentCount = deleted.Count(i => !requested.Contains(i.Value));

                var result = new JsonObject
                {
                    ["requested"] = ids.Count,
                    ["totalDeleted"] = deleted.Count,
                    ["dependentsDeleted"] = dependentCount,
                };
                if (dependentCount > 0)
                    result["dependentIds"] = new JsonArray(deleted.Where(i => !requested.Contains(i.Value)).Take(200)
                        .Select(i => (JsonNode?)JsonValue.Create(i.Value)).ToArray());
                if (dryRun)
                    result["note"] = "Nothing was deleted. Inspect dependentIds with revit_get_elements if needed, " +
                                     "then call again with dryRun=false to delete.";
                return result;
            });
        }

        private static XYZ Flatten(XYZ p)
        {
            return new XYZ(p.X, p.Y, 0);
        }

        private static Line BoundLine(XYZ start, XYZ end, string what)
        {
            if (start.DistanceTo(end) < Units.MmToFeet(1.0))
                throw Args.Invalid("The " + what + " start and end points coincide (shorter than 1 mm).");
            return Line.CreateBound(start, end);
        }
    }
}
