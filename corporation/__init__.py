"""The corporation: a company of roles that works on the owner's bare-metal runner
and keeps every step in a ledger that forgets nothing (docs/agents/logos-corporation.md).

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
- ``commands`` the owner's steering lines in the chat ("корпорация: …")
- ``routes``   POST /api/corporation for the board window
"""
