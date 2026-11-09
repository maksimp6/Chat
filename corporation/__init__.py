"""The corporation: a mobile app company of roles that works on the owner's bare-metal
runner and keeps every step in a ledger that forgets nothing (docs/agents/logos-corporation.md).

Owner, 09.11.2026: "build me a mobile app company" — the company makes and sells mobile
applications: source projects for Android and installable mobile web applications (PWA);
``lines.COMPANY_LINES`` and ``roles.DEFAULT_GOAL`` say so.

Owner, 08.11.2026: "i want full company running there, imagine like corporation
making money so let them be there, build infrastructure and no lose any data of it
and lets steer it"; "so i as an owner do what owner must and nothing more"; "we must
make system synchronous step after step".

- ``ledger``   the append-only, hash-chained journal and the state derived from it
- ``roles``    the departments: instructions, tools, budgets, model
- ``tools``    the ``corporation`` tool the roles call (projects, tasks, products, offers)
- ``channels`` the company's own hands outside (Telegram, mail) behind the Court gate
- ``cash``     revenue and expenses, counted from the ledger
- ``cycle``    the synchronous loop: one step, one role, one runner; shifts by model
- ``office``   an employee's own dialog (fixed id ``logos-corp-<role>``), where the owner talks to one
- ``staff``    onboarding: each employee writes their own personnel file; the Court checks the set
- ``commands`` the owner's steering lines in the chat ("корпорация: …")
- ``routes``   POST /api/corporation for the board window
"""
