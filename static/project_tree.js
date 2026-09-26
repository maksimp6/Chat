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
        var response = await window.AliceDispatcher.request("/api/project-tree", {credentials: "same-origin", cache: "no-store"});
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

        var UI = window.AliceCoreAPI.ui;
        var body = document.createElement("div");
        body.className = "project-tree-state";
        body.textContent = "Загрузка...";
        var modal = UI.modal.create({
            id: "project-tree-modal",
            title: "🌳 Структура проекта",
            titleTag: "h3",
            closeAction: "project-tree.close",
            body: body
        });
        (document.querySelector(".alice-pro-app") || document.body).appendChild(modal);
        UI.modal.open(modal);

        var state = body;
        loadProjectTree().then(function (data) {
            state.textContent = data.root;
            var tree = document.createElement("div");
            tree.className = "project-tree";
            data.nodes.forEach(function (node) { renderNode(node, tree); });
            state.replaceWith(tree);
        }).catch(function (error) {
            log("load_failed", {code: error.code || "PROJECT_TREE_ERROR", message: error.message});
            state.textContent = "Ошибка структуры проекта: " + error.message;
            var retry = UI.button({
                text: "Повторить",
                action: "project-tree.retry"
            });
            modal.querySelector(".modal-content").appendChild(retry);
        });
    }

    window.ProjectTree = {load: loadProjectTree, open: openTree};

    var actions = window.AliceCoreAPI.ui.actions;
    actions.register("project-tree.close", function (payload) {
        var modal = payload.element.closest(".modal");
        if (modal) modal.remove();
    });
    actions.register("project-tree.retry", function (payload) {
        var modal = payload.element.closest(".modal");
        if (modal) modal.remove();
        openTree();
    });

    actions.register("project-tree.open", function () {
        openTree();
    });
})();
