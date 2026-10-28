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

  // The server's error codes in plain Russian: what happened and what to do. The code itself goes to the console.
  const ERROR_TEXT = {
    "branch is required": "Укажите ветку и нажмите «Создать».",
    "commit is required": "Укажите коммит и повторите.",
    "invalid git ref": "Имя ветки или коммита записано неверно. Исправьте его и повторите.",
    "git command failed": "Git на сервере не смог прочитать репозиторий. Проверьте имя ветки и повторите.",
    "commit is not reachable from branch": "Этого коммита нет в указанной ветке. Проверьте ветку и коммит.",
    "git did not return a commit SHA": "Git на сервере не нашёл коммит ветки. Проверьте имя ветки и повторите.",
    "git is unavailable on this server":
      "На сервере Logos нет git, поэтому окружение не создать. Установите git на сервере Logos.",
    environment_ref_invalid: "Окружение не создано: ветка или коммит не найдены. Проверьте их имена и повторите.",
    environment_not_found: "Окружение не найдено: его уже удалили. Проверьте список окружений.",
    environment_not_running: "Окружение не запущено. Нажмите «Старт» и повторите.",
    environment_runtime_timeout: "Окружение не ответило вовремя. Повторите запрос.",
    environment_runtime_unreachable: "Окружение не отвечает. Нажмите «Перезапуск» и повторите.",
    environment_state_conflict:
      "Сейчас это действие недоступно для окружения. Проверьте его статус в списке и повторите.",
    environment_operation_failed: "Операция с окружением не выполнена. Проверьте статус окружения и повторите.",
  };
  // Codes that carry a value after a fixed beginning, such as the branch name or the two states.
  const ERROR_PREFIX_TEXT = [
    ["git ref is not available:", "Ветка или коммит не найдены в репозитории. Проверьте имя и повторите."],
    ["invalid environment transition:", ERROR_TEXT.environment_state_conflict],
  ];

  function isRussianText(value) {
    return typeof value === "string" && /[А-Яа-яЁё]/.test(value);
  }

  // A timed-out request may still finish on the server, so the message says that instead of the browser text.
  // A server answer is named by its code or HTTP status; only a request without an answer is a connection problem.
  function errorText(error) {
    if (error && (error.name === "TimeoutError" || error.name === "AbortError")) {
      return "Сервер не ответил вовремя: операция могла продолжиться. Проверьте статус окружения.";
    }
    const status = error && error.status;
    const code = (error && error.code) || "";
    if (!status) {
      console.warn("[ENVIRONMENTS] No answer from the server:", error);
      return "Нет связи с сервером. Проверьте соединение и нажмите «Обновить».";
    }
    console.warn("[ENVIRONMENTS] Request failed:", status, code, error.detail || "");
    if (Object.prototype.hasOwnProperty.call(ERROR_TEXT, code)) return ERROR_TEXT[code];
    const prefixed = ERROR_PREFIX_TEXT.find((entry) => code.startsWith(entry[0]));
    if (prefixed) return prefixed[1];
    if (isRussianText(error.detail)) return error.detail;
    return status >= 500
      ? "Ошибка на сервере (код " + status + "). Проверьте статус окружения и повторите позже."
      : "Сервер отклонил запрос (код " + status + "). Нажмите «Обновить» и повторите.";
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
    if (!response.ok) {
      const error = new Error(data.error || "HTTP " + response.status);
      error.status = response.status;
      error.code = typeof data.error === "string" ? data.error : "";
      error.detail = typeof data.detail === "string" ? data.detail : "";
      throw error;
    }
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
              // The server's own text is technical, so it sits under «Подробности» below the Russian explanation.
              env.status === "FAILED" && env.error
                ? '<div class="environment-card-meta">Окружение не работает из-за ошибки. Нажмите «Стоп», затем «Старт».' +
                  '<details class="msg-details"><summary class="msg-details-summary">Подробности</summary>' +
                  esc(env.error) +
                  "</details></div>"
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
        if (
          op === "delete" &&
          !(await window.LogosDialog.confirm("Удалить окружение?", { confirmText: "Удалить", danger: true }))
        ) {
          return;
        }
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
