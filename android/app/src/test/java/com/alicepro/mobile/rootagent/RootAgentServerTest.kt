package com.alicepro.mobile.rootagent

import java.io.ByteArrayOutputStream
import java.net.InetAddress
import java.net.Socket
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class RootAgentServerTest {
    private val token = "root-agent-test-" + "t".repeat(32)
    private var probe = RootShellResult(exitCode = 0, output = "0\n", timedOut = false, truncated = false)
    private var statusDump = RootShellResult(exitCode = 0, output = "isKeyguardShowing=true\n", timedOut = false, truncated = false)
    private var treeDump = RootShellResult(
        0, "<?xml version='1.0' ?><hierarchy rotation=\"0\"><node text=\"a\\\"b\" /></hierarchy>\n", false, false,
    )
    private var shotDump = RootShellResult(0, "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==\n", false, false)
    private val operations = RootOperations(
        runRoot = { argv -> if (argv == RootOperations.STATUS_ARGV) statusDump else probe },
        deviceInfo = { mapOf("model" to "Redmi 9", "sdk" to "29") },
        runTree = { treeDump },
        runScreenshot = { shotDump },
    )
    private lateinit var server: RootAgentServer
    private var port = 0

    @Before
    fun start() {
        server = RootAgentServer(bindHost = "127.0.0.1", port = 0, token = { token }, operations = operations)
        port = server.start()
    }

    @After
    fun stop() {
        server.stop()
    }

    private fun raw(request: String): String {
        Socket(InetAddress.getByName("127.0.0.1"), port).use { socket ->
            socket.soTimeout = 3_000
            socket.getOutputStream().write(request.toByteArray(Charsets.ISO_8859_1))
            socket.getOutputStream().flush()
            val out = ByteArrayOutputStream()
            socket.getInputStream().copyTo(out)
            return out.toString(Charsets.UTF_8.name())
        }
    }

    private fun get(path: String, auth: String? = token, extra: String = "", host: String = "127.0.0.1:$port"): String {
        val authLine = if (auth != null) "Authorization: Bearer $auth\r\n" else ""
        return raw("GET $path HTTP/1.1\r\nHost: $host\r\n$authLine$extra\r\n")
    }

    private fun status(response: String): Int = response.substringAfter(' ').substringBefore(' ').toInt()

    @Test
    fun healthRequiresTheToken() {
        assertEquals(401, status(get("/v1/health", auth = null)))
        assertEquals(401, status(get("/v1/health", auth = token.dropLast(1))))
        val ok = get("/v1/health")
        assertEquals(200, status(ok))
        assertTrue(ok.endsWith("{\"status\":\"ok\"}"))
    }

    @Test
    fun deviceInfoIsReadOnlyJson() {
        val response = get("/v1/device-info")
        assertEquals(200, status(response))
        assertTrue(response.contains("\"model\":\"Redmi 9\""))
        assertTrue(response.contains("\"sdk\":\"29\""))
    }

    @Test
    fun rootProbeReportsUidZero() {
        assertTrue(get("/v1/root-probe").endsWith("{\"root\":true}"))
        probe = RootShellResult(exitCode = 1, output = "denied", timedOut = false, truncated = false)
        val denied = get("/v1/root-probe")
        assertEquals(200, status(denied))
        assertTrue(denied.endsWith("{\"root\":false}"))
        assertFalse(denied.contains("denied"))
    }

    @Test
    fun rootProbeTimeoutIsAGatewayTimeout() {
        probe = RootShellResult(exitCode = null, output = "", timedOut = true, truncated = false)
        assertEquals(504, status(get("/v1/root-probe")))
    }

    @Test
    fun statusReportsOnlyTheKeyguardBoolean() {
        val locked = get("/v1/status")
        assertEquals(200, status(locked))
        assertTrue(locked.endsWith("{\"keyguard_locked\":true}"))
        statusDump = RootShellResult(0, "isKeyguardShowing=false\nmCurrentFocus=Window{42 u0 com.secret.app}", false, false)
        val unlocked = get("/v1/status")
        assertTrue(unlocked.endsWith("{\"keyguard_locked\":false}"))
        assertFalse(unlocked.contains("com.secret.app"))
        statusDump = RootShellResult(null, "", timedOut = true, truncated = false)
        assertEquals(504, status(get("/v1/status")))
    }

    @Test
    fun screenTreeReturnsEscapedXml() {
        statusDump = RootShellResult(0, "isKeyguardShowing=false\n", false, false)
        val ok = get("/v1/screen/tree")
        assertEquals(200, status(ok))
        assertTrue(ok, ok.endsWith("{\"xml\":\"<?xml version='1.0' ?><hierarchy rotation=\\\"0\\\"><node text=\\\"a\\\\\\\"b\\\" /></hierarchy>\"}"))
        treeDump = RootShellResult(1, "secret", false, false)
        val failed = get("/v1/screen/tree")
        assertEquals(502, status(failed))
        assertFalse(failed.contains("secret"))
        treeDump = RootShellResult(null, "", timedOut = true, truncated = false)
        assertEquals(504, status(get("/v1/screen/tree")))
        assertEquals(401, status(get("/v1/screen/tree", auth = null)))
    }

    @Test
    fun screenshotReturnsBase64Png() {
        statusDump = RootShellResult(0, "isKeyguardShowing=false\n", false, false)
        val ok = get("/v1/screen/capture")
        assertEquals(200, status(ok))
        assertTrue(ok, ok.endsWith("{\"format\":\"png\",\"png_base64\":\"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==\"}"))
        shotDump = RootShellResult(1, "secret", false, false)
        val failed = get("/v1/screen/capture")
        assertEquals(502, status(failed))
        assertFalse(failed.contains("secret"))
        shotDump = RootShellResult(null, "", timedOut = true, truncated = false)
        assertEquals(504, status(get("/v1/screen/capture")))
        assertEquals(401, status(get("/v1/screen/capture", auth = null)))
    }

    @Test
    fun screenContentIsRefusedWhileLockedOrUnknown() {
        treeDump = RootShellResult(0, "<?xml ?><hierarchy><node text=\"secret\"/></hierarchy>", false, false)
        shotDump = RootShellResult(0, "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==\n", false, false)
        val lockedOrUnknown = listOf(
            RootShellResult(0, "isKeyguardShowing=true\n", false, false),
            RootShellResult(null, "", timedOut = true, truncated = false),
            RootShellResult(1, "", false, false),
        )
        for (dump in lockedOrUnknown) {
            statusDump = dump
            for (path in listOf("/v1/screen/tree", "/v1/screen/capture")) {
                val response = get(path)
                assertEquals("$path with $dump", 423, status(response))
                assertTrue(response.endsWith("{\"error\":\"device_locked\"}"))
                assertFalse(response.contains("secret"))
            }
        }
    }

    @Test
    fun browserOriginsAreRejected() {
        assertEquals(403, status(get("/v1/health", extra = "Origin: https://evil.example\r\n")))
        assertEquals(403, status(get("/v1/health", extra = "Sec-Fetch-Site: cross-site\r\n")))
    }

    @Test
    fun foreignHostHeaderIsRejected() {
        assertEquals(403, status(get("/v1/health", host = "attacker.example:$port")))
        assertEquals(200, status(get("/v1/health", host = "localhost:$port")))
    }

    @Test
    fun onlyKnownGetRoutes() {
        assertEquals(404, status(get("/v1/shell")))
        val post = raw("POST /v1/health HTTP/1.1\r\nHost: 127.0.0.1:$port\r\nAuthorization: Bearer $token\r\nContent-Length: 0\r\n\r\n")
        assertEquals(405, status(post))
    }

    @Test
    fun malformedAndOversizedRequestsFailClosed() {
        assertEquals(400, status(raw("garbage\r\n\r\n")))
        val huge = "X-Fill: " + "a".repeat(20_000) + "\r\n"
        assertEquals(431, status(get("/v1/health", extra = huge)))
    }

    @Test
    fun errorsNeverEchoTheToken() {
        val response = get("/v1/missing")
        assertFalse(response.contains(token))
    }

    @Test(expected = IllegalArgumentException::class)
    fun nonLoopbackBindIsRefused() {
        RootAgentServer(bindHost = "0.0.0.0", port = 0, token = { token }, operations = operations)
    }

    @Test(expected = IllegalArgumentException::class)
    fun shortTokenIsRefused() {
        RootAgentServer(bindHost = "127.0.0.1", port = 0, token = { "short" }, operations = operations).start()
    }
}
