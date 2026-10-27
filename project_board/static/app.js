"use strict";

const state = { threads: [], tasks: [], section: "threads", selected: null };
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

async function api(method, path, data) {
  const response = await fetch(path, {
    method,
    cache: "no-store",
    headers: {
      ...(data ? { "Content-Type": "application/json" } : {}),
    },
    ...(data ? { body: JSON.stringify(data) } : {}),
  });
  const output = await response.json();
  if (!response.ok) throw new Error(output.error || "HTTP " + response.status);
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
  } catch (e) { error(e.message); }
}

async function showThread(id) {
  const thread = await api("GET", "/api/threads/" + encodeURIComponent(id));
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
      await api("POST", "/api/threads/" + id + "/posts", {
        body: area.value, request_id: requestId(),
      });
      await refresh();
      await showThread(id);
      error("Комментарий сохранён в Memory DB");
    } catch (ex) { error(ex.message); }
  });
  detail.append(form);
}

async function showTask(id) {
  const task = await api("GET", "/api/tasks/" + encodeURIComponent(id));
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
  ["backlog", "ready", "blocked", "in_progress", "review", "done"].forEach((value) => {
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
    try {
      await api("POST", "/api/tasks/" + id + "/status", {
        status: select.value, accepted_by_owner: approved.checked,
        request_id: requestId(),
      });
      await refresh();
      await showTask(id);
      error("Статус обновлён, без merge");
    } catch (ex) { error(ex.message); }
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
      await api("POST", "/api/tasks/" + id + "/evidence", {
        summary: summary.value, url: url.value, request_id: requestId(),
      });
      await refresh();
      await showTask(id);
      error("Доказательство сохранено как непроверенное");
    } catch (ex) { error(ex.message); }
  });
  detail.append(status, evidence);
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
  try {
    const item = await api("POST", "/api/threads", {
      title: form.get("title"), body: form.get("body"),
      category: form.get("category"), request_id: requestId(),
    });
    await refresh();
    await openItem(item.id);
    e.target.reset();
    error("Обсуждение сохранено");
  } catch (ex) { error(ex.message); }
});

$("create-task").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const dependencies = String(form.get("dependencies") || "")
    .split(",").map((x) => window.LogosWikifunctions.trim(x)).filter(Boolean);
  try {
    const item = await api("POST", "/api/tasks", {
      title: form.get("title"), depends_on: dependencies, request_id: requestId(),
    });
    await refresh();
    await openItem(item.id);
    e.target.reset();
    error("Задача сохранена");
  } catch (ex) { error(ex.message); }
});

refresh().then(() => {
  $("connection").textContent = "Подключено";
  selectSection("shame");
}).catch((e) => {
  $("connection").textContent = "Ошибка подключения";
  error(e.message);
});
