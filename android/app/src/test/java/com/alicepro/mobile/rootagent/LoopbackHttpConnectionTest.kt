package com.alicepro.mobile.rootagent

import java.net.InetAddress
import java.net.ServerSocket
import java.net.URI
import java.util.concurrent.atomic.AtomicReference
import org.junit.Assert.*
import org.junit.Test

class LoopbackHttpConnectionTest {
    private fun exchange(response: String, verify: (RootAgentClient.Response, String) -> Unit) {
        ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { server ->
            server.soTimeout = 2000
            val request = AtomicReference<String>()
            val failure = AtomicReference<Throwable>()
            val peer = Thread {
                try {
                    server.accept().use { socket ->
                        socket.soTimeout = 2000
                        val input = socket.getInputStream().bufferedReader(Charsets.US_ASCII)
                        val lines = mutableListOf<String>()
                        while (true) {
                            val line = input.readLine() ?: break
                            if (line.isEmpty()) break
                            lines.add(line)
                        }
                        request.set(lines.joinToString("\n"))
                        socket.getOutputStream().write(response.toByteArray(Charsets.ISO_8859_1))
                    }
                } catch (error: Throwable) {
                    failure.set(error)
                }
            }.apply { isDaemon = true; start() }
            RootAgentClient(server.localPort, totalTimeoutMs = 1500) { "synthetic-token" }.use { client ->
                val result = client.request(RootAgentClient.Endpoint.HEALTH)
                peer.join(2500)
                assertFalse("peer must finish", peer.isAlive)
                failure.get()?.let { throw AssertionError("synthetic peer failed", it) }
                verify(result, request.get())
            }
        }
    }

    private fun assertRejected(response: String) = exchange(response) { result, _ ->
        assertEquals("unreachable", result.error)
        assertNull(result.code)
        assertEquals("", result.body)
        assertFalse(result.toString().contains("synthetic-screen"))
    }

    @Test
    fun duplicateLengthAndTransferEncodingAreRejected() {
        assertRejected("HTTP/1.1 200 OK\r\nContent-Length: 16\r\ncontent-length: 16\r\n\r\nsynthetic-screen")
        assertRejected("HTTP/1.1 200 OK\r\nContent-Length: 16\r\nTransfer-Encoding: chunked\r\n\r\nsynthetic-screen")
    }

    @Test
    fun missingNegativeAndOverflowingLengthAreRejected() {
        for (field in listOf("", "Content-Length: -1\r\n", "Content-Length: 9223372036854775808\r\n")) {
            assertRejected("HTTP/1.1 200 OK\r\n${field}\r\nsynthetic-screen")
        }
    }

    @Test
    fun invalidStatusAndHeaderBytesAreRejected() {
        assertRejected("HTTP/2 200 OK\r\nContent-Length: 0\r\n\r\n")
        assertRejected("HTTP/1.1 999 Invalid\r\nContent-Length: 0\r\n\r\n")
        for (value in listOf('\u0000', '\u00ff')) {
            assertRejected("HTTP/1.1 200 OK\r\nX-Test: $value\r\nContent-Length: 0\r\n\r\n")
        }
    }

    @Test
    fun oversizedAndTruncatedHeadersAreRejected() {
        assertRejected("HTTP/1.1 200 OK\r\nX-Test: ${"x".repeat(16 * 1024)}\r\nContent-Length: 0\r\n\r\n")
        assertRejected("HTTP/1.1 200 OK\r\nContent-Length: 16\r\n")
    }

    @Test
    fun truncatedBodyNeverReturnsPartialScreenOrSuccess() {
        assertRejected("HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nsynthetic-screen")
    }

    @Test
    fun redirectNeverConnectsToTargetOrForwardsCredentials() {
        ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { target ->
            target.soTimeout = 200
            exchange("HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:${target.localPort}/v1/health\r\nContent-Length: 0\r\n\r\n") { result, request ->
                assertEquals(302, result.code)
                assertNull(result.error)
                assertEquals("", result.body)
                assertTrue(request.contains("Authorization: Bearer synthetic-token"))
                assertThrows(java.net.SocketTimeoutException::class.java) { target.accept().close() }
            }
        }
    }

    @Test
    fun destinationAndRouteRestrictionsFailBeforeNetwork() {
        for (url in listOf(
            "http://192.0.2.1:8766/v1/health",
            "https://127.0.0.1:8766/v1/health",
            "http://127.0.0.1:8766/unknown",
            "http://127.0.0.1:8766/v1/health?token=test",
            "http://user@127.0.0.1:8766/v1/health",
            "http://127.0.0.1:8766/v1/health#fragment",
        )) {
            assertThrows(IllegalArgumentException::class.java) { LoopbackHttpConnection(URI(url)) }
        }
    }
}
