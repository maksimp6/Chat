(function () {
  "use strict";

  function makeField(id, label, placeholder, autocomplete) {
    var wrapper = document.createElement("label");
    wrapper.className = "provider-field";
    wrapper.textContent = label;
    var input = document.createElement("input");
    input.id = id;
    input.type = "password";
    input.placeholder = placeholder || "";
    input.autocomplete = autocomplete || "off";
    wrapper.appendChild(input);
    return wrapper;
  }

  async function fetchStatus(output) {
    var response = await window.AliceDispatcher.request("/api/provider-credentials/status", {
      credentials: "same-origin",
    });
    var data = await response.json();
    var item = data.providers && data.providers[0];
    output.textContent = item && item.status ? "Yandex Cloud: " + item.status : "Yandex Cloud: not configured";
  }

  window.AliceProviderCredentials = {
    mount: function (box) {
      box.textContent = "";
      var project = document.createElement("input");
      project.id = "provider-yandex-project";
      project.placeholder = "Yandex project ID";
      box.appendChild(project);
      box.appendChild(makeField("provider-yandex-key", "Yandex Cloud API key", "API key", "new-password"));
      var save = document.createElement("button");
      save.type = "button";
      save.textContent = "Сохранить";
      var output = document.createElement("div");
      save.addEventListener("click", async function () {
        var apiKey = document.getElementById("provider-yandex-key").value.trim();
        var projectId = project.value.trim();
        if (!apiKey || !projectId) {
          output.textContent = "Нужны Yandex API key и project ID.";
          return;
        }
        var response = await window.AliceDispatcher.request("/api/provider-credentials", {
          method: "PUT",
          credentials: "same-origin",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({yandex_api_key: apiKey, yandex_project_id: projectId}),
        });
        var data = await response.json();
        output.textContent = response.ok ? "Сохранено." : (data.detail || data.error || "Ошибка");
        if (response.ok) {
          document.getElementById("provider-yandex-key").value = "";
          await fetchStatus(output);
        }
      });
      box.appendChild(save);
      box.appendChild(output);
      fetchStatus(output).catch(function () { output.textContent = "Статус недоступен."; });
    },
  };
})();
