(function () {
  "use strict";
  window.openSshRuntimeModal = function () {
    var UI = window.SettingsUI;
    var CoreUI = window.LogosCoreAPI.ui;
    var existing = document.getElementById("ssh-runtime-modal-custom");
    if (existing) existing.remove();
    var tabSsh = [
      '<div id="tab-ssh" class="llm-tab-content">',
      '<div id="ssh-settings-status" class="settings-lead">Загрузка конфигурации...</div>',
      UI.chk("set-ssh-enabled", false, "<strong>🔐 Среда SSH</strong> включена"),
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
      '<div class="settings-field-wide">' +
        UI.lbl("Общий known_hosts (серверный путь)") +
        UI.inp("set-ssh-known-hosts", "text", "", {"placeholder": "/srv/logos/ssh/known_hosts"}) +
        "</div>",
      '<fieldset id="ssh-inactive-restrictions" class="settings-fieldset" disabled>',
      '<legend>Ограничения отключены</legend>',
      '<div class="settings-lead">' +
        "Список разрешённых команд и ограничения привилегированных команд не применяются. Подтверждения операций не запрашиваются. Прежние значения ниже сохраняются без изменений." +
        "</div>",
      '<div class="settings-field-wide">' +
        UI.lbl("Список разрешённых команд (regex, один шаблон на строку)") +
        UI.ta(
          "set-ssh-command-allowlist",
          "",
          "height:80px;font-family:monospace;font-size:11px;",
        ) +
        "</div>",
      UI.chk("set-ssh-allow-privileged", false, "Разрешать привилегированные команды (sudo/su/doas/pkexec)"),
      UI.chk("set-ssh-approval-write", true, "Требовать подтверждение записи файлов"),
      UI.chk("set-ssh-approval-privileged", true, "Требовать подтверждение привилегированных команд"),
      "</fieldset>",
      '<div class="settings-field-wide">' +
        UI.lbl("Именованные цели (JSON)") +
        UI.ta("set-ssh-targets", "", "height:180px;font-family:monospace;font-size:11px;") +
        "</div>",
      '<div class="settings-hint">',
      "Приватный ключ и его содержимое никогда не передаются через этот интерфейс. В targets указывается только серверный путь к существующему ключу и known_hosts.",
      "</div>",
      '<div class="settings-hint">',
      "Настройки среды SSH только для чтения: изменить их здесь нельзя.",
      "</div>",
      "</div>",
    ].join("");

    var content = document.createElement("div");
    content.innerHTML = tabSsh;
    // Настройки только для чтения: форма закрыта и после ответа GET.
    content.querySelectorAll("input, textarea, button").forEach(function (el) {
      el.disabled = true;
    });

    var ov = CoreUI.modal.create({
      id: "ssh-runtime-modal-custom",
      title: "Среда SSH",
      body: content,
    });
    CoreUI.modal.open(ov);

    var sshStatusEl = document.getElementById("ssh-settings-status");

    function setSshStatus(message, error) {
      if (!sshStatusEl) return;
      sshStatusEl.textContent = message;
      sshStatusEl.classList.toggle("is-error", Boolean(error));
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
      setSshStatus("Конфигурация загружена.");
    }

    window.LogosDispatcher.request("/api/ssh-runtime/settings")
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error(data.detail || data.error || "Не удалось загрузить настройки среды SSH");
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
    // A click on the backdrop and Escape close the window through LogosCoreAPI.ui.modal.
  };
})();
