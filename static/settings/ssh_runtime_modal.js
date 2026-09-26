(function() {
    "use strict";
    window.openSshRuntimeModal = function() {
        var UI = window.SettingsUI;
        var existing = document.getElementById("ssh-runtime-modal-custom");
        if (existing) existing.remove();
        var ov = document.createElement("div");
        ov.id = "ssh-runtime-modal-custom";
        ov.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:10001;display:flex;align-items:center;justify-content:center;";
        var md = document.createElement("div");
        md.style.cssText = "background:var(--m-bg,#fff);border-radius:12px;padding:20px;max-width:720px;width:94%;max-height:90vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.3);color:var(--m-text,#222);";
        var header = document.createElement("div");
        header.style.cssText = "display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;";
        header.innerHTML = '<h2 style="margin:0;font-size:18px;color:var(--m-text,#222);">🔐 SSH Runtime</h2><button class="alice-btn settings-contract-btn" id="set-close-btn" type="button">&times;</button>';
        md.appendChild(header);
        var tabSsh = [
            '<div id="tab-ssh" class="llm-tab-content">',
            UI.section("SSH Runtime"),
            '<div id="ssh-settings-status" style="font-size:12px;color:var(--m-muted,#666);margin-bottom:10px;">Загрузка конфигурации...</div>',
            UI.chk('set-ssh-enabled', false, '<strong>🔐 SSH Runtime</strong> включён'),
            UI.gap2(
                '<div>' + UI.chk('set-ssh-readonly', false, 'Только чтение') + '</div>',
                '<div>' + UI.lbl('Макс. вывод (байт)') + UI.inp('set-ssh-max-output', 'number', 1048576, ' min="4096" max="10485760" step="4096"') + '</div>'
            ),
            UI.gap2(
                '<div>' + UI.chk('set-ssh-allow-exec', true, 'Разрешить команды') + '</div>',
                '<div>' + UI.chk('set-ssh-allow-write', true, 'Разрешить запись файлов') + '</div>'
            ),
            '<div style="margin-top:10px;">' + UI.lbl('Общий known_hosts (серверный путь)') + UI.inp('set-ssh-known-hosts', 'text', '', ' placeholder="/srv/alice/ssh/known_hosts"') + '</div>',
            '<div style="margin-top:10px;">' + UI.lbl('Command allowlist (regex, один шаблон на строку)') + UI.ta('set-ssh-command-allowlist', '', 'height:80px;font-family:monospace;font-size:11px;') + '</div>',
            UI.chk('set-ssh-allow-privileged', false, 'Разрешать privileged-команды (sudo/su/doas/pkexec)', '', 'Опасные операции остаются под approval gate.'),
            UI.chk('set-ssh-approval-write', true, 'Требовать подтверждение записи файлов'),
            UI.chk('set-ssh-approval-privileged', true, 'Требовать подтверждение privileged-команд'),
            '<div style="margin-top:10px;">' + UI.lbl('Named targets (JSON)') + UI.ta('set-ssh-targets', '', 'height:180px;font-family:monospace;font-size:11px;') + '</div>',
            '<div style="display:flex;gap:8px;align-items:end;margin-top:10px;">',
            '<div style="flex:1;">' + UI.lbl('Проверить target') + UI.inp('set-ssh-test-target', 'text', '', ' placeholder="preview"') + '</div>',
            '<button class="alice-btn settings-contract-btn" id="set-ssh-test-btn" type="button">Проверить</button>',
            '</div>',
            '<div style="margin-top:10px;color:var(--m-muted,#666);font-size:12px;line-height:1.5;">',
            'Приватный ключ и его содержимое никогда не передаются через этот интерфейс. В targets указывается только серверный путь к существующему ключу и known_hosts. ',
            'Проверка подключения выполняет только фиксированную команду true.',
            '</div>',
            '<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px;">',
            '<button class="alice-btn settings-contract-btn" id="set-ssh-save-btn" type="button">Сохранить SSH</button>',
            '</div>',
            '</div>'
        ].join('');


        var content = document.createElement("div");
        content.innerHTML = tabSsh;
        md.appendChild(content);
        ov.appendChild(md);
        document.body.appendChild(ov);
        var sshStatusEl = document.getElementById('ssh-settings-status');
        var sshTestTargetEl = document.getElementById('set-ssh-test-target');

        function setSshStatus(message, error) {
            if (!sshStatusEl) return;
            sshStatusEl.textContent = message;
            sshStatusEl.style.color = error ? 'var(--m-danger,#c33)' : 'var(--m-muted,#666)';
        }

        function populateSshSettings(data) {
            var ssh = data || {};
            document.getElementById('set-ssh-enabled').checked = ssh.enabled === true;
            document.getElementById('set-ssh-readonly').checked = ssh.read_only === true;
            document.getElementById('set-ssh-allow-exec').checked = ssh.allow_command_execution !== false;
            document.getElementById('set-ssh-allow-write').checked = ssh.allow_write_operations !== false;
            document.getElementById('set-ssh-max-output').value = ssh.max_output_bytes || 1048576;
            document.getElementById('set-ssh-known-hosts').value = ssh.known_hosts || '';
            document.getElementById('set-ssh-command-allowlist').value = (ssh.command_allowlist || []).join('\n');
            document.getElementById('set-ssh-allow-privileged').checked = ssh.allow_privileged_operations === true;
            document.getElementById('set-ssh-approval-write').checked = ssh.approval_required_for_write !== false;
            document.getElementById('set-ssh-approval-privileged').checked = ssh.approval_required_for_privileged !== false;
            document.getElementById('set-ssh-targets').value = JSON.stringify(ssh.targets || {}, null, 2);
            document.getElementById('set-ssh-allow-exec').disabled = ssh.read_only === true;
            document.getElementById('set-ssh-allow-write').disabled = ssh.read_only === true;
            var targetNames = Object.keys(ssh.targets || {});
            if (sshTestTargetEl && targetNames.length && !sshTestTargetEl.value) sshTestTargetEl.value = targetNames[0];
            var last = ssh.last_test;
            if (last) {
                setSshStatus('Последняя проверка: ' + (last.success ? 'успешна' : 'ошибка') +
                    (last.target ? ' · ' + last.target : '') +
                    (last.duration_ms != null ? ' · ' + last.duration_ms + ' ms' : ''));
            } else {
                setSshStatus('Конфигурация загружена.');
            }
        }

        window.AliceDispatcher.request('/api/ssh-runtime/settings')
            .then(function(r) {
                return r.json().then(function(data) {
                    if (!r.ok) throw new Error(data.error || 'Не удалось загрузить SSH settings');
                    return data;
                });
            })
            .then(populateSshSettings)
            .catch(function(err) {
                setSshStatus(err.message, true);
            });

        document.getElementById('set-ssh-readonly').addEventListener('change', function() {
            var disabled = this.checked;
            document.getElementById('set-ssh-allow-exec').disabled = disabled;
            document.getElementById('set-ssh-allow-write').disabled = disabled;
            if (disabled) {
                document.getElementById('set-ssh-allow-exec').checked = false;
                document.getElementById('set-ssh-allow-write').checked = false;
            }
        });

        document.getElementById('set-ssh-save-btn').addEventListener('click', function() {
            var targetsText = document.getElementById('set-ssh-targets').value.trim();
            var targets;
            try {
                targets = targetsText ? JSON.parse(targetsText) : {};
            } catch (err) {
                setSshStatus('Targets JSON: ' + err.message, true);
                return;
            }

            var payload = {
                enabled: document.getElementById('set-ssh-enabled').checked,
                read_only: document.getElementById('set-ssh-readonly').checked,
                allow_command_execution: document.getElementById('set-ssh-allow-exec').checked,
                allow_write_operations: document.getElementById('set-ssh-allow-write').checked,
                max_output_bytes: parseInt(document.getElementById('set-ssh-max-output').value, 10) || 1048576,
                known_hosts: document.getElementById('set-ssh-known-hosts').value.trim() || null,
                command_allowlist: document.getElementById('set-ssh-command-allowlist').value.split(/\r?\n/).map(function(v){ return v.trim(); }).filter(Boolean),
                allow_privileged_operations: document.getElementById('set-ssh-allow-privileged').checked,
                approval_required_for_write: document.getElementById('set-ssh-approval-write').checked,
                approval_required_for_privileged: document.getElementById('set-ssh-approval-privileged').checked,
                targets: targets
            };

            setSshStatus('Сохранение...');
            window.AliceDispatcher.request('/api/ssh-runtime/settings', {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            }).then(function(r) {
                return r.json().then(function(data) {
                    if (!r.ok) throw new Error(data.error || 'Не удалось сохранить');
                    return data;
                });
            }).then(function(data) {
                populateSshSettings(data.settings || payload);
                setSshStatus('SSH Runtime settings сохранены.');
            }).catch(function(err) {
                setSshStatus(err.message, true);
            });
        });

        document.getElementById('set-ssh-test-btn').addEventListener('click', function() {
            var target = (sshTestTargetEl && sshTestTargetEl.value || '').trim();
            if (!target) {
                setSshStatus('Укажите target для проверки.', true);
                return;
            }
            setSshStatus('Проверка подключения...');
            window.AliceDispatcher.request('/api/ssh-runtime/test', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({target: target})
            }).then(function(r) {
                return r.json().then(function(data) {
                    if (!r.ok) throw new Error(data.error || 'SSH connection failed');
                    return data;
                });
            }).then(function(data) {
                setSshStatus(
                    'Подключение успешно: ' + data.target + ' · пользователь ' + data.linux_user +
                    ' · ' + data.duration_ms + ' ms'
                );
                window.AliceDispatcher.request('/api/ssh-runtime/settings')
                    .then(function(r) { return r.json(); })
                    .then(populateSshSettings)
                    .catch(function() {});
            }).catch(function(err) {
                setSshStatus(err.message, true);
            });
        });


        function closeModal() { ov.remove(); }
        var closeButton = document.getElementById('set-close-btn');
        if (closeButton) closeButton.addEventListener('click', closeModal);
        ov.addEventListener('click', function(e) { if (e.target === ov) closeModal(); });
    };
})();
