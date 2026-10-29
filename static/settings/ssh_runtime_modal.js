(function () {
  "use strict";
  window.openSshRuntimeModal = function () {
    var UI = window.SettingsUI;
    var CoreUI = window.LogosCoreAPI.ui;
    var existing = document.getElementById("ssh-runtime-modal-custom");
    if (existing) existing.remove();
    var tabSsh = [
      '<div id="tab-ssh" class="llm-tab-content">',
      UI.section("SSH Runtime"),
      '<div id="ssh-settings-status" style="font-size:12px;color:var(--m-muted,#666);margin-bottom:10px;">Загрузка конфигурации...</div>',
      UI.chk("set-ssh-enabled", false, "<strong>🔐 SSH Runtime</strong> включён"),
      UI.gap2(
        "<div>" + UI.chk("set-ssh-readonly", false, "Только чтение") + "</div>",
        "<div>" +
          UI.lbl("Макс. вывод (байт)") +
          UI.inp(
            "set-ssh-max-output",
            "number",
            1048576,
            {"min": "4096", "max": "10485760", "step": "4096"},
          ) +
          "</div>",
      ),
      UI.gap2(
        "<div>" + UI.chk("set-ssh-allow-exec", true, "Разрешить команды") + "</div>",
        "<div>" + UI.chk("set-ssh-allow-write", true, "Разрешить запись файлов") + "</div>",
      ),
      '<div style="margin-top:10px;">' +
        UI.lbl("Общий known_hosts (серверный путь)") +
        UI.inp("set-ssh-known-hosts", "text", "", {"placeholder": "/srv/logos/ssh/known_hosts"}) +
        "</div>",
      '<fieldset id="ssh-inactive-restrictions" disabled style="margin-top:10px;border:1px solid var(--m-border,#ddd);border-radius:6px;padding:10px;">',
      '<legend>Ограничения отключены</legend>',
      '<div style="font-size:12px;color:var(--m-muted,#666);margin-bottom:8px;">' +
        "Список разрешённых команд и ограничения privileged-команд не применяются. Подтверждения операций не запрашиваются. Прежние значения ниже сохраняются без изменений." +
        "</div>",
      '<div style="margin-top:10px;">' +
        UI.lbl("Список разрешённых команд (regex, один шаблон на строку)") +
        UI.ta(
          "set-ssh-command-allowlist",
          "",
          "height:80px;font-family:monospace;font-size:11px;",
        ) +
        "</div>",
      UI.chk("set-ssh-allow-privileged", false, "Разрешать privileged-команды (sudo/su/doas/pkexec)"),
      UI.chk("set-ssh-approval-write", true, "Требовать подтверждение записи файлов"),
      UI.chk("set-ssh-approval-privileged", true, "Требовать подтверждение privileged-команд"),
      "</fieldset>",
      '<div style="margin-top:10px;">' +
        UI.lbl("Именованные цели (JSON)") +
        UI.ta("set-ssh-targets", "", "height:180px;font-family:monospace;font-size:11px;") +
        "</div>",
      '<div style="margin-top:10px;color:var(--m-muted,#666);font-size:12px;line-height:1.5;">',
      "Приватный ключ и его содержимое никогда не передаются через этот интерфейс. В targets указывается только серверный путь к существующему ключу и known_hosts.",
      "</div>",
      '<div style="margin-top:10px;color:var(--m-muted,#666);font-size:12px;line-height:1.5;">',
      "Настройки SSH Runtime только для чтения: изменить их здесь нельзя.",
      "</div>",
      "</div>",
    ].join("");

    var content = document.createElement("div");
    content.className = "ssh-runtime-body";
    content.innerHTML = tabSsh;
    // Форма закрыта до ответа GET: иначе значения по умолчанию перезаписали бы сохранённую конфигурацию (пункт 62)
    var sshControls = content.querySelectorAll("input, textarea, button");
    function setSshFormLocked(locked) {
      sshControls.forEach(function (el) {
        el.disabled = locked;
      });
    }
    setSshFormLocked(true);

    var ov = CoreUI.modal.create({
      id: "ssh-runtime-modal-custom",
      title: "🔐 SSH Runtime",
      className: "ssh-runtime-modal-custom",
      contentClassName: "ssh-runtime-modal-content",
      body: content,
    });
    var modalContent = ov.querySelector(".modal-content");
    modalContent.style.cssText =
      "background:var(--m-bg,#fff);border-radius:12px;padding:20px;box-shadow:0 8px 32px rgba(0,0,0,0.3);color:var(--m-text,#222);";
    (document.querySelector(".logos-pro-app") || document.body).appendChild(ov);
    CoreUI.modal.open(ov);

    var sshStatusEl = document.getElementById("ssh-settings-status");

    function setSshStatus(message, error) {
      if (!sshStatusEl) return;
      sshStatusEl.textContent = message;
      sshStatusEl.style.color = error ? "var(--m-danger,#c33)" : "var(--m-muted,#666)";
    }

    function showLastTest(last) {
      if (last) {
        setSshStatus(
          "Последняя проверка: " +
            (last.success ? "успешна" : "ошибка") +
            (last.target ? " · " + last.target : "") +
            (last.duration_ms != null ? " · " + last.duration_ms + " мс" : ""),
          !last.success,
        );
      } else {
        setSshStatus("Конфигурация загружена.");
      }
    }

    function populateSshSettings(data) {
      var ssh = data || {};
      document.getElementById("set-ssh-enabled").checked = ssh.enabled === true;
      document.getElementById("set-ssh-readonly").checked = ssh.read_only === true;
      document.getElementById("set-ssh-allow-exec").checked = ssh.allow_command_execution !== false;
      document.getElementById("set-ssh-allow-write").checked = ssh.allow_write_operations !== false;
      document.getElementById("set-ssh-max-output").value = ssh.max_output_bytes || 1048576;
      document.getElementById("set-ssh-known-hosts").value = ssh.known_hosts || "";
      document.getElementById("set-ssh-command-allowlist").value = (
        ssh.command_allowlist || []
      ).join("\n");
      document.getElementById("set-ssh-allow-privileged").checked =
        ssh.allow_privileged_operations === true;
      document.getElementById("set-ssh-approval-write").checked =
        ssh.approval_required_for_write !== false;
      document.getElementById("set-ssh-approval-privileged").checked =
        ssh.approval_required_for_privileged !== false;
      document.getElementById("set-ssh-targets").value = JSON.stringify(ssh.targets || {}, null, 2);
      showLastTest(ssh.last_test);
    }

    window.LogosDispatcher.request("/api/ssh-runtime/settings")
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error(data.detail || data.error || "Не удалось загрузить SSH settings");
          return data;
        });
      })
      .then(function (data) {
        // Settings are read-only: the form stays locked after it is filled.
        populateSshSettings(data);
      })
      .catch(function (err) {
        setSshStatus(err.message, true);
      });

    ov.addEventListener("click", function (e) {
      if (e.target === ov) CoreUI.modal.close(ov);
    });
  };
})();
