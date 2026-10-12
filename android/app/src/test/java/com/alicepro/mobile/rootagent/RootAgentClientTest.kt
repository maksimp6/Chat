package com.alicepro.mobile.rootagent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class RootAgentClientTest {
    private val token = "client-test-" + "c".repeat(32)

    private fun serverWith(probe: RootShellResult): Pair<RootAgentServer, Int> {
        val ops = RootOperations(runRoot = { probe }, deviceInfo = { mapOf("model" to "Redmi 9") })
        val server = RootAgentServer("127.0.0.1", 0, { token }, ops)
        return server to server.start()
    }

    @Test
    fun selfCheckReportsHealthAndRoot() {
        val (server, port) = serverWith(RootShellResult(0, "0\n", timedOut = false, truncated = false))
        try {
            val report = RootAgentClient(port) { token }.selfCheck()
            assertEquals(RootAgentClient.Report(healthy = true, root = true, error = null), report)
        } finally {
            server.stop()
        }
    }

    @Test
    fun selfCheckReportsNoRootAndNeverLeaksTheToken() {
        val (server, port) = serverWith(RootShellResult(1, "", timedOut = false, truncated = false))
        try {
            val report = RootAgentClient(port) { token }.selfCheck()
            assertEquals(true, report.healthy)
            assertEquals(false, report.root)
            assertFalse(report.toString().contains(token))
        } finally {
            server.stop()
        }
    }

    @Test
    fun selfCheckWithoutServerIsAnErrorNotACrash() {
        val (server, port) = serverWith(RootShellResult(0, "0", timedOut = false, truncated = false))
        server.stop()
        val report = RootAgentClient(port) { token }.selfCheck()
        assertEquals(false, report.healthy)
        assertEquals(null, report.root)
        assertEquals("unreachable", report.error)
    }
}
