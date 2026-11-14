"""Read-only look at COM registrations — the "first API check" for a workstation."""

from __future__ import annotations

import platform
import struct
import sys
from typing import Any


def environment() -> dict[str, Any]:
    return {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "python_bitness": struct.calcsize("P") * 8,
    }


def progid_info(progid: str) -> dict[str, Any]:
    """Is `progid` registered, which CLSID and which server binary does it point to."""
    info: dict[str, Any] = {"progid": progid, "registered": False}
    try:
        import winreg
    except ImportError:
        info["note"] = "not a Windows machine"
        return info
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\CLSID") as key:
            clsid = winreg.QueryValue(key, None)
    except OSError:
        return info
    info.update(registered=True, clsid=clsid)
    for view_name, view in (("64-bit", winreg.KEY_WOW64_64KEY), ("32-bit", winreg.KEY_WOW64_32KEY)):
        for server in ("LocalServer32", "InprocServer32"):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\{server}", 0,
                                    winreg.KEY_READ | view) as key:
                    info.setdefault("servers", []).append(
                        {"registry_view": view_name, "kind": server, "path": winreg.QueryValue(key, None)})
            except OSError:
                continue
    return info


def find_progids(prefixes: tuple[str, ...], limit: int = 50) -> list[str]:
    """ProgIDs in HKEY_CLASSES_ROOT starting with any prefix (case-insensitive)."""
    try:
        import winreg
    except ImportError:
        return []
    found: list[str] = []
    lowered = tuple(p.lower() for p in prefixes)
    index = 0
    while len(found) < limit:
        try:
            name = winreg.EnumKey(winreg.HKEY_CLASSES_ROOT, index)
        except OSError:
            break
        index += 1
        if name.lower().startswith(lowered):
            found.append(name)
    return found


def com_members(obj: Any, limit: int = 300) -> list[dict[str, Any]]:
    """Names of the methods/properties a late-bound (pywin32) COM object exposes,
    read from its type information. Returns [] when the object has none."""
    try:
        import pythoncom  # type: ignore

        disp = getattr(obj, "_oleobj_", obj)
        tinfo = disp.GetTypeInfo()
        attr = tinfo.GetTypeAttr()
        kinds = {pythoncom.INVOKE_FUNC: "method", pythoncom.INVOKE_PROPERTYGET: "property_get",
                 pythoncom.INVOKE_PROPERTYPUT: "property_put", pythoncom.INVOKE_PROPERTYPUTREF: "property_putref"}
        out: list[dict[str, Any]] = []
        for i in range(attr.cFuncs):
            fd = tinfo.GetFuncDesc(i)
            names = tinfo.GetNames(fd.memid)
            out.append({"name": names[0], "kind": kinds.get(fd.invkind, str(fd.invkind)),
                        "params": list(names[1:])})
            if len(out) >= limit:
                break
        return out
    except Exception:  # noqa: BLE001 - introspection is best-effort
        return []
