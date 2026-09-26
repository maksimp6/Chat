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

  async function request(path, options = {}) {
    const response = await window.AliceDispatcher.request(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "HTTP " + response.status);
    return data;
  }

  function modal() {
    let node = document.getElementById("environments-modal");
    if (node) return node;
    node = document.createElement("div");
    node.id = "environments-modal";
    node.className = "environments-modal";
    node.innerHTML = [
      '<div class="environments-box" role="dialog" aria-modal="true" aria-labelledby="environments-title">',
      '<button class="alice-btn environments-close" type="button" aria-label="Закрыть">&times;</button>',
      '<div class="environments-head"><div><h2 id="environments-title">Environments</h2><p>Immutable branch runtimes</p></div>',
      '<button class="alice-btn environments-refresh" type="button">Обновить</button></div>',
      '<form class="environments-create"><input name="branch" placeholder="feature/my-branch" autocomplete="off" required><button class="alice-btn" type="submit">Создать</button></form>',
      '<div class="environments-status" aria-live="polite" hidden></div><div class="environments-list"></div></div>',
    ].join("");
    document.body.appendChild(node);
    node.querySelector(".environments-close").onclick = () => node.classList.remove("visible");
    node.querySelector(".environments-refresh").onclick = load;
    node.querySelector(".environments-create").onsubmit = async (event) => {
      event.preventDefault();
      const input = event.currentTarget.branch;
      try {
        await request("/api/environments", {
          method: "POST",
          body: JSON.stringify({ branch: input.value.trim() }),
        });
        input.value = "";
        await load();
      } catch (error) {
        setStatus(error.message);
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
            const action = env.status === "RUNNING" ? "stop" : "start";
            const label = env.status === "RUNNING" ? "Стоп" : "Старт";
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
              '<div class="environment-card-actions">',
              '<a href="',
              esc(env.url),
              '" target="_blank" rel="noopener">Открыть</a>',
              '<button class="alice-btn" data-op="',
              action,
              '" data-id="',
              esc(env.environment_id),
              '">',
              label,
              "</button>",
              '<button class="alice-btn" data-op="restart" data-id="',
              esc(env.environment_id),
              '">Перезапуск</button>',
              '<button class="alice-btn" data-op="delete" data-id="',
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
        try {
          if (op === "delete" && !window.confirm("Удалить environment?")) return;
          const method = op === "delete" ? "DELETE" : "POST";
          const suffix = op === "delete" ? "" : "/" + op;
          await request("/api/environments/" + encodeURIComponent(id) + suffix, { method });
          await load();
        } catch (error) {
          setStatus(error.message);
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
      setStatus(error.message);
    }
  }

  function open() {
    modal().classList.add("visible");
    load();
  }

  window.AliceEnvironments = { open, load };

  document.addEventListener("DOMContentLoaded", () => {
    const header = document.getElementById("header-actions-2");
    if (!header || document.getElementById("environments-btn")) return;
    const button = document.createElement("button");
    button.id = "environments-btn";
    button.className = "alice-btn header-btn";
    button.title = "Environments";
    button.setAttribute("aria-label", "Environments");
    button.textContent = "🌿";
    button.onclick = open;
    header.insertBefore(button, header.firstChild);
  });
})();
