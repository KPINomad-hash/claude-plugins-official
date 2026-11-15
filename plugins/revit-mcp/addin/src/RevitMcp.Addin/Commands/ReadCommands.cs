using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json.Nodes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using RevitMcp.Bridge;

namespace RevitMcp.Addin
{
    internal static class ReadCommands
    {
        public static JsonNode? Status(CommandContext ctx, JsonObject args)
        {
            var app = ctx.UiApp.Application;
            var result = new JsonObject
            {
                ["pid"] = Process.GetCurrentProcess().Id,
                ["revitVersion"] = app.VersionNumber,
                ["revitBuild"] = app.VersionBuild,
                ["revitLanguage"] = app.Language.ToString(),
                ["addinVersion"] = App.AddinVersion,
                ["writeEnabled"] = App.Settings.AllowWrite,
            };

            var documents = new JsonArray();
            foreach (Document d in app.Documents)
            {
                if (d.IsLinked) continue;
                documents.Add(new JsonObject
                {
                    ["title"] = d.Title,
                    ["path"] = d.PathName,
                    ["isFamily"] = d.IsFamilyDocument,
                    ["isModified"] = d.IsModified,
                });
            }
            result["openDocuments"] = documents;

            UIDocument? uiDoc = ctx.UiDocOrNull;
            if (uiDoc == null)
            {
                result["activeDocument"] = null;
                result["note"] = "No document is open. Most tools need the user to open a project first.";
                return result;
            }

            result["activeDocument"] = uiDoc.Document.Title;
            View? view = uiDoc.Document.ActiveView;
            if (view != null)
                result["activeView"] = new JsonObject
                {
                    ["id"] = view.Id.Value,
                    ["name"] = view.Name,
                    ["viewType"] = view.ViewType.ToString(),
                };
            result["selectionCount"] = uiDoc.Selection.GetElementIds().Count;
            return result;
        }

        public static JsonNode? GetDocumentInfo(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            var result = new JsonObject
            {
                ["title"] = doc.Title,
                ["path"] = doc.PathName,
                ["isFamilyDocument"] = doc.IsFamilyDocument,
                ["isWorkshared"] = doc.IsWorkshared,
                ["isModified"] = doc.IsModified,
                ["isReadOnly"] = doc.IsReadOnly,
            };

            try
            {
                ForgeTypeId lengthUnit = doc.GetUnits().GetFormatOptions(SpecTypeId.Length).GetUnitTypeId();
                result["displayLengthUnit"] = LabelUtils.GetLabelForUnit(lengthUnit);
            }
            catch { /* optional */ }
            result["apiLengthUnit"] = "mm (all *Mm fields of these tools are millimetres)";

            ProjectInfo? info = doc.ProjectInformation;
            if (info != null)
            {
                result["projectInfo"] = new JsonObject
                {
                    ["name"] = info.Name,
                    ["number"] = info.Number,
                    ["client"] = info.ClientName,
                    ["address"] = info.Address,
                    ["buildingName"] = info.BuildingName,
                    ["status"] = info.Status,
                    ["issueDate"] = info.IssueDate,
                    ["elementId"] = info.Id.Value,
                };
            }

            var phases = new JsonArray();
            foreach (Phase phase in doc.Phases) phases.Add(new JsonObject { ["id"] = phase.Id.Value, ["name"] = phase.Name });
            result["phases"] = phases;

            var links = new JsonArray();
            foreach (Element link in new FilteredElementCollector(doc).OfClass(typeof(RevitLinkInstance)))
                links.Add(new JsonObject { ["id"] = link.Id.Value, ["name"] = link.Name });
            result["revitLinks"] = links;

            if (doc.IsWorkshared)
            {
                var worksets = new JsonArray();
                foreach (Workset ws in new FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset))
                    worksets.Add(new JsonObject { ["id"] = ws.Id.IntegerValue, ["name"] = ws.Name, ["isOpen"] = ws.IsOpen });
                result["worksets"] = worksets;
            }

            result["warningCount"] = doc.GetWarnings().Count;
            result["levelCount"] = new FilteredElementCollector(doc).OfClass(typeof(Level)).GetElementCount();
            result["modelElementCount"] = new FilteredElementCollector(doc).WhereElementIsNotElementType()
                .Count(e => e.Category != null && e.Category.CategoryType == CategoryType.Model);
            return result;
        }

