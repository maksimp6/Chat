package com.alicepro.mobile.rootagent

import java.util.Base64
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ScreenshotTest {
    private val png = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 1, 2, 3, 4, 5)
    private val encoded = Base64.getEncoder().encodeToString(png)

    private fun ops(result: RootShellResult, seen: MutableList<List<String>> = mutableListOf()) = RootOperations(
        runRoot = { error("small runner must not be used for screenshots") },
        deviceInfo = { emptyMap() },
        runTree = { error("tree runner must not be used for screenshots") },
        runScreenshot = { argv -> seen.add(argv); result },
    )

    @Test
    fun wrappedBase64PngIsNormalised() {
        val seen = mutableListOf<List<String>>()
        val wrapped = encoded.chunked(8).joinToString("\n") + "\n"
        val shot = ops(RootShellResult(0, wrapped, false, false), seen).screenshot()
        assertEquals(RootOperations.Screenshot.Answered(encoded), shot)
        assertEquals(listOf(RootOperations.SCREENSHOT_ARGV), seen)
    }

    @Test
    fun screenshotUsesAFixedArgv() {
        assertEquals(listOf("su", "-c", "screencap -p | base64"), RootOperations.SCREENSHOT_ARGV)
    }

    @Test
    fun timeoutIsReported() {
        assertEquals(RootOperations.Screenshot.TimedOut, ops(RootShellResult(null, "", true, false)).screenshot())
    }

    @Test
    fun failedTruncatedOrNonPngOutputIsUnavailable() {
        val notPng = Base64.getEncoder().encodeToString("GIF89a-not-a-png".toByteArray())
        val cases = listOf(
            RootShellResult(1, encoded, false, false),
            RootShellResult(0, encoded, false, truncated = true),
            RootShellResult(0, "", false, false),
            RootShellResult(0, "permission denied", false, false),
            RootShellResult(0, notPng, false, false),
            RootShellResult(0, "$encoded!!", false, false),
            RootShellResult(null, "", false, false, failedToStart = true),
        )
        for (case in cases) {
            assertEquals(case.toString(), RootOperations.Screenshot.Unavailable, ops(case).screenshot())
        }
    }

    @Test
    fun unavailableNeverCarriesRawOutput() {
        assertTrue(ops(RootShellResult(1, "secret", false, false)).screenshot() === RootOperations.Screenshot.Unavailable)
    }
}
