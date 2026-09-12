// === PROMPTS: История и шаблоны промптов ===
(function() {
    const PROMPTS_KEY = 'alice_pro_prompts';
    const MAX_PROMPTS = 50;

    window.PromptsHistory = {
        // Получить все промпты
        getAll: function() {
            const data = localStorage.getItem(PROMPTS_KEY);
            return data ? JSON.parse(data) : [];
        },

        // Добавить промпт
        add: function(text) {
            if (!text || !text.trim()) return;
            
            const prompts = this.getAll();
            const trimmed = text.trim();
            
            // Удаляем дубликат если есть
            const index = prompts.indexOf(trimmed);
            if (index !== -1) {
                prompts.splice(index, 1);
            }
            
            // Добавляем в начало
            prompts.unshift(trimmed);
            
            // Ограничиваем количество
            if (prompts.length > MAX_PROMPTS) {
                prompts.pop();
            }
            
            localStorage.setItem(PROMPTS_KEY, JSON.stringify(prompts));
        },

        // Удалить промпт
        remove: function(text) {
            const prompts = this.getAll();
            const index = prompts.indexOf(text);
            if (index !== -1) {
                prompts.splice(index, 1);
                localStorage.setItem(PROMPTS_KEY, JSON.stringify(prompts));
            }
        },

        // Очистить историю
        clear: function() {
            localStorage.removeItem(PROMPTS_KEY);
        },

        // Получить последние N промптов
        getRecent: function(limit = 10) {
            return this.getAll().slice(0, limit);
        }
    };
})();
