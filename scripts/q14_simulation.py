"""Bounded educational Q14 model, never an electrical switching procedure."""

from __future__ import annotations

from dataclasses import asdict, dataclass

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
    Scenario("S2", "После команды отключения осталось напряжение", NORMAL, "BLOCKED", hidden_voltage=True),
    Scenario("S3", "Попытка заземления до проверки", EARLY_GROUND, "BLOCKED"),
    Scenario("S4", "Поздние запреты при основном порядке", ARM_ORDER, "BLOCKED"),
    Scenario("S5", "АРМ: условия исключения заданы, не установлены", ARM_ORDER, "MODEL_ACCEPTED", arm_conditions_assumed=True),
    Scenario("S6", "Готовность заявлена без действий", ("CLAIM_DONE", "ADMIT"), "BLOCKED"),
    Scenario("S7", "Непригодное доказательство отсутствия напряжения", NORMAL, "BLOCKED", valid_absence_evidence=False),
    Scenario("S8", "Отрицательный контроль: снят барьер, осталось напряжение", EARLY_GROUND, "HAZARDOUS_OPERATION", hidden_voltage=True, guards_enabled=False),
    Scenario("S9", "После проверки напряжение появилось вновь", NORMAL[:4] + ("REENERGIZE",) + NORMAL[4:], "BLOCKED"),
    Scenario("S10", "Допуск без завершающих мер", NORMAL[:5] + ("ADMIT",), "BLOCKED"),
    Scenario("S11", "Подготовка оборвана до допуска", NORMAL[:4], "INCOMPLETE"),
    Scenario("S12", "Неизвестное событие не означает успех", ("UNKNOWN",), "ERROR"),
)


def simulate(case: Scenario) -> dict[str, object]:
    """Check a deliberately limited state model; never infer a real injury."""
    facts: set[str] = set()
    energized = True
    trace: list[dict[str, object]] = []

    def finish(outcome: str, reason: str) -> dict[str, object]:
        return {
            "scenario": asdict(case),
            "outcome": outcome,
            "reason": reason,
            "facts": sorted(facts),
            "trace": trace,
            "actual_harm": "NOT_ESTABLISHED",
            "injury_severity": "NOT_MODELLED",
            "physical_installation_verified": False,
        }

    def missing(*required: str) -> bool:
        return case.guards_enabled and not set(required).issubset(facts)

    for event in case.events:
        trace.append({
            "evidence_id": f"{case.id}:E{len(trace) + 1}",
            "event": event,
            "actor": "simulated_operator",
            "facts_before": sorted(facts),
            "energized_in_model": energized,
        })
        if event == "ISOLATE":
            energized = case.hidden_voltage
            facts.add("ISOLATE_COMMAND")
        elif event in {"SECURE", "PROHIBIT"}:
            if missing("ISOLATE_COMMAND"):
                return finish("BLOCKED", "Нет предшествующего этапа отключения")
            facts.add(event)
        elif event == "CHECK_ABSENCE":
            required = ("ISOLATE_COMMAND",) if case.arm_conditions_assumed else (
                "ISOLATE_COMMAND", "SECURE", "PROHIBIT"
            )
            if missing(*required):
                return finish("BLOCKED", "Не выполнены предпосылки выбранного профиля")
            if not case.valid_absence_evidence:
                return finish("BLOCKED", "Нет пригодного доказательства отсутствия напряжения")
            if energized:
                return finish("BLOCKED", "В модели обнаружено напряжение")
            facts.add("ABSENCE_CONFIRMED")
        elif event == "REENERGIZE":
            energized = True
            facts.discard("ABSENCE_CONFIRMED")
        elif event == "GROUND":
            if missing("ABSENCE_CONFIRMED"):
                return finish("BLOCKED", "Нет действующего подтверждения отсутствия напряжения")
            if energized:
                return finish("HAZARDOUS_OPERATION", "В модели начато заземление под напряжением")
            facts.add("GROUND")
        elif event == "FINISH_SITE":
            if missing("GROUND"):
                return finish("BLOCKED", "Завершающие меры заявлены до заземления")
            facts.add("FINISH_SITE")
        elif event == "CLAIM_DONE":
            trace[-1]["note"] = "Заявление не изменяет состояние и не создаёт доказательств"
        elif event == "ADMIT":
            if missing("ISOLATE_COMMAND", "SECURE", "PROHIBIT", "ABSENCE_CONFIRMED", "GROUND", "FINISH_SITE"):
                return finish("BLOCKED", "Недостаточно фактов для условного допуска")
            if energized:
                return finish("HAZARDOUS_OPERATION", "В модели допущена работа под напряжением")
            facts.add("ADMIT")
        else:
            return finish("ERROR", "Неизвестное событие: выполнение остановлено")
    if "ADMIT" in facts:
        return finish("MODEL_ACCEPTED", "Выполнены только заданные ограничения модели")
    return finish("INCOMPLETE", "Сценарий не дошёл до условного допуска")


def run_cases() -> list[dict[str, object]]:
    """Verify the oracle separately; expected outcomes never enter AI prompts."""
    results = [simulate(case) for case in CASES]
    for case, result in zip(CASES, results, strict=True):
        if result["outcome"] != case.expected:
            raise ValueError(f"scenario_expectation_failed:{case.id}")
    return results
