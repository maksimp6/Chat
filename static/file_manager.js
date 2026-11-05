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
      // === Секция векторной базы ===
      '<div style="margin-bottom:20px;padding:16px;border:1px solid var(--m-section-border,#eee);border-radius:8px;background:var(--m-editor,#f8f9fa);">',
      '    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">',
      '        <h3 style="margin:0;font-size:15px;color:var(--m-text,#222);">Векторная база</h3>',
      '        <button id="btn-refresh-vs" type="button" class="logos-btn file-manager-vs-refresh-btn">Обновить</button>',
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

    // === Векторная база ===
    // Logos has no Yandex API, so no Yandex vector stores: its own vector database holds every file kept here (the
    // collection "files") next to the collections of its dialogs and repository. The section only shows them; Logos
    // changes them with its vectors tool when asked in chat.
    function renderVectorBase(collections) {
      var cont = document.getElementById("vs-manager-list");
      if (!cont) return;
      cont.replaceChildren();
      if (collections.length === 0) {
        var empty = document.createElement("div");
        empty.className = "file-manager-state";
        empty.textContent = "Векторная база пуста.";
        cont.appendChild(empty);
        return;
      }
      collections.forEach(function (entry) {
        var row = document.createElement("div");
        row.className = "file-manager-vs-row";
        var details = document.createElement("div");
        details.className = "file-manager-vs-details";
        var name = document.createElement("div");
        name.className = "file-manager-vs-name";
        name.textContent = String(entry.collection);
        var meta = document.createElement("div");
        meta.className = "file-manager-vs-meta";
        meta.textContent = "Документов: " + (entry.documents || 0) + " · фрагментов: " + (entry.chunks || 0);
        details.appendChild(name);
        details.appendChild(meta);
        row.appendChild(details);
        cont.appendChild(row);
      });
    }

    function loadVsList() {
      var cont = document.getElementById("vs-manager-list");
      if (cont)
        cont.innerHTML =
          '<div style="padding:10px;text-align:center;color:var(--m-muted,#999);font-size:12px;">Загрузка...</div>';
      window.LogosDispatcher.request(
        "/api/vectors/collections",
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
        { timeoutMs: LIST_TIMEOUT_MS },
      )
        .then(function (r) {
          return readJson(r).then(function (data) {
            if (!r.ok || !data || data.success !== true || !Array.isArray(data.collections)) {
              throw statusError(r, data && typeof data.error === "string" ? data.error : "");
            }
            return data.collections;
          });
        })
        .then(renderVectorBase)
        .catch(function (error) {
          console.error("[FILE MANAGER] Vector database load failed:", error);
          var target = document.getElementById("vs-manager-list");
          if (!target) return;
          target.innerHTML =
            '<div style="padding:12px;color:var(--m-danger,#c33);font-size:12px;">' +
            '<div style="font-weight:600;margin-bottom:6px;">Не удалось прочитать векторную базу</div>' +
            '<div style="margin-bottom:8px;word-break:break-word;">' +
            UI.escapeHtml(describeError(error)) +
            "</div>" +
            '<button class="logos-btn file-manager-vs-refresh-btn" id="btn-retry-vs" type="button">Повторить</button>' +
            "</div>";
          var retry = document.getElementById("btn-retry-vs");
          if (retry) retry.addEventListener("click", loadVsList);
        });
    }

    document.getElementById("btn-refresh-vs").addEventListener("click", loadVsList);

    // Where a kept file stands in the vector database (workspace/stored_files.py): text and whether it is a failure.
    function vectorState(file) {
      var vectors = (file && file.vectors) || {};
      if (vectors.state === "done") {
        if (vectors.text !== "name_only") return { text: "В векторной базе · фрагментов: " + (vectors.chunks || 0) };
        var pdf = /\.pdf$/i.test(String(file.filename || ""));
        return {
          text:
            "В векторной базе только имя и размер: " +
            (pdf ? "у Logos нет программы для чтения PDF." : "текст файла не прочитан."),
        };
      }
      if (vectors.state === "failed") {
        return { text: "Не записан в векторную базу: " + (vectors.error || "ошибка") + ".", error: true };
      }
      if (vectors.last_error) {
        return { text: "Записывается в векторную базу снова. Прошлая попытка: " + vectors.last_error, error: true };
      }
      return { text: "Записывается в векторную базу..." };
    }

    // While a file is being written into the vector database the list is read again, once at a time, as long as this
    // window is open.
    var refreshPending = false;
    function refreshWhileWriting(list) {
      if (refreshPending) return;
      refreshPending = true;
      window.LogosCoreAPI.scheduler.defer(function () {
        refreshPending = false;
        if (document.getElementById("file-list") !== list) return;
        loadFiles();
        loadVsList();
      }, 15000);
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
      var vectors = vectorState(file);
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
        '    <div style="font-size:12px;word-break:break-word;color:' +
          (vectors.error ? "var(--m-danger,#c33)" : "var(--m-muted,#666)") +
          ';">' +
          UI.escapeHtml(vectors.text) +
          "</div>",
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
          if (r.ok) return r.json();
          // The server's reason (a disk that cannot be read, for example) is shown.
          return readJson(r).then(function (data) {
            throw statusError(r, data && typeof data.error === "string" ? data.error : "");
          });
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
          var writing = data.data.some(function (file) {
            return !file.vectors || file.vectors.state !== "done";
          });
          if (writing) refreshWhileWriting(list);

          list.querySelectorAll(".fm-delete-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
              var fileId = this.getAttribute("data-id");
              var deleteBtn = this;
              window.LogosDialog.confirm("Удалить файл? Он будет удалён и из векторной базы.", {
                confirmText: "Удалить",
                danger: true,
              }).then(function (confirmed) {
                if (!confirmed) return;
                deleteBtn.disabled = true;
                deleteBtn.innerText = "...";
                // The file is deleted on the phone at once; the wait is for a slow link.
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
          // The stored file shows in the list at once, also while other uploads still run.
          listNow = true;
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
          // Everything arrived and the copy stays: «Повторить» only completes the upload once the cause (a full disk,
          // for example) is fixed; after no answer «Сохранить всё равно» completes it once the list was checked.
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
          // The list is read again when this upload stored its file or the person must check it, and after the last
          // upload; the upload rows are apart from it, so none disappears early.
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
})();
