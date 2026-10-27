"use strict";

const state = { threads: [], tasks: [], section: "threads", selected: null };
// Зеркало transitions из project_board/store.py: в списке статусов только допустимые переходы.
const TRANSITIONS = {
  backlog: ["ready", "blocked"],
  blocked: ["ready"],
  ready: ["in_progress", "blocked"],
  in_progress: ["review", "blocked"],
  review: ["in_progress", "done", "blocked"],
  done: [],
};
const SERVER_ERRORS = {
  "invalid status transition": "Такой переход статуса недопустим",
  "dependencies not done": "Сначала должны быть завершены зависимости",
  "owner acceptance required; this is NOT merge": "Для Done нужна приёмка владельцем (это НЕ merge)",
};
const $ = (id) => document.getElementById(id);

function node(tag, text = "", className = "") {
  const item = document.createElement(tag);
  item.textContent = text;
  if (className) item.className = className;
  return item;
}

function error(message = "") {
  const el = $("message");
  el.textContent = message;
  el.classList.toggle("error", Boolean(message));
}

function requestId() {
  return crypto.randomUUID();
}

// request_id живёт до успешного ответа и привязан к содержимому: повтор той же отправки
// после потери ответа не создаёт дубль, а изменённый черновик получает новый ключ.
const pendingIds = new Map();
async function postOnce(button, path, payload) {
  const signature = path + "\n" + JSON.stringify(payload);
  if (!pendingIds.has(signature)) pendingIds.set(signature, requestId());
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Отправка…";
  try {
    const result = await api("POST", path, { ...payload, request_id: pendingIds.get(signature) });
    pendingIds.delete(signature);
    return result;
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function api(method, path, data) {
  const response = await fetch(path, {
    method,
    cache: "no-store",
    headers: {
      ...(data ? { "Content-Type": "application/json" } : {}),
    },
    ...(data ? { body: JSON.stringify(data) } : {}),
  }).catch(() => {
    throw new Error("Нет связи с сервером");
  });
  const output = await response.json();
  if (!response.ok) throw new Error(SERVER_ERRORS[output.error] || output.error || "HTTP " + response.status);
  return output;
}

async function refresh() {
  const [topics, tasks] = await Promise.all([
    api("GET", "/api/threads"),
    api("GET", "/api/tasks"),
  ]);
  state.threads = topics.threads;
  state.tasks = tasks.tasks;
  drawList();
}

function drawList() {
  const root = $("list");
  root.replaceChildren();
  const tasks = state.section === "tasks";
  const items = tasks ? state.tasks :
    state.threads.filter((x) => state.section !== "shame" || x.category === "shame");
  $("list-title").textContent = tasks ? "Задачи" :
    state.section === "shame" ? "Доска позора" : "Обсуждения";
  $("new").hidden = state.section === "shame";
  if (!items.length) root.append(node("p", "Пока пусто", "muted"));
  items.forEach((item) => {
    const button = node("button", "", "entry");
    button.type = "button";
    if (state.selected === item.id) button.classList.add("active");
    const title = node("strong", item.title);
    const note = node("small", tasks ?
      item.status + " · " + item.id :
      item.category + " · " + item.post_count + " сообщений");
    button.append(title, note);
    button.addEventListener("click", () => openItem(item.id));
    root.append(button);
  });
}

function hideComposer() {
  $("composer").hidden = true;
  $("create-task").hidden = true;
  $("create-thread").hidden = true;
}

async function openItem(id) {
  hideComposer();
  error();
  state.selected = id;
  drawList();
  try {
    if (state.section === "tasks") await showTask(id);
    else await showThread(id);
  } catch (e) { if (state.selected === id) error(e.message); }
}

async function showThread(id) {
  const thread = await api("GET", "/api/threads/" + encodeURIComponent(id));
  // ответ на предыдущий клик не должен перерисовать уже открытую запись
  if (state.selected !== id) return;
  const detail = $("detail");
  detail.replaceChildren(
    node("span", thread.category === "shame" ? "Доска позора" : "Обсуждение", "pill"),
    node("h2", thread.title, "detail-title"),
  );
  thread.posts.forEach((post) => {
    const card = node("article", "", "post");
    card.append(node("small", post.actor + " · " + post.at), node("div", post.body));
    detail.append(card);
  });
  const form = node("form");
  const label = node("label", "Новый комментарий");
  const area = node("textarea");
  area.name = "body";
  area.required = true;
  area.maxLength = 12000;
  area.rows = 4;
  label.append(area);
  const button = node("button", "Опубликовать комментарий");
  button.type = "submit";
  form.append(label, button);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await postOnce(button, "/api/threads/" + id + "/posts", { body: area.value });
    } catch (ex) { error(ex.message); return; }
    // поле очищается сразу после сохранения: ошибка обновления ленты не ведёт к повтору
    area.value = "";
    error("Комментарий сохранён в Memory DB");
    try {
      await refresh();
      await showThread(id);
    } catch (ex) { error("Комментарий сохранён, но лента не обновилась: " + ex.message); }
  });
  detail.append(form);
}

