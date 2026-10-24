"""Merge noisy OCR observations into stable screen state; read-only."""

import re


class StateMerger:
    """Track observed text nodes across frames with short disappearance grace."""

    def __init__(self, missing_limit=2):
        self.nodes = {}
        self.next_id = 1
        self.missing_limit = missing_limit
        self.revision = 0

    @staticmethod
    def _system_text(node):
        text = node.get("text", "").strip()
        return bool(re.fullmatch(r"\d{1,2}:\d{2}(?:\s.*)?", text))

    @staticmethod
    def _near(a, b):
        ax, ay, aw, ah = a["bbox"]
        bx, by, bw, bh = b["bbox"]
        return abs((ax + aw / 2) - (bx + bw / 2)) <= 28 and abs((ay + ah / 2) - (by + bh / 2)) <= 24

    def merge(self, observations):
        self.revision += 1
        events = []
        matched = set()
        for incoming in observations.get("nodes", []):
            if self._system_text(incoming):
                continue
            candidates = [
                (key, state)
                for key, state in self.nodes.items()
                if key not in matched and self._near(state["node"], incoming)
            ]
            same = [
                (key, state)
                for key, state in candidates
                if state["node"]["text"].casefold() == incoming["text"].casefold()
            ]
            if same:
                key, state = min(
                    same, key=lambda pair: abs(pair[1]["node"]["bbox"][0] - incoming["bbox"][0])
                )
                state["node"] = dict(incoming, stable_id=key)
                state["missing"] = 0
                matched.add(key)
            else:
                key = f"e{self.next_id}"
                self.next_id += 1
                node = dict(incoming, stable_id=key)
                self.nodes[key] = {"node": node, "missing": 0}
                matched.add(key)
                events.append({"type": "element_appeared", "id": key, "text": incoming["text"]})
        for key, state in list(self.nodes.items()):
            if key not in matched:
                state["missing"] += 1
                if state["missing"] >= self.missing_limit:
                    events.append(
                        {"type": "element_disappeared", "id": key, "text": state["node"]["text"]}
                    )
                    del self.nodes[key]
        return {
            "revision": self.revision,
            "nodes": [state["node"] for state in self.nodes.values()],
            "events": events,
            "count": len(self.nodes),
        }
