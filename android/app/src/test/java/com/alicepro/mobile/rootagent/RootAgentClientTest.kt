package com.alicepro.mobile.rootagent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RootAgentClientTest {
    private val token = "client-test-" + "c".repeat(32)

    @Test
    fun individualScreenRequestReadsCompleteBodyBeyondOldProbeLimit() {
        val xml = "<?xml version='1.0'?><hierarchy><node text='" + "a".repeat(9000) + "'/></hierarchy>"
        val (server, port) = serverWith(
            RootShellResult(0, "0", false, false),
            tree = RootShellResult(0, xml, false, false),
        )
        try {
            val response = RootAgentClient(port) { token }.request(RootAgentClient.Endpoint.TREE)
            assertEquals(200, response.code)
            assertTrue(response.body.contains(xml))
            assertFalse(response.toString().contains("hierarchy"))
            assertFalse(response.toString().contains(token))
        } finally {
            server.stop()
        }
    }

    @Test
    fun oversizedMetadataResponseIsRejectedInsteadOfSilentlyTruncated() {
        val ops = RootOperations(runRoot = { RootShellResult(0, "0", false, false) }, deviceInfo = { mapOf("model" to "a".repeat(5000)) })
        val server = RootAgentServer("127.0.0.1", 0, { token }, ops)
        val port = server.start()
        try {
            val response = RootAgentClient(port) { token }.request(RootAgentClient.Endpoint.DEVICE)
            assertEquals("response_too_large", response.error)
            assertEquals("", response.body)
            assertEquals(null, response.code)
        } finally {
            server.stop()
        }
    }

    @Test
    fun individualRequestPreservesAuthorizationAndLockedRefusal() {
        val (server, port) = serverWith(RootShellResult(0, "0", false, false), status = RootShellResult(0, "isKeyguardShowing=true", false, false))
        try {
            val refused = RootAgentClient(port) { "wrong" }.request(RootAgentClient.Endpoint.CAPTURE)
            assertEquals(401, refused.code)
            val locked = RootAgentClient(port) { token }.request(RootAgentClient.Endpoint.CAPTURE)
            assertEquals(423, locked.code)
            assertEquals("{\"error\":\"device_locked\"}", locked.body)
        } finally {
            server.stop()
        }
    }

    private fun serverWith(
        probe: RootShellResult,
        status: RootShellResult = RootShellResult(0, "isKeyguardShowing=false\n", false, false),
        tree: RootShellResult = RootShellResult(0, "<?xml version='1.0' ?><hierarchy rotation=\"0\"></hierarchy>", false, false),
        shot: RootShellResult = RootShellResult(0, "iVBORw0KGgoB\n", false, false),
    ): Pair<RootAgentServer, Int> {
        val ops = RootOperations(
            runRoot = { argv -> if (argv == RootOperations.STATUS_ARGV) status else probe },
            deviceInfo = { mapOf("model" to "Redmi 9") },
            runTree = { tree },
            runScreenshot = { shot },
        )
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

    @Test
    fun screenCheckReportsOnlyStatusCodesWhenUnlocked() {
        val (server, port) = serverWith(RootShellResult(0, "0", false, false))
        try {
            val report = RootAgentClient(port) { token }.screenCheck()
            assertEquals(RootAgentClient.ScreenReport(keyguardLocked = false, tree = 200, capture = 200, error = null), report)
            assertFalse(report.toString().contains("hierarchy"))
            assertFalse(report.toString().contains("iVBOR"))
        } finally {
            server.stop()
        }
    }

    @Test
    fun screenCheckWhileLockedReportsTheRefusal() {
        val (server, port) = serverWith(
            RootShellResult(0, "0", false, false),
            status = RootShellResult(0, "isKeyguardShowing=true\n", false, false),
        )
        try {
            val report = RootAgentClient(port) { token }.screenCheck()
            assertEquals(RootAgentClient.ScreenReport(keyguardLocked = true, tree = 423, capture = 423, error = null), report)
        } finally {
            server.stop()
        }
    }

    @Test
    fun screenCheckReportsFailedReadsAndStatusTimeout() {
        val (server, port) = serverWith(
            RootShellResult(0, "0", false, false),
            status = RootShellResult(null, "", timedOut = true, truncated = false),
        )
        try {
            val report = RootAgentClient(port) { token }.screenCheck()
            assertEquals(null, report.keyguardLocked)
            assertEquals(423, report.tree)
            assertEquals("status_http_504", report.error)
        } finally {
            server.stop()
        }
    }

    @Test
    fun screenCheckWithoutServerIsAnErrorNotACrash() {
        val (server, port) = serverWith(RootShellResult(0, "0", false, false))
        server.stop()
        val report = RootAgentClient(port) { token }.screenCheck()
        assertEquals(RootAgentClient.ScreenReport(null, null, null, "unreachable"), report)
    }
}
