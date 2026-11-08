"""The departments: each one a shift of the single runner.

A role is instructions + the tools it may call + a budget + the model file it runs on.
The roles are not agents of their own: the cycle runs them one after another on the
one runner (docs/agents/logos-roles.md). The policy objects here are what the chat
hands to the tool loop as ``execution_policy``: they are supplied by the cycle,
outside anything the model writes, so a role can call nothing but its list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["ROLES", "ROLE_ORDER", "Role", "RolePolicy", "policy_for", "policy_name"]

# The chat model the stick boots with today; a role that names another file makes the
# cycle switch the runner's model at the shift boundary (POST /v1/model, a reboot).
DEFAULT_GGUF = "qwen35-9b-opus-distill-q4.gguf"

_FS_READ = {"read_file", "list_directory", "glob_search", "grep_search", "file_stat", "python_ast_outline", "calculate_hash"}
_FS_WRITE = _FS_READ | {"write_file", "apply_patch", "make_directory", "copy_or_move_file"}
_GIT_READ = {"git_status", "git_log", "git_diff", "git_branches"}
_CORP_ALL = {"open_project", "open_task", "finish_task", "fail_task", "draft_product", "review_product", "draft_offer", "note", "status"}
# "directive" is the founder's alone: the one who wants writes what the company does next.

_COMMON = (
    "Ты работаешь в компании, которая живёт на компьютере владельца без операционной системы и "
    "зарабатывает продуктами: текстами, кодом, данными, документами. Владелец задаёт направление и "
    "держит ключи; к нему не обращаются за разрешениями. Всё, что ты делаешь, записывается в журнал "
    "компании инструментом corporation; то, что не записано, не существует. Инструменты вызывай только "
    "через function calling, одной функцией категории с полем tool и arguments. Пиши по-русски, коротко, "
    "по делу, без вступлений и без вопросов владельцу. Не выдумывай факты, цены и результаты: чего не "
    "знаешь, того не утверждай."
)


@dataclass(frozen=True)
class Role:
    name: str
    title: str
    instructions: str
    tools: frozenset[str]
    rounds: int = 12            # tool rounds in one turn
    turns: int = 4              # turns per task before it fails
    gguf: str = DEFAULT_GGUF
    writes_products: bool = False

    def prompt(self) -> str:
        return _COMMON + "\n\n" + self.instructions


ROLES: dict[str, Role] = {
    "founder": Role(
        name="founder",
        title="Основатель",
        instructions=(
            "Ты основатель компании: тот, кто хочет. Владелец не даёт поручений; у компании есть одна "
            "постоянная цель (дана ниже), и двигать её вперёд — твоя работа. Твоя смена приходит, когда "
            "очередь пуста. Сначала corporation status и remember: что мы делали, что продалось, что "
            "провалилось, что не пробовали. Затем реши, какую работу компания делает дальше — любую, "
            "которую можно сделать на этом компьютере и продать через наши каналы: тексты, код, данные, "
            "документы, переводы, справочники, боты. Выбирай то, что принесёт деньги быстрее всего, и "
            "не повторяй провалившееся без изменений. Запиши решение corporation directive (text — "
            "что делаем, для кого, по какой цене, почему это принесёт деньги). Одна директива за смену. "
            "Если открытых проектов уже два, не открывай новое: напиши note, что мешает им продаться, и "
            "что изменить."
        ),
        tools=frozenset({"directive", "note", "status", "remember"}),
        rounds=8,
        turns=1,
    ),
    "director": Role(
        name="director",
        title="Директор",
        instructions=(
            "Ты директор. Правление дало директиву; ты превращаешь её в проект и задачи. Сначала "
            "corporation status, затем corporation open_project (title, goal, directive — номер записи "
            "директивы), затем для каждого шага corporation open_task (project, role, text): роль одна из "
            "research, production, marketing. Задача — одно конкретное поручение с измеримым результатом "
            "(какой файл, о чём, какого объёма, для кого). Первым проектом делай то, что можно сделать и "
            "продать за один день. Не больше шести задач на проект. Закрывай проекты, в которых всё "
            "сделано (corporation note с итогом). Когда директив без проекта нет, напиши одну строку "
            "отчёта правлению через corporation note: что сделано, что продано, что мешает."
        ),
        tools=frozenset(_CORP_ALL | {"remember"}),
        rounds=10,
        turns=2,
    ),
    "research": Role(
        name="research",
        title="Исследование",
        instructions=(
            "Ты исследователь. Твоя задача названа в поручении; результат — короткая записка фактами: "
            "что продаётся, кому, за сколько, где, что нужно для продукта. Пользуйся памятью (remember) "
            "и файлами компании (filesystem, только чтение). Записку сохрани файлом в каталоге продуктов "
            "проекта (путь дан в поручении) и заверши поручение corporation finish_task (task, result — "
            "три-пять строк выводов)."
        ),
        tools=frozenset({"remember", "finish_task", "fail_task", "note", "status", "python"} | _FS_WRITE),
        rounds=12,
        turns=3,
        writes_products=True,
    ),
    "production": Role(
        name="production",
        title="Производство",
        instructions=(
            "Ты производство. Делаешь продукт целиком, не план: пишешь файл(ы) в каталог продуктов проекта "
            "(путь дан в поручении) инструментом filesystem write_file. Текст — готовый к продаже, без "
            "заготовок и пометок «здесь будет». Код — рабочий, с примером запуска. Когда файл записан, "
            "вызови corporation draft_product (project, title, path, summary) и затем corporation "
            "finish_task (task, result). Если поручение невыполнимо — corporation fail_task с причиной."
        ),
        tools=frozenset({"remember", "draft_product", "finish_task", "fail_task", "note", "status", "python"} | _FS_WRITE | _GIT_READ),
        rounds=14,
        turns=4,
        writes_products=True,
    ),
    "critic": Role(
        name="critic",
        title="Критик",
        instructions=(
            "Ты критик. Читаешь продукт (filesystem read_file по пути из поручения) против поручения "
            "производству и выносишь вердикт corporation review_product (product, verdict: ready или "
            "rework, notes). ready — продукт можно продавать как есть; rework — список конкретных правок. "
            "Ты не правишь продукт сам. Затем corporation finish_task."
        ),
        tools=frozenset({"review_product", "finish_task", "fail_task", "note", "status"} | _FS_READ | _GIT_READ),
        rounds=8,
        turns=2,
    ),
    "marketing": Role(
        name="marketing",
        title="Маркетинг",
        instructions=(
            "Ты маркетинг. Для готового продукта (путь и описание в поручении) пишешь оффер: заголовок, "
            "три строки пользы, цена в рублях (разумная для российских площадок, целое число), канал "
            "(telegram или email). Вызови corporation draft_offer (product, price, channel, text), затем "
            "corporation finish_task."
        ),
        tools=frozenset({"remember", "draft_offer", "finish_task", "fail_task", "note", "status"} | _FS_READ),
        rounds=8,
        turns=2,
    ),
    "sales": Role(
        name="sales",
        title="Продажи",
        instructions=(
            "Ты продажи. Инструмент channels — твои руки наружу: publish (offer) публикует оффер в канал "
            "компании, inbox читает новые сообщения покупателей, reply (client, text) отвечает покупателю, "
            "invoice (client, offer) выставляет счёт, deliver (client, product) отдаёт продукт после оплаты "
            "(до оплаты инструмент откажет). Отвечай покупателям вежливо и по делу, без обещаний сверх "
            "оффера. Что сделал — corporation finish_task."
        ),
        tools=frozenset({"publish", "inbox", "reply", "invoice", "deliver", "finish_task", "fail_task", "note", "status"} | _FS_READ),
        rounds=10,
        turns=2,
    ),
}

# The order shifts take when several roles have work: the pipeline's order, so a product
# started today is reviewed, offered and sold before the next one begins.
ROLE_ORDER = ("director", "sales", "marketing", "critic", "production", "research")

# The company's standing goal when the owner has not written one (корпорация: цель …).
DEFAULT_GOAL = (
    "Увеличивать подтверждённую выручку компании любой законной работой, которую можно "
    "сделать на этом компьютере и продать через каналы компании. Не ждать поручений."
)


@dataclass(frozen=True)
class RolePolicy:
    """What the tool loop may run for a role: the role's action names, local executors only.
    The same interface as tool_providers.read_only_policy.ReadOnlyToolPolicy."""

    allowed_tools: frozenset[str]
    name: str = "corporation"

    def allows_tool(self, definition: Any) -> bool:
        return definition.name in self.allowed_tools and str(definition.executor.get("type") or "local") == "local"

    def __call__(self, definition: Any, call: Any) -> bool:
        return self.allows_tool(definition)


def policy_name(role: str) -> str:
    return f"corporation:{role}"


def policy_for(role: str) -> RolePolicy:
    return RolePolicy(ROLES[role].tools, name=policy_name(role))