        public static JsonNode? ListCategories(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            bool activeViewOnly = Args.Bool(args, "activeViewOnly", false);
            bool includeAnnotation = Args.Bool(args, "includeAnnotation", false);

            FilteredElementCollector collector = activeViewOnly
                ? new FilteredElementCollector(doc, doc.ActiveView.Id)
                : new FilteredElementCollector(doc);

            var counts = new Dictionary<long, (Category Category, int Count)>();
            foreach (Element e in collector.WhereElementIsNotElementType())
            {
                Category? category = e.Category;
                if (category == null) continue;
                if (category.CategoryType != CategoryType.Model &&
                    !(includeAnnotation && category.CategoryType == CategoryType.Annotation)) continue;
                long key = category.Id.Value;
                counts[key] = counts.TryGetValue(key, out var entry) ? (entry.Category, entry.Count + 1) : (category, 1);
            }

            var rows = new JsonArray();
            foreach (var entry in counts.Values.OrderByDescending(v => v.Count))
            {
                rows.Add(new JsonObject
                {
                    ["name"] = entry.Category.Name,
                    ["builtInCategory"] = ModelHelpers.BuiltInCategoryName(entry.Category),
                    ["count"] = entry.Count,
                });
            }
            return new JsonObject
            {
                ["scope"] = activeViewOnly ? "active view" : "whole model",
                ["categories"] = rows,
            };
        }

        public static JsonNode? QueryElements(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            int limit = Args.Int(args, "limit", 50, 1, 500);
            int offset = Args.Int(args, "offset", 0, 0, int.MaxValue);
            bool types = string.Equals(Args.String(args, "elementKind"), "types", StringComparison.OrdinalIgnoreCase);
            List<string> fields = Args.Strings(args, "fields", 20);

            FilteredElementCollector collector;
            long? viewId = Args.Id(args, "viewId");
            if (Args.Bool(args, "activeViewOnly", false))
                collector = new FilteredElementCollector(doc, doc.ActiveView.Id);
            else if (viewId.HasValue)
            {
                if (!(doc.GetElement(new ElementId(viewId.Value)) is View))
                    throw new BridgeException(ErrorCodes.NotFound, "viewId " + viewId + " is not a view.");
                collector = new FilteredElementCollector(doc, new ElementId(viewId.Value));
            }
            else
                collector = new FilteredElementCollector(doc);

            collector = types ? collector.WhereElementIsElementType() : collector.WhereElementIsNotElementType();

            string? categoryName = Args.String(args, "category");
            string? className = Args.String(args, "className");
            if (categoryName != null)
                collector = collector.WherePasses(new ElementCategoryFilter(ModelHelpers.ResolveCategory(doc, categoryName)));
            if (className != null)
            {
                Type? type = typeof(Element).Assembly.GetType("Autodesk.Revit.DB." + className, false, true)
                             ?? typeof(Element).Assembly.GetType("Autodesk.Revit.DB.Structure." + className, false, true)
                             ?? typeof(Element).Assembly.GetType("Autodesk.Revit.DB.Architecture." + className, false, true)
                             ?? typeof(Element).Assembly.GetType("Autodesk.Revit.DB.Mechanical." + className, false, true)
                             ?? typeof(Element).Assembly.GetType("Autodesk.Revit.DB.Plumbing." + className, false, true)
                             ?? typeof(Element).Assembly.GetType("Autodesk.Revit.DB.Electrical." + className, false, true);
                if (type == null || !typeof(Element).IsAssignableFrom(type))
                    throw Args.Invalid("'" + className + "' is not a Revit API element class (examples: Wall, Floor, FamilyInstance, Rebar).");
                try
                {
                    collector = collector.OfClass(type);
                }
                catch (Exception)
                {
                    throw Args.Invalid("Revit cannot filter by class '" + className + "'. Filter by category instead.");
                }
            }

            IEnumerable<Element> elements = collector;
            if (categoryName == null && className == null && !types)
                elements = elements.Where(e => e.Category != null && e.Category.CategoryType == CategoryType.Model);

            if (args["level"] != null)
            {
                ElementId levelId = ModelHelpers.ResolveLevel(doc, args["level"], "level").Id;
                elements = elements.Where(e => ModelHelpers.LevelIdOf(e) == levelId);
            }

            string? typeContains = Args.String(args, "typeNameContains");
            string? familyContains = Args.String(args, "familyNameContains");
            if (typeContains != null || familyContains != null)
            {
                elements = elements.Where(e =>
                {
                    ElementType? t = e as ElementType ?? ModelHelpers.TypeOf(doc, e);
                    if (t == null) return false;
                    return (typeContains == null || Contains(t.Name, typeContains)) &&
                           (familyContains == null || Contains(t.FamilyName, familyContains));
                });
            }

            if (args["parameterFilter"] != null)
            {
                JsonObject filter = args["parameterFilter"] as JsonObject
                                    ?? throw Args.Invalid("'parameterFilter' must be an object {name, op, value}.");
                Func<Element, bool> predicate = BuildParameterPredicate(doc, filter);
                elements = elements.Where(predicate);
            }

            // Stable order so offset-based paging is repeatable.
            List<Element> matched = elements.OrderBy(e => e.Id.Value).ToList();
            var rows = new JsonArray();
            foreach (Element e in matched.Skip(offset).Take(limit))
            {
                JsonObject row = ModelHelpers.Summary(doc, e);
                if (fields.Count > 0)
                {
                    var values = new JsonObject();
                    foreach (string field in fields)
                    {
                        Parameter? p = ModelHelpers.FindParameter(doc, e, field);
                        values[field] = p == null ? null : ModelHelpers.DisplayValue(doc, p);
                    }
                    row["params"] = values;
                }
                rows.Add(row);
            }

            int returned = rows.Count;
            var result = new JsonObject
            {
                ["total"] = matched.Count,
                ["offset"] = offset,
                ["returned"] = returned,
                ["hasMore"] = offset + returned < matched.Count,
                ["elements"] = rows,
            };
            if (offset + returned < matched.Count) result["nextOffset"] = offset + returned;
            return result;
        }

