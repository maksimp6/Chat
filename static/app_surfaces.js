(function () {
  "use strict";

  var modal = null;
  var selectedSurface = null;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function request(path, init) {
    return window.AliceDispatcher.request(path, init || {}).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          if (!response.ok) throw new Error(data.error || "HTTP " + response.status);
          return data;
        });
    });
  }

  function sendInput(surfaceId, event) {
    return request("/api/app-surfaces/" + encodeURIComponent(surfaceId) + "/input", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(event),
    });
  }

  function renderViewer(container, surface) {
    selectedSurface = surface;
    container.replaceChildren();

    var title = el("div", "app-surface-view-title", surface.title);
    var meta = el(
      "div",
      "app-surface-view-meta",
      surface.source + " · " + surface.width + "×" + surface.height,
    );
    var frame = document.createElement("img");
    frame.className = "app-surface-frame";
    frame.alt = "Окно " + surface.title;

    function refreshFrame() {
      frame.src =
        "/api/app-surfaces/" +
        encodeURIComponent(surface.id) +
        "/frame?v=" +
        encodeURIComponent(surface.frame_version || Date.now());
    }

    frame.addEventListener("click", function (event) {
      var rect = frame.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      var x = Math.round(((event.clientX - rect.left) / rect.width) * surface.width);
      var y = Math.round(((event.clientY - rect.top) / rect.height) * surface.height);
      sendInput(surface.id, { type: "mouse_down", x: x, y: y, button: 1 })
        .then(function () {
          return sendInput(surface.id, { type: "mouse_up", x: x, y: y, button: 1 });
        })
        .catch(function (error) {
          console.error("[APP_SURFACES] input failed:", error);
        });
    });

    var refresh = el("button", "alice-btn app-surface-refresh", "Обновить кадр");
    refresh.type = "button";
    refresh.addEventListener("click", refreshFrame);

    container.appendChild(title);
    container.appendChild(meta);
    container.appendChild(frame);
    container.appendChild(refresh);

    if (surface.has_frame) refreshFrame();
    else frame.alt = "Кадр ещё не опубликован";
  }

  function loadSurfaces(list, viewer) {
    list.replaceChildren(el("div", "app-surface-state", "Загрузка…"));
    request("/api/app-surfaces")
      .then(function (data) {
        list.replaceChildren();
        var surfaces = Array.isArray(data.surfaces) ? data.surfaces : [];
        surfaces.forEach(function (surface) {
          var row = el("button", "alice-btn app-surface-row");
          row.type = "button";
          row.appendChild(el("strong", "", surface.title));
          row.appendChild(el("span", "", surface.source));
          row.addEventListener("click", function () {
            renderViewer(viewer, surface);
          });
          list.appendChild(row);
        });
        if (!surfaces.length) {
          list.appendChild(
            el(
              "div",
              "app-surface-state",
              "Нет подключённых приложений. Producer зарегистрирует окно через App Surface API.",
            ),
          );
          viewer.replaceChildren();
          selectedSurface = null;
        } else if (!selectedSurface) {
          renderViewer(viewer, surfaces[0]);
        }
      })
      .catch(function (error) {
        list.replaceChildren(el("div", "app-surface-state app-surface-error", error.message));
      });
  }

  function close() {
    if (modal) window.AliceCoreAPI.ui.modal.close(modal);
  }

  function open() {
    if (modal) modal.remove();

    var body = el("div", "app-surfaces-body");
    var toolbar = el("div", "app-surfaces-toolbar");
    var refresh = el("button", "alice-btn app-surface-refresh", "Обновить список");
    refresh.type = "button";
    toolbar.appendChild(refresh);

    var layout = el("div", "app-surfaces-layout");
    var list = el("div", "app-surfaces-list");
    var viewer = el("div", "app-surfaces-viewer");
    layout.appendChild(list);
    layout.appendChild(viewer);
    body.appendChild(toolbar);
    body.appendChild(layout);

    modal = window.AliceCoreAPI.ui.modal.create({
      id: "app-surfaces-modal",
      title: "Apps",
      className: "app-surfaces-overlay",
      contentClassName: "app-surfaces-card",
      closeAction: "app-surfaces.close",
      body: body,
    });

    (document.querySelector(".alice-pro-app") || document.body).appendChild(modal);
    window.AliceCoreAPI.ui.modal.open(modal);

    refresh.addEventListener("click", function () {
      loadSurfaces(list, viewer);
    });
    loadSurfaces(list, viewer);
  }

  window.AliceCoreAPI.ui.actions.register("app-surfaces.open", open);
  window.AliceCoreAPI.ui.actions.register("app-surfaces.close", close);
  window.openAppSurfacesModal = open;
})();
