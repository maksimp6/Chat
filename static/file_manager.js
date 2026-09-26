window.fetchVectorStores = function() {
    return window.AliceDispatcher.request('/api/vector-stores')
        .then(function(r) {
            return r.json().catch(function() { return {}; }).then(function(data) {
                if (!r.ok) {
                    var message = "HTTP " + r.status;
                    if (data && typeof data === "object") {
                        if (typeof data.error === "string") message = data.error;
                        else if (data.error && typeof data.error.message === "string") message = data.error.message;
                    }
                    throw new Error("Vector Stores: " + message);
                }
                return data;
            });
        })
        .then(function(data) {
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
                else if (candidate.vector_stores && typeof candidate.vector_stores === "object") next = candidate.vector_stores;
                else if (candidate.stores && typeof candidate.stores === "object") next = candidate.stores;
                else if (candidate.items && typeof candidate.items === "object") next = candidate.items;

                if (!next || next === candidate) break;
                candidate = next;
            }

            throw new Error("Некорректный ответ Vector Stores: ожидался массив хранилищ.");
        });
};

(function() {
    "use strict";

    window.openFileManagerModal = function() {
        var UI = window.SettingsUI;
        var existing = document.getElementById("file-manager-modal");
        if (existing) existing.remove();

        var CoreUI = window.AliceCoreAPI.ui;
        var body = document.createElement("div");
        body.className = "file-manager-body";
        var ov = CoreUI.modal.create({
            id: "file-manager-modal",
            title: "Менеджер файлов",
            className: "file-manager-modal",
            contentClassName: "file-manager-box",
            closeAction: "file-manager.close",
            body: body
        });
        var md = body;

        md.innerHTML = [
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">',
            '    <h2 style="margin:0;color:var(--m-text,#222);">Менеджер файлов</h2>',
            '    <div style="display:flex;gap:8px;align-items:center;">',
            '        <label for="file-upload" style="cursor:pointer;padding:8px 16px;background:var(--m-accent,#4a90d9);color:#fff;border-radius:6px;font-size:13px;">Загрузить файлы</label>',
            '        <input type="file" id="file-upload" style="display:none" multiple>',
            '        <button id="close-fm-btn" style="border:none;background:none;font-size:24px;cursor:pointer;color:var(--m-muted,#666);line-height:1;">&times;</button>',
            '    </div>',
            '</div>',
            // === Секция Vector Stores ===
            '<div style="margin-bottom:20px;padding:16px;border:1px solid var(--m-section-border,#eee);border-radius:8px;background:var(--m-editor,#f8f9fa);">',
            '    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">',
            '        <h3 style="margin:0;font-size:15px;color:var(--m-text,#222);">Vector Stores</h3>',
            '        <div style="display:flex;gap:8px;">',
            '            <input id="vs-name-input" type="text" placeholder="Название..." style="padding:6px 10px;border:1px solid var(--m-border,#ddd);border-radius:4px;font-size:12px;background:var(--m-input-bg,#fff);color:var(--m-text,#222);width:160px;">',
            '            <button id="btn-create-vs" style="padding:6px 12px;background:var(--m-accent,#4a90d9);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;">+ Создать</button>',
            '            <button id="btn-refresh-vs" style="padding:6px 12px;border:1px solid var(--m-border,#ddd);background:var(--m-card,#fff);border-radius:4px;cursor:pointer;font-size:12px;color:var(--m-text,#222);">Обновить</button>',
            '        </div>',
            '    </div>',
            '    <div id="vs-manager-list" style="max-height:200px;overflow-y:auto;"></div>',
            '</div>',
            // === Секция файлов ===
            '<h3 style="margin:0 0 10px;font-size:15px;color:var(--m-text,#222);">Файлы</h3>',
            '<div id="file-list" style="max-height:400px;overflow-y:auto;">',
            '    <div style="padding:20px;text-align:center;color:var(--m-muted,#999);">Загрузка списка файлов...</div>',
            '</div>'
        ].join('');

        (document.querySelector(".alice-pro-app") || document.body).appendChild(ov);
        CoreUI.modal.open(ov);

        function formatBytes(bytes) {
            if (!bytes || bytes <= 0) return "0 B";
            var units = ["B", "KB", "MB", "GB"];
            var i = Math.floor(Math.log(bytes) / Math.log(1024));
            return (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + " " + units[i];
        }

        function copyToClipboard(text) {
            try { navigator.clipboard.writeText(text); } catch(e) {}
        }

        // === Рендер списка Vector Stores ===
        function renderVsManagerList(stores) {
            var cont = document.getElementById('vs-manager-list');
            if (!cont) return;
            cont.replaceChildren();

            if (!Array.isArray(stores)) {
                var invalid = document.createElement('div');
                invalid.className = 'file-manager-state file-manager-state-error';
                invalid.textContent = 'Ошибка: список Vector Stores имеет неожиданный формат.';
                cont.appendChild(invalid);
                return;
            }
            if (stores.length === 0) {
                var empty = document.createElement('div');
                empty.className = 'file-manager-state';
                empty.textContent = 'Нет векторных хранилищ. Создайте новое.';
                cont.appendChild(empty);
                return;
            }

            stores.forEach(function(vs) {
                var row = document.createElement('div');
                row.className = 'file-manager-vs-row';

                var details = document.createElement('div');
                details.className = 'file-manager-vs-details';

                var name = document.createElement('div');
                name.className = 'file-manager-vs-name';
                name.textContent = vs.name || '(без названия)';

                var meta = document.createElement('div');
                meta.className = 'file-manager-vs-meta';
                var idLabel = document.createTextNode('ID: ');
                var idCode = document.createElement('code');
                idCode.className = 'vs-id-copy file-manager-copy-id';
                idCode.dataset.id = vs.id || '';
                idCode.textContent = (vs.id || '').substring(0, 12) + '...';
                var fileCount = vs.file_counts ? (vs.file_counts.completed || 0) : 0;
                meta.appendChild(idLabel);
                meta.appendChild(idCode);
                meta.appendChild(document.createTextNode(' · Файлов: ' + fileCount));

                details.appendChild(name);
                details.appendChild(meta);

                var addBtn = document.createElement('button');
                addBtn.type = 'button';
                addBtn.className = 'vs-add-files-btn file-manager-secondary-btn';
                addBtn.dataset.id = vs.id || '';
                addBtn.dataset.name = vs.name || '';
                addBtn.textContent = '+ Файлы';

                var deleteBtn = document.createElement('button');
                deleteBtn.type = 'button';
                deleteBtn.className = 'vs-delete-btn file-manager-danger-btn';
                deleteBtn.dataset.id = vs.id || '';
                deleteBtn.textContent = 'Удалить';

                row.appendChild(details);
                row.appendChild(addBtn);
                row.appendChild(deleteBtn);
                cont.appendChild(row);
            });

            cont.querySelectorAll('.vs-id-copy').forEach(function(code) {
                code.addEventListener('click', function() {
                    var id = this.dataset.id || '';
                    copyToClipboard(id);
                    var original = this.textContent;
                    this.textContent = 'скопировано!';
                    var self = this;
                    window.AliceCoreAPI.scheduler.defer(function() { self.textContent = original; }, 1500);
                });
            });

            cont.querySelectorAll('.vs-add-files-btn').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    openAddFilesToVsModal(this.dataset.id || '', this.dataset.name || '');
                });
            });

            cont.querySelectorAll('.vs-delete-btn').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    var button = this;
                    var vsId = button.dataset.id || '';
                    if (!confirm('Удалить векторное хранилище?')) return;
                    button.disabled = true;
                    button.textContent = '...';
                    window.AliceDispatcher.request('/api/vector-stores/' + encodeURIComponent(vsId), { method: 'DELETE' })
                        .then(function(response) {
                            if (!response.ok) throw new Error('HTTP ' + response.status);
                            return response.json().catch(function() { return {}; });
                        })
                        .then(function() { loadVsList(); })
                        .catch(function(error) {
                            button.disabled = false;
                            button.textContent = 'Удалить';
                            alert('Ошибка: ' + error.message);
                        });
                });
            });
        }

        function loadVsList() {
            var cont = document.getElementById('vs-manager-list');
            if (cont) cont.innerHTML = '<div style="padding:10px;text-align:center;color:var(--m-muted,#999);font-size:12px;">Загрузка...</div>';
            window.fetchVectorStores()
                .then(function(list) { renderVsManagerList(list); })
                .catch(function(error) {
                    console.error('[FILE MANAGER] Vector Stores load failed:', error);
                    if (!cont) return;
                    var message = error && error.message ? error.message : 'Не удалось загрузить Vector Stores.';
                    cont.innerHTML =
                        '<div style="padding:12px;color:var(--m-danger,#c33);font-size:12px;">' +
                        '<div style="font-weight:600;margin-bottom:6px;">Не удалось загрузить Vector Stores</div>' +
                        '<div style="margin-bottom:8px;word-break:break-word;">' + window.SettingsUI.escapeHtml(message) + '</div>' +
                        '<button id="btn-retry-vs" style="padding:6px 12px;border:1px solid var(--m-border,#ddd);background:var(--m-card,#fff);border-radius:4px;cursor:pointer;font-size:12px;color:var(--m-text,#222);">Повторить</button>' +
                        '</div>';
                    var retry = document.getElementById('btn-retry-vs');
                    if (retry) retry.addEventListener('click', loadVsList);
                });
        }

        // Создание нового Vector Store
        document.getElementById('btn-create-vs').addEventListener('click', function() {
            var name = document.getElementById('vs-name-input').value.trim();
            if (!name) { alert('Введите название'); return; }
            this.disabled = true;
            this.innerText = '...';
            window.AliceDispatcher.request('/api/vector-stores', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name })
            })
            .then(function(r) { return r.json(); })
            .then(function() {
                document.getElementById('vs-name-input').value = '';
                loadVsList();
            })
            .catch(function(e) { alert('Ошибка: ' + e.message); })
            .finally(function() {
                var btn = document.getElementById('btn-create-vs');
                btn.disabled = false;
                btn.innerText = '+ Создать';
            });
        });

        document.getElementById('btn-refresh-vs').addEventListener('click', loadVsList);

        // === Модалка добавления файлов в Vector Store ===
        function openAddFilesToVsModal(vsId, vsName) {
            var existingBg = document.getElementById('vs-add-files-modal');
            if (existingBg) existingBg.remove();

            var addBody = document.createElement('div');
            addBody.className = 'file-manager-add-body';
            var bg = CoreUI.modal.create({
                id: 'vs-add-files-modal',
                title: 'Файлы → ' + (vsName || vsId.substring(0, 8)),
                titleTag: 'h3',
                className: 'file-manager-add-modal',
                contentClassName: 'file-manager-add-box',
                closeAction: 'file-manager.add.close',
                body: addBody
            });
            var panel = addBody;

            var fileListEl = document.createElement('div');
            fileListEl.id = 'vs-add-filelist';
            fileListEl.className = 'file-manager-add-list';

            var confirm = document.createElement('button');
            confirm.type = 'button';
            confirm.id = 'vs-add-confirm';
            confirm.className = 'file-manager-primary-btn';
            confirm.textContent = 'Добавить выбранные файлы';

            panel.appendChild(header);
            panel.appendChild(fileListEl);
            panel.appendChild(confirm);
            (document.querySelector(".alice-pro-app") || document.body).appendChild(bg);
            CoreUI.modal.open(bg);

            function renderAddFilesState(message, isError) {
                fileListEl.replaceChildren();
                var state = document.createElement('div');
                state.className = 'file-manager-state' + (isError ? ' file-manager-state-error' : '');
                state.textContent = message;
                fileListEl.appendChild(state);
            }

            renderAddFilesState('Загрузка...', false);

            window.AliceDispatcher.request('/api/files')
                .then(function(r) {
                    return r.json().then(function(data) {
                        if (!r.ok) throw new Error((data && data.error) || ('HTTP ' + r.status));
                        return data;
                    });
                })
                .then(function(data) {
                    var files = Array.isArray(data.data) ? data.data : [];
                    if (files.length === 0) {
                        renderAddFilesState('Нет файлов. Загрузите их в менеджере.', false);
                        return;
                    }
                    fileListEl.replaceChildren();
                    files.forEach(function(f) {
                        var row = document.createElement('label');
                        row.className = 'file-manager-add-row';

                        var checkbox = document.createElement('input');
                        checkbox.type = 'checkbox';
                        checkbox.className = 'vs-add-file-chk';
                        checkbox.value = f.id || '';

                        var filename = document.createElement('span');
                        filename.className = 'file-manager-add-name';
                        filename.textContent = f.filename || '';

                        var size = document.createElement('span');
                        size.className = 'file-manager-add-size';
                        size.textContent = formatBytes(f.bytes);

                        row.appendChild(checkbox);
                        row.appendChild(filename);
                        row.appendChild(size);
                        fileListEl.appendChild(row);
                    });
                })
                .catch(function(e) {
                    renderAddFilesState('Ошибка: ' + e.message, true);
                });

            confirm.addEventListener('click', function() {
                var checked = [];
                fileListEl.querySelectorAll('.vs-add-file-chk:checked').forEach(function(chk) {
                    checked.push(chk.value);
                });
                if (checked.length === 0) { alert('Выберите файлы'); return; }

                confirm.disabled = true;
                confirm.textContent = 'Добавление...';

                window.AliceDispatcher.request('/api/vector-stores/' + encodeURIComponent(vsId) + '/files', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_ids: checked })
                })
                .then(function(r) {
                    return r.json().then(function(data) {
                        if (!r.ok) throw new Error((data && data.error) || ('HTTP ' + r.status));
                        return data;
                    });
                })
                .then(function() {
                    closeAddModal();
                    loadVsList();
                })
                .catch(function(e) { alert('Ошибка: ' + e.message); })
                .finally(function() {
                    if (!document.getElementById('vs-add-confirm')) return;
                    confirm.disabled = false;
                    confirm.textContent = 'Добавить выбранные файлы';
                });
            });
        }


        // === Рендер файлов ===
        function renderFile(file) {
            var list = document.getElementById('file-list');
            if (list.children.length === 1 && list.children[0].innerText.includes('Загрузка')) {
                list.innerHTML = '';
            }

            var div = document.createElement('div');
            div.id = 'file-row-' + file.id;
            div.style.cssText = "padding:10px;border-bottom:1px solid var(--m-section-border,#eee);display:flex;align-items:center;gap:12px;";
            div.innerHTML = [
                '<div style="flex:1;min-width:0;">',
                '    <div style="font-weight:bold;color:var(--m-text,#222);word-break:break-all;">' + UI.escapeHtml(file.filename) + '</div>',
                '    <div style="font-size:12px;color:var(--m-muted,#666);">' + formatBytes(file.bytes) + ' · ID: <code style="cursor:pointer;color:var(--m-accent,#4a90d9);" class="file-id-copy" data-id="' + UI.escapeHtml(file.id) + '">' + UI.escapeHtml(file.id.substring(0, 12)) + '...</code></div>',
                '</div>',
                '<button class="fm-delete-btn" data-id="' + UI.escapeHtml(file.id) + '" style="padding:6px 12px;background:var(--m-danger,#c33);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;">Удалить</button>'
            ].join('');
            list.appendChild(div);

            // Копирование ID файла
            var codeEl = div.querySelector('.file-id-copy');
            if (codeEl) {
                codeEl.addEventListener('click', function() {
                    copyToClipboard(this.getAttribute('data-id'));
                    var orig = this.textContent;
                    this.textContent = 'скопировано!';
                    var self = this;
                    window.AliceCoreAPI.scheduler.defer(function() { self.textContent = orig; }, 1500);
                });
            }
        }

        function loadFiles() {
            var list = document.getElementById('file-list');
            list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--m-muted,#999);">Загрузка списка файлов...</div>';

            window.AliceDispatcher.request('/api/files')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    list.innerHTML = '';
                    if (!data.data || data.data.length === 0) {
                        list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--m-muted,#999);">Нет загруженных файлов.</div>';
                        return;
                    }
                    data.data.forEach(function(file) { renderFile(file); });

                    list.querySelectorAll('.fm-delete-btn').forEach(function(btn) {
                        btn.addEventListener('click', function() {
                            var fileId = this.getAttribute('data-id');
                            if (!confirm('Удалить файл? Он будет удалён из всех хранилищ.')) return;
                            this.disabled = true;
                            this.innerText = '...';
                            window.AliceDispatcher.request('/api/files/' + fileId, { method: 'DELETE' })
                                .then(function(r) { return r.json(); })
                                .then(function(res) {
                                    if (res.deleted || res.ok) {
                                        var row = document.getElementById('file-row-' + fileId);
                                        if (row) row.remove();
                                        if (list.children.length === 0) {
                                            list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--m-muted,#999);">Нет загруженных файлов.</div>';
                                        }
                                    } else {
                                        alert('Ошибка: ' + (res.error || 'неизвестно'));
                                    }
                                })
                                .catch(function(e) { alert('Ошибка сети: ' + e.message); });
                        });
                    });
                })
                .catch(function(e) {
                    list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--m-danger,#c33);">Ошибка: ' + e.message + '</div>';
                });
        }

        // Загрузка файлов
        document.getElementById('file-upload').addEventListener('change', function(e) {
            var files = e.target.files;
            if (!files.length) return;

            var list = document.getElementById('file-list');
            if (list.children.length === 1 && list.children[0].innerText.includes('Нет загруженных')) {
                list.innerHTML = '';
            }

            for (var i = 0; i < files.length; i++) {
                (function(file) {
                    var div = document.createElement('div');
                    div.style.cssText = "padding:10px;border-bottom:1px solid var(--m-section-border,#eee);display:flex;align-items:center;gap:12px;opacity:0.6;";
                    div.innerHTML = [
                        '<div style="flex:1;min-width:0;">',
                        '    <div style="font-weight:bold;color:var(--m-text,#222);word-break:break-all;">' + UI.escapeHtml(file.name) + ' <span style="font-size:11px;color:var(--m-accent,#4a90d9);">(загрузка...)</span></div>',
                        '    <div style="font-size:12px;color:var(--m-muted,#666);">' + formatBytes(file.size) + '</div>',
                        '</div>'
                    ].join('');
                    list.insertBefore(div, list.firstChild);

                    var formData = new FormData();
                    formData.append('file', file);
                    formData.append('purpose', 'assistants');

                    window.AliceDispatcher.request('/api/files', { method: 'POST', body: formData })
                        .then(function(r) {
                            if (!r.ok) return r.json().then(function(err) { throw new Error(err.error || 'HTTP ' + r.status); });
                            return r.json();
                        })
                        .then(function(res) {
                            div.remove();
                            renderFile(res);
                        })
                        .catch(function(err) {
                            div.innerHTML = [
                                '<div style="flex:1;min-width:0;">',
                                '    <div style="font-weight:bold;color:var(--m-danger,#c33);word-break:break-all;">' + UI.escapeHtml(file.name) + ' <span style="font-size:11px;">(ошибка)</span></div>',
                                '    <div style="font-size:12px;color:var(--m-muted,#666);">' + err.message + '</div>',
                                '</div>'
                            ].join('');
                            div.style.opacity = '1';
                        });
                })(files[i]);
            }
            e.target.value = '';
        });

        // Первичная загрузка
        loadVsList();
        loadFiles();
    };
    window.AliceCoreAPI.ui.actions.register("file-manager.close", function (payload) {
        var modal = payload.element.closest(".modal");
        if (modal) modal.remove();
    });
    window.AliceCoreAPI.ui.actions.register("file-manager.add.close", function (payload) {
        var modal = payload.element.closest(".modal");
        if (modal) modal.remove();
    });

})();
