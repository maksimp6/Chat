(function () {
  const translations = {
    ru: {
      new_chat: "Новый чат",
      search: "Поиск",
      export: "Экспорт",
      import: "Импорт",
      prompts: "Промпты",
      settings: "Настройки",
      voice: "Голос",
      text: "Текст",
      send: "Отправить",
      type_message: "Введите сообщение...",
      listening: "Слушаю...",
      speaking: "Алиса говорит...",
      press_mic: "Нажмите на микрофон",
      delete_confirm: "Удалить диалог?",
      clear_history: "Очистить историю?",
      no_results: "Ничего не найдено",
      copy: "Копировать",
      delete: "Удалить",
      save_prompt: "Сохранить в промпты",
      use_prompt: "Использовать",
      stats: "Статистика",
      conversations: "Диалоги",
      messages: "Сообщения",
      cost: "Стоимость",
      theme: "Тема",
      language: "Язык",
      notifications: "Уведомления",
    },
    en: {
      new_chat: "New Chat",
      search: "Search",
      export: "Export",
      import: "Import",
      prompts: "Prompts",
      settings: "Settings",
      voice: "Voice",
      text: "Text",
      send: "Send",
      type_message: "Type a message...",
      listening: "Listening...",
      speaking: "Alice is speaking...",
      press_mic: "Press the microphone",
      delete_confirm: "Delete conversation?",
      clear_history: "Clear history?",
      no_results: "No results found",
      copy: "Copy",
      delete: "Delete",
      save_prompt: "Save to prompts",
      use_prompt: "Use",
      stats: "Statistics",
      conversations: "Conversations",
      messages: "Messages",
      cost: "Cost",
      theme: "Theme",
      language: "Language",
      notifications: "Notifications",
    },
  };

  let currentLang = localStorage.getItem("language") || "ru";

  window.i18n = {
    t: function (key) {
      return translations[currentLang][key] || key;
    },
    setLang: function (lang) {
      currentLang = lang;
      localStorage.setItem("language", lang);
      document.documentElement.lang = lang;
      // Обновляем все элементы с data-i18n
      document.querySelectorAll("[data-i18n]").forEach((el) => {
        el.textContent = this.t(el.dataset.i18n);
      });
    },
    getLang: function () {
      return currentLang;
    },
  };

  // Инициализация
  document.addEventListener("DOMContentLoaded", () => {
    window.i18n.setLang(currentLang);
  });
})();
