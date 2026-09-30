package com.alicepro.mobile

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.IBinder
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.SecureRandom
import java.util.concurrent.TimeUnit

/**
 * Foreground service that exposes a local HTTP API for root-level device control.
 *
 * The server binds exclusively to 127.0.0.1 (never 0.0.0.0), requires a
 * bearer token on every request, and enforces a 10-second timeout and 64 KB
 * output cap on every su invocation.  The token is stored in private
 * SharedPreferences and is never written to any log or returned in any response
 * body.
 */
class RootAgentService : Service() {

    private var serverSocket: ServerSocket? = null
    private var serverThread: Thread? = null

    // -------------------------------------------------------------------------
    // Service lifecycle
    // -------------------------------------------------------------------------

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> handleStart()
            ACTION_STOP  -> handleStop()
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        stopServer()
        super.onDestroy()
    }

    // -------------------------------------------------------------------------
    // Start / stop helpers
    // -------------------------------------------------------------------------

    private fun handleStart() {
        AppLogger.info("RootAgentService", "Start requested")
        val token = loadOrCreateToken()
        startForeground(NOTIF_ID, buildNotification())
        startServer(token)
    }

    private fun handleStop() {
        AppLogger.info("RootAgentService", "Stop requested")
        stopServer()
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
            .remove(PREF_TOKEN)
            .remove(PREF_PORT)
            .apply()
        @Suppress("DEPRECATION")
        stopForeground(true)
        stopSelf()
    }

    // -------------------------------------------------------------------------
    // Token management
    // -------------------------------------------------------------------------

    private fun loadOrCreateToken(): String {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val existing = prefs.getString(PREF_TOKEN, "").orEmpty()
        if (existing.isNotBlank()) return existing
        val fresh = generateToken()
        prefs.edit().putString(PREF_TOKEN, fresh).apply()
        return fresh
    }

    // -------------------------------------------------------------------------
    // Notification
    // -------------------------------------------------------------------------

    private fun buildNotification(): Notification {
        // Channel is created in MainActivity.onCreate for API 26+.
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setContentTitle("Root Agent active")
            .setContentText("Listening on localhost:${currentPort()}")
            .setSmallIcon(android.R.drawable.ic_menu_manage)
            .setOngoing(true)
            .build()
    }

    private fun currentPort(): Int =
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).getInt(PREF_PORT, DEFAULT_PORT)

    // -------------------------------------------------------------------------
    // HTTP server
    // -------------------------------------------------------------------------

    private fun startServer(token: String) {
        serverThread = Thread {
            try {
                val loopback = InetAddress.getByName("127.0.0.1")
                val ss = ServerSocket(DEFAULT_PORT, 50, loopback)
                serverSocket = ss
                // Save the actual bound port so the bridge can expose it.
                getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
                    .putInt(PREF_PORT, ss.localPort)
                    .apply()
                AppLogger.info("RootAgentService", "Listening on 127.0.0.1:${ss.localPort}")
                while (!ss.isClosed) {
                    try {
                        val client = ss.accept()
                        handleClient(client, token)
                    } catch (e: Exception) {
                        if (!ss.isClosed) {
                            AppLogger.warning("RootAgentService", "Accept error: ${e.message}")
                        }
                    }
                }
            } catch (e: Exception) {
                AppLogger.error("RootAgentService", "Server error", e)
            }
        }
        serverThread!!.isDaemon = true
        serverThread!!.start()
    }

    private fun stopServer() {
        try { serverSocket?.close() } catch (_: Exception) {}
        serverSocket = null
        serverThread?.interrupt()
        serverThread = null
    }

    private fun handleClient(client: Socket, token: String) {
        try {
            client.use {
                val reader = BufferedReader(InputStreamReader(it.getInputStream(), Charsets.UTF_8))

                // Parse request line.
                val requestLine = reader.readLine() ?: return
                val parts = requestLine.split(" ")
                if (parts.size < 2) {
                    writeResponse(it, 400, "Bad Request", """{"error":"bad request"}""")
                    return
                }
                val method = parts[0].uppercase()
                val path   = parts[1]

                // Parse headers.
                val headers = mutableMapOf<String, String>()
                var line = reader.readLine()
                while (line != null && line.isNotEmpty()) {
                    val idx = line.indexOf(':')
                    if (idx > 0) {
                        val key   = line.substring(0, idx).trim().lowercase()
                        val value = line.substring(idx + 1).trim()
                        headers[key] = value
                    }
                    line = reader.readLine()
                }

                // Read body if present.
                val contentLength = headers["content-length"]?.toIntOrNull() ?: 0
                val body = if (contentLength > 0) {
                    val buf = CharArray(contentLength.coerceAtMost(MAX_OUTPUT_BYTES))
                    reader.read(buf, 0, buf.size)
                    String(buf)
                } else ""

                // Authenticate — every endpoint requires a valid bearer token.
                val authHeader = headers["authorization"] ?: ""
                if (!authHeader.equals("Bearer $token", ignoreCase = false)) {
                    writeResponse(it, 401, "Unauthorized", """{"error":"unauthorized"}""")
                    return
                }

                // Route.
                val responseBody = when {
                    method == "GET"  && path == "/health"    -> handleHealth()
                    method == "POST" && path == "/exec"       -> handleExec(body)
                    method == "POST" && path == "/screenshot" -> handleScreenshot()
                    method == "POST" && path == "/tap"        -> handleTap(body)
                    method == "POST" && path == "/swipe"      -> handleSwipe(body)
                    method == "POST" && path == "/text"       -> handleText(body)
                    method == "GET"  && path == "/packages"  -> handlePackages()
                    method == "GET"  && path == "/processes" -> handleProcesses()
                    else -> { writeResponse(it, 404, "Not Found", """{"error":"not found"}"""); return }
                }

                writeResponse(it, 200, "OK", responseBody)
            }
        } catch (e: Exception) {
            AppLogger.warning("RootAgentService", "Client handler error: ${e.message}")
        }
    }

    private fun writeResponse(socket: Socket, status: Int, statusText: String, body: String) {
        val bytes = body.toByteArray(Charsets.UTF_8)
        val response = buildString {
            append("HTTP/1.1 $status $statusText\r\n")
            append("Content-Type: application/json\r\n")
            append("Content-Length: ${bytes.size}\r\n")
            append("Connection: close\r\n")
            append("\r\n")
        }
        socket.getOutputStream().apply {
            write(response.toByteArray(Charsets.UTF_8))
            write(bytes)
            flush()
        }
    }

    // -------------------------------------------------------------------------
    // Endpoint handlers
    // -------------------------------------------------------------------------

    private fun handleHealth(): String {
        return try {
            val result = runSu("id")
            if (result.exitCode == 0) {
                """{"ok":true}"""
            } else {
                """{"ok":false,"error":"root unavailable"}"""
            }
        } catch (e: Exception) {
            """{"ok":false,"error":"root unavailable"}"""
        }
    }

    private fun handleExec(body: String): String {
        val cmd = try { JSONObject(body).getString("cmd") } catch (e: Exception) {
            return """{"error":"missing cmd field"}"""
        }
        val result = runSu(cmd)
        return JSONObject().apply {
            put("stdout", result.stdout)
            put("stderr", result.stderr)
            put("exit_code", result.exitCode)
        }.toString()
    }

    private fun handleScreenshot(): String {
        val result = runSuRaw(listOf("su", "-c", "screencap -p"))
        val b64 = android.util.Base64.encodeToString(result, android.util.Base64.NO_WRAP)
        return """{"image_b64":"$b64"}"""
    }

    private fun handleTap(body: String): String {
        return try {
            val json = JSONObject(body)
            val x = json.getInt("x")
            val y = json.getInt("y")
            runSu("input tap $x $y")
            """{"ok":true}"""
        } catch (e: Exception) {
            """{"error":"${e.message?.replace("\"", "\\\"")}"}"""
        }
    }

    private fun handleSwipe(body: String): String {
        return try {
            val json = JSONObject(body)
            val x1 = json.getInt("x1")
            val y1 = json.getInt("y1")
            val x2 = json.getInt("x2")
            val y2 = json.getInt("y2")
            val duration = json.optInt("duration", 300)
            runSu("input swipe $x1 $y1 $x2 $y2 $duration")
            """{"ok":true}"""
        } catch (e: Exception) {
            """{"error":"${e.message?.replace("\"", "\\\"")}"}"""
        }
    }

    private fun handleText(body: String): String {
        return try {
            val rawText = JSONObject(body).getString("text")
            // Shell-escape single quotes: replace ' with '\''
            val escaped = rawText.replace("'", "'\\''")
            runSu("input text '$escaped'")
            """{"ok":true}"""
        } catch (e: Exception) {
            """{"error":"${e.message?.replace("\"", "\\\"")}"}"""
        }
    }

    private fun handlePackages(): String {
        val result = runSu("pm list packages")
        val lines = result.stdout.lines()
        val packages = JSONArray()
        for (l in lines) {
            val trimmed = l.trim()
            if (trimmed.startsWith("package:")) {
                packages.put(trimmed.removePrefix("package:"))
            }
        }
        return JSONObject().put("packages", packages).toString()
    }

    private fun handleProcesses(): String {
        val result = runSu("ps -A")
        val output = result.stdout.trim().take(MAX_OUTPUT_BYTES)
        return JSONObject().put("processes", output).toString()
    }

    // -------------------------------------------------------------------------
    // su execution helpers
    // -------------------------------------------------------------------------

    private data class SuResult(val stdout: String, val stderr: String, val exitCode: Int)

    private fun runSu(cmd: String): SuResult {
        val process = ProcessBuilder("su", "-c", cmd)
            .redirectErrorStream(false)
            .start()
        val stdoutFuture = readStreamAsync(process.inputStream)
        val stderrFuture = readStreamAsync(process.errorStream)
        val finished = process.waitFor(TIMEOUT_SECONDS, TimeUnit.SECONDS)
        if (!finished) {
            process.destroy()
            return SuResult("", "timeout", -1)
        }
        val stdout = stdoutFuture.get().take(MAX_OUTPUT_BYTES)
        val stderr = stderrFuture.get().take(MAX_OUTPUT_BYTES)
        return SuResult(stdout, stderr, process.exitValue())
    }

    /**
     * Variant that returns raw bytes (for screenshot).
     */
    private fun runSuRaw(command: List<String>): ByteArray {
        val process = ProcessBuilder(command)
            .redirectErrorStream(false)
            .start()
        val finished = process.waitFor(TIMEOUT_SECONDS, TimeUnit.SECONDS)
        if (!finished) {
            process.destroy()
            return ByteArray(0)
        }
        return process.inputStream.readBytes().let {
            if (it.size > MAX_OUTPUT_BYTES) it.copyOf(MAX_OUTPUT_BYTES) else it
        }
    }

    private fun readStreamAsync(stream: java.io.InputStream): java.util.concurrent.Future<String> {
        val executor = java.util.concurrent.Executors.newSingleThreadExecutor()
        return executor.submit<String> {
            try {
                stream.bufferedReader(Charsets.UTF_8).use { it.readText() }
            } catch (e: Exception) {
                ""
            } finally {
                executor.shutdown()
            }
        }
    }

    // -------------------------------------------------------------------------
    // Companion
    // -------------------------------------------------------------------------

    companion object {
        const val ACTION_START = "com.alicepro.mobile.ROOT_AGENT_START"
        const val ACTION_STOP  = "com.alicepro.mobile.ROOT_AGENT_STOP"
        const val CHANNEL_ID   = "root_agent_channel"
        const val NOTIF_ID     = 9001

        private const val PREFS_NAME       = "alice_pro"
        private const val PREF_TOKEN       = "root_agent_token"
        private const val PREF_PORT        = "root_agent_port"
        private const val DEFAULT_PORT     = 7327
        private const val TIMEOUT_SECONDS  = 10L
        const val MAX_OUTPUT_BYTES = 65536

        /**
         * Generates a 64-character lowercase hex token backed by 32 bytes of
         * SecureRandom entropy.  The token is never written to any log.
         */
        fun generateToken(): String {
            val bytes = ByteArray(32)
            SecureRandom().nextBytes(bytes)
            return bytes.joinToString("") { "%02x".format(it) }
        }
    }
}
