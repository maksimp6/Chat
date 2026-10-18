package com.alicepro.launcher;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Message;
import android.os.Messenger;
import android.widget.TextView;

/**
 * Берёт строку статуса агента у сервиса Logos. Токен агента сюда не попадает.
 * Сервис защищён permission com.alicepro.mobile.STATUS (signature).
 */
public class AgentStatusClient {
    private static final String LOGOS_PACKAGE = "com.alicepro.mobile";
    private static final String STATUS_SERVICE = "com.alicepro.mobile.rootagent.LauncherStatusService";
    private static final int MSG_REQUEST = 0;
    private static final int MSG_REPLY = 1;

    private final Context context;
    private final TextView view;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private Messenger service;
    private boolean bound;

    private final Messenger replyTo;

    private final ServiceConnection connection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder binder) {
            service = new Messenger(binder);
            request();
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            service = null;
        }
    };

    private final Runnable poll = new Runnable() {
        @Override
        public void run() {
            request();
            handler.postDelayed(this, 30000L);
        }
    };

    public AgentStatusClient(Context context, TextView view) {
        this.context = context;
        this.view = view;
        replyTo = new Messenger(new Handler(Looper.getMainLooper(), msg -> {
        if (msg.what == MSG_REPLY) {
            Bundle data = msg.getData();
            view.setText(data.getString("status", "Нет данных"));
        }
        return true;
    }));
    }

    public void start() {
        Intent intent = new Intent();
        intent.setClassName(LOGOS_PACKAGE, STATUS_SERVICE);
        try {
            bound = context.bindService(intent, connection, Context.BIND_AUTO_CREATE);
        } catch (SecurityException e) {
            bound = false;
        }
        if (!bound) {
            view.setText("Logos не подключена");
        }
        handler.post(poll);
    }

    public void stop() {
        handler.removeCallbacks(poll);
        if (bound) {
            context.unbindService(connection);
            bound = false;
        }
        service = null;
    }

    private void request() {
        if (service == null) {
            return;
        }
        Message msg = Message.obtain(null, MSG_REQUEST);
        msg.replyTo = replyTo;
        try {
            service.send(msg);
        } catch (Exception e) {
            view.setText("Logos не отвечает");
        }
    }
}