async function showTask(id) {
  const task = await api("GET", "/api/tasks/" + encodeURIComponent(id));
  if (state.selected !== id) return task;  // устаревший ответ не перерисовывает открытую задачу
  const detail = $("detail");
  detail.replaceChildren(
    node("span", task.id + " · " + task.status, "pill"),
    node("h2", task.title, "detail-title"),
  );
  if (task.depends_on.length) {
    detail.append(node("p", "Зависит от: " + task.depends_on.join(", "), "muted"));
  }
  task.evidence.forEach((proof) => {
    const item = node("article", "", "post");
    item.append(node("strong", proof.summary), node("small", "Не проверено · " + proof.at));
    if (proof.url) {
      const link = node("a", "Источник");
      link.href = proof.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      item.append(link);
    }
    detail.append(item);
  });
  const status = node("form", "", "controls");
  const label = node("label", "Изменить статус");
  const select = node("select");
  // в списке только текущий статус и допустимые переходы: сервер отклоняет остальные
  const allowed = TRANSITIONS[task.status] || [];
  const statuses = ["backlog", "ready", "blocked", "in_progress", "review", "done"];
  statuses.filter((value) => value === task.status || allowed.includes(value)).forEach((value) => {
    const option = node("option", value);
    option.value = value;
    option.selected = value === task.status;
    select.append(option);
  });
  label.append(select);
  const approvedLabel = node("label", "Приёмка владельцем для Done (НЕ merge)");
  const approved = node("input");
  approved.type = "checkbox";
  approvedLabel.prepend(approved);
  const save = node("button", "Сохранить статус");
  save.type = "submit";
  status.append(label, approvedLabel, save);
  status.addEventListener("submit", async (e) => {
    e.preventDefault();
    // тот же статус сервер отклонил бы с 409: запрос не отправляем
    if (select.value === task.status) {
      error("Статус не изменился");
      return;
    }
    const payload = { status: select.value, accepted_by_owner: approved.checked };
    try {
      await postOnce(save, "/api/tasks/" + id + "/status", payload);
      await refresh();
      await showTask(id);
      error("Статус обновлён, без merge");
    } catch (ex) {
      // ответ мог потеряться уже после применения: сверяем с сервером, а не показываем старый статус
      const fresh = await reloadTask(id);
      if (fresh && fresh.status === payload.status) error("Статус обновлён, без merge");
      else error(fresh ? ex.message + ". Сейчас статус: " + fresh.status : ex.message);
    }
  });
  const evidence = node("form");
  const summaryLabel = node("label", "Добавить доказательство (не VERIFIED)");
  const summary = node("input");
  summary.required = true;
  summary.maxLength = 400;
  summaryLabel.append(summary);
  const urlLabel = node("label", "Ссылка HTTPS (необязательно)");
  const url = node("input");
  url.type = "url";
  urlLabel.append(url);
  const submit = node("button", "Добавить доказательство");
  submit.type = "submit";
  evidence.append(summaryLabel, urlLabel, submit);
  evidence.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await postOnce(submit, "/api/tasks/" + id + "/evidence", { summary: summary.value, url: url.value });
      // поля очищаются сразу после сохранения: ошибка обновления не должна привести к повтору
      summary.value = "";
      url.value = "";
      await refresh();
      await showTask(id);
      error("Доказательство сохранено как непроверенное");
    } catch (ex) { error(ex.message); }
  });
  detail.append(status, evidence);
  return task;
}

// Перечитать список и задачу после неоднозначной ошибки; null, если сервер недоступен.
async function reloadTask(id) {
  await refresh().catch(() => {});
  return showTask(id).catch(() => null);
}

function selectSection(section) {
  state.section = section;
  state.selected = null;
  hideComposer();
  error();
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.section === section);
    button.setAttribute("aria-pressed", String(button.dataset.section === section));
  });
  $("detail").replaceChildren(node("p", "Выберите запись слева.", "muted"));
  drawList();
  if (section === "shame") {
    const first = state.threads.find((t) => t.category === "shame");
    if (first) openItem(first.id);
  }
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => selectSection(button.dataset.section));
});

$("new").addEventListener("click", () => {
  const isTask = state.section === "tasks";
  $("composer").hidden = false;
  $("create-task").hidden = !isTask;
  $("create-thread").hidden = isTask;
  $("composer-title").textContent = isTask ? "Новая задача" : "Новое обсуждение";
});

$("create-thread").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  let item;
  try {
    item = await postOnce(e.target.querySelector("button"), "/api/threads", {
      title: form.get("title"), body: form.get("body"), category: form.get("category"),
    });
  } catch (ex) { error(ex.message); return; }
  e.target.reset();
  try {
    await refresh();
    await openItem(item.id);
    error("Обсуждение сохранено");
  } catch (ex) { error("Обсуждение сохранено, но список не обновился: " + ex.message); }
});

$("create-task").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const dependencies = String(form.get("dependencies") || "")
    .split(",").map((x) => x.trim()).filter(Boolean);
  let item;
  try {
    item = await postOnce(e.target.querySelector("button"), "/api/tasks", {
      title: form.get("title"), depends_on: dependencies,
    });
  } catch (ex) { error(ex.message); return; }
  e.target.reset();
  try {
    await refresh();
    await openItem(item.id);
    error("Задача сохранена");
  } catch (ex) { error("Задача сохранена, но список не обновился: " + ex.message); }
});

refresh().then(() => {
  $("connection").textContent = "Подключено";
  selectSection("shame");
}).catch((e) => {
  $("connection").textContent = "Ошибка подключения";
  error(e.message);
});