        private static bool Contains(string? text, string part)
        {
            return text != null && text.IndexOf(part, StringComparison.CurrentCultureIgnoreCase) >= 0;
        }

        private static Func<Element, bool> BuildParameterPredicate(Document doc, JsonObject filter)
        {
            string name = Args.RequiredString(filter, "name");
            string op = (Args.String(filter, "op") ?? "equals").ToLowerInvariant();
            JsonNode? valueNode = filter["value"];

            if (op == "exists" || op == "empty")
            {
                return e =>
                {
                    Parameter? p = ModelHelpers.FindParameter(doc, e, name);
                    bool hasValue = p != null && p.HasValue && !string.IsNullOrEmpty(ModelHelpers.DisplayValue(doc, p));
                    return op == "exists" ? hasValue : !hasValue;
                };
            }

            if (valueNode == null) throw Args.Invalid("parameterFilter.value is required for op '" + op + "'.");

            if (op == "gt" || op == "gte" || op == "lt" || op == "lte")
            {
                double target = Args.ToDouble(valueNode, "parameterFilter.value");
                return e =>
                {
                    Parameter? p = ModelHelpers.FindParameter(doc, e, name);
                    double? number = p == null ? null : ModelHelpers.NumericValue(p);
                    if (!number.HasValue) return false;
                    switch (op)
                    {
                        case "gt": return number.Value > target;
                        case "gte": return number.Value >= target;
                        case "lt": return number.Value < target;
                        default: return number.Value <= target;
                    }
                };
            }

            if (op != "equals" && op != "not_equals" && op != "contains")
                throw Args.Invalid("parameterFilter.op must be one of: equals, not_equals, contains, gt, gte, lt, lte, exists, empty.");

            bool numericTarget = false;
            double targetNumber = 0;
            string targetText;
            if (valueNode is JsonValue jv && jv.TryGetValue(out string? s)) targetText = s ?? "";
            else if (valueNode is JsonValue jb && jb.TryGetValue(out bool b))
            {
                numericTarget = true;
                targetNumber = b ? 1 : 0;
                targetText = b ? "1" : "0";
            }
            else
            {
                numericTarget = true;
                targetNumber = Args.ToDouble(valueNode, "parameterFilter.value");
                targetText = targetNumber.ToString(CultureInfo.InvariantCulture);
            }

            return e =>
            {
                Parameter? p = ModelHelpers.FindParameter(doc, e, name);
                if (p == null) return op == "not_equals";
                bool match;
                if (op == "contains")
                    match = Contains(ModelHelpers.DisplayValue(doc, p), targetText);
                else if (numericTarget && ModelHelpers.NumericValue(p) is double number)
                    match = Math.Abs(number - targetNumber) <= 1e-6 * Math.Max(1.0, Math.Abs(targetNumber));
                else
                    match = string.Equals(ModelHelpers.DisplayValue(doc, p) ?? "", targetText, StringComparison.CurrentCultureIgnoreCase);
                return op == "not_equals" ? !match : match;
            };
        }

