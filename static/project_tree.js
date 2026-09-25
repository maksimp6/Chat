(function () {
    "use strict";

    function log(event, details) {
        var payload = Object.assign({component: "project_tree", event: event}, details || {});
        (console.info || console.log).call(console, "[PROJECT_TREE]", payload);
    }

    function renderNode(node, parent) {
        var row = document.createElement("div");
        row.className = "project-tree-row";
        row.dataset.path = node.path;
        var icon = document.createElement("span");
        icon.className = "project-tree-icon";
        icon.textContent = node.icon || "📄";
        var label = document.createElement("span");
        label.textContent = node.name;
        row.appendChild(icon);
        row.appendChild(label);
        parent.appendChild(row);
        if (node.kind === "directory" && Array.isArray(node.children)) {
            var children = document.createElement("div");
            children.className = "project-tree-children";
            node.children.forEach(function (child) { renderNode(child, children); });
            parent.appendChild(children);
        }
    }

    async function loadProjectTree() {
        log("load_started");
        var response = await window.fetch("/api/project-tree", {credentials: "same-origin", cache: "no-store"});
        if (!response.ok) {
            var error = new Error("Project tree request failed: HTTP " + response.status);
            error.code = "PROJECT_TREE_HTTP_ERROR";
            throw error;
        }
        var data = await response.json();
        if (!data || !Array.isArray(data.nodes)) {
            var invalid = new Error("Project tree returned an invalid payload");
            invalid.code = "PROJECT_TREE_INVALID_PAYLOAD";
            throw invalid;
        }
        log("load_succeeded", {node_count: data.nodes.length});
        return data;
    }

    function openTree() {
        var existing = document.getElementById("project-tree-modal");
        if (existing) existing.remove();

        var modal = document.createElement("div");
        modal.id = "project-tree-modal";
        modal.className = "modal visible";

        var box = document.createElement("div");
        box.className = "modal-content";
        box.innerHTML = '<button type="button" class="project-tree-close">&times;</button><h3>🌳 Структура проекта</h3><div class="project-tree-state">Загрузка...</div>';
        modal.appendChild(box);
        (document.querySelector(".alice-pro-app") || document.body).appendChild(modal);

        box.querySelector(".project-tree-close").addEventListener("click", function () { modal.remove(); });

        var state = box.querySelector(".project-tree-state");
        loadProjectTree().then(function (data) {
            state.textContent = data.root;
            var tree = document.createElement("div");
            tree.className = "project-tree";
            data.nodes.forEach(function (node) { renderNode(node, tree); });
            state.replaceWith(tree);
        }).catch(function (error) {
            log("load_failed", {code: error.code || "PROJECT_TREE_ERROR", message: error.message});
            state.textContent = "Ошибка структуры проекта: " + error.message;
            var retry = document.createElement("button");
            retry.type = "button";
            retry.textContent = "Повторить";
            retry.addEventListener("click", function () { modal.remove(); openTree(); });
            box.appendChild(retry);
        });
    }

    window.ProjectTree = {load: loadProjectTree, open: openTree};

    document.addEventListener("DOMContentLoaded", function () {
        var button = document.getElementById("project-tree-btn");
        if (button) button.addEventListener("click", openTree);
    });
})();
