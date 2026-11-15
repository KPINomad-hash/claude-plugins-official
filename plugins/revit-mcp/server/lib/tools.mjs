// Tool catalog. `method` is the command name implemented by the Revit add-in
// (addin/src/RevitMcp.Addin/CommandInfrastructure.cs); test/catalog.test.mjs
// checks that the two lists match.
//
// Conventions shared by all tools:
//  - lengths and coordinates are millimetres in the project's internal coordinate
//    system; fields carrying them end in "Mm";
//  - element ids are integers;
//  - list tools page with limit/offset and report total/hasMore/nextOffset.

const pointMm = (description) => ({
  type: 'array',
  items: { type: 'number' },
  minItems: 2,
  maxItems: 3,
  description: `${description} [x, y] or [x, y, z] in millimetres.`,
});

const levelRef = {
  type: ['string', 'integer'],
  description: 'Level name (as shown in Revit) or level element id.',
};

const limit = (def, max) => ({
  type: 'integer',
  minimum: 1,
  maximum: max,
  default: def,
  description: `Maximum rows to return (default ${def}, max ${max}).`,
});

const offset = {
  type: 'integer',
  minimum: 0,
  default: 0,
  description: 'Rows to skip; use nextOffset from the previous reply to get the next page.',
};

const idList = (max, description) => ({
  type: 'array',
  items: { type: 'integer' },
  minItems: 1,
  maxItems: max,
  description,
});

const object = (properties, required = []) => ({
  type: 'object',
  properties,
  required,
  additionalProperties: false,
});

const READ = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
const UI = { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false };
const ADD = { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false };
const EDIT = { readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: false };
const DESTROY = { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: false };

const WRITE_NOTE =
  ' Changes the model: works only after the user turns on "Allow changes" on the Revit MCP ribbon panel. ' +
  'The whole call is one transaction the user can undo with Ctrl+Z; if Revit reports an error, nothing is changed.';