        public static JsonNode? GetElements(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            List<ElementId> ids = Args.Ids(args, "ids", 50);
            bool includeParameters = Args.Bool(args, "includeParameters", true);
            bool includeTypeParameters = Args.Bool(args, "includeTypeParameters", false);

            var rows = new JsonArray();
            var missing = new JsonArray();
            foreach (ElementId id in ids)
            {
                Element? e = doc.GetElement(id);
                if (e == null)
                {
                    missing.Add(id.Value);
                    continue;
                }

                JsonObject row = ModelHelpers.Summary(doc, e);
                row["class"] = e.GetType().Name;
                if (!string.IsNullOrEmpty(e.Name)) row["name"] = e.Name;
                row["uniqueId"] = e.UniqueId;
                if (e.Pinned) row["pinned"] = true;
                if (e.GroupId != null && e.GroupId != ElementId.InvalidElementId) row["groupId"] = e.GroupId.Value;
                if (e is FamilyInstance fi && fi.Host != null) row["hostId"] = fi.Host.Id.Value;

                JsonObject? location = ModelHelpers.LocationJson(e);
                if (location != null) row["location"] = location;
                JsonObject? box = ModelHelpers.BoundingBoxJson(e);
                if (box != null) row["boundingBox"] = box;

                if (includeParameters)
                {
                    JsonArray parameters = ModelHelpers.ParametersJson(doc, e, e is ElementType ? "type" : "instance");
                    if (includeTypeParameters && !(e is ElementType))
                    {
                        ElementType? type = ModelHelpers.TypeOf(doc, e);
                        if (type != null)
                            foreach (JsonNode? p in ModelHelpers.ParametersJson(doc, type, "type"))
                                parameters.Add(p!.DeepClone());
                    }
                    row["parameters"] = parameters;
                }
                rows.Add(row);
            }

            var result = new JsonObject { ["elements"] = rows };
            if (missing.Count > 0) result["notFound"] = missing;
            return result;
        }

        public static JsonNode? GetSelection(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            ICollection<ElementId> ids = ctx.UiDoc.Selection.GetElementIds();
            var rows = new JsonArray();
            foreach (ElementId id in ids.OrderBy(i => i.Value).Take(200))
            {
                Element? e = doc.GetElement(id);
                if (e != null) rows.Add(ModelHelpers.Summary(doc, e));
            }
            var result = new JsonObject { ["count"] = ids.Count, ["elements"] = rows };
            if (ids.Count > 200) result["note"] = "Only the first 200 selected elements are listed.";
            return result;
        }

        public static JsonNode? ListLevelsAndGrids(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            var levels = new JsonArray();
            foreach (Level level in new FilteredElementCollector(doc).OfClass(typeof(Level)).Cast<Level>().OrderBy(l => l.Elevation))
            {
                levels.Add(new JsonObject
                {
                    ["id"] = level.Id.Value,
                    ["name"] = level.Name,
                    ["elevationMm"] = Units.FeetToMm(level.Elevation),
                });
            }

            var grids = new JsonArray();
            foreach (Grid grid in new FilteredElementCollector(doc).OfClass(typeof(Grid)).Cast<Grid>().OrderBy(g => g.Name, StringComparer.CurrentCulture))
            {
                var row = new JsonObject { ["id"] = grid.Id.Value, ["name"] = grid.Name };
                Curve? curve = grid.Curve;
                if (curve != null && curve.IsBound)
                {
                    row["startMm"] = Units.PointMm(curve.GetEndPoint(0));
                    row["endMm"] = Units.PointMm(curve.GetEndPoint(1));
                    row["isStraight"] = curve is Line;
                }
                grids.Add(row);
            }
            return new JsonObject { ["levels"] = levels, ["grids"] = grids };
        }

