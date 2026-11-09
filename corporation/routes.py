"""POST /api/corporation: the board window reads here; actions are the owner's commands.

One endpoint, ``action`` picks what to do (status, conveyor, ledger, projects, start, stop,
directive, decision, sold, expense, channel, backup), the same words as the chat
commands, so the window and the chat are one steering wheel.
"""

from __future__ import annotations

from typing import Any

from corporation import cash, commands, cycle
from corporation import ledger as journal
from webframe import Blueprint, jsonify, request

corporation_bp = Blueprint("corporation", __name__)

_COMMAND_WORDS = {
    "start": "старт",
    "stop": "стоп",
    "directive": "директива",
    "decision": "решение",
    "sold": "продано",
    "expense": "расход",
    "channel": "канал",
    "backup": "снимок",
}


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
        if action in _COMMAND_WORDS:
            text = str(data.get("text") or "")
            reply = commands.handle(
                f"корпорация: {_COMMAND_WORDS[action]} {text}".strip()
            )
            return jsonify({"success": True, "reply": reply})
        return jsonify({"success": False, "error": f"unknown action: {action}"}), 400
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
