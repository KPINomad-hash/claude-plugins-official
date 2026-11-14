"""Minimal reader for IFC (STEP physical file) exports: storeys, structural
elements and which storey contains them. Enough to check a SAPFIR model
hand-over; not a general IFC library."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ELEMENT_TYPES = {
    "IFCCOLUMN": "column", "IFCBEAM": "beam", "IFCSLAB": "slab", "IFCWALL": "wall",
    "IFCWALLSTANDARDCASE": "wall", "IFCMEMBER": "member", "IFCPLATE": "plate",
    "IFCFOOTING": "footing", "IFCPILE": "pile", "IFCSTAIR": "stair", "IFCROOF": "roof",
}
_ENTITY = re.compile(r"\s*#(\d+)\s*=\s*([A-Z0-9_]+)\s*\((.*)\)\s*$", re.S)
_X2 = re.compile(r"\\X2\\([0-9A-F]+)\\X0\\")
_X = re.compile(r"\\X\\([0-9A-F]{2})")


def decode_string(raw: str) -> str:
    text = _X2.sub(lambda m: "".join(chr(int(m.group(1)[i:i + 4], 16)) for i in range(0, len(m.group(1)), 4)), raw)
    text = _X.sub(lambda m: chr(int(m.group(1), 16)), text)
    return text.replace("''", "'")


def split_args(body: str) -> list[str]:
    """Split a STEP argument list on top-level commas (strings and nested lists kept whole)."""
    args, depth, in_str, start = [], 0, False, 0
    i = 0
    while i < len(body):
        ch = body[i]
        if in_str:
            if ch == "'":
                if body[i + 1:i + 2] == "'":
                    i += 1
                else:
                    in_str = False
        elif ch == "'":
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(body[start:i].strip())
            start = i + 1
        i += 1
    args.append(body[start:].strip())
    return args


def statements(data: str):
    """Yield STEP statements, splitting on ';' outside quoted strings."""
    start, in_str = 0, False
    for i, ch in enumerate(data):
        if ch == "'":
            in_str = not in_str  # a doubled quote toggles twice, which is correct
        elif ch == ";" and not in_str:
            yield data[start:i]
            start = i + 1


def _text(arg: str) -> str | None:
    return decode_string(arg[1:-1]) if len(arg) >= 2 and arg[0] == "'" else None


def _number(arg: str) -> float | None:
    try:
        return float(arg)
    except ValueError:
        return None


def read_ifc(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    data = raw.split("DATA;", 1)[-1]
    schema = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", raw)
    storeys: dict[str, dict[str, Any]] = {}
    elements: dict[str, dict[str, Any]] = {}
    containment: list[tuple[list[str], str]] = []
    for statement in statements(data):
        match = _ENTITY.match(statement)
        if match is None:
            continue
        eid, etype, body = match.group(1), match.group(2), match.group(3)
        if etype == "IFCBUILDINGSTOREY":
            a = split_args(body)
            storeys[eid] = {"id": f"#{eid}", "global_id": _text(a[0]), "name": _text(a[2]),
                            "elevation": _number(a[9]) if len(a) > 9 else None, "elements": {}}
        elif etype in ELEMENT_TYPES:
            a = split_args(body)
            elements[eid] = {"id": f"#{eid}", "type": ELEMENT_TYPES[etype], "ifc_class": etype,
                             "global_id": _text(a[0]), "name": _text(a[2]),
                             "tag": _text(a[7]) if len(a) > 7 else None, "storey": None}
        elif etype == "IFCRELCONTAINEDINSPATIALSTRUCTURE":
            a = split_args(body)
            containment.append((re.findall(r"#(\d+)", a[4]), a[5].lstrip("#")))
    for members, storey_id in containment:
        storey = storeys.get(storey_id)
        for member in members:
            element = elements.get(member)
            if element is None or storey is None:
                continue
            element["storey"] = storey["name"] or storey["id"]
            storey["elements"][element["type"]] = storey["elements"].get(element["type"], 0) + 1
    totals: dict[str, int] = {}
    for element in elements.values():
        totals[element["type"]] = totals.get(element["type"], 0) + 1
    return {
        "schema": schema.group(1) if schema else None,
        "storeys": sorted(storeys.values(), key=lambda s: (s["elevation"] is None, s["elevation"] or 0.0)),
        "elements": list(elements.values()),
        "totals": totals,
        "not_assigned_to_storey": sum(1 for e in elements.values() if e["storey"] is None),
    }