        public static JsonNode? ListViews(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            string? viewType = Args.String(args, "viewType");
            string? nameContains = Args.String(args, "nameContains");
            int limit = Args.Int(args, "limit", 100, 1, 500);
            int offset = Args.Int(args, "offset", 0, 0, int.MaxValue);

            ViewType wanted = ViewType.Undefined;
            if (viewType != null && !Enum.TryParse(viewType, true, out wanted))
                throw Args.Invalid("Unknown viewType '" + viewType + "'. Examples: FloorPlan, CeilingPlan, EngineeringPlan, Section, Elevation, ThreeD, Schedule, DraftingView, Legend, Detail.");

            List<View> views = new FilteredElementCollector(doc).OfClass(typeof(View)).Cast<View>()
                .Where(v => !v.IsTemplate && v.ViewType != ViewType.DrawingSheet &&
                            v.ViewType != ViewType.ProjectBrowser && v.ViewType != ViewType.SystemBrowser &&
                            v.ViewType != ViewType.Internal && v.ViewType != ViewType.Undefined)
                .Where(v => viewType == null || v.ViewType == wanted)
                .Where(v => nameContains == null || Contains(v.Name, nameContains))
                .OrderBy(v => v.ViewType.ToString()).ThenBy(v => v.Name, StringComparer.CurrentCulture)
                .ToList();

            var rows = new JsonArray();
            foreach (View v in views.Skip(offset).Take(limit))
            {
                var row = new JsonObject
                {
                    ["id"] = v.Id.Value,
                    ["name"] = v.Name,
                    ["viewType"] = v.ViewType.ToString(),
                };
                if (v.GenLevel != null) row["level"] = v.GenLevel.Name;
                if (v.Scale > 0) row["scale"] = v.Scale;
                rows.Add(row);
            }
            return Page(views.Count, offset, rows, "views");
        }

        public static JsonNode? ListSheets(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            int limit = Args.Int(args, "limit", 100, 1, 500);
            int offset = Args.Int(args, "offset", 0, 0, int.MaxValue);
            List<ViewSheet> sheets = new FilteredElementCollector(doc).OfClass(typeof(ViewSheet)).Cast<ViewSheet>()
                .Where(s => !s.IsPlaceholder)
                .OrderBy(s => s.SheetNumber, StringComparer.CurrentCulture).ToList();

            var rows = new JsonArray();
            foreach (ViewSheet sheet in sheets.Skip(offset).Take(limit))
            {
                var placed = new JsonArray();
                foreach (ElementId viewId in sheet.GetAllPlacedViews())
                    if (doc.GetElement(viewId) is View v)
                        placed.Add(new JsonObject { ["id"] = v.Id.Value, ["name"] = v.Name, ["viewType"] = v.ViewType.ToString() });
                rows.Add(new JsonObject
                {
                    ["id"] = sheet.Id.Value,
                    ["number"] = sheet.SheetNumber,
                    ["name"] = sheet.Name,
                    ["placedViews"] = placed,
                });
            }
            return Page(sheets.Count, offset, rows, "sheets");
        }

