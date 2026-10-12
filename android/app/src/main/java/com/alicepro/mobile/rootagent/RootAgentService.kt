package com.alicepro.mobile.rootagent

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.alicepro.mobile.AppLogger

/**
 * Foreground owner of the RootAgent core (#650, first slice). Started only
 * explicitly from inside the app; never exported, never auto-started. It serves
 * the loopback API from [RootAgentServer] while running and stops it on destroy.
 */
class RootAgentService : Service() {
    private var server: RootAgentServer? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            notification(),
            if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0,
        )
        if (server == null) {
            try {
                server = startServer()
                AppLogger.info("RootAgentService", "RootAgent loopback API started", mapOf("port" to PORT.toString()))
            } catch (error: Exception) {
                AppLogger.error("RootAgentService", "RootAgent loopback API failed to start", error)
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    private fun startServer(): RootAgentServer {
        val token = RootAgentTokenStore(this).loadOrCreate()
        val shell = RootShell(timeoutMs = ROOT_TIMEOUT_MS, maxOutputBytes = ROOT_OUTPUT_BYTES)
        val treeShell = RootShell(timeoutMs = TREE_TIMEOUT_MS, maxOutputBytes = TREE_OUTPUT_BYTES)
        val operations = RootOperations(runRoot = shell::run, deviceInfo = ::deviceInfo, runTree = treeShell::run)
        val candidate = RootAgentServer(RootAgentServer.LOOPBACK, PORT, { token }, operations)
        candidate.start()
        return candidate
    }

    override fun onDestroy() {
        server?.stop()
        server = null
        AppLogger.info("RootAgentService", "RootAgent loopback API stopped")
        super.onDestroy()
    }

    private fun deviceInfo(): Map<String, String> = mapOf(
        "manufacturer" to Build.MANUFACTURER,
        "model" to Build.MODEL,
        "device" to Build.DEVICE,
        "release" to Build.VERSION.RELEASE,
        "sdk" to Build.VERSION.SDK_INT.toString(),
    )

    private fun notification(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL_ID) == null) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Root agent", NotificationManager.IMPORTANCE_LOW),
            )
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_warning)
            .setContentTitle("Alice RootAgent")
            .setContentText("Локальный API 127.0.0.1:$PORT работает")
            .setOngoing(true)
            .build()
    }

    companion object {
        const val PORT = 8766
        const val ACTION_STOP = "com.alicepro.mobile.rootagent.STOP"
        private const val CHANNEL_ID = "root_agent"
        private const val NOTIFICATION_ID = 8766
        private const val ROOT_TIMEOUT_MS = 5_000L
        private const val ROOT_OUTPUT_BYTES = 4096
        private const val TREE_TIMEOUT_MS = 15_000L
        private const val TREE_OUTPUT_BYTES = 2 * 1024 * 1024

        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, RootAgentService::class.java))
        }

        fun stop(context: Context) {
            context.startService(Intent(context, RootAgentService::class.java).setAction(ACTION_STOP))
        }
    }
}
