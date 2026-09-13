import json
from agent_tools import dispatch_tool, TOOLS_SCHEMA

class AgentCore:
    def __init__(self, system_prompt: str = None):
        self.system_prompt = system_prompt or (
            "Ты — автономный агент разработки в среде Termux (Android). "
            "Тебе доступны инструменты для работы с файловой системой, bash-командами и Git "
            "(включая обычные и bare-репозитории). "
            "Анализируй вывод каждого шага перед следующим действием."
        )
        self.tools = TOOLS_SCHEMA

    def execute_tool_call(self, tool_name: str, arguments: dict) -> dict:
        """Единая точка входа для исполнения вызовов модели."""
        return dispatch_tool(tool_name, arguments)

    def handle_turn(self, tool_calls: list) -> list:
        """
        Обрабатывает список вызовов от модели, возвращая список tool_outputs
        в формате для передачи обратно в контекст LLM.
        """
        results = []
        for call in tool_calls:
            call_id = call.get("id", "call_default")
            func_name = call.get("function", {}).get("name")
            raw_args = call.get("function", {}).get("arguments", {})
            
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {}
            else:
                args = raw_args

            output = self.execute_tool_call(func_name, args)
            results.append({
                "tool_call_id": call_id,
                "output": output
            })
        return results

agent_core = AgentCore()
