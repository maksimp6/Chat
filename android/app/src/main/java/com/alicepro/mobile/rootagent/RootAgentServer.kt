package com.alicepro.mobile.rootagent

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.security.MessageDigest
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit

/**
 * Loopback-only HTTP API of the RootAgent core (#650, first slice).
 *
 * - Binds only to 127.0.0.1; any other address is refused at construction.
 * - Every request needs `Authorization: Bearer <token>` (constant-time check).
 * - Requests carrying browser markers (`Origin`, non-`none` `Sec-Fetch-Site`) or a
 *   Host other than 127.0.0.1/localhost on this port are refused, so a WebView or
 *   a DNS-rebinding page cannot reach it.
 * - GET /v1/health, /v1/device-info, /v1/status, /v1/screen/tree, /v1/screen/capture,
 *   /v1/root-probe only; screen tree and capture are refused (423) unless the lock screen is
 *   known to be off. One request per
 *   connection, bounded header size, read timeout and worker count.
 */
class RootAgentServer(
    bindHost: String,
    private val port: Int,
    private val token: () -> String,
    private val operations: RootOperations,
) {
    private val address: InetAddress
    private var socket: ServerSocket? = null
    private var workers: ExecutorService? = null
    private var acceptor: Thread? = null

    @Volatile
    private var boundPort = 0

    init {
        require(bindHost == LOOPBACK) { "RootAgent API is loopback-only" }
        require(port in 0..65535) { "invalid port" }
        address = InetAddress.getByAddress(byteArrayOf(127, 0, 0, 1))
    }

    /** Starts listening and returns the bound port. */
    @Synchronized
    fun start(): Int {
        check(socket == null) { "already started" }
        require(token().length >= MIN_TOKEN_LENGTH) { "token too short" }
        val server = ServerSocket()
        server.reuseAddress = true
        server.bind(InetSocketAddress(address, port), BACKLOG)
        val pool = ThreadPoolExecutor(
            MAX_WORKERS, MAX_WORKERS, 30, TimeUnit.SECONDS,
            java.util.concurrent.ArrayBlockingQueue(MAX_QUEUED),
        ) { r -> Thread(r, "root-agent-worker").apply { isDaemon = true } }
        socket = server
        workers = pool
        boundPort = server.localPort
        acceptor = Thread({ acceptLoop(server, pool) }, "root-agent-accept").apply {
            isDaemon = true
            start()
        }
        return boundPort
    }

    @Synchronized
    fun stop() {
        try {
            socket?.close()
        } catch (_: IOException) {
        }
        workers?.shutdownNow()
        acceptor?.join(1_000)
        socket = null
        workers = null
        acceptor = null
    }

    private fun acceptLoop(server: ServerSocket, pool: ExecutorService) {
        while (!server.isClosed) {
            val client = try {
                server.accept()
            } catch (_: SocketException) {
                return
            } catch (_: IOException) {
                continue
            }
            try {
                pool.execute { serve(client) }
            } catch (_: RejectedExecutionException) {
                closeQuietly(client)
            }
        }
    }

    private fun serve(client: Socket) {
        client.use {
            try {
                it.soTimeout = READ_TIMEOUT_MS
                val (status, body) = handle(it.getInputStream())
                write(it, status, body)
            } catch (_: IOException) {
                // Peer went away or timed out; nothing to report to it.
            }
        }
    }

    private fun handle(input: InputStream): Pair<Int, String?> {
        val head = readHead(input) ?: return 431 to error("header_too_large")
        val lines = head.split("\r\n")
        val requestLine = lines.first().split(' ')
        if (requestLine.size != 3 || !requestLine[1].startsWith("/") ||
            (requestLine[2] != "HTTP/1.1" && requestLine[2] != "HTTP/1.0")
        ) {
            return 400 to error("bad_request")
        }
        val headers = HashMap<String, String>()
        for (line in lines.drop(1)) {
            if (line.isEmpty()) continue
            val colon = line.indexOf(':')
            if (colon <= 0 || line[0] == ' ' || line[0] == '\t') return 400 to error("bad_request")
            val name = line.substring(0, colon).lowercase()
            if (name.any { c -> c == ' ' || c == '\t' }) return 400 to error("bad_request")
            if (headers.put(name, line.substring(colon + 1).trim()) != null) {
                return 400 to error("bad_request")
            }
        }
        val fetchSite = headers["sec-fetch-site"]
        if (headers.containsKey("origin") || (fetchSite != null && fetchSite != "none")) {
            return 403 to error("browser_requests_refused")
        }
        val host = headers["host"]
        if (host != "$LOOPBACK:$boundPort" && host != "localhost:$boundPort") {
            return 403 to error("host_refused")
        }
        if (!authorized(headers["authorization"])) return 401 to error("unauthorized")
        val method = requestLine[0]
        val path = requestLine[1]
        if (path !in ROUTES) return 404 to error("not_found")
        if (method != "GET") return 405 to error("method_not_allowed")
        return when (path) {
            "/v1/health" -> 200 to "{\"status\":\"ok\"}"
            "/v1/device-info" -> 200 to json(operations.deviceInfo())
            "/v1/status" -> when (val status = operations.status()) {
                is RootOperations.Status.Answered -> 200 to "{\"keyguard_locked\":${status.keyguardLocked}}"
                RootOperations.Status.TimedOut -> 504 to error("status_timeout")
            }
            "/v1/screen/tree", "/v1/screen/capture" ->
                guardedScreenContent(path)
            else -> when (val probe = operations.rootProbe()) {
                is RootOperations.Probe.Answered -> 200 to "{\"root\":${probe.root}}"
                RootOperations.Probe.TimedOut -> 504 to error("root_probe_timeout")
            }
        }
    }

    /** Screen content is served only when the lock screen is known to be off. */
    private fun unlocked(): Boolean =
        (operations.status() as? RootOperations.Status.Answered)?.keyguardLocked == false

    private fun guardedScreenContent(path: String): Pair<Int, String?> {
        if (!unlocked()) return 423 to error("device_locked")
        val content = screenContent(path)
        // Capture can take seconds. Discard success if Keyguard locked or became unknown meanwhile.
        if (content.first == 200 && !unlocked()) return 423 to error("device_locked")
        return content
    }

    private fun screenContent(path: String): Pair<Int, String?> = when (path) {
        "/v1/screen/tree" -> when (val tree = operations.screenTree()) {
            is RootOperations.ScreenTree.Answered -> 200 to json(mapOf("xml" to tree.xml))
            RootOperations.ScreenTree.Unavailable -> 502 to error("screen_tree_unavailable")
            RootOperations.ScreenTree.TimedOut -> 504 to error("screen_tree_timeout")
        }
        else -> when (val shot = operations.screenshot()) {
            is RootOperations.Screenshot.Answered ->
                200 to "{\"format\":\"png\",\"png_base64\":\"${shot.pngBase64}\"}"
            RootOperations.Screenshot.Unavailable -> 502 to error("screenshot_unavailable")
            RootOperations.Screenshot.TimedOut -> 504 to error("screenshot_timeout")
        }
    }

    private fun authorized(value: String?): Boolean {
        val expected = ("Bearer " + token()).toByteArray(Charsets.UTF_8)
        val given = (value ?: "").toByteArray(Charsets.UTF_8)
        return MessageDigest.isEqual(expected, given)
    }

    /** Reads up to the blank line; null when the head exceeds [MAX_HEAD_BYTES]. */
    private fun readHead(input: InputStream): String? {
        val out = ByteArrayOutputStream()
        var matched = 0
        val terminator = byteArrayOf('\r'.code.toByte(), '\n'.code.toByte(), '\r'.code.toByte(), '\n'.code.toByte())
        while (true) {
            val b = input.read()
            if (b < 0) break
            if (out.size() >= MAX_HEAD_BYTES) return null
            out.write(b)
            matched = if (b.toByte() == terminator[matched]) matched + 1 else if (b == '\r'.code) 1 else 0
            if (matched == 4) break
        }
        val bytes = out.toByteArray()
        if (bytes.any { it < 0 || it == 0.toByte() }) return ""
        return String(bytes, Charsets.ISO_8859_1).removeSuffix("\r\n\r\n")
    }

    private fun write(client: Socket, status: Int, body: String?) {
        val payload = (body ?: "").toByteArray(Charsets.UTF_8)
        val head = buildString {
            append("HTTP/1.1 ").append(status).append(' ').append(REASONS[status] ?: "Error").append("\r\n")
            append("Content-Type: application/json\r\n")
            append("Content-Length: ").append(payload.size).append("\r\n")
            append("Cache-Control: no-store\r\nConnection: close\r\n\r\n")
        }
        val out = client.getOutputStream()
        out.write(head.toByteArray(Charsets.ISO_8859_1))
        out.write(payload)
        out.flush()
        client.shutdownOutput()
    }

    companion object {
        const val LOOPBACK = "127.0.0.1"
        const val MIN_TOKEN_LENGTH = 32
        private const val BACKLOG = 16
        private const val MAX_WORKERS = 4
        private const val MAX_QUEUED = 16
        private const val READ_TIMEOUT_MS = 5_000
        private const val MAX_HEAD_BYTES = 16 * 1024
        private val ROUTES = setOf("/v1/health", "/v1/device-info", "/v1/status", "/v1/screen/tree", "/v1/screen/capture", "/v1/root-probe")
        private val REASONS = mapOf(
            200 to "OK", 400 to "Bad Request", 401 to "Unauthorized", 403 to "Forbidden",
            404 to "Not Found", 405 to "Method Not Allowed", 431 to "Request Header Fields Too Large",
            423 to "Locked", 502 to "Bad Gateway", 504 to "Gateway Timeout",
        )

        private fun error(code: String) = "{\"error\":\"$code\"}"

        internal fun json(values: Map<String, String>): String =
            values.entries.joinToString(",", "{", "}") { (k, v) -> "\"${escape(k)}\":\"${escape(v)}\"" }

        private fun escape(value: String): String = buildString {
            for (c in value) {
                when {
                    c == '"' -> append("\\\"")
                    c == '\\' -> append("\\\\")
                    c < ' ' -> append(String.format("\\u%04x", c.code))
                    else -> append(c)
                }
            }
        }

        private fun closeQuietly(socket: Socket) {
            try {
                socket.close()
            } catch (_: IOException) {
            }
        }
    }
}
