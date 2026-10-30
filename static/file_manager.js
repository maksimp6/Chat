window.fetchVectorStores = function () {
  // Same budget as the file list (LIST_TIMEOUT_MS below): the server may wait 30 s per attempt.
  return window.LogosDispatcher.request("/api/vector-stores", undefined, { timeoutMs: 90000 })
    .then(function (r) {
      return r
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          if (!r.ok) {
            var message = "HTTP " + r.status;
            if (data && typeof data === "object") {
              if (typeof data.error === "string") message = data.error;
              else if (data.error && typeof data.error.message === "string")
                message = data.error.message;
            }
            var failure = new Error("Vector Stores: " + message);
            failure.status = r.status;
            throw failure;
          }
          return data;
        });
    })
    .then(function (data) {
      var candidate = data;
      for (var i = 0; i < 3; i++) {
        if (Array.isArray(candidate)) return candidate;
        if (!candidate || typeof candidate !== "object") break;

        var next = null;
        if (Array.isArray(candidate.data)) return candidate.data;
        if (Array.isArray(candidate.vector_stores)) return candidate.vector_stores;
        if (Array.isArray(candidate.stores)) return candidate.stores;
        if (Array.isArray(candidate.items)) return candidate.items;

        if (candidate.data && typeof candidate.data === "object") next = candidate.data;
        else if (candidate.vector_stores && typeof candidate.vector_stores === "object")
          next = candidate.vector_stores;
        else if (candidate.stores && typeof candidate.stores === "object") next = candidate.stores;
        else if (candidate.items && typeof candidate.items === "object") next = candidate.items;

        if (!next || next === candidate) break;
        candidate = next;
      }

      throw new Error("Некорректный ответ Vector Stores: ожидался массив хранилищ.");
    });
};

