(() => {
  const state = { environments: [] };

  function esc(value) {
    return String(value ?? "").replace(
      /[&<>"']/g,
      (ch) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[ch],
    );
  }

  // Web addresses and same-origin paths such as "/environments/<id>/" become links; other schemes in
  // server data would run as script. "//host" and backslashes would leave the origin, so they are refused.
  const SAME_ORIGIN_PATH_RE = /^\/(?![\/\\])[^\\\x00-\x20\x7f]*$/;
  function safeHref(value) {
    const text = String(value ?? "");
    return /^https?:\/\//i.test(text) || SAME_ORIGIN_PATH_RE.test(text) ? text : "#";
  }

  // Environment operations run git and runtime start-up on the server, so they outlast the 10 s default.
  const OPERATION_OPTIONS = { timeoutMs: 120000 };

  const ERROR_TEXT = {
    environment_operation_failed: "Не удалось выполнить операцию.",
  };

  // A timed-out request may still finish on the server, so the message says that instead of the browser text.
  function errorText(error) {
    if (error && (error.name === "TimeoutError" || error.name === "AbortError")) {
      return "Сервер не ответил вовремя: операция могла продолжиться. Проверьте статус окружения.";
    }
    return ERROR_TEXT[error.message] || error.message;
  }

  async function request(path, options = {}, dispatchOptions) {
    const response = await window.LogosDispatcher.request(
      path,
      {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
      },
      dispatchOptions,
    );
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "HTTP " + response.status);
    return data;
  }

  function modal() {
    let node = document.getElementById("environments-modal");
    if (node) return node;

    const UI = window.LogosCoreAPI.ui;
    const body = document.createElement("div");
    body.className = "environments-body";
    body.innerHTML = [
      '<div class="environments-head"><div><p>Неизменяемые окружения веток</p></div>',
      '<button class="logos-btn environments-refresh" type="button">Обновить</button></div>',
      '<form class="environments-create"><input name="branch" placeholder="feature/my-branch" autocomplete="off" required><button class="logos-btn" type="submit">Создать</button></form>',
      '<div class="environments-status" aria-live="polite" hidden></div><div class="environments-list"></div>',
    ].join("");

    node = UI.modal.create({
      id: "environments-modal",
      title: "Окружения",
      className: "environments-modal",
      contentClassName: "environments-box",
      closeAction: "environments.close",
      body: body,
    });
    (document.querySelector(".logos-pro-app") || document.body).appendChild(node);
    node.querySelector(".environments-refresh").onclick = load;
    let creating = false;
    node.querySelector(".environments-create").onsubmit = async (event) => {
      event.preventDefault();
      // A repeated submit while the first request runs would create a second identical environment.
      if (creating) return;
      creating = true;
      const form = event.currentTarget;
      const input = form.branch;
      const submit = form.querySelector('button[type="submit"]');
      submit.disabled = true;
      try {
        await request(
          "/api/environments",
          { method: "POST", body: JSON.stringify({ branch: input.value.trim() }) },
          OPERATION_OPTIONS,
        );
        input.value = "";
        await load();
      } catch (error) {
        // The server may have created the environment before the error, so the list is re-read first.
        await load();
        setStatus(errorText(error));
      } finally {
        creating = false;
        submit.disabled = false;
      }
    };
    return node;
  }

  function setStatus(message) {
    const node = modal().querySelector(".environments-status");
    node.textContent = message || "";
    node.hidden = !message;
  }

  function render() {
    const list = modal().querySelector(".environments-list");
    list.innerHTML = state.environments.length
      ? state.environments
          .map((env) => {
            // A FAILED environment cannot start or restart. Stop moves it to STOPPED, from where Start works.
            const stoppable = env.status === "RUNNING" || env.status === "FAILED";
            const action = stoppable ? "stop" : "start";
            const label = stoppable ? "Стоп" : "Старт";
            return [
              '<article class="environment-card">',
              '<div class="environment-card-main"><strong>',
              esc(env.branch_name),
              "</strong>",
              "<code>",
              esc(String(env.commit_sha).slice(0, 12)),
              "</code>",
              '<span class="environment-status environment-status-',
              esc(String(env.status).toLowerCase()),
              '">',
              esc(env.status),
              "</span></div>",
              '<div class="environment-card-meta">',
              esc(env.environment_id),
              " · ",
              esc(env.data_namespace),
              "</div>",
              env.status === "FAILED" && env.error
                ? '<div class="environment-card-meta">Ошибка: ' + esc(env.error) + "</div>"
                : "",
              '<div class="environment-card-actions">',
              '<a href="',
              esc(safeHref(env.url)),
              '" target="_blank" rel="noopener">Открыть</a>',
              '<button class="logos-btn" data-op="',
              action,
              '" data-id="',
              esc(env.environment_id),
              '">',
              label,
              "</button>",
              env.status === "FAILED"
                ? ""
                : '<button class="logos-btn" data-op="restart" data-id="' +
                  esc(env.environment_id) +
                  '">Перезапуск</button>',
              '<button class="logos-btn" data-op="delete" data-id="',
              esc(env.environment_id),
              '">Удалить</button>',
              "</div></article>",
            ].join("");
          })
          .join("")
      : '<div class="environments-empty">Нет окружений</div>';

    list.querySelectorAll("button[data-op]").forEach((button) => {
      button.onclick = async () => {
        const op = button.dataset.op;
        const id = button.dataset.id;
        if (op === "delete" && !window.confirm("Удалить окружение?")) return;
        // A second click while the operation runs would repeat it; the list re-renders on success.
        button.disabled = true;
        try {
          const method = op === "delete" ? "DELETE" : "POST";
          const suffix = op === "delete" ? "" : "/" + op;
          await request(
            "/api/environments/" + encodeURIComponent(id) + suffix,
            { method },
            OPERATION_OPTIONS,
          );
          await load();
        } catch (error) {
          button.disabled = false;
          // The server may have finished the operation anyway, so the list is re-read before the message shows.
          await load();
          setStatus(errorText(error));
        }
      };
    });
  }

  async function load() {
    try {
      const data = await request("/api/environments");
      state.environments = Array.isArray(data.environments) ? data.environments : [];
      setStatus("");
      render();
    } catch (error) {
      setStatus(errorText(error));
    }
  }

  function open() {
    window.LogosCoreAPI.ui.modal.open(modal());
    load();
  }

  window.LogosCoreAPI.ui.actions.register("environments.close", function (payload) {
    const node = payload.element.closest(".modal");
    if (node) window.LogosCoreAPI.ui.modal.close(node);
  });


  document.addEventListener("DOMContentLoaded", () => {
    const header = document.getElementById("header-actions-2");
    if (!header || document.getElementById("environments-btn")) return;
    const button = document.createElement("button");
    button.id = "environments-btn";
    button.className = "logos-btn header-btn";
    button.title = "Окружения";
    button.setAttribute("aria-label", "Окружения");
    button.textContent = "🌿";
    button.onclick = open;
    header.insertBefore(button, header.firstChild);
  });
})();
