package com.alicepro.mobile.rootagent

import android.content.Intent
import android.graphics.BitmapFactory
import android.os.Build
import android.util.Base64
import android.util.Xml
import androidx.test.platform.app.InstrumentationRegistry
import java.io.IOException
import java.net.HttpURLConnection
import java.net.Socket
import java.net.URL
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.xmlpull.v1.XmlPullParser

/** Manual physical-device tests. No emulator pass, input, install or existing-service takeover. */
class RootAgentHardwareTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private var ownsService = false
    private var token = ""

    @Before
    fun setUp() {
        assertEquals("Redmi 9", Build.MODEL)
        assertEquals("galahad", Build.DEVICE)
        val expected = InstrumentationRegistry.getArguments().getString("expectedVersionName")
        assertTrue("Pass the artifact's exact expectedVersionName", !expected.isNullOrBlank())
        val context = instrumentation.targetContext
        val installed = context.packageManager.getPackageInfo(context.packageName, 0)
        assertEquals(expected, installed.versionName)
        val expectedCode = InstrumentationRegistry.getArguments().getString("expectedVersionCode")?.toLongOrNull()
        assertNotNull("Pass the artifact's exact expectedVersionCode", expectedCode)
        assertEquals(expectedCode, installed.longVersionCode)
        val occupied = try {
            Socket().use { it.connect(java.net.InetSocketAddress("127.0.0.1", 8766), 500) }
            true
        } catch (_: IOException) {
            false
        }
        assertFalse("Refuse to take over an existing RootAgent service", occupied)
        token = RootAgentTokenStore(context).loadOrCreate()
        ownsService = true
        try {
            RootAgentService.start(context)
            val deadline = System.nanoTime() + 10_000_000_000L
            var ready = false
            while (!ready && System.nanoTime() < deadline) {
                ready = try { get("/v1/health").first == 200 } catch (_: IOException) { false }
                if (!ready) Thread.sleep(100)
            }
            assertTrue("RootAgentService did not become healthy", ready)
            val probe = get("/v1/root-probe")
            assertEquals(200, probe.first)
            assertTrue("Root not granted to the app process", JSONObject(probe.second).getBoolean("root"))
        } catch (failure: Throwable) {
            stopOwnedService()
            throw failure
        }
    }

    @After
    fun tearDown() {
        stopOwnedService()
    }

    private fun stopOwnedService() {
        try {
            if (ownsService) {
                instrumentation.targetContext.stopService(Intent(instrumentation.targetContext, RootAgentService::class.java))
            }
        } finally {
            ownsService = false
            token = ""
        }
    }

    private fun get(path: String, auth: String? = token): Pair<Int, String> {
        val connection = URL("http://127.0.0.1:8766$path").openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 2_000
            // Keyguard probe (5 s) + capture (15 s), allowing a future second guard probe.
            connection.readTimeout = 28_000
            connection.instanceFollowRedirects = false
            auth?.let { connection.setRequestProperty("Authorization", "Bearer $it") }
            val code = connection.responseCode
            val stream = if (code < 400) connection.inputStream else connection.errorStream
            val bytes = stream.use { input ->
                val output = java.io.ByteArrayOutputStream()
                val chunk = ByteArray(4096)
                while (input != null) {
                    val count = input.read(chunk)
                    if (count < 0) break
                    assertTrue("Response exceeds bound", output.size() + count <= 18 * 1024 * 1024)
                    output.write(chunk, 0, count)
                }
                output.toByteArray()
            }
            return code to String(bytes, Charsets.UTF_8)
        } finally {
            connection.disconnect()
        }
    }

    private fun locked(): Boolean {
        val response = get("/v1/status")
        assertEquals(200, response.first)
        return JSONObject(response.second).getBoolean("keyguard_locked")
    }

    @Test
    fun testAuthorizationAndReadOnlyMetadata() {
        for (path in listOf("/v1/health", "/v1/root-probe", "/v1/screen/tree", "/v1/screen/capture")) {
            for (auth in listOf(null, "invalid")) {
                val response = get(path, auth)
                assertEquals(401, response.first)
                assertEquals("{\"error\":\"unauthorized\"}", response.second)
            }
        }
        val response = get("/v1/device-info")
        assertEquals(200, response.first)
        assertEquals("Redmi 9", JSONObject(response.second).getString("model"))
        assertEquals("galahad", JSONObject(response.second).getString("device"))
    }

    @Test
    fun testRealCommandFailureOutputBoundAndTimeout() {
        val shell = RootShell(timeoutMs = 5_000, maxOutputBytes = 16)
        val missing = shell.run(listOf("su", "-c", "exec /system/bin/alice-no-such-tool-650"))
        assertFalse(missing.timedOut)
        assertTrue("Missing utility must fail", missing.exitCode != null && missing.exitCode != 0)
        val bounded = shell.run(listOf("su", "-c", "printf %032d 0"))
        assertEquals(0, bounded.exitCode)
        assertTrue(bounded.truncated)
        assertEquals(16, bounded.output.length)
        val started = System.nanoTime()
        val timeout = RootShell(timeoutMs = 500, maxOutputBytes = 16).run(listOf("/system/bin/sleep", "3"))
        assertTrue(timeout.timedOut)
        assertTrue("Unbounded process timeout", (System.nanoTime() - started) / 1_000_000 < 2_500)
        val probe = get("/v1/root-probe")
        assertEquals(200, probe.first)
        assertTrue("Service remains usable after command faults", JSONObject(probe.second).getBoolean("root"))
    }

    @Test
    fun testLockedScreenReturns423WithoutContent() {
        assertTrue("Requires an already locked phone; this test never locks it", locked())
        for (path in listOf("/v1/screen/tree", "/v1/screen/capture")) {
            val response = get(path)
            assertEquals(423, response.first)
            assertEquals("{\"error\":\"device_locked\"}", response.second)
        }
        assertTrue("Lock state changed during the test", locked())
    }

    @Test
    fun testUnlockedXmlAndDecodedPng() {
        assertFalse("Requires owner-unlocked phone; this test never unlocks it", locked())
        val tree = get("/v1/screen/tree")
        assertEquals(200, tree.first)
        val parser = Xml.newPullParser()
        parser.setInput(JSONObject(tree.second).getString("xml").reader())
        var root: String? = null
        var nodes = 0
        while (parser.next() != XmlPullParser.END_DOCUMENT) {
            if (parser.eventType == XmlPullParser.START_TAG) {
                if (root == null) root = parser.name
                if (parser.name == "node") nodes++
            }
        }
        assertEquals("hierarchy", root)
        assertTrue("No actual UI nodes", nodes > 0)
        val shot = get("/v1/screen/capture")
        assertEquals(200, shot.first)
        val body = JSONObject(shot.second)
        assertEquals("png", body.getString("format"))
        val bytes = Base64.decode(body.getString("png_base64"), Base64.NO_WRAP)
        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        assertNotNull("PNG must decode, signature alone is insufficient", bitmap)
        try {
            assertTrue(bitmap!!.width > 0 && bitmap.height > 0)
        } finally {
            bitmap?.recycle()
        }
        assertFalse("Lock state changed during the test", locked())
    }
}
