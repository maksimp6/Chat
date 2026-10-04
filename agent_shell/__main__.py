"""CLI: python -m agent_shell add|list|run-next|approve|show|recover."""

from __future__ import annotations

import argparse
import json
import os
import sys

from agent_shell.handlers import default_handlers
from agent_shell.runner import run_next
from agent_shell.store import TaskStore


def main(argv=None, handlers=None):
    parser = argparse.ArgumentParser(prog="agent_shell", description=__doc__)
    parser.add_argument("--db", default=os.environ.get("ALICE_SHELL_DB", "agent_shell.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("--role", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--kind", required=True)
    add.add_argument("--needs-approval", action="store_true")
    commands.add_parser("list")
    commands.add_parser("run-next")
    commands.add_parser("recover")
    for name in ("approve", "show"):
        commands.add_parser(name).add_argument("task_id", type=int)
    args = parser.parse_args(argv)
    store = TaskStore(args.db)
    if args.command == "add":
        print(
            store.add(
                role=args.role, title=args.title, kind=args.kind, needs_approval=args.needs_approval
            )
        )
    elif args.command == "list":
        for task in store.list():
            print(
                f"{task['id']}\t{task['status']}\t{task['role']}\t{task['kind']}\t{task['title']}"
            )
    elif args.command == "run-next":
        task = run_next(store, handlers if handlers is not None else default_handlers())
        print("no queued task" if task is None else f"{task['id']} {task['status']}")
    elif args.command == "recover":
        print(json.dumps(store.recover_interrupted()))
    elif args.command == "approve":
        try:
            store.approve(args.task_id)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    else:
        task = store.get(args.task_id)
        if task is None:
            print("task not found", file=sys.stderr)
            return 1
        print(json.dumps({**task, "events": store.events(args.task_id)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