        public static JsonNode? ListFamilyTypes(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            int limit = Args.Int(args, "limit", 100, 1, 500);
            int offset = Args.Int(args, "offset", 0, 0, int.MaxValue);
            string? familyContains = Args.String(args, "familyNameContains");
            string? typeContains = Args.String(args, "typeNameContains");

            FilteredElementCollector collector = new FilteredElementCollector(doc).WhereElementIsElementType();
            string? categoryName = Args.String(args, "category");
            if (categoryName != null)
                collector = collector.WherePasses(new ElementCategoryFilter(ModelHelpers.ResolveCategory(doc, categoryName)));

            List<ElementType> types = collector.OfType<ElementType>()
                .Where(t => t.Category != null)
                .Where(t => familyContains == null || Contains(t.FamilyName, familyContains))
                .Where(t => typeContains == null || Contains(t.Name, typeContains))
                .OrderBy(t => t.Category.Name, StringComparer.CurrentCulture)
                .ThenBy(t => t.FamilyName, StringComparer.CurrentCulture)
                .ThenBy(t => t.Name, StringComparer.CurrentCulture)
                .ToList();

            var rows = new JsonArray();
            foreach (ElementType t in types.Skip(offset).Take(limit))
            {
                var row = new JsonObject
                {
                    ["typeId"] = t.Id.Value,
                    ["category"] = t.Category.Name,
                    ["family"] = t.FamilyName,
                    ["type"] = t.Name,
                };
                // Loadable family types can be placed with revit_place_family_instances;
                // system types (walls, floors...) have their own creation tools.
                row["placeable"] = t is FamilySymbol;
                rows.Add(row);
            }
            return Page(types.Count, offset, rows, "types");
        }

        public static JsonNode? GetScheduleData(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            int maxRows = Args.Int(args, "maxRows", 200, 1, 2000);
            int offset = Args.Int(args, "offset", 0, 0, int.MaxValue);

            ViewSchedule? schedule = null;
            long? id = Args.Id(args, "scheduleId");
            string? name = Args.String(args, "name");
            if (id.HasValue) schedule = doc.GetElement(new ElementId(id.Value)) as ViewSchedule;
            else if (name != null)
                schedule = new FilteredElementCollector(doc).OfClass(typeof(ViewSchedule)).Cast<ViewSchedule>()
                    .FirstOrDefault(s => !s.IsTemplate && string.Equals(s.Name, name, StringComparison.CurrentCultureIgnoreCase));
            else throw Args.Invalid("Give 'scheduleId' or 'name'. Use revit_list_views with viewType 'Schedule' to find schedules.");

            if (schedule == null)
                throw new BridgeException(ErrorCodes.NotFound,
                    "Schedule not found. Use revit_list_views with viewType 'Schedule' to find schedules.");

            TableSectionData body = schedule.GetTableData().GetSectionData(SectionType.Body);
            int rowCount = body.NumberOfRows, columnCount = body.NumberOfColumns;
            var rows = new JsonArray();
            for (int r = offset; r < rowCount && rows.Count < maxRows; r++)
            {
                var cells = new JsonArray();
                for (int c = 0; c < columnCount; c++) cells.Add(schedule.GetCellText(SectionType.Body, r, c));
                rows.Add(cells);
            }

            var result = new JsonObject
            {
                ["scheduleId"] = schedule.Id.Value,
                ["name"] = schedule.Name,
                ["columnCount"] = columnCount,
                ["totalRows"] = rowCount,
                ["offset"] = offset,
                ["returned"] = rows.Count,
                ["hasMore"] = offset + rows.Count < rowCount,
                ["rows"] = rows,
                ["note"] = "Rows are as displayed in Revit; with offset 0 the first rows are the column headers.",
            };
            return result;
        }

        public static JsonNode? ListWarnings(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            int limit = Args.Int(args, "limit", 50, 1, 500);
            int offset = Args.Int(args, "offset", 0, 0, int.MaxValue);
            IList<FailureMessage> warnings = doc.GetWarnings();

            var groups = warnings.GroupBy(w => w.GetDescriptionText())
                .OrderByDescending(g => g.Count())
                .Select(g => (JsonNode?)new JsonObject { ["description"] = g.Key, ["count"] = g.Count() })
                .Take(30).ToArray();

            var rows = new JsonArray();
            foreach (FailureMessage w in warnings.Skip(offset).Take(limit))
            {
                rows.Add(new JsonObject
                {
                    ["description"] = w.GetDescriptionText(),
                    ["severity"] = w.GetSeverity().ToString(),
                    ["elementIds"] = new JsonArray(w.GetFailingElements().Take(20)
                        .Select(i => (JsonNode?)JsonValue.Create(i.Value)).ToArray()),
                });
            }
            JsonObject result = Page(warnings.Count, offset, rows, "warnings");
            result["byDescription"] = new JsonArray(groups);
            return result;
        }

