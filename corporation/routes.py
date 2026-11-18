"""POST /api/corporation: the board window reads here; actions are the owner's commands.

One endpoint, ``action`` picks what to do (status, conveyor, ledger, projects, staff, start,
stop, goal, directive, decision, sold, expense, channel, backup, onboarding, hire, shift, next,
files), the same words as the chat commands, so the window and the chat are one steering wheel.
``shift`` is the door for a worker outside the box (``corporation/outside.py``); ``next`` tells a
worker outside the box what the company does next; ``files`` gives it the files of a project.
A command delivered by an outside executor names its model in ``brain``; the records it writes
carry ``via``. The three commands that start a thread (start, onboarding, hire) are not stamped:
the note the executor leaves before the command is the record of who delivered it.
"""

from __future__ import annotations

import re
from typing import Any

from corporation import cash, commands, context, cycle
from corporation import ledger as journal
from webframe import Blueprint, jsonify, request

corporation_bp = Blueprint("corporation", __name__)

_COMMAND_WORDS = {
    "start": "старт",
    "stop": "стоп",
    "goal": "цель",
    "directive": "директива",
    "decision": "решение",
    "sold": "продано",
    "expense": "расход",
    "channel": "канал",
    "backup": "снимок",
    "onboarding": "онбординг",
    "hire": "найм",
}
# a model id: no spaces, no line breaks
_BRAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]*\Z")
# the commands that start a thread of the box's own runner
_STARTS_THREAD = frozenset({"start", "onboarding", "hire"})


@corporation_bp.route("/api/corporation", methods=["GET", "POST"])
def corporation_action() -> Any:
    data = request.get_json(silent=True) if request.method == "POST" else {}
    data = data if isinstance(data, dict) else {}
    action = str(data.get("action") or request.args.get("action") or "status")
    try:
        if action == "status":
            return jsonify({"success": True, **cycle.status()})
        if action == "conveyor":
            from corporation import conveyor

            return jsonify({"success": True, **conveyor.board()})
        if action == "ledger":
            count = int(data.get("count") or request.args.get("count") or 50)
            return jsonify(
                {
                    "success": True,
                    "entries": journal.ledger().tail(max(1, count)),
                    "verify": journal.ledger().verify(),
                }
            )
        if action == "projects":
            st = journal.state()
            projects = []
            for project in st.projects.values():
                tasks = [st.tasks[t] for t in project["tasks"] if t in st.tasks]
                projects.append(
                    {
                        **project,
                        "tasks": tasks,
                        "products": [
                            p
                            for p in st.products.values()
                            if p["project"] == project["id"]
                        ],
                        "offers": [
                            o
                            for o in st.offers.values()
                            if o["project"] == project["id"]
                        ],
                    }
                )
            return jsonify(
                {
                    "success": True,
                    "projects": projects,
                    "directives": st.directives,
                    "clients": list(st.clients.values()),
                    "decisions": st.decisions[-20:],
                    "cash": cash.summary(st),
                }
            )
        if action == "staff":
            from corporation import staff
            from corporation.roles import all_roles

            st = journal.state()
            return jsonify(
                {
                    "success": True,
                    "staff": st.staff,
                    "hired": st.hired,
                    "onboarding_running": staff.running(),
                    "roles": {
                        name: {"title": role.title, "person": role.person}
                        for name, role in all_roles().items()
                    },
                }
            )
        if action == "next":
            # the company's next step for a worker outside the box; a GET only looks
            from corporation import outside

            look = request.method != "POST" or bool(data.get("dry"))
            return jsonify(
                {"success": True, **outside.next_step({**data, "dry": look})}
            )
        if action == "files":
            # a project's product files, for the worker who continues or inspects
            # the work
            from corporation import outside

            project = data.get("project") or request.args.get("project")
            return jsonify(
                {
                    "success": True,
                    **outside.project_files({**data, "project": project}),
                }
            )
        if action == "shift":
            # a shift done by a worker outside the box, in a role; every record it
            # writes names its model (corporation/outside.py)
            from corporation import outside

            return jsonify({"success": True, **outside.run_shift(data)})
        if action in _COMMAND_WORDS:
            text = str(data.get("text") or "")
            line = f"корпорация: {_COMMAND_WORDS[action]} {text}".strip()
            # a command delivered by an outside executor names its model: the ledger
            # stamps it into every record the command writes (data.via)
            brain = data.get("brain")
            if brain is None or brain == "":
                brain = ""
            elif not isinstance(brain, str) or not _BRAIN.match(brain):
                raise ValueError("brain names a model id")
            # the words that start a thread are not stamped: the note the executor
            # leaves before such a command is the record of who delivered it
            if not brain or action in _STARTS_THREAD:
                reply = commands.handle(line)
            else:
                brain_token = context.set_brain(brain)
                try:
                    reply = commands.handle(line)
                finally:
                    context.reset_brain(brain_token)
            return jsonify({"success": True, "reply": reply})
        return jsonify({"success": False, "error": f"unknown action: {action}"}), 400
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
