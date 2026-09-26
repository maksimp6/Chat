(function() {
    window.Notifications = {
        enabled: false,
        
        init: function() {
            if ('Notification' in window) {
                this.enabled = Notification.permission === 'granted';
            }
        },
        
        requestPermission: function() {
            if ('Notification' in window) {
                Notification.requestPermission().then(permission => {
                    this.enabled = permission === 'granted';
                    if (this.enabled) {
                        this.show('Уведомления включены', 'Вы будете получать уведомления о завершении запросов');
                    }
                });
            }
        },
        
        show: function(title, body, icon) {
            if (!this.enabled) return;
            
            const notification = new Notification(title, {
                body: body,
                icon: icon || ((window.__ALICE_STATIC_BASE || '/static') + '/icon-192.png'),
                tag: 'alice-pro'
            });
            
            notification.onclick = function() {
                window.focus();
                notification.close();
            };
            
            window.AliceCoreAPI.scheduler.defer(() => notification.close(), 5000);
        },
        
        notifyRequestComplete: function(model) {
            this.show('Запрос завершён', `Ответ от ${model} готов`);
        }
    };
    
    document.addEventListener('DOMContentLoaded', () => {
        window.Notifications.init();
    });
})();