        public static JsonNode? ExportViewImage(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            int pixels = Args.Int(args, "pixelWidth", 1400, 400, 4000);
            long? viewId = Args.Id(args, "viewId");
            View view = viewId.HasValue
                ? doc.GetElement(new ElementId(viewId.Value)) as View
                  ?? throw new BridgeException(ErrorCodes.NotFound, "viewId " + viewId + " is not a view.")
                : doc.ActiveView;
            if (view.IsTemplate || !view.CanBePrinted)
                throw new BridgeException(ErrorCodes.RevitError, "View '" + view.Name + "' cannot be exported as an image.");

            string folder = Path.Combine(Path.GetTempPath(), "RevitMcp", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(folder);
            try
            {
                var options = new ImageExportOptions
                {
                    ExportRange = ExportRange.SetOfViews,
                    FilePath = Path.Combine(folder, "view"),
                    ZoomType = ZoomFitType.FitToPage,
                    FitDirection = FitDirectionType.Horizontal,
                    PixelSize = pixels,
                    HLRandWFViewsFileType = ImageFileType.PNG,
                    ShadowViewsFileType = ImageFileType.PNG,
                    ImageResolution = ImageResolution.DPI_150,
                };
                options.SetViewsAndSheets(new List<ElementId> { view.Id });
                doc.ExportImage(options);

                // Revit appends the view type and name to the file name.
                string? file = Directory.GetFiles(folder, "*.png").FirstOrDefault();
                if (file == null)
                    throw new BridgeException(ErrorCodes.RevitError, "Revit produced no image for view '" + view.Name + "'.");

                return new JsonObject
                {
                    ["viewId"] = view.Id.Value,
                    ["viewName"] = view.Name,
                    ["viewType"] = view.ViewType.ToString(),
                    ["mimeType"] = "image/png",
                    ["imageBase64"] = Convert.ToBase64String(File.ReadAllBytes(file)),
                };
            }
            finally
            {
                try { Directory.Delete(folder, true); } catch { /* temp folder */ }
            }
        }

        private static JsonObject Page(int total, int offset, JsonArray rows, string key)
        {
            var result = new JsonObject
            {
                ["total"] = total,
                ["offset"] = offset,
                ["returned"] = rows.Count,
                ["hasMore"] = offset + rows.Count < total,
                [key] = rows,
            };
            if (offset + rows.Count < total) result["nextOffset"] = offset + rows.Count;
            return result;
        }
    }

    internal static class UiCommands
    {
        public static JsonNode? SelectElements(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            List<ElementId> ids = Args.Ids(args, "ids", 2000);
            bool zoom = Args.Bool(args, "zoomTo", true);

            List<ElementId> existing = ids.Where(i => doc.GetElement(i) != null).ToList();
            if (existing.Count == 0)
                throw new BridgeException(ErrorCodes.NotFound, "None of the given ids exist in '" + doc.Title + "'.");

            ctx.UiDoc.Selection.SetElementIds(existing);
            if (zoom) ctx.UiDoc.ShowElements(existing);
            return new JsonObject
            {
                ["selected"] = existing.Count,
                ["notFound"] = ids.Count - existing.Count,
                ["zoomed"] = zoom,
            };
        }

        public static JsonNode? SetActiveView(CommandContext ctx, JsonObject args)
        {
            Document doc = ctx.Doc;
            long id = Args.Id(args, "viewId") ?? throw Args.Invalid("'viewId' is required.");
            if (!(doc.GetElement(new ElementId(id)) is View view) || view.IsTemplate)
                throw new BridgeException(ErrorCodes.NotFound, "viewId " + id + " is not a view that can be opened.");
            ctx.UiDoc.ActiveView = view;
            return new JsonObject
            {
                ["activeView"] = new JsonObject
                {
                    ["id"] = view.Id.Value,
                    ["name"] = view.Name,
                    ["viewType"] = view.ViewType.ToString(),
                },
            };
        }
    }
}
