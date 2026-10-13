package com.alicepro.mobile.rootagent

import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.ServerSocket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import org.junit.Assert.*
import org.junit.Test

class RootAgentCancellationTest {
    @Test
    fun realSocketCloseInterruptsBlockedBodyAndPeerObservesDisconnect() {
        val server = ServerSocket(0)
        val sent = CountDownLatch(1)
        val peerClosed = CountDownLatch(1)
        val peer = Thread {
            server.accept().use { socket ->
                socket.soTimeout = 3000
                val input = socket.getInputStream().bufferedReader()
                while (!input.readLine().isNullOrEmpty()) {}
                socket.getOutputStream().write("HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{".toByteArray())
                socket.getOutputStream().flush()
                sent.countDown()
                if (input.read() == -1) peerClosed.countDown()
            }
        }.apply { isDaemon = true; start() }
        val client = RootAgentClient(server.localPort) { "synthetic-token" }
        val result = AtomicReference<RootAgentClient.Response>()
        val thread = Thread { result.set(client.request(RootAgentClient.Endpoint.HEALTH)) }
        try {
            thread.start()
            assertTrue(sent.await(2, TimeUnit.SECONDS))
            client.close()
            assertTrue(peerClosed.await(1, TimeUnit.SECONDS))
            thread.join(1000)
            assertFalse(thread.isAlive)
            assertEquals("cancelled", result.get().error)
            assertEquals("", result.get().body)
        } finally {
            client.close()
            server.close()
            peer.join(1000)
        }
    }

    private class DelayedConnection(private val drip: Boolean = false) : HttpURLConnection(URL("http://127.0.0.1/")) {
        val reading = CountDownLatch(1)
        val disconnected = CountDownLatch(1)
        override fun connect() = Unit
        override fun usingProxy() = false
        override fun disconnect() { disconnected.countDown() }
        override fun getResponseCode() = 200
        override fun getInputStream() = object : InputStream() {
            override fun read(): Int {
                reading.countDown()
                if (drip) {
                    if (disconnected.await(20, TimeUnit.MILLISECONDS)) return -1
                    return ' '.code
                }
                disconnected.await(3, TimeUnit.SECONDS)
                return -1
            }
        }
    }

    @Test
    fun closeDisconnectsBlockedRequestWithoutReturningSuccessOrContent() {
        val connection = DelayedConnection()
        val client = RootAgentClient(8766, connectionFactory = { connection }) { "synthetic-token" }
        val result = AtomicReference<RootAgentClient.Response>()
        val thread = Thread { result.set(client.request(RootAgentClient.Endpoint.CAPTURE)) }
        thread.start()
        assertTrue(connection.reading.await(2, TimeUnit.SECONDS))
        client.close()
        assertTrue(connection.disconnected.await(1, TimeUnit.SECONDS))
        thread.join(2000)
        assertFalse(thread.isAlive)
        assertEquals("cancelled", result.get().error)
        assertEquals("", result.get().body)
        assertNull(result.get().code)
        assertEquals("cancelled", client.request(RootAgentClient.Endpoint.HEALTH).error)
    }

    @Test
    fun totalDeadlineStopsDrippingResponseDespiteContinuousProgress() {
        val connection = DelayedConnection(drip = true)
        val client = RootAgentClient(8766, connectionFactory = { connection }, totalTimeoutMs = 150) { "synthetic-token" }
        val started = System.nanoTime()
        val response = client.request(RootAgentClient.Endpoint.TREE)
        assertEquals("timeout", response.error)
        assertEquals("", response.body)
        assertTrue((System.nanoTime() - started) / 1_000_000 < 2000)
        assertEquals(0, connection.disconnected.count)
    }

    @Test
    fun remainingActionBudgetShortensTheRequestDeadlineAndZeroBudgetNeverConnects() {
        val connection = DelayedConnection()
        var connections = 0
        val client = RootAgentClient(8766, connectionFactory = { connections++; connection }, totalTimeoutMs = 3000) { "synthetic-token" }
        assertEquals("timeout", client.request(RootAgentClient.Endpoint.HEALTH, 0).error)
        assertEquals(0, connections)
        val started = System.nanoTime()
        assertEquals("timeout", client.request(RootAgentClient.Endpoint.HEALTH, 100).error)
        assertTrue((System.nanoTime() - started) / 1_000_000 < 1500)
        assertEquals(1, connections)
        assertEquals(0, connection.disconnected.count)
    }
}
