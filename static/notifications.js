(function () {
  window.Notifications = {
    enabled: false,

    init: function () {
      if ("Notification" in window) {
        this.enabled = Notification.permission === "granted";
      }
    },

    requestPermission: function () {
      if ("Notification" in window) {
        Notification.requestPermission().then((permission) => {
          this.enabled = permission === "granted";
          if (this.enabled) {
            this.show(
              "Уведомления включены",
              "Вы будете получать уведомления о завершении запросов",
            );
          }
        });
      }
    },

    show: function (title, body, icon) {
      if (!this.enabled) return;

      const notification = new Notification(title, {
        body: body,
        icon: icon || (window.__LOGOS_STATIC_BASE || "/static") + "/icon-192.png",
        tag: "logos-pro",
      });

      notification.onclick = function () {
        window.focus();
        notification.close();
      };

      if (window.LogosCoreAPI && window.LogosCoreAPI.scheduler) {
        window.LogosCoreAPI.scheduler.defer(function () {
          notification.close();
        }, 5000);
      } else {
        notification.close();
      }
    },

    notifyRequestComplete: function (model) {
      this.show("Запрос завершён", `Ответ от ${model} готов`);
    },
  };

  document.addEventListener("DOMContentLoaded", () => {
    window.Notifications.init();
  });
})();