(function () {
  "use strict";

  var LIST_TIMEOUT_MS = 90000;
  var NO_ANSWER_TEXT = "Сервер не ответил вовремя или нет связи. Повторите.";

  // A non-OK response as an Error that keeps its status, so a gateway timeout can be told apart.
  function statusError(r, message) {
    var error = new Error(message || "HTTP " + r.status);
    error.status = r.status;
    return error;
  }

  function readJson(r) {
    return r.json().catch(function () {
      return {};
    });
  }

  // True when no usable answer came back: a browser timeout, a network failure or a 504.
  function isNoAnswer(error) {
    return (
      Boolean(error) &&
      (error.status === 504 ||
        error instanceof TypeError ||
        error.name === "AbortError" ||
        error.name === "TimeoutError")
    );
  }

  // Browser errors are in English and do not help the user, so a no-answer case gets Russian text.
  function describeError(error) {
    if (isNoAnswer(error)) return NO_ANSWER_TEXT;
    return (error && error.message) || "Неизвестная ошибка";
  }

  // The upload rows outlive the window: an upload goes on (for hours on a slow link) while the window is closed, and
  // its row comes back, with its «Отменить», «Повторить» or «Сохранить всё равно», when the window opens again.
  var uploadRows = new Set();
  // Uploads whose pieces all arrived but whose completion failed keep their copy on the server for those buttons, and
  // keep the page from its automatic reload while they wait for the person (upload id -> release of that hold). Only a
  // reload the person starts drops them.
  var keptUploads = new Map();
  // Uploads running in any window, and the file list of the window open now: a row carried into a new window acts on
  // that window.
  var pendingUploads = 0;
  var reloadFileList = function () {};

  function keepUpload(uploadId) {
    if (keptUploads.has(uploadId)) return;
    var release = typeof window.LogosDispatcher.hold === "function" ? window.LogosDispatcher.hold() : function () {};
    keptUploads.set(uploadId, release);
  }

  function releaseKept(uploadId) {
    var release = keptUploads.get(uploadId);
    keptUploads.delete(uploadId);
    if (release) release();
  }

  window.addEventListener("pagehide", function () {
    keptUploads.forEach(function (release, uploadId) {
      try {
        fetch("/api/uploads/" + encodeURIComponent(uploadId), { method: "DELETE", keepalive: true }).catch(function () {});
      } catch (error) {
        // The page is going: nothing more can be done for it.
      }
    });
  });

  window.openFileManagerModal = function () {
    var UI = window.SettingsUI;
    var existing = document.getElementById("file-manager-modal");
    if (existing) existing.remove();

    var CoreUI = window.LogosCoreAPI.ui;
    var body = document.createElement("div");
    body.className = "file-manager-body";
    var ov = CoreUI.modal.create({
      id: "file-manager-modal",
      title: "Менеджер файлов",
      className: "file-manager-modal",
      contentClassName: "file-manager-box",
      closeAction: "file-manager.close",
      body: body,
    });
    var md = body;

    md.innerHTML = [
      '<div class="file-manager-toolbar">',
      '    <div class="file-manager-toolbar-actions">',
      '        <input type="file" id="file-upload" style="position:absolute;opacity:0;width:1px;height:1px;pointer-events:none" multiple>',
      '        <label for="file-upload" style="display:inline-flex;align-items:center;min-height:44px;box-sizing:border-box;cursor:pointer;padding:8px 16px;background:#1f6feb;color:#fff;border-radius:6px;font-size:13px;">Загрузить файлы</label>',
      "    </div>",
      "</div>",
      // === Секция Vector Stores ===
      '<div style="margin-bottom:20px;padding:16px;border:1px solid var(--m-section-border,#eee);border-radius:8px;background:var(--m-editor,#f8f9fa);">',
      '    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">',
      '        <h3 style="margin:0;font-size:15px;color:var(--m-text,#222);">Векторные хранилища</h3>',
      '        <div style="display:flex;gap:8px;">',
      '            <input id="vs-name-input" type="text" placeholder="Название..." style="padding:6px 10px;border:1px solid var(--m-border,#ddd);border-radius:4px;font-size:12px;background:var(--m-input-bg,#fff);color:var(--m-text,#222);width:160px;">',
      '            <button id="btn-create-vs" type="button" class="logos-btn file-manager-vs-create-btn">+ Создать</button>',
      '            <button id="btn-refresh-vs" type="button" class="logos-btn file-manager-vs-refresh-btn">Обновить</button>',
      "        </div>",
      "    </div>",
      '    <div id="vs-manager-list" style="max-height:200px;overflow-y:auto;"></div>',
      "</div>",
      // === Секция файлов ===
      '<h3 style="margin:0 0 10px;font-size:15px;color:var(--m-text,#222);">Файлы</h3>',
      // Upload rows and their errors live here, not in #file-list, which is rebuilt on every refresh.
      '<div id="file-upload-status"></div>',
      '<div id="file-list" style="max-height:400px;overflow-y:auto;">',
      '    <div style="padding:20px;text-align:center;color:var(--m-muted,#999);">Загрузка списка файлов...</div>',
      "</div>",
    ].join("");

    (document.querySelector(".logos-pro-app") || document.body).appendChild(ov);
    CoreUI.modal.open(ov);

    function formatBytes(bytes) {
      if (!bytes || bytes <= 0) return "0 B";
      var units = ["B", "KB", "MB", "GB"];
      var i = Math.floor(Math.log(bytes) / Math.log(1024));
      return (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + " " + units[i];
    }

    // Resolves with whether the copy worked, so the caller can say so.
    function copyToClipboard(text) {
      return Promise.resolve()
        .then(function () {
          return navigator.clipboard.writeText(text);
        })
        .then(
          function () {
            return true;
          },
          function () {
            return false;
          },
        );
    }

    // === Рендер списка Vector Stores ===
    function renderVsManagerList(stores) {
      var cont = document.getElementById("vs-manager-list");
      if (!cont) return;
      cont.replaceChildren();

      if (!Array.isArray(stores)) {
        var invalid = document.createElement("div");
        invalid.className = "file-manager-state file-manager-state-error";
        invalid.textContent = "Ошибка: список Vector Stores имеет неожиданный формат.";
        cont.appendChild(invalid);
        return;
      }
      if (stores.length === 0) {
        var empty = document.createElement("div");
        empty.className = "file-manager-state";
        empty.textContent = "Нет векторных хранилищ. Создайте новое.";
        cont.appendChild(empty);
        return;
      }

      stores.forEach(function (vs) {
        var row = document.createElement("div");
        row.className = "file-manager-vs-row";

        var details = document.createElement("div");
        details.className = "file-manager-vs-details";

        var name = document.createElement("div");
        name.className = "file-manager-vs-name";
        name.textContent = vs.name || "(без названия)";

        var meta = document.createElement("div");
        meta.className = "file-manager-vs-meta";
        var idLabel = document.createTextNode("ID: ");
        var idCode = document.createElement("code");
        idCode.className = "vs-id-copy file-manager-copy-id";
        idCode.dataset.id = vs.id || "";
        idCode.textContent = (vs.id || "").substring(0, 12) + "...";
        var fileCount = vs.file_counts ? vs.file_counts.completed || 0 : 0;
        meta.appendChild(idLabel);
        meta.appendChild(idCode);
        meta.appendChild(document.createTextNode(" · Файлов: " + fileCount));

        details.appendChild(name);
        details.appendChild(meta);

        var addBtn = document.createElement("button");
        addBtn.type = "button";
        addBtn.className = "logos-btn vs-add-files-btn file-manager-secondary-btn";
        addBtn.dataset.id = vs.id || "";
        addBtn.dataset.name = vs.name || "";
        addBtn.textContent = "+ Файлы";

        var deleteBtn = document.createElement("button");
        deleteBtn.type = "button";
        deleteBtn.className = "logos-btn vs-delete-btn file-manager-danger-btn";
        deleteBtn.dataset.id = vs.id || "";
        deleteBtn.textContent = "Удалить";

        row.appendChild(details);
        row.appendChild(addBtn);
        row.appendChild(deleteBtn);
        cont.appendChild(row);
      });

      cont.querySelectorAll(".vs-id-copy").forEach(function (code) {
        code.addEventListener("click", function () {
          var self = this;
          var original = this.textContent;
          copyToClipboard(this.dataset.id || "").then(function (ok) {
            self.textContent = ok ? "скопировано!" : "не скопировано";
            window.LogosCoreAPI.scheduler.defer(function () {
              self.textContent = original;
            }, 1500);
          });
        });
      });

      cont.querySelectorAll(".vs-add-files-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          openAddFilesToVsModal(this.dataset.id || "", this.dataset.name || "");
        });
      });

      cont.querySelectorAll(".vs-delete-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var button = this;
          var vsId = button.dataset.id || "";
          window.LogosDialog.confirm("Удалить векторное хранилище?", {
            confirmText: "Удалить",
            danger: true,
          }).then(function (confirmed) {
            if (!confirmed) return;
            var originalText = button.textContent;
            button.disabled = true;
            button.textContent = "...";
            window.LogosDispatcher.request("/api/vector-stores/" + encodeURIComponent(vsId), {
              method: "DELETE",
            })
              .then(function (response) {
                if (!response.ok) return window.LogosHttpError(response).then(function (e) { throw e; });
                return response.json().catch(function () {
                  return {};
                });
              })
              .then(function () {
                loadVsList();
              })
              .catch(function (error) {
                button.disabled = false;
                button.textContent = originalText;
                button.textContent = "Удалить";
                LogosToast.error(window.LogosErrorText("Не удалось удалить", error));
              });
          });
        });
      });
    }

    function loadVsList() {
      var cont = document.getElementById("vs-manager-list");
      if (cont)
        cont.innerHTML =
          '<div style="padding:10px;text-align:center;color:var(--m-muted,#999);font-size:12px;">Загрузка...</div>';
      window
        .fetchVectorStores()
        .then(function (list) {
          renderVsManagerList(list);
        })
        .catch(function (error) {
          console.error("[FILE MANAGER] Vector Stores load failed:", error);
          if (!cont) return;
          var message = describeError(error);
          // A timeout says nothing about the provider key, so the key hint is shown for real answers only.
          var keyHint = isNoAnswer(error)
            ? ""
            : '<div style="margin-bottom:8px;color:var(--m-muted,#666);">Проверьте ключ провайдера и доступ к векторным хранилищам в настройках.</div>';
          cont.innerHTML =
            '<div style="padding:12px;color:var(--m-danger,#c33);font-size:12px;">' +
            '<div style="font-weight:600;margin-bottom:6px;">Не удалось загрузить векторные хранилища</div>' +
            '<div style="margin-bottom:8px;word-break:break-word;">' +
            window.SettingsUI.escapeHtml(message) +
            "</div>" +
            keyHint +
            '<button class="logos-btn file-manager-vs-refresh-btn" id="btn-retry-vs">Повторить</button>' +
            "</div>";
          var retry = document.getElementById("btn-retry-vs");
          if (retry) retry.addEventListener("click", loadVsList);
        });
    }

    // Создание нового Vector Store
    document.getElementById("btn-create-vs").addEventListener("click", function () {
      var name = document.getElementById("vs-name-input").value.trim();
      if (!name) {
        LogosToast.error("Введите название");
        return;
      }
      this.disabled = true;
      this.innerText = "...";
      window.LogosDispatcher.request("/api/vector-stores", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name }),
      })
        .then(function (r) {
          if (!r.ok) return window.LogosHttpError(r).then(function (e) { throw e; });
        })
        .then(function () {
          document.getElementById("vs-name-input").value = "";
          loadVsList();
        })
        .catch(function (e) {
          LogosToast.error(window.LogosErrorText("Не удалось создать хранилище", e));
        })
        .finally(function () {
          var btn = document.getElementById("btn-create-vs");
          btn.disabled = false;
          btn.innerText = "+ Создать";
        });
    });

    document.getElementById("btn-refresh-vs").addEventListener("click", loadVsList);

    // === Модалка добавления файлов в Vector Store ===
    function openAddFilesToVsModal(vsId, vsName) {
      var existingBg = document.getElementById("vs-add-files-modal");
      if (existingBg) existingBg.remove();

      var addBody = document.createElement("div");
      addBody.className = "file-manager-add-body";
      var bg = CoreUI.modal.create({
        id: "vs-add-files-modal",
        title: "Файлы → " + (vsName || vsId.substring(0, 8)),
        titleTag: "h3",
        className: "file-manager-add-modal",
        contentClassName: "file-manager-add-box",
        closeAction: "file-manager.add.close",
        body: addBody,
      });
      var panel = addBody;

      var fileListEl = document.createElement("div");
      fileListEl.id = "vs-add-filelist";
      fileListEl.className = "file-manager-add-list";

      var confirm = document.createElement("button");
      confirm.type = "button";
      confirm.id = "vs-add-confirm";
      confirm.className = "logos-btn file-manager-primary-btn";
      confirm.textContent = "Добавить выбранные файлы";

      panel.appendChild(fileListEl);
      panel.appendChild(confirm);
      (document.querySelector(".logos-pro-app") || document.body).appendChild(bg);
      CoreUI.modal.open(bg);

      function closeAddModal() {
        CoreUI.modal.close(bg);
      }

      function renderAddFilesState(message, isError) {
        fileListEl.replaceChildren();
        var state = document.createElement("div");
        state.className = "file-manager-state" + (isError ? " file-manager-state-error" : "");
        state.textContent = message;
        fileListEl.appendChild(state);
      }

      // Loads the files that can be added. A failure leaves a retry button in the panel.
      function loadAddableFiles() {
        renderAddFilesState("Загрузка...", false);

        window.LogosDispatcher.request("/api/files", undefined, { timeoutMs: LIST_TIMEOUT_MS })
          .then(function (r) {
            if (r.ok) return r.json();
            return readJson(r).then(function (data) {
              throw statusError(r, data && data.error);
            });
          })
          .then(function (data) {
            var files = Array.isArray(data.data) ? data.data : [];
            if (files.length === 0) {
              renderAddFilesState("Нет файлов. Загрузите их в менеджере.", false);
              return;
            }
            fileListEl.replaceChildren();
            files.forEach(function (f) {
              var row = document.createElement("label");
              row.className = "file-manager-add-row";

              var checkbox = document.createElement("input");
              checkbox.type = "checkbox";
              checkbox.className = "vs-add-file-chk";
              checkbox.value = f.id || "";

              var filename = document.createElement("span");
              filename.className = "file-manager-add-name";
              filename.textContent = f.filename || "";

              var size = document.createElement("span");
              size.className = "file-manager-add-size";
              size.textContent = formatBytes(f.bytes);

              row.appendChild(checkbox);
              row.appendChild(filename);
              row.appendChild(size);
              fileListEl.appendChild(row);
            });
          })
          .catch(function (e) {
            renderAddFilesState("Не удалось загрузить список файлов. " + describeError(e), true);
            var retry = document.createElement("button");
            retry.type = "button";
            retry.className = "logos-btn file-manager-secondary-btn";
            retry.textContent = "Повторить";
            retry.addEventListener("click", loadAddableFiles);
            fileListEl.appendChild(retry);
          });
      }

      loadAddableFiles();

      confirm.addEventListener("click", function () {
        var checked = [];
        fileListEl.querySelectorAll(".vs-add-file-chk:checked").forEach(function (chk) {
          checked.push(chk);
        });
        if (checked.length === 0) {
          LogosToast.error("Выберите файлы");
          return;
        }

        confirm.disabled = true;
        confirm.textContent = "Добавление...";

        // The route takes one file_id per request. An added file is unchecked, so a retry
        // after a failure does not add it a second time.
        function addFrom(index) {
          if (index >= checked.length) return Promise.resolve();
          return window.LogosDispatcher.request(
            "/api/vector-stores/" + encodeURIComponent(vsId) + "/files",
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ file_id: checked[index].value }),
            },
          )
            .then(function (r) {
              if (!r.ok)
                return readJson(r).then(function (data) {
                  throw statusError(r, data && data.error);
                });
            })
            .then(function () {
              checked[index].checked = false;
              return addFrom(index + 1);
            });
        }

        addFrom(0)
          .then(function () {
            closeAddModal();
            loadVsList();
          })
          .catch(function (e) {
            loadVsList();
            LogosToast.error("Не удалось добавить файл. " + describeError(e));
          })
          .finally(function () {
            if (!document.getElementById("vs-add-confirm")) return;
            confirm.disabled = false;
            confirm.textContent = "Добавить выбранные файлы";
          });
      });
    }

    // === Рендер файлов ===
    function renderFile(file) {
      var list = document.getElementById("file-list");
      // Any single child that is not a file row is a placeholder; its text is not checked.
      if (list.children.length === 1 && list.children[0].id.indexOf("file-row-") !== 0) {
        list.innerHTML = "";
      }

      var div = document.createElement("div");
      div.id = "file-row-" + file.id;
      div.style.cssText =
        "padding:10px;border-bottom:1px solid var(--m-section-border,#eee);display:flex;align-items:center;gap:12px;";
      div.innerHTML = [
        '<div style="flex:1;min-width:0;">',
        '    <div style="font-weight:bold;color:var(--m-text,#222);word-break:break-all;">' +
          UI.escapeHtml(file.filename) +
          "</div>",
        '    <div style="font-size:12px;color:var(--m-muted,#666);">' +
          formatBytes(file.bytes) +
          ' · ID: <code style="cursor:pointer;color:var(--m-accent,#4a90d9);" class="file-id-copy" data-id="' +
          UI.escapeHtml(file.id) +
          '">' +
          UI.escapeHtml(file.id.substring(0, 12)) +
          "...</code></div>",
        "</div>",
        '<a class="logos-btn file-manager-secondary-btn" href="/api/files/' +
          encodeURIComponent(file.id) +
          '/content" download>Скачать</a>',
        '<button class="logos-btn fm-delete-btn" data-id="' +
          UI.escapeHtml(file.id) +
          '">Удалить</button>',
      ].join("");
      list.appendChild(div);

      // Копирование ID файла
      var codeEl = div.querySelector(".file-id-copy");
      if (codeEl) {
        codeEl.addEventListener("click", function () {
          var self = this;
          var orig = this.textContent;
          copyToClipboard(this.getAttribute("data-id")).then(function (ok) {
            self.textContent = ok ? "скопировано!" : "не скопировано";
            window.LogosCoreAPI.scheduler.defer(function () {
              self.textContent = orig;
            }, 1500);
          });
        });
      }
    }

    function loadFiles() {
      var list = document.getElementById("file-list");
      if (!list) return;
      list.innerHTML =
        '<div style="padding:20px;text-align:center;color:var(--m-muted,#999);">Загрузка списка файлов...</div>';

      window.LogosDispatcher.request("/api/files", undefined, { timeoutMs: LIST_TIMEOUT_MS })
        .then(function (r) {
          if (!r.ok) throw statusError(r);
          return r.json();
        })
        .then(function (data) {
          list.innerHTML = "";
          if (!data.data || data.data.length === 0) {
            list.innerHTML =
              '<div style="padding:20px;text-align:center;color:var(--m-muted,#999);">Нет загруженных файлов.</div>';
            return;
          }
          data.data.forEach(function (file) {
            renderFile(file);
          });

          list.querySelectorAll(".fm-delete-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
              var fileId = this.getAttribute("data-id");
              var deleteBtn = this;
              window.LogosDialog.confirm("Удалить файл? Он будет удалён из всех хранилищ.", {
                confirmText: "Удалить",
                danger: true,
              }).then(function (confirmed) {
                if (!confirmed) return;
                deleteBtn.disabled = true;
                deleteBtn.innerText = "...";
                // The server waits up to 30 s for a DELETE and does not retry it, so wait a little longer.
                window.LogosDispatcher.request(
                  "/api/files/" + encodeURIComponent(fileId),
                  { method: "DELETE" },
                  { timeoutMs: 35000 },
                )
                  .then(function (r) {
                    // 404: the file is already gone on the server, so its row is stale and goes too.
                    if (r.status === 404) return { deleted: true };
                    if (!r.ok) throw statusError(r);
                    return r.json();
                  })
                  .then(function (res) {
                    if (!res.deleted && !res.ok) throw new Error("delete not confirmed");
                    var row = document.getElementById("file-row-" + fileId);
                    if (row) row.remove();
                    if (list.children.length === 0) {
                      list.innerHTML =
                        '<div style="padding:20px;text-align:center;color:var(--m-muted,#999);">Нет загруженных файлов.</div>';
                    }
                  })
                  .catch(function () {
                    // The outcome is unclear or the delete failed: reload to show what the server has now.
                    LogosToast.error("Не удалось удалить файл. Список обновлён.");
                    loadFiles();
                  });
              });
            });
          });
        })
        .catch(function (e) {
          list.innerHTML =
            '<div style="padding:20px;text-align:center;color:var(--m-danger,#c33);">' +
            '<div style="margin-bottom:10px;">Не удалось загрузить список файлов. ' +
            UI.escapeHtml(describeError(e)) +
            "</div>" +
            '<button class="logos-btn file-manager-secondary-btn" id="btn-retry-files" type="button">Повторить</button>' +
            "</div>";
          var retry = document.getElementById("btn-retry-files");
          if (retry) retry.addEventListener("click", loadFiles);
        });
    }

    // Загрузка файлов.
    // Строки загрузок и ошибок лежат в #file-upload-status, а не в #file-list: список
    // перечитывается целиком, и вместе с ним исчезли бы строки ошибок и незавершённых загрузок.
    var uploadStatusEl = document.getElementById("file-upload-status");
    reloadFileList = loadFiles;
    uploadRows.forEach(function (row) {
      uploadStatusEl.appendChild(row);
    });

    // One file sent in pieces that survive a broken connection (static/upload.js): on a slow link a file of any size
    // arrives, and a drop costs at most the piece in flight. ``uploadId`` goes on with an upload whose pieces all
    // arrived but whose completion failed, without sending the file again; ``force`` completes it although Files may
    // have stored it (the person checked the list).
    function startUpload(file, uploadId, force) {
      // The copy is used now: the send's own hold keeps the page until it ends.
      if (uploadId) releaseKept(uploadId);
      var div = document.createElement("div");
      div.style.cssText =
        "padding:10px;border-bottom:1px solid var(--m-section-border,#eee);display:flex;align-items:center;gap:12px;opacity:0.6;";
      div.innerHTML = [
        '<div style="flex:1;min-width:0;">',
        '    <div style="font-weight:bold;color:var(--m-text,#222);word-break:break-all;">' +
          UI.escapeHtml(file.name) +
          ' <span class="file-upload-progress" style="font-size:11px;color:var(--m-accent,#4a90d9);">(загрузка...)</span></div>',
        '    <div style="font-size:12px;color:var(--m-muted,#666);">' +
          formatBytes(file.size) +
          "</div>",
        "</div>",
      ].join("");
      // The window open now, also when this is «Повторить» on a row carried from an earlier window.
      (document.getElementById("file-upload-status") || uploadStatusEl).appendChild(div);
      uploadRows.add(div);
      pendingUploads += 1;
      // The list is read again once, when the last upload ends or when the person must check it now.
      var listNow = false;
      var forgetRow = function () {
        uploadRows.delete(div);
        div.remove();
      };

      var progress = div.querySelector(".file-upload-progress");
      // On a slow link an upload can take hours and goes on through drops: «Отменить» stops it and drops the copy on
      // the server. Once the file is being stored it cannot be stopped, so the button goes.
      var controller = new AbortController();
      var cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "logos-btn";
      cancel.textContent = "Отменить";
      cancel.addEventListener("click", function () {
        controller.abort();
      });
      div.appendChild(cancel);
      Promise.resolve()
        .then(function () {
          return window.LogosUpload.send(file, {
            name: file.name,
            kind: "file",
            purpose: "assistants",
            signal: controller.signal,
            uploadId: uploadId || undefined,
            resumable: true,
            force: Boolean(force),
            onProgress: function (received, size) {
              if (progress && size > 0) progress.textContent = "(загрузка " + Math.floor((received * 100) / size) + "%)";
            },
            onCompleting: function () {
              // Files may be storing the file now: it cannot be stopped.
              cancel.hidden = true;
              if (progress) progress.textContent = "(сохранение...)";
            },
            onCompletionWaiting: function () {
              // Files is not reachable: nothing is being stored, so the wait can be stopped.
              cancel.hidden = false;
              if (progress) progress.textContent = "(файлы недоступны, повтор...)";
            },
          });
        })
        .then(function () {
          forgetRow();
        })
        .catch(function (err) {
          if (err && err.name === "UploadStoppedError") {
            forgetRow();
            return;
          }
          // Files may have stored a file whose completion got no answer in time, so the user is told to check the
          // list first instead of sending it again.
          // A completion that got an answer tells by its code: only completion_uncertain means it may be stored.
          var uncertain = err && err.completion
            ? Boolean(err.body && err.body.error === "completion_uncertain")
            : isNoAnswer(err);
          var text = uncertain
            ? "Сервер не ответил вовремя: файл мог сохраниться. Проверьте список, прежде чем загружать его снова."
            : err.message;
          div.innerHTML = [
            '<div style="flex:1;min-width:0;">',
            '    <div style="font-weight:bold;color:var(--m-danger,#c33);word-break:break-all;">' +
              UI.escapeHtml(file.name) +
              ' <span style="font-size:11px;">(ошибка)</span></div>',
            '    <div style="font-size:12px;color:var(--m-muted,#666);">' +
              UI.escapeHtml(text) +
              "</div>",
            "</div>",
          ].join("");
          div.style.opacity = "1";
          // Everything arrived and the copy stays: «Повторить» only completes the upload once the cause (a provider key,
          // for example) is fixed; after completion_uncertain «Сохранить всё равно» sends it once the list was checked.
          // «Убрать» drops the copy.
          if (err && err.completion && err.uploadId) {
            keepUpload(err.uploadId);
            var again = document.createElement("button");
            again.type = "button";
            again.className = "logos-btn";
            again.textContent = uncertain ? "Сохранить всё равно" : "Повторить";
            again.addEventListener("click", function () {
              forgetRow();
              startUpload(file, err.uploadId, uncertain);
            });
            var drop = document.createElement("button");
            drop.type = "button";
            drop.className = "logos-btn";
            drop.textContent = "Убрать";
            drop.addEventListener("click", function () {
              // The copy stays known (and dropped on the way out of the page) until the server confirms.
              var release = keptUploads.get(err.uploadId);
              forgetRow();
              window.LogosUpload.discard(err.uploadId).then(function () {
                if (keptUploads.get(err.uploadId) === release) releaseKept(err.uploadId);
              });
            });
            div.append(again, drop);
            // The list is what the person checks before «Сохранить всё равно»: it is read again now.
            if (uncertain) listNow = true;
          } else {
            // Nothing is kept for this row: it does not come back when the window opens again.
            uploadRows.delete(div);
          }
        })
        .finally(function () {
          pendingUploads -= 1;
          // The list is rebuilt once, after the last upload settles, so no row disappears early.
          if (pendingUploads === 0 || listNow) reloadFileList();
        });
    }

    document.getElementById("file-upload").addEventListener("change", function (e) {
      var files = e.target.files;
      if (!files.length) return;

      for (var i = 0; i < files.length; i++) {
        startUpload(files[i], null);
      }
      e.target.value = "";
    });

    // Первичная загрузка
    loadVsList();
    loadFiles();
  };
  window.LogosCoreAPI.ui.actions.register("file-manager.close", function (payload) {
    var modal = payload.element.closest(".modal");
    if (modal) window.LogosCoreAPI.ui.modal.close(modal);
  });
  window.LogosCoreAPI.ui.actions.register("file-manager.add.close", function (payload) {
    var modal = payload.element.closest(".modal");
    if (modal) window.LogosCoreAPI.ui.modal.close(modal);
  });
})();
