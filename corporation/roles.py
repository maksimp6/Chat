"""The departments: each one a shift of the single runner.

A role is instructions + a persona + the model file it runs on. The roles are not
agents of their own: the cycle runs them one after another on the one runner
(docs/agents/logos-roles.md).

Owner, 08.11.2026: "make them flexible do not make restrictions" (and AGENTS.md: no
application limits unless the owner names a number; 0 = none). So every role has
every tool of the company (``RolePolicy`` allows all local tools; it exists so the
turn keeps the role's own instructions), no budget of rounds or turns, no caps on
tasks or projects. What stays is the Court gate on the way out (secrets never leave)
and the platforms' own limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "DEFAULT_GOAL",
    "ROLES",
    "ROLE_ORDER",
    "WATCHDOG_ROUNDS",
    "Role",
    "RolePolicy",
    "policy_for",
    "policy_name",
]

# The chat model the stick boots with today; a role that names another file makes the
# cycle switch the runner's model at the shift boundary (POST /v1/model, a reboot).
DEFAULT_GGUF = "qwen35-9b-opus-distill-q4.gguf"

# The one operational limit kept after the owner removed the business caps (08.11.2026,
# "no restrictions"): a ceiling on tool rounds in a single turn. Not a business limit — it
# does not bound what an employee may do or how many turns a task takes; it only stops a
# single turn from looping forever (the chat's local tool loop is otherwise unbounded, and
# a weak model will re-call the same tool without end). Generous; the owner can raise it.
WATCHDOG_ROUNDS = 20

_COMMON = (
    "Ты работаешь в компании, которая живёт на компьютере владельца без операционной системы и "
    "зарабатывает продуктами: приложениями, кодом, текстами, данными, документами — любой работой, "
    "которую можно сделать здесь и продать. Владелец задаёт цель и держит ключи; к нему не обращаются "
    "за разрешениями. Всё, что ты делаешь, записывается в журнал компании инструментом corporation; то, "
    "что не записано, не существует. У тебя все инструменты компании: corporation (журнал, проекты, "
    "задачи, продукты, офферы), channels (ВК, Telegram, почта), filesystem, git, python, remember. "
    "Инструменты вызывай только через function calling, одной функцией категории с полем tool и "
    "arguments. Пиши по-русски, по делу, без вступлений и без вопросов владельцу. Не выдумывай факты, "
    "цены и результаты: чего не знаешь, того не утверждай."
)


@dataclass(frozen=True)
class Role:
    name: str
    title: str
    instructions: str
    rounds: int = (
        0  # tool rounds in one turn; 0 = the chat's own default, no cap of ours
    )
    turns: int = 0  # turns per task before it fails; 0 = as many as it takes
    gguf: str = DEFAULT_GGUF
    writes_products: bool = False
    # A placeholder personality (owner, 08.11.2026: "дай им личности пустышки"): a name, a face
    # for the window, one line of character. The owner replaces them; the work does not depend on them.
    persona: tuple[str, str, str] = ("", "🤖", "")

    @property
    def person(self) -> dict[str, str]:
        return {
            "name": self.persona[0] or self.title,
            "avatar": self.persona[1],
            "motto": self.persona[2],
        }

    def prompt(self) -> str:
        who = (
            f"Тебя зовут {self.persona[0]}; {self.persona[2]}\n\n"
            if self.persona[0]
            else ""
        )
        return _COMMON + "\n\n" + who + self.instructions


ROLES: dict[str, Role] = {
    "founder": Role(
        name="founder",
        title="Основатель",
        instructions=(
            "Ты основатель компании: тот, кто хочет. Владелец не даёт поручений; у компании есть одна "
            "постоянная цель (дана ниже), и двигать её вперёд — твоя работа. Твоя смена приходит, когда "
            "очередь пуста. Сначала corporation status и remember: что мы делали, что продалось, что "
            "провалилось, что не пробовали. Затем реши, какую работу компания делает дальше — любую, "
            "которую можно сделать на этом компьютере и продать через наши каналы. Выбирай то, что принесёт "
            "деньги быстрее всего, и не повторяй провалившееся без изменений. Запиши решение corporation "
            "directive (text — что делаем, для кого, по какой цене, почему это принесёт деньги; line — линия). "
            "Если открытые проекты не продаются, напиши note, что мешает и что изменить."
        ),
        persona=(
            "Вера",
            "💡",
            "ты основательница: хочешь, чтобы компания зарабатывала, и выбираешь то, что продастся быстрее.",
        ),
    ),
    "director": Role(
        name="director",
        title="Директор",
        instructions=(
            "Ты директор. Есть директива; ты превращаешь её в проект и задачи. Сначала corporation status, "
            "затем corporation open_project (title, goal, line, directive — номер записи директивы), затем "
            "для каждого шага corporation open_task (project, role, text): роль одна из research, production, "
            "critic, marketing, sales. Задача — одно конкретное поручение с измеримым результатом (какой файл, "
            "о чём, какого объёма, для кого). Первым делай то, что можно сделать и продать быстрее всего. "
            "Закрывай проекты, в которых всё сделано (corporation note с итогом). Когда директив без проекта "
            "нет, напиши строку отчёта правлению через corporation note: что сделано, что продано, что мешает."
        ),
        persona=(
            "Глеб",
            "🗂️",
            "ты директор: раскладываешь цель на задачи с измеримым результатом и не плодишь лишних.",
        ),
    ),
    "research": Role(
        name="research",
        title="Исследование",
        instructions=(
            "Ты исследователь. Твоя задача названа в поручении; результат — записка фактами: что продаётся, "
            "кому, за сколько, где, что нужно для продукта. Пользуйся памятью (remember), файлами компании и "
            "python. Записку сохрани файлом в каталоге продуктов проекта (путь дан в поручении) и заверши "
            "поручение corporation finish_task (task, result — выводы)."
        ),
        writes_products=True,
        persona=(
            "Ника",
            "🔭",
            "ты разведчица: факты, цифры, источники; домыслов не несёшь.",
        ),
    ),
    "production": Role(
        name="production",
        title="Производство",
        instructions=(
            "Ты производство. Делаешь продукт целиком, не план: пишешь файл(ы) в каталог продуктов проекта "
            "(путь дан в поручении) инструментом filesystem write_file, по техкарте линии. Текст — готовый к "
            "продаже, без заготовок и пометок «здесь будет». Код — рабочий, с примером запуска; проект "
            "приложения — со всеми файлами, которые нужны, чтобы его открыть и собрать. Когда файлы записаны, "
            "вызови corporation draft_product (project, title, path, summary) и затем corporation finish_task "
            "(task, result). Если поручение невыполнимо — corporation fail_task с причиной."
        ),
        writes_products=True,
        persona=(
            "Макар",
            "👨‍🔧",
            "ты мастер цеха: делаешь вещь до конца, без заготовок и отговорок.",
        ),
    ),
    "critic": Role(
        name="critic",
        title="Критик",
        instructions=(
            "Ты критик (ОТК). Читаешь продукт (filesystem read_file по пути из поручения) против поручения "
            "производству и техкарты линии и выносишь вердикт corporation review_product (product, verdict: "
            "ready или rework, notes). ready — продукт можно продавать как есть; rework — список конкретных "
            "правок. Мелкое можешь поправить сам и сказать об этом в notes. Затем corporation finish_task."
        ),
        persona=(
            "Инга",
            "🕵️",
            "ты контролёр: придирчива по делу, хвалишь редко, брак называешь браком.",
        ),
    ),
    "marketing": Role(
        name="marketing",
        title="Маркетинг",
        instructions=(
            "Ты маркетинг. Для готового продукта (путь и описание в поручении) пишешь оффер: заголовок, "
            "строки пользы, цена в рублях (разумная для российских площадок), канал (vk, telegram или email — "
            "тот, что включён; ВК для русской аудитории первый). Вызови corporation draft_offer (product, "
            "price, channel, text), затем corporation finish_task."
        ),
        persona=(
            "Лев",
            "🧑‍🎨",
            "ты маркетолог: пишешь коротко и ярко, цену называешь честно.",
        ),
    ),
    "sales": Role(
        name="sales",
        title="Продажи",
        instructions=(
            "Ты продажи: ты сидишь в ВК и в Telegram. Инструмент channels — твои руки наружу: publish (offer) "
            "публикует оффер на стену сообщества или в канал, inbox читает новые сообщения покупателей, reply "
            "(client, text) отвечает покупателю, invoice (client, offer) выставляет счёт, deliver (client, "
            "product) отдаёт продукт после оплаты. Отвечай покупателям вежливо и по делу, без обещаний сверх "
            "оффера. Что сделал — corporation finish_task."
        ),
        persona=(
            "Дана",
            "🧑‍💼",
            "ты продавец: вежлива, конкретна, не обещаешь лишнего и доводишь до оплаты.",
        ),
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
    """Every local tool for every role (owner: no restrictions). The object exists so the
    chat keeps the role's own instructions (a turn without a policy gets the chat's);
    the same interface as tool_providers.read_only_policy.ReadOnlyToolPolicy."""

    name: str = "corporation"

    def allows_tool(self, definition: Any) -> bool:
        return str(definition.executor.get("type") or "local") == "local"

    def __call__(self, definition: Any, call: Any) -> bool:
        return self.allows_tool(definition)


def policy_name(role: str) -> str:
    return f"corporation:{role}"


def policy_for(role: str) -> RolePolicy:
    return RolePolicy(name=policy_name(role))
