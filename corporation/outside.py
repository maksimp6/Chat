"""The door for workers outside the box: a shift done by a strong model, in a role.

Owner, 18.11.2026: "we can cheat to have unlimited powerful smart workers with data so we
must use this advantage to for our company". The local runner is one small model; the
owner also has strong models with the internet at hand. So a role's shift may be done
outside the box: the outside worker does the thinking and the writing where it runs, and
brings the result here — product files and the same ``corporation`` / ``channels`` tool
calls a local turn would make. Nothing is generated in Logos for such a shift: no model
is called here, no data leaves, the local cycle is untouched.

What the door guarantees:

- the **role** is real (built-in or hired), and every record of the shift has it as actor;
- the **brain** is named by the caller and stamped into every record the shift writes
  (``data.via``, by the ledger itself), so the journal never passes outside work off as
  the local runner's, and the embeddings carry the same mark;
- **files** land only inside the project's own products folder;
- the **tools** are the company's own functions, with their own checks — a product must
  exist as a file, an offer needs a product the critic passed, and everything going
  outside still passes the Court gate in ``channels``;
- the shift ends with one ``note`` of the role: what was done, which calls, which files.

One function; ``POST /api/corporation {action: "shift", …}`` is its only caller.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

from corporation import context
from corporation import ledger as journal
from corporation.roles import get_role

__all__ = ["run_shift"]


def _write_file(project: str, item: Any) -> str:
    """One product file of the shift, inside ``products/<project>/``; returns its path."""
    if not isinstance(item, dict):
        raise ValueError("a file is {path, content} or {path, content_base64}")
    relative = Path(str(item.get("path") or ""))
    content = item.get("content")
    encoded = item.get("content_base64")
    # text as it is; anything else (an icon, an archive) as base64
    if isinstance(encoded, str) and content is None:
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"content_base64 of {relative} is not base64") from exc
    elif isinstance(content, str) and encoded is None:
        payload = content.encode("utf-8")
    else:
        raise ValueError("a file needs either text content or content_base64")
    if not str(relative) or str(relative) == ".":
        raise ValueError("a file needs a relative path")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"a file path stays inside the project's folder: {relative}")
    from corporation.tools import _project_dir

    folder = _project_dir(project)
    target = folder / relative
    try:
        target.resolve().relative_to(folder.resolve())
    except ValueError as exc:
        raise ValueError(
            f"a file path stays inside the project's folder: {relative}"
        ) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return str(target)


def _call(call: Any) -> dict[str, Any]:
    """One tool call of the shift: the company's own function, with its own checks."""
    if not isinstance(call, dict):
        raise ValueError("a call is {category, tool, arguments}")
    category = str(call.get("category") or "corporation")
    name = str(call.get("tool") or "")
    arguments = call.get("arguments")
    if category == "corporation":
        from corporation.tools import CORPORATION_TOOLS as table
    elif category == "channels":
        from corporation.channels import CHANNEL_TOOLS as table
    else:
        raise ValueError("category is corporation or channels")
    if name not in table:
        raise ValueError(f"no such {category} action: {name}; actions: {', '.join(table)}")
    try:
        result = table[name]["func"](dict(arguments or {}))
    except Exception as exc:  # the tool's own refusal is the answer, as in a local turn
        return {
            "category": category,
            "tool": name,
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {"category": category, "tool": name, "success": True, "result": result}


def run_shift(data: dict[str, Any]) -> dict[str, Any]:
    """Do one outside shift: write the files, make the calls, leave the note.

    ``data``: role, brain, optional project and task, files [{path, content}] (text) or
    [{path, content_base64}] (anything else: an icon, an archive), calls
    [{category, tool, arguments}], reply (what the worker says it did). The calls run in
    order and stop at the first one a tool refuses; the answer says how far it got.
    """
    role = str(data.get("role") or "")
    if get_role(role) is None:
        raise ValueError(f"no such employee: {role}")
    brain = str(data.get("brain") or "").strip()
    if not brain:
        raise ValueError("brain is required: name the model that does this shift")
    project = str(data.get("project") or "") or None
    task = str(data.get("task") or "") or None
    files = data.get("files") or []
    calls = data.get("calls") or []
    if not isinstance(files, list) or not isinstance(calls, list):
        raise ValueError("files and calls are lists")
    st = journal.state()
    if project is not None and project not in st.projects:
        raise ValueError(
            f"no such project: {project}; projects: {', '.join(st.projects) or 'none'}"
        )
    if files and project is None:
        raise ValueError("files need a project: they go into its products folder")
    if task is not None and task not in st.tasks:
        raise ValueError(f"no such task: {task}")

    role_token = context.set_role(role)
    brain_token = context.set_brain(brain)
    try:
        written = [_write_file(project or "", item) for item in files]
        results: list[dict[str, Any]] = []
        for call in calls:
            outcome = _call(call)
            results.append(outcome)
            if not outcome["success"]:
                break
        failed = next((r for r in results if not r["success"]), None)
        note = journal.ledger().append(
            "note",
            role,
            {
                "text": str(data.get("reply") or "")[:4000]
                or f"смена вне ящика: {len(written)} файлов, {len(results)} вызовов",
                "shift": "outside",
                "files": " ".join(written),
                "calls": " ".join(f"{r['category']}.{r['tool']}" for r in results),
                "stopped_at": f"{failed['category']}.{failed['tool']}: {failed['error']}"
                if failed
                else "",
            },
            project=project,
            task=task,
        )
    finally:
        context.reset_brain(brain_token)
        context.reset(role_token)
    return {
        "role": role,
        "brain": brain,
        "files": written,
        "results": results,
        "calls_asked": len(calls),
        "calls_made": len(results),
        "complete": failed is None and len(results) == len(calls),
        "note": note["seq"],
    }
