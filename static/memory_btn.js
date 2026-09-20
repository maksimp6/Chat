(function () {
    "use strict";

    function createMemoryButton() {
        const header = document.getElementById("header");
        if (!header) {
            console.error("[Memory] Header not found");
            return;
        }

        if (document.getElementById("memory-btn")) {
            return;
        }

        const button = document.createElement("button");
        button.id = "memory-btn";
        button.className = "header-btn";
        button.title = "Управление памятью";
        button.setAttribute("aria-label", "Управление памятью");
        button.textContent = "🧠";
        button.addEventListener("click", openMemoryModal);

        // The memory button belongs to the same responsive action flow
        // as the other header buttons, not as a separate header row.
        const actionRow = header.querySelector(".header-actions");
        const themeButton = document.getElementById("theme-toggle");

        if (actionRow) {
            if (themeButton && themeButton.parentElement === actionRow) {
                actionRow.insertBefore(button, themeButton);
            } else {
                actionRow.appendChild(button);
            }
        } else {
            header.appendChild(button);
        }
    }

    window.openMemoryModal = async function () {
        const modal = document.getElementById("memoryModal");
        if (!modal) {
            console.error("[Memory] Modal not found");
            return;
        }
        modal.style.display = "flex";
        await loadMemoryData();
    };

    window.closeMemoryModal = function () {
        const modal = document.getElementById("memoryModal");
        if (modal) modal.style.display = "none";
    };

    async function loadMemoryData() {
        try {
            const response = await fetch("/api/memory/manage");
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            const enabled = document.getElementById("memEnabled");
            const limit = document.getElementById("memLimit");
            const count = document.getElementById("memCount");
            const list = document.getElementById("memoryFactsList");
            if (enabled) enabled.checked = Boolean(data.config?.enabled);
            if (limit) limit.value = data.config?.max_context_facts ?? 15;
            const facts = Array.isArray(data.facts) ? data.facts : [];
            if (count) count.textContent = facts.length;
            if (!list) return;
            list.innerHTML = "";
            if (facts.length === 0) {
                const empty = document.createElement("p");
                empty.textContent = "Память пуста.";
                empty.style.color = "#888";
                empty.style.fontSize = "13px";
                list.appendChild(empty);
                return;
            }
            facts.forEach(function (fact) {
                const item = document.createElement("div");
                item.style.cssText = "background:#2a2a2a;padding:8px;margin-bottom:6px;border-radius:4px;font-size:12px;";
                const category = document.createElement("span");
                category.textContent = `[${fact.category || "general"}]`;
                category.style.cssText = "color:#4da6ff;font-weight:bold;text-transform:uppercase;";
                item.appendChild(category);
                item.appendChild(document.createTextNode(" " + (fact.fact || "")));
                list.appendChild(item);
            });
        } catch (error) {
            console.error("[Memory] Failed to load memory:", error);
        }
    }

    window.updateMemoryConfig = async function () {
        const enabled = document.getElementById("memEnabled");
        const limit = document.getElementById("memLimit");
        const maxContextFacts = parseInt(limit?.value, 10) || 15;
        try {
            const response = await fetch("/api/memory/config", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enabled: Boolean(enabled?.checked), max_context_facts: maxContextFacts })
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            await loadMemoryData();
        } catch (error) {
            console.error("[Memory] Failed to update config:", error);
        }
    };

    window.clearMemory = async function (category) {
        if (!confirm("Очистить память?")) return;
        try {
            const response = await fetch("/api/memory/clear", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ category: category })
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            await loadMemoryData();
        } catch (error) {
            console.error("[Memory] Failed to clear memory:", error);
        }
    };

    document.addEventListener("DOMContentLoaded", createMemoryButton);
})();
