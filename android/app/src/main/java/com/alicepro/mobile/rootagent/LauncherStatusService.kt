package com.alicepro.mobile.rootagent

import android.app.Service
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Message
import android.os.Messenger
import java.net.HttpURLConnection
import java.net.URL

/**
 * Отдаёт лаунчеру только строку статуса агента. Токен остаётся внутри приложения.
 * Доступ ограничен permission com.alicepro.mobile.STATUS (protectionLevel=signature):
 * подключиться могут только приложения с той же подписью.
 */
class LauncherStatusService : Service() {
    private lateinit var worker: HandlerThread
    private lateinit var workerHandler: Handler
    private lateinit var messenger: Messenger

    override fun onCreate() {
        super.onCreate()
        worker = HandlerThread("launcher-status").also { it.start() }
        workerHandler = Handler(worker.looper)
        messenger = Messenger(Handler(mainLooper) { msg -> handleRequest(msg) })
    }

    override fun onBind(intent: Intent?): IBinder = messenger.binder

    override fun onDestroy() {
        worker.quitSafely()
        super.onDestroy()
    }

    private fun handleRequest(msg: Message): Boolean {
        val replyTo = msg.replyTo ?: return true
        workerHandler.post {
            val status = queryStatus()
            val reply = Message.obtain(null, REPLY_STATUS)
            reply.data = Bundle().apply { putString(KEY_STATUS, status) }
            try {
                replyTo.send(reply)
            } catch (_: Exception) {
                // Клиент уже отключился, отвечать некому.
            }
        }
        return true
    }

    /** Запрос к локальному серверу агента; токен читается здесь и наружу не уходит. */
    private fun queryStatus(): String {
        return try {
            val token = RootAgentTokenStore(this).loadOrCreate()
            val conn = URL("http://${RootAgentServer.LOOPBACK}:${RootAgentService.PORT}/v1/status")
                .openConnection() as HttpURLConnection
            conn.connectTimeout = 3000
            conn.readTimeout = 3000
            conn.setRequestProperty("Authorization", "Bearer $token")
            val code = conn.responseCode
            conn.disconnect()
            when (code) {
                200 -> "Агент на связи"
                else -> "Агент недоступен ($code)"
            }
        } catch (_: Exception) {
            "Агент недоступен"
        }
    }

    companion object {
        const val REPLY_STATUS = 1
        const val KEY_STATUS = "status"
    }
}