export const tools = [
  {
    name: 'revit_status',
    method: 'status',
    title: 'Revit status',
    description:
      'Check the connection to Revit: running Revit sessions, Revit version, open documents, the active document ' +
      'and view, and whether model changes are allowed. Call this first, and again whenever another tool reports ' +
      'a connection problem. Works even when no document is open.',
    inputSchema: object({}),
    annotations: READ,
  },
  {
    name: 'revit_use_instance',
    method: null,
    title: 'Choose Revit session',
    description:
      'Choose which running Revit session the other tools talk to, when revit_status lists more than one. ' +
      'The choice lasts until this MCP server restarts.',
    inputSchema: object({ pid: { type: 'integer', description: 'Process id of the Revit session, from revit_status.' } }, ['pid']),
    annotations: UI,
  },
  {
    name: 'revit_get_document_info',
    method: 'get_document_info',
    title: 'Document info',
    description:
      'Summary of the active document: file path, project information, display length unit, phases, worksets, ' +
      'Revit links, number of warnings, levels and model elements.',
    inputSchema: object({}),
    annotations: READ,
  },
  {
    name: 'revit_list_categories',
    method: 'list_categories',
    title: 'List categories',
    description:
      'Categories that actually contain elements, with element counts, largest first. Use it to learn what is in the ' +
      'model and to get exact category names for revit_query_elements. Names are in the language of the Revit UI; ' +
      'builtInCategory (e.g. OST_Walls) is language-independent.',
    inputSchema: object({
      activeViewOnly: { type: 'boolean', default: false, description: 'Count only elements visible in the active view.' },
      includeAnnotation: { type: 'boolean', default: false, description: 'Also list annotation categories (dimensions, tags, text).' },
    }),
    annotations: READ,
  },
  {
    name: 'revit_query_elements',
    method: 'query_elements',
    title: 'Query elements',
    description:
      'Find elements by category, class, level, family/type name or a parameter condition. Returns compact rows ' +
      '(id, category, family, type, level) plus the parameter values named in `fields`. All filters are combined ' +
      'with AND. Always filter by category when you can: an unfiltered query scans every model element. ' +
      'Use revit_get_elements for the full parameter list of specific elements.',
    inputSchema: object({
      category: {
        type: 'string',
        description: 'Category name as shown in Revit, or a BuiltInCategory name such as "OST_StructuralColumns" or "Walls".',
      },
      className: { type: 'string', description: 'Revit API class name, e.g. "Wall", "Floor", "FamilyInstance", "Rebar".' },
      elementKind: {
        type: 'string',
        enum: ['instances', 'types'],
        default: 'instances',
        description: 'instances: placed elements. types: element types (family types).',
      },
      level: levelRef,
      activeViewOnly: { type: 'boolean', default: false, description: 'Only elements visible in the active view.' },
      viewId: { type: 'integer', description: 'Only elements visible in this view.' },
      familyNameContains: { type: 'string', description: 'Case-insensitive part of the family name.' },
      typeNameContains: { type: 'string', description: 'Case-insensitive part of the type name.' },
      parameterFilter: object(
        {
          name: { type: 'string', description: 'Parameter name as shown in Revit (instance or type parameter).' },
          op: {
            type: 'string',
            enum: ['equals', 'not_equals', 'contains', 'gt', 'gte', 'lt', 'lte', 'exists', 'empty'],
            default: 'equals',
          },
          value: {
            type: ['string', 'number', 'boolean'],
            description:
              'Value to compare with. Numbers are compared in the unit Revit displays for the parameter ' +
              '(mm for lengths in a metric project). Not needed for exists/empty.',
          },
        },
        ['name'],
      ),
      fields: {
        type: 'array',
        items: { type: 'string' },
        maxItems: 20,
        description: 'Parameter names whose displayed values to include in each row, e.g. ["Mark", "Volume"].',
      },
      limit: limit(50, 500),
      offset,
    }),
    annotations: READ,
  },
  {
    name: 'revit_get_elements',
    method: 'get_elements',
    title: 'Get element details',
    description:
      'Full details of up to 50 elements by id: class, type, level, host, location (point or line, in mm), ' +
      'bounding box and all parameters that have a value. Each parameter has `value` (as displayed in Revit, with ' +
      'units), `number` (numeric value in the displayed unit), `scope` and `readOnly`.',
    inputSchema: object(
      {
        ids: idList(50, 'Element ids.'),
        includeParameters: { type: 'boolean', default: true, description: 'Set false to get only identity and geometry.' },
        includeTypeParameters: { type: 'boolean', default: false, description: 'Also include the parameters of each element\'s type.' },
      },
      ['ids'],
    ),
    annotations: READ,
  },
  {
    name: 'revit_get_selection',
    method: 'get_selection',
    title: 'Get selection',
    description: 'The elements the user currently has selected in Revit (first 200). Use it when the user says "this", "selected" or "these elements".',
    inputSchema: object({}),
    annotations: READ,
  },
  {
    name: 'revit_list_levels_and_grids',
    method: 'list_levels_and_grids',
    title: 'List levels and grids',
    description: 'All levels (name, elevation in mm, lowest first) and all grid lines (name, start and end points in mm).',
    inputSchema: object({}),
    annotations: READ,
  },
  {
    name: 'revit_list_views',
    method: 'list_views',
    title: 'List views',
    description: 'Views of the document (plans, sections, elevations, 3D, schedules, drafting views...), without view templates and sheets.',
    inputSchema: object({
      viewType: {
        type: 'string',
        description: 'Revit ViewType, e.g. FloorPlan, CeilingPlan, EngineeringPlan, Section, Elevation, ThreeD, Schedule, DraftingView, Legend, Detail.',
      },
      nameContains: { type: 'string', description: 'Case-insensitive part of the view name.' },
      limit: limit(100, 500),
      offset,
    }),
    annotations: READ,
  },
  {
    name: 'revit_list_sheets',
    method: 'list_sheets',
    title: 'List sheets',
    description: 'Sheets ordered by sheet number, each with the views placed on it.',
    inputSchema: object({ limit: limit(100, 500), offset }),
    annotations: READ,
  },
  {
    name: 'revit_list_family_types',
    method: 'list_family_types',
    title: 'List family types',
    description:
      'Element types loaded in the project (category, family, type, typeId). Use it to find the typeId for ' +
      'revit_place_family_instances (rows with placeable=true) or the wallTypeId for revit_create_walls. ' +
      'Filter by category: a project holds thousands of types.',
    inputSchema: object({
      category: { type: 'string', description: 'Category name or BuiltInCategory name, e.g. "Structural Columns" or "OST_StructuralFraming".' },
      familyNameContains: { type: 'string', description: 'Case-insensitive part of the family name.' },
      typeNameContains: { type: 'string', description: 'Case-insensitive part of the type name.' },
      limit: limit(100, 500),
      offset,
    }),
    annotations: READ,
  },
  {
    name: 'revit_get_schedule_data',
    method: 'get_schedule_data',
    title: 'Read schedule',
    description:
      'The body of a schedule as rows of text cells, exactly as Revit displays them (the first rows are the column ' +
      'headers). Find schedules with revit_list_views and viewType "Schedule".',
    inputSchema: object({
      scheduleId: { type: 'integer', description: 'Element id of the schedule view.' },
      name: { type: 'string', description: 'Exact schedule name; used when scheduleId is not given.' },
      maxRows: { type: 'integer', minimum: 1, maximum: 2000, default: 200, description: 'Maximum rows to return.' },
      offset,
    }),
    annotations: READ,
  },
  {
    name: 'revit_list_warnings',
    method: 'list_warnings',
    title: 'List warnings',
    description:
      'Revit warnings of the document: a summary grouped by warning text (most frequent first) and individual ' +
      'warnings with the ids of the elements involved.',
    inputSchema: object({ limit: limit(50, 500), offset }),
    annotations: READ,
  },
  {
    name: 'revit_export_view_image',
    method: 'export_view_image',
    title: 'Export view image',
    description:
      'Render a view to a PNG and return it as an image, so you can see the model. Defaults to the active view. ' +
      'Useful to check the result of a change or to understand a layout; large images cost many tokens, so keep ' +
      'the default width unless details are unreadable.',
    inputSchema: object({
      viewId: { type: 'integer', description: 'View to export; the active view when omitted.' },
      pixelWidth: { type: 'integer', minimum: 400, maximum: 4000, default: 1400, description: 'Image width in pixels.' },
    }),
    annotations: READ,
  },
  {
    name: 'revit_select_elements',
    method: 'select_elements',
    title: 'Select and show elements',
    description:
      'Select elements in the Revit UI and zoom to them, so the user can see what you are talking about. ' +
      'Replaces the current selection. Does not change the model.',
    inputSchema: object(
      {
        ids: idList(2000, 'Element ids to select.'),
        zoomTo: { type: 'boolean', default: true, description: 'Zoom the view to the elements (may switch to a view where they are visible).' },
      },
      ['ids'],
    ),
    annotations: UI,
  },
  {
    name: 'revit_set_active_view',
    method: 'set_active_view',
    title: 'Open view',
    description: 'Open a view in the Revit UI and make it the active view. Does not change the model.',
    inputSchema: object({ viewId: { type: 'integer', description: 'View id from revit_list_views or revit_list_sheets.' } }, ['viewId']),
    annotations: UI,
  },
  {
    name: 'revit_set_parameters',
    method: 'set_parameters',
    write: true,
    title: 'Set parameter values',
    description:
      'Set parameter values on elements, up to 500 changes per call. A number is taken in the unit Revit displays ' +
      'for that parameter (mm for lengths in a metric project); a string is parsed like user input ("2500", "2.5 m"). ' +
      'Only parameters of the addressed element are changed: to edit a type parameter, pass the typeId as elementId ' +
      '(this affects every element of that type). Changes that fail are listed in `failed`; the others are still applied. ' +
      'Use dryRun to preview before/after values without changing anything.' +
      WRITE_NOTE,
    inputSchema: object(
      {
        changes: {
          type: 'array',
          minItems: 1,
          maxItems: 500,
          items: object(
            {
              elementId: { type: 'integer' },
              parameter: { type: 'string', description: 'Parameter name as shown in Revit, or a BuiltInParameter name.' },
              value: { type: ['string', 'number', 'boolean', 'null'], description: 'New value; null clears a text parameter.' },
            },
            ['elementId', 'parameter', 'value'],
          ),
        },
        dryRun: { type: 'boolean', default: false, description: 'Report what would change, then roll back.' },
      },
      ['changes'],
    ),
    annotations: EDIT,
  },
  {
    name: 'revit_create_levels',
    method: 'create_levels',
    write: true,
    title: 'Create levels',
    description: 'Create levels at the given elevations. Plan views are not created.' + WRITE_NOTE,
    inputSchema: object(
      {
        levels: {
          type: 'array',
          minItems: 1,
          maxItems: 100,
          items: object(
            {
              name: { type: 'string', description: 'Level name; must be unique. Revit picks a name when omitted.' },
              elevationMm: { type: 'number', description: 'Elevation above the project base in millimetres.' },
            },
            ['elevationMm'],
          ),
        },
      },
      ['levels'],
    ),
    annotations: ADD,
  },
  {
    name: 'revit_create_grids',
    method: 'create_grids',
    write: true,
    title: 'Create grids',
    description: 'Create straight grid lines.' + WRITE_NOTE,
    inputSchema: object(
      {
        grids: {
          type: 'array',
          minItems: 1,
          maxItems: 200,
          items: object(
            {
              name: { type: 'string', description: 'Grid name; must be unique. Revit numbers it when omitted.' },
              startMm: pointMm('Start point.'),
              endMm: pointMm('End point.'),
            },
            ['startMm', 'endMm'],
          ),
        },
      },
      ['grids'],
    ),
    annotations: ADD,
  },
  {
    name: 'revit_create_walls',
    method: 'create_walls',
    write: true,
    title: 'Create walls',
    description:
      'Create straight walls along a line on a level. The line is the wall location line (wall centreline by default).' +
      WRITE_NOTE,
    inputSchema: object(
      {
        walls: {
          type: 'array',
          minItems: 1,
          maxItems: 200,
          items: object(
            {
              startMm: pointMm('Start of the wall line; z is ignored.'),
              endMm: pointMm('End of the wall line; z is ignored.'),
              level: levelRef,
              heightMm: { type: 'number', exclusiveMinimum: 0, description: 'Unconnected wall height in millimetres.' },
              baseOffsetMm: { type: 'number', default: 0, description: 'Offset of the wall base from the level.' },
              wallTypeId: { type: 'integer', description: 'Wall type id from revit_list_family_types; the project default wall type when omitted.' },
              structural: { type: 'boolean', default: false, description: 'Create a structural (bearing) wall.' },
            },
            ['startMm', 'endMm', 'level', 'heightMm'],
          ),
        },
      },
      ['walls'],
    ),
    annotations: ADD,
  },
  {
    name: 'revit_place_family_instances',
    method: 'place_family_instances',
    write: true,
    title: 'Place family instances',
    description:
      'Place instances of loadable families: columns, foundations, equipment, furniture (point-based, give pointMm) ' +
      'and beams or braces (line-based, give pointMm and endPointMm). Get typeId from revit_list_family_types. ' +
      'Families that need a host face or wall (doors, windows) are not supported.' +
      WRITE_NOTE,
    inputSchema: object(
      {
        instances: {
          type: 'array',
          minItems: 1,
          maxItems: 200,
          items: object(
            {
              typeId: { type: 'integer', description: 'Family type id (a row with placeable=true in revit_list_family_types).' },
              pointMm: pointMm('Insertion point, or the start point of a line-based instance.'),
              endPointMm: pointMm('End point; makes the instance line-based (beam, brace).'),
              level: levelRef,
              rotationDeg: { type: 'number', description: 'Rotation about the vertical axis through pointMm, counter-clockwise, degrees. Point-based instances only.' },
              structuralType: {
                type: 'string',
                enum: ['nonstructural', 'column', 'beam', 'brace', 'footing'],
                description: 'Structural usage. By default derived from the family category.',
              },
            },
            ['typeId', 'pointMm'],
          ),
        },
      },
      ['instances'],
    ),
    annotations: ADD,
  },
  {
    name: 'revit_transform_elements',
    method: 'transform_elements',
    write: true,
    title: 'Move, rotate or copy elements',
    description:
      'Move elements by a vector and/or rotate them about a vertical axis; with copy=true the originals stay and ' +
      'the copies are moved (their ids are returned in newIds). The move is applied before the rotation.' +
      WRITE_NOTE,
    inputSchema: object(
      {
        ids: idList(2000, 'Elements to transform.'),
        moveMm: pointMm('Translation vector.'),
        rotateDeg: { type: 'number', description: 'Rotation angle in degrees, counter-clockwise seen from above.' },
        rotateOriginMm: pointMm('Point the vertical rotation axis passes through; required with rotateDeg.'),
        copy: { type: 'boolean', default: false, description: 'Transform copies and keep the originals.' },
      },
      ['ids'],
    ),
    annotations: EDIT,
  },
  {
    name: 'revit_delete_elements',
    method: 'delete_elements',
    write: true,
    title: 'Delete elements',
    description:
      'Delete elements. Revit also deletes everything that depends on them (hosted doors and windows, tags, ' +
      'dimensions...), so this tool runs as a dry run by default: it reports how many elements would be deleted ' +
      'and their ids, and deletes nothing. Show the user that result, and call again with dryRun=false only after ' +
      'they agree.' +
      WRITE_NOTE,
    inputSchema: object(
      {
        ids: idList(2000, 'Elements to delete.'),
        dryRun: { type: 'boolean', default: true, description: 'true (default): only report what would be deleted. false: delete.' },
      },
      ['ids'],
    ),
    annotations: DESTROY,
  },
];

export function visibleTools({ readOnly }) {
  return tools.filter((t) => !(readOnly && t.write));
}

export function findTool(name) {
  return tools.find((t) => t.name === name);
}
