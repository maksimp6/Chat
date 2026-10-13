package com.alicepro.mobile.rootagent

import java.net.Socket
import java.util.Base64
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

/** Fault injection uses the actual HTTP server; no device acceptance is implied. */
class RootAgentFaultTest {
    private val token = "fault-test-" + "x".repeat(32)
    private val marker = "PRIVATE_SCREEN_MARKER"
    private val unlocked = RootShellResult(0, "isKeyguardShowing=false", false, false)

    private fun exchange(ops: RootOperations, route: String, auth: String? = token): String {
        val server = RootAgentServer("127.0.0.1", 0, { token }, ops)
        val port = server.start()
        try {
            return Socket("127.0.0.1", port).use { socket ->
                socket.soTimeout = 4_000
                val header = auth?.let { "Authorization: Bearer $it\r\n" } ?: ""
                socket.getOutputStream().write(
                    "GET $route HTTP/1.1\r\nHost: 127.0.0.1:$port\r\n$header\r\n".toByteArray(),
                )
                socket.getInputStream().bufferedReader().readText()
            }
        } finally {
            server.stop()
        }
    }

    private fun status(response: String) = response.substringAfter(' ').substringBefore(' ').toInt()

    @Test
    fun corruptPngWithValidSignatureIsUnavailable() {
        val signature = byteArrayOf(0x89.toByte(), 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a)
        for (bytes in listOf(signature, signature + marker.toByteArray())) {
            val ops = RootOperations(
                runRoot = { unlocked }, deviceInfo = { emptyMap() },
                runScreenshot = { RootShellResult(0, Base64.getEncoder().encodeToString(bytes), false, false) },
            )
            assertEquals(RootOperations.Screenshot.Unavailable, ops.screenshot())
        }
    }

    private val validPng = Base64.getDecoder().decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==",
    )

    private fun assertRejectedPng(bytes: ByteArray) {
        val ops = RootOperations(
            runRoot = { unlocked }, deviceInfo = { emptyMap() },
            runScreenshot = { RootShellResult(0, Base64.getEncoder().encodeToString(bytes), false, false) },
        )
        assertEquals(RootOperations.Screenshot.Unavailable, ops.screenshot())
    }

    @Test
    fun truncatedPngIsUnavailable() {
        assertRejectedPng(validPng.copyOf(validPng.size - 12))
    }

    @Test
    fun badChunkCrcIsUnavailable() {
        assertRejectedPng(validPng.copyOf().also { it[29] = (it[29].toInt() xor 1).toByte() })
    }

    @Test
    fun lockDuringScreenshotDiscardsContent() {
        var locked = false
        val encoded = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg=="
        val ops = RootOperations(
            runRoot = { RootShellResult(0, "isKeyguardShowing=$locked", false, false) },
            deviceInfo = { emptyMap() },
            runScreenshot = {
                locked = true
                RootShellResult(0, encoded, false, false)
            },
        )
        val response = exchange(ops, "/v1/screen/capture")
        assertEquals(423, status(response))
        assertFalse(response.contains(encoded))
    }

    @Test
    fun lockDuringTreeCaptureDiscardsContent() {
        var locked = false
        val ops = RootOperations(
            runRoot = { RootShellResult(0, "isKeyguardShowing=$locked", false, false) },
            deviceInfo = { emptyMap() },
            runTree = {
                locked = true
                RootShellResult(0, "<?xml version='1.0'?><hierarchy><node text='$marker'/></hierarchy>", false, false)
            },
        )
        val response = exchange(ops, "/v1/screen/tree")
        assertEquals(423, status(response))
        assertFalse(response.contains(marker))
    }

    @Test
    fun unknownStateAfterCaptureDiscardsContent() {
        var captured = false
        val ops = RootOperations(
            runRoot = { if (captured) RootShellResult(null, marker, true, false) else unlocked },
            deviceInfo = { emptyMap() },
            runTree = {
                captured = true
                RootShellResult(0, "<?xml version='1.0'?><hierarchy><node text='$marker'/></hierarchy>", false, false)
            },
        )
        val response = exchange(ops, "/v1/screen/tree")
        assertEquals(423, status(response))
        assertFalse(response.contains(marker))
    }

    @Test
    fun failedOrTruncatedPostCaptureGuardDiscardsBothFormats() {
        for (failure in listOf(
            RootShellResult(1, marker, false, false),
            RootShellResult(0, "isKeyguardShowing=false", false, true),
            RootShellResult(null, marker, false, false, failedToStart = true),
            RootShellResult(0, "isKeyguardShowing=invalid", false, false),
        )) {
            for (route in listOf("/v1/screen/tree", "/v1/screen/capture")) {
                var captured = false
                val encoded = Base64.getEncoder().encodeToString(validPng)
                val ops = RootOperations(
                    runRoot = { if (captured) failure else unlocked }, deviceInfo = { emptyMap() },
                    runTree = {
                        captured = true
                        RootShellResult(0, "<?xml version='1.0'?><hierarchy><node text='$marker'/></hierarchy>", false, false)
                    },
                    runScreenshot = {
                        captured = true
                        RootShellResult(0, encoded, false, false)
                    },
                )
                val response = exchange(ops, route)
                assertEquals(423, status(response))
                assertFalse(response.contains(marker))
                assertFalse(response.contains(encoded))
            }
        }
    }

    @Test
    fun unauthorizedRequestsNeverInvokeRootOrScreenRunners() {
        val ops = RootOperations(
            runRoot = { error("unauthorized root execution") }, deviceInfo = { error("unauthorized metadata") },
            runTree = { error("unauthorized tree execution") }, runScreenshot = { error("unauthorized capture execution") },
        )
        for (route in listOf("/v1/root-probe", "/v1/status", "/v1/screen/tree", "/v1/screen/capture")) {
            for (auth in listOf(null, "wrong-token")) {
                val response = exchange(ops, route, auth)
                assertEquals(401, status(response))
                assertFalse(response.contains(token))
            }
        }
    }

    @Test
    fun untrustedKeyguardNeverInvokesScreenRunners() {
        for (dump in listOf(
            RootShellResult(0, "isKeyguardShowing=false", false, true),
            RootShellResult(null, marker, false, false, failedToStart = true),
            RootShellResult(0, "isKeyguardShowing=invalid", false, false),
            RootShellResult(0, "isKeyguardShowing=false mKeyguardShowing=true", false, false),
        )) {
            val ops = RootOperations(
                runRoot = { dump }, deviceInfo = { emptyMap() },
                runTree = { error("locked tree execution") }, runScreenshot = { error("locked capture execution") },
            )
            for (route in listOf("/v1/screen/tree", "/v1/screen/capture")) {
                val response = exchange(ops, route)
                assertEquals(423, status(response))
                assertFalse(response.contains(marker))
            }
        }
    }

    @Test
    fun commandFailuresAndTimeoutsDoNotLeakPartialContent() {
        for (failure in listOf(
            RootShellResult(1, marker, false, false),
            RootShellResult(null, marker, false, false, failedToStart = true),
            RootShellResult(0, marker, false, true),
            RootShellResult(null, marker, true, false),
        )) {
            val ops = RootOperations(
                runRoot = { unlocked }, deviceInfo = { emptyMap() },
                runTree = { failure }, runScreenshot = { failure },
            )
            for (route in listOf("/v1/screen/tree", "/v1/screen/capture")) {
                val response = exchange(ops, route)
                assertEquals(if (failure.timedOut) 504 else 502, status(response))
                assertFalse(response.contains(marker))
            }
        }
    }
}
