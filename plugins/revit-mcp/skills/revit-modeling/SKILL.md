---
name: revit-modeling
description: How to work with an Autodesk Revit model through the revit_* MCP tools - checking the connection, finding elements, reading and setting parameters, creating and deleting elements safely. Use whenever the user asks about or wants to change the Revit model, mentions Revit, RVT, BIM elements, families, levels, grids, schedules or sheets, or when a revit_* tool returns an error.
---

# Working with Revit through MCP

The `revit_*` tools talk to the Revit session running on the user's Windows machine. Revit is a
single-user desktop application: every call runs on Revit's main thread, only when Revit is idle.

## Start of every task

1. Call `revit_status`. It tells you whether Revit is reachable, which document and view are
   active, and whether changes are allowed (`writeEnabled`).
2. If `connected` is false, stop and tell the user to start Revit and check for the "Revit MCP"
   panel on the Add-Ins tab. Do not retry in a loop.
3. If the task is about "this" or "the selected" elements, call `revit_get_selection`.

## Units and identifiers

- Every length and coordinate in tool input and output is **millimetres** (fields end in `Mm`),
  whatever units the project displays. Angles are degrees.
- Parameter values are different: `value` is the text Revit displays (with its unit), `number` is
  the numeric value in the unit Revit displays for that parameter. When you set a parameter with a
  number, it is taken in that same displayed unit.
- Element ids are integers and are only valid within the current document.
- Category and parameter names are in the language of the Revit UI (often Russian). Do not guess
  them: get category names from `revit_list_categories` (or use language-independent names such as
  `OST_StructuralColumns`) and parameter names from `revit_get_elements`.

## Finding things

- Narrow first: `revit_list_categories` → `revit_query_elements` with `category` (+ `level`,
  `typeNameContains`, `parameterFilter`) → `revit_get_elements` for a few ids.
- Ask for parameter values in bulk with `fields` on `revit_query_elements` instead of calling
  `revit_get_elements` for hundreds of ids.
- Lists are paged. When `hasMore` is true and the task needs everything (counts, totals, audits),
  keep reading with `offset: nextOffset`; say so if you only looked at part of the data. `total`
  is always the full count, so counting never needs paging.
- For quantities already tabulated by the user, prefer `revit_get_schedule_data`.
- To check geometry or layout visually, use `revit_export_view_image`.
- To point the user at elements, use `revit_select_elements`.

## Changing the model

- Write tools work only when the user has turned on "Allow changes" in Revit. If a tool answers
  that changes are switched off, tell the user where the switch is and wait; never look for
  another way to make the change.
- Before any change, say what you are about to do and on how many elements.
- Each call is one transaction named "MCP: ..." that the user can undo with Ctrl+Z. Tell the
  user this after a change. Prefer one call with many items over many calls, so one undo reverts
  the whole step.
- `revit_set_parameters`: use `dryRun: true` first for bulk edits and show a few before/after
  values. Type parameters change every element of the type; address the `typeId` explicitly and
  warn the user.
- `revit_delete_elements` is a dry run unless `dryRun: false`. Always run the dry run, report
  `totalDeleted` and `dependentsDeleted` (hosted elements, tags, dimensions go too), and delete
  only after the user confirms.
- Placing families: get `typeId` from `revit_list_family_types` (rows with `placeable: true`),
  and the level from `revit_list_levels_and_grids`. Coordinates are in the project's internal
  coordinate system; read existing grids or neighbouring elements to position new ones.
- After creating or moving elements, verify: read them back with `revit_get_elements` or look at
  `revit_export_view_image`. Report `revitWarnings` returned by the tool.

## Errors

| Message says | What to do |
| --- | --- |
| No running Revit session | Ask the user to start Revit / install the add-in. |
| Revit did not become idle | Revit shows a dialog or the user is mid-command. Ask them to finish, then retry once. |
| Model changes are switched off | Ask the user to turn on "Allow changes" on the Revit MCP ribbon panel. |
| Revit rolled the change back | Nothing was changed. Read the reason, fix the input, do not repeat the same call. |
| Did not answer within N s | The command may still be running. Check `revit_status` and read the model before repeating a change. |

There is no tool for running arbitrary code or scripts in Revit, by design. If a task needs
something the tools do not cover, say so instead of approximating it.
