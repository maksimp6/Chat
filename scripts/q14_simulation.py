"""Bounded educational Q14 model, never an electrical switching procedure."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

BASELINE_HEAD = "d6f5cafbfcb337006444177d8f15400ab4ddb274"


@dataclass(frozen=True)
class Scenario:
    """Inputs are stipulated conditions, not observations of a real installation."""

    id: str
    title: str
    events: tuple[str, ...]
    expected: str
    arm_conditions_assumed: bool = False
    hidden_voltage: bool = False
    valid_absence_evidence: bool = True
    guards_enabled: bool = True


NORMAL = ("ISOLATE", "SECURE", "PROHIBIT", "CHECK_ABSENCE", "GROUND", "FINISH_SITE", "ADMIT")
EARLY_GROUND = ("ISOLATE", "SECURE", "PROHIBIT", "GROUND", "CHECK_ABSENCE", "FINISH_SITE", "ADMIT")
ARM_ORDER = ("ISOLATE", "CHECK_ABSENCE", "GROUND", "SECURE", "PROHIBIT", "FINISH_SITE", "ADMIT")
CASES = (
    Scenario("S1", "Основной порядок в исправной модели", NORMAL, "MODEL_ACCEPTED"),
    Scenario(
        "S2", "После команды отключения осталось напряжение", NORMAL, "BLOCKED", hidden_voltage=True
    ),
    Scenario("S3", "Попытка заземления до проверки", EARLY_GROUND, "BLOCKED"),
    Scenario("S4", "Поздние запреты при основном порядке", ARM_ORDER, "BLOCKED"),
    Scenario(
        "S5",
        "АРМ: условия исключения заданы, не установлены",
        ARM_ORDER,
        "MODEL_ACCEPTED",
        arm_conditions_assumed=True,
    ),
    Scenario("S6", "Готовность заявлена без действий", ("CLAIM_DONE", "ADMIT"), "BLOCKED"),
    Scenario(
        "S7",
        "Непригодное доказательство отсутствия напряжения",
        NORMAL,
        "BLOCKED",
        valid_absence_evidence=False,
    ),
    Scenario(
        "S8",
        "Отрицательный контроль: снят барьер, осталось напряжение",
        EARLY_GROUND,
        "HAZARDOUS_OPERATION",
        hidden_voltage=True,
        guards_enabled=False,
    ),
    Scenario(
        "S9",
        "После проверки напряжение появилось вновь",
        NORMAL[:4] + ("REENERGIZE",) + NORMAL[4:],
        "BLOCKED",
    ),
    Scenario("S10", "Допуск без завершающих мер", NORMAL[:5] + ("ADMIT",), "BLOCKED"),
    Scenario("S11", "Подготовка оборвана до допуска", NORMAL[:4], "INCOMPLETE"),
    Scenario("S12", "Неизвестное событие не означает успех", ("UNKNOWN",), "ERROR"),
)


@dataclass
class ModelState:
    """Mutable state belongs only to one educational scenario."""

    energized: bool = True
    facts: set[str] = field(default_factory=set)


def requirements(case: Scenario) -> dict[str, tuple[str, ...]]:
    check = ("ISOLATE_COMMAND",)
    if not case.arm_conditions_assumed:
        check += ("SECURE", "PROHIBIT")
    return {
        "ISOLATE": (),
        "SECURE": ("ISOLATE_COMMAND",),
        "PROHIBIT": ("ISOLATE_COMMAND",),
        "CHECK_ABSENCE": check,
        "GROUND": ("ABSENCE_CONFIRMED",),
        "FINISH_SITE": ("GROUND",),
        "CLAIM_DONE": (),
        "REENERGIZE": (),
        "ADMIT": (
            "ISOLATE_COMMAND",
            "SECURE",
            "PROHIBIT",
            "ABSENCE_CONFIRMED",
            "GROUND",
            "FINISH_SITE",
        ),
    }


def check_absence(case: Scenario, state: ModelState) -> tuple[str, str] | None:
    if not case.valid_absence_evidence:
        return "BLOCKED", "Нет пригодного доказательства отсутствия напряжения"
    if state.energized:
        return "BLOCKED", "В модели обнаружено напряжение"
    state.facts.add("ABSENCE_CONFIRMED")
    return None


def advance(event: str, case: Scenario, state: ModelState) -> tuple[str, str] | None:
    if event == "ISOLATE":
        state.energized = case.hidden_voltage
        state.facts.add("ISOLATE_COMMAND")
    elif event == "REENERGIZE":
        state.energized = True
        state.facts.discard("ABSENCE_CONFIRMED")
    elif event == "CHECK_ABSENCE":
        return check_absence(case, state)
    elif event in {"GROUND", "ADMIT"} and state.energized:
        return "HAZARDOUS_OPERATION", "В модели начата опасная операция под напряжением"
    elif event != "CLAIM_DONE":
        state.facts.add(event)
    return None


def simulate(case: Scenario) -> dict[str, object]:
    """Check a deliberately limited state model; never infer a real injury."""
    state = ModelState()
    trace: list[dict[str, object]] = []
    required = requirements(case)

    def finish(outcome: str, reason: str) -> dict[str, object]:
        return {
            "scenario": asdict(case),
            "outcome": outcome,
            "reason": reason,
            "facts": sorted(state.facts),
            "trace": trace,
            "actual_harm": "NOT_ESTABLISHED",
            "injury_severity": "NOT_MODELLED",
            "physical_installation_verified": False,
        }

    for event in case.events:
        trace.append(
            {
                "evidence_id": f"{case.id}:E{len(trace) + 1}",
                "event": event,
                "actor": "simulated_operator",
                "facts_before": sorted(state.facts),
                "energized_in_model": state.energized,
            }
        )
        if event not in required:
            return finish("ERROR", "Неизвестное событие: выполнение остановлено")
        if case.guards_enabled and not set(required[event]).issubset(state.facts):
            return finish("BLOCKED", "Нет требуемых фактов для выбранного профиля")
        if event == "CLAIM_DONE":
            trace[-1]["note"] = "Заявление не создаёт фактов или доказательств"
        outcome = advance(event, case, state)
        if outcome is not None:
            return finish(*outcome)
    if "ADMIT" in state.facts:
        return finish("MODEL_ACCEPTED", "Выполнены только заданные ограничения модели")
    return finish("INCOMPLETE", "Сценарий не дошёл до условного допуска")


def run_cases() -> list[dict[str, object]]:
    """Verify the oracle separately; expected outcomes never enter AI prompts."""
    results = [simulate(case) for case in CASES]
    for case, result in zip(CASES, results, strict=True):
        if result["outcome"] != case.expected:
            raise ValueError(f"scenario_expectation_failed:{case.id}")
    return results
