package com.alicepro.mobile

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.IBinder
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.security.SecureRandom
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

/**
 * Foreground service that exposes a local HTTP API for root-level device control.
 *
 * Binding is exclusively to 127.0.0.1. Every request requires a bearer token checked
 * before any su call. Binary screenshot responses use Content-Type image/png; all
 * other responses use application/json.
 */
class RootAgentService : Service() {

    @Volatile private var serverSocket: ServerSocket? = null
    @Volatile private var serverThread: Thread? = null
    private val activeClients = CopyOnWriteArrayList<Socket>()
    private val ioExecutor: ExecutorService = Executors.newCachedThreadPool()

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
        ioExecutor.shutdownNow()
        super.onDestroy()
    }

    // -------------------------------------------------------------------------
    // Start / stop – idempotent
    // -------------------------------------------------------------------------

    private fun handleStart() {
        // Guard against competing listeners from repeated START intents
        if (serverSocket?.isClosed == false) {
            AppLogger.info("RootAgentService", "Already running; ignoring duplicate start")
            return
        }
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
    // Notification – includes a visible user-operable Stop action
    // -------------------------------------------------------------------------

    private fun buildNotification(): Notification {
        val stopPending = PendingIntent.getService(
            this, 0,
            Intent(this, RootAgentService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
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
            .addAction(android.R.drawable.ic_delete, "Stop", stopPending)
            .build()
    }

    private fun currentPort(): Int =
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).getInt(PREF_PORT, DEFAULT_PORT)

    // -------------------------------------------------------------------------
    // HTTP server
    // -------------------------------------------------------------------------

    private fun startServer(token: String) {
        if (serverSocket?.isClosed == false) return
        val t = Thread {
            try {
                val ss = ServerSocket(DEFAULT_PORT, 50, InetAddress.getByName("127.0.0.1"))
                serverSocket = ss
                getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
                    .putInt(PREF_PORT, ss.localPort).apply()
                AppLogger.info("RootAgentService", "Listening on 127.0.0.1:${ss.localPort}")
                while (!ss.isClosed) {
                    try {
                        val client = ss.accept()
                        activeClients.add(client)
                        ioExecutor.execute {
                            try {
                                handleClient(client, token)
                            } finally {
                                activeClients.remove(client)
                                try { client.close() } catch (_: Exception) {}
                            }
                        }
                    } catch (e: Exception) {
                        if (!ss.isClosed) AppLogger.warning("RootAgentService", "Accept error: ${e.message}")
                    }
                }
            } catch (e: Exception) {
                AppLogger.error("RootAgentService", "Server error", e)
            }
        }
        t.isDaemon = true
        serverThread = t
        t.start()
    }

    private fun stopServer() {
        try { serverSocket?.close() } catch (_: Exception) {}
        serverSocket = null
        // Close all active clients so no idle connection blocks service stop
        activeClients.forEach { try { it.close() } catch (_: Exception) {} }
        activeClients.clear()
        serverThread?.interrupt()
        serverThread = null
    }

    private fun handleClient(client: Socket, token: String) {
        // Bound idle time so one slow client cannot hold the service indefinitely
        client.soTimeout = SOCKET_TIMEOUT_MS
        try {
            val stream = client.getInputStream()
            val out    = client.getOutputStream()

            // Parse request line
            val requestLine = readHttpLine(stream) ?: return
            val parts = requestLine.split(" ")
            if (parts.size < 2) { writeJson(out, 400, "Bad Request", """{"error":"bad request"}"""); return }
            val method = parts[0].uppercase()
            val path   = parts[1]

            // Parse headers with count and per-line length bounds
            val headers = mutableMapOf<String, String>()
            var headerCount = 0
            var hLine = readHttpLine(stream)
            while (hLine != null && hLine.isNotEmpty()) {
                if (++headerCount > MAX_HEADER_COUNT) {
                    writeJson(out, 431, "Request Header Fields Too Large", """{"error":"too many headers"}"""); return
                }
                val idx = hLine.indexOf(':')
                if (idx > 0) headers[hLine.substring(0, idx).trim().lowercase()] = hLine.substring(idx + 1).trim()
                hLine = readHttpLine(stream)
            }

            // Authenticate BEFORE reading body and before any su call
            if (!checkAuth(headers["authorization"] ?: "", token)) {
                writeJson(out, 401, "Unauthorized", """{"error":"unauthorized"}"""); return
            }

            // Read body as exact bytes with byte-correct Content-Length
            val clVal = headers["content-length"]?.toLongOrNull()
            if (clVal != null && (clVal < 0 || clVal > MAX_BODY_BYTES)) {
                writeJson(out, 413, "Content Too Large", """{"error":"body too large"}"""); return
            }
            val bodyBytes = if (clVal != null && clVal > 0) readExactBytes(stream, clVal.toInt()) else ByteArray(0)
            val bodyStr = try {
                bodyBytes.toString(Charsets.UTF_8)
            } catch (_: Exception) {
                writeJson(out, 400, "Bad Request", """{"error":"invalid utf-8 body"}"""); return
            }

            when {
                method == "GET"  && path == "/health"    -> writeJson(out, 200, "OK", handleHealth())
                method == "POST" && path == "/exec"       -> writeJson(out, 200, "OK", handleExec(bodyStr))
                method == "POST" && path == "/screenshot" -> handleScreenshotResponse(out)
                method == "POST" && path == "/tap"        -> writeJson(out, 200, "OK", handleTap(bodyStr))
                method == "POST" && path == "/swipe"      -> writeJson(out, 200, "OK", handleSwipe(bodyStr))
                method == "POST" && path == "/text"       -> writeJson(out, 200, "OK", handleText(bodyStr))
                method == "GET"  && path == "/packages"  -> writeJson(out, 200, "OK", handlePackages())
                method == "GET"  && path == "/processes" -> writeJson(out, 200, "OK", handleProcesses())
                else -> writeJson(out, 404, "Not Found", """{"error":"not found"}""")
            }
        } catch (e: SocketTimeoutException) {
            AppLogger.warning("RootAgentService", "Client read timeout")
        } catch (e: Exception) {
            // Log only the class name – never log request bodies, tokens, or credential text
            AppLogger.warning("RootAgentService", "Client handler error: ${e.javaClass.simpleName}")
        }
    }

    private fun writeJson(out: java.io.OutputStream, status: Int, statusText: String, body: String) {
        writeBytes(out, status, statusText, "application/json", body.toByteArray(Charsets.UTF_8))
    }

    private fun writeBytes(out: java.io.OutputStream, status: Int, statusText: String, contentType: String, body: ByteArray) {
        val header = "HTTP/1.1 $status $statusText\r\nContent-Type: $contentType\r\nContent-Length: ${body.size}\r\nConnection: close\r\n\r\n"
        out.write(header.toByteArray(Charsets.US_ASCII))
        out.write(body)
        out.flush()
    }

    // -------------------------------------------------------------------------
    // Endpoint handlers
    // -------------------------------------------------------------------------

    private fun handleHealth(): String = try {
        val r = runSu("id")
        if (r.exitCode == 0) """{"ok":true}""" else """{"ok":false,"error":"root unavailable"}"""
    } catch (_: Exception) { """{"ok":false,"error":"root unavailable"}""" }

    private fun handleExec(body: String): String {
        val cmd = try { JSONObject(body).getString("cmd") }
        catch (_: Exception) { return """{"error":"missing cmd field"}""" }
        return try {
            val r = runSu(cmd)
            JSONObject().apply {
                put("stdout", r.stdout)
                put("stderr", r.stderr)
                put("exit_code", r.exitCode)
                if (r.stdoutTruncated) put("stdout_truncated", true)
            }.toString()
        } catch (_: Exception) { JSONObject().put("error", "exec failed").toString() }
    }

    private fun handleScreenshotResponse(out: java.io.OutputStream) {
        // runSuRaw completes before any response bytes are written; exceptions map to JSON errors
        try {
            val bytes = runSuRaw(listOf("su", "-c", "screencap -p"))
            writeBytes(out, 200, "OK", "image/png", bytes)
        } catch (_: ScreenshotTooLargeException) {
            writeJson(out, 500, "Internal Server Error", """{"error":"screenshot too large"}""")
        } catch (_: RootDeniedException) {
            writeJson(out, 500, "Internal Server Error", """{"error":"root denied"}""")
        } catch (_: Exception) {
            writeJson(out, 500, "Internal Server Error", """{"error":"screenshot failed"}""")
        }
    }

    private fun handleTap(body: String): String {
        val json = try { JSONObject(body) } catch (_: Exception) { return """{"error":"invalid json"}""" }
        val x = json.optInt("x", -1)
        val y = json.optInt("y", -1)
        if (x < 0 || y < 0) return """{"error":"x and y must be non-negative integers"}"""
        return try {
            val r = runSu("input tap $x $y")
            if (r.exitCode != 0) JSONObject().put("error", "tap failed (exit ${r.exitCode})").toString()
            else """{"ok":true}"""
        } catch (_: Exception) { """{"error":"tap failed"}""" }
    }

    private fun handleSwipe(body: String): String {
        val json = try { JSONObject(body) } catch (_: Exception) { return """{"error":"invalid json"}""" }
        val x1 = json.optInt("x1", -1); val y1 = json.optInt("y1", -1)
        val x2 = json.optInt("x2", -1); val y2 = json.optInt("y2", -1)
        val dur = json.optInt("duration", 300)
        if (x1 < 0 || y1 < 0 || x2 < 0 || y2 < 0) return """{"error":"x1,y1,x2,y2 must be non-negative integers"}"""
        if (dur < 0 || dur > 60_000) return """{"error":"duration must be 0-60000 ms"}"""
        return try {
            val r = runSu("input swipe $x1 $y1 $x2 $y2 $dur")
            if (r.exitCode != 0) JSONObject().put("error", "swipe failed (exit ${r.exitCode})").toString()
            else """{"ok":true}"""
        } catch (_: Exception) { """{"error":"swipe failed"}""" }
    }

    private fun handleText(body: String): String {
        val json = try { JSONObject(body) } catch (_: Exception) { return """{"error":"invalid json"}""" }
        val raw = try { json.getString("text") } catch (_: Exception) { return """{"error":"missing text field"}""" }
        val escaped = shellEscapeText(raw)
        return try {
            val r = runSu("input text '$escaped'")
            if (r.exitCode != 0) JSONObject().put("error", "text input failed (exit ${r.exitCode})").toString()
            else """{"ok":true}"""
        } catch (_: Exception) { """{"error":"text input failed"}""" }
    }

    private fun handlePackages(): String {
        val r = runSu("pm list packages")
        val arr = JSONArray()
        r.stdout.lines().forEach { l ->
            val t = l.trim()
            if (t.startsWith("package:")) arr.put(t.removePrefix("package:"))
        }
        return JSONObject().put("packages", arr).toString()
    }

    private fun handleProcesses(): String = JSONObject().put("processes", runSu("ps -A").stdout).toString()

    // -------------------------------------------------------------------------
    // su execution helpers
    // -------------------------------------------------------------------------

    data class SuResult(
        val stdout: String,
        val stderr: String,
        val exitCode: Int,
        val stdoutTruncated: Boolean = false,
    )

    class ScreenshotTooLargeException : Exception("screenshot exceeds size limit")
    class RootDeniedException : Exception("root execution denied")

    /**
     * Runs [cmd] under su, draining stdout and stderr concurrently before the process
     * exits. Applies a single end-to-end deadline; cleans up process and executors on
     * timeout or error.
     */
    private fun runSu(cmd: String): SuResult {
        val process = ProcessBuilder("su", "-c", cmd).redirectErrorStream(false).start()
        val deadline = System.currentTimeMillis() + TIMEOUT_SECONDS * 1000

        val stdoutF: Future<Pair<String, Boolean>> =
            ioExecutor.submit<Pair<String, Boolean>> { collectBounded(process.inputStream, MAX_TEXT_OUTPUT_BYTES) }
        val stderrF: Future<Pair<String, Boolean>> =
            ioExecutor.submit<Pair<String, Boolean>> { collectBounded(process.errorStream, MAX_TEXT_OUTPUT_BYTES) }

        if (!process.waitFor(TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
            process.destroyForcibly()
            stdoutF.cancel(true)
            stderrF.cancel(true)
            return SuResult("", "timeout", -1)
        }

        val remainMs = (deadline - System.currentTimeMillis()).coerceAtLeast(1000L)
        val (stdout, truncated) = try {
            stdoutF.get(remainMs, TimeUnit.MILLISECONDS)
        } catch (_: TimeoutException) { stdoutF.cancel(true); Pair("", false) }
        val (stderr, _) = try {
            stderrF.get(remainMs, TimeUnit.MILLISECONDS)
        } catch (_: TimeoutException) { stderrF.cancel(true); Pair("", false) }

        return SuResult(stdout, stderr, process.exitValue(), truncated)
    }

    /**
     * Runs [command] and returns raw stdout bytes, draining stderr concurrently.
     * Throws [ScreenshotTooLargeException] if output exceeds [MAX_SCREENSHOT_BYTES],
     * [RootDeniedException] if process exits non-zero, or [IOException] on timeout.
     * Never returns truncated image data as success.
     */
    private fun runSuRaw(command: List<String>): ByteArray {
        val process = ProcessBuilder(command).redirectErrorStream(false).start()
        val stderrDrain: Future<Unit> =
            ioExecutor.submit<Unit> { try { process.errorStream.copyTo(NullOutputStream) } catch (_: Exception) {} }
        try {
            val baos = ByteArrayOutputStream()
            val buf = ByteArray(8192)
            var total = 0L
            while (true) {
                val n = process.inputStream.read(buf)
                if (n == -1) break
                total += n
                if (total > MAX_SCREENSHOT_BYTES) {
                    process.destroyForcibly()
                    throw ScreenshotTooLargeException()
                }
                baos.write(buf, 0, n)
            }
            if (!process.waitFor(TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
                process.destroyForcibly()
                throw IOException("screenshot process timeout")
            }
            if (process.exitValue() != 0) throw RootDeniedException()
            return baos.toByteArray()
        } finally {
            stderrDrain.cancel(true)
        }
    }

    // -------------------------------------------------------------------------
    // Companion – static utilities exposed for unit testing
    // -------------------------------------------------------------------------

    companion object {
        const val ACTION_START = "com.alicepro.mobile.ROOT_AGENT_START"
        const val ACTION_STOP  = "com.alicepro.mobile.ROOT_AGENT_STOP"
        const val CHANNEL_ID   = "root_agent_channel"
        const val NOTIF_ID     = 9001

        private const val PREFS_NAME         = "alice_pro"
        private const val PREF_TOKEN         = "root_agent_token"
        private const val PREF_PORT          = "root_agent_port"
        private const val DEFAULT_PORT       = 7327
        private const val TIMEOUT_SECONDS    = 10L
        const val MAX_TEXT_OUTPUT_BYTES      = 65_536
        const val MAX_SCREENSHOT_BYTES       = 10L * 1024 * 1024  // 10 MB
        const val MAX_BODY_BYTES             = 65_536L
        private const val SOCKET_TIMEOUT_MS  = 30_000
        private const val MAX_HEADER_COUNT   = 64
        const val MAX_HEADER_LINE_BYTES      = 8_192

        private val NullOutputStream = object : java.io.OutputStream() {
            override fun write(b: Int) {}
            override fun write(b: ByteArray, off: Int, len: Int) {}
        }

        /** Generates a 64-char lowercase hex token from 32 bytes of SecureRandom. Never logged. */
        fun generateToken(): String {
            val bytes = ByteArray(32)
            SecureRandom().nextBytes(bytes)
            return bytes.joinToString("") { "%02x".format(it) }
        }

        /** Shell-escapes text for embedding inside a single-quoted sh argument. */
        fun shellEscapeText(text: String): String = text.replace("'", "'\\''")

        /** Returns true only when [header] is exactly "Bearer <token>" (case-sensitive). */
        fun checkAuth(header: String, token: String): Boolean = header == "Bearer $token"

        /**
         * Reads one HTTP line from [stream], stripping the trailing CR.
         * Returns null on immediate EOF. Throws [IOException] if line exceeds
         * [MAX_HEADER_LINE_BYTES].
         */
        fun readHttpLine(stream: InputStream): String? {
            val sb = StringBuilder()
            while (true) {
                val b = stream.read()
                if (b == -1) return if (sb.isEmpty()) null else sb.toString()
                if (b == '\n'.code) {
                    val s = sb.toString()
                    return if (s.endsWith('\r')) s.dropLast(1) else s
                }
                if (sb.length >= MAX_HEADER_LINE_BYTES) throw IOException("header line too long")
                sb.append(b.toChar())
            }
        }

        /**
         * Reads exactly [length] bytes from [stream], handling short reads.
         * Returns a shorter array only on premature EOF.
         */
        fun readExactBytes(stream: InputStream, length: Int): ByteArray {
            val buf = ByteArray(length)
            var offset = 0
            while (offset < length) {
                val n = stream.read(buf, offset, length - offset)
                if (n == -1) break
                offset += n
            }
            return if (offset == length) buf else buf.copyOf(offset)
        }

        /**
         * Collects up to [limit] bytes from [stream] into a UTF-8 string.
         * Continues draining (discarding) bytes past [limit] so the producing
         * process is never blocked by a full pipe.
         * Returns the collected string and whether data was discarded.
         */
        fun collectBounded(stream: InputStream, limit: Int): Pair<String, Boolean> {
            val baos = ByteArrayOutputStream(minOf(limit, 4096))
            val buf = ByteArray(4096)
            var truncated = false
            try {
                while (true) {
                    val n = stream.read(buf)
                    if (n == -1) break
                    val remaining = limit - baos.size()
                    val written = if (remaining > 0) minOf(n, remaining) else 0
                    if (written > 0) baos.write(buf, 0, written)
                    if (n > written) truncated = true
                }
            } catch (_: Exception) {}
            return Pair(baos.toByteArray().toString(Charsets.UTF_8), truncated)
        }
    }
}
