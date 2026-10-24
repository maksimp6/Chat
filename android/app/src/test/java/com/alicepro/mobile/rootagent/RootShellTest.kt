package com.alicepro.mobile.rootagent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RootShellTest {
    private val sh = RootShell(timeoutMs = 2_000, maxOutputBytes = 64)

    @Test
    fun capturesOutputAndExitCode() {
        val result = sh.run(listOf("/bin/sh", "-c", "printf hello; exit 3"))
        assertEquals("hello", result.output)
        assertEquals(3, result.exitCode)
        assertFalse(result.timedOut)
        assertFalse(result.truncated)
    }

    @Test
    fun argumentsAreNotReinterpretedByAShell() {
        val result = sh.run(listOf("/bin/echo", "a; rm -rf /tmp/never"))
        assertEquals("a; rm -rf /tmp/never\n", result.output)
    }

    @Test
    fun outputIsBounded() {
        val result = sh.run(listOf("/bin/sh", "-c", "yes x | head -c 10000"))
        assertEquals(64, result.output.length)
        assertTrue(result.truncated)
    }

    @Test
    fun timeoutKillsTheProcess() {
        val fast = RootShell(timeoutMs = 200, maxOutputBytes = 64)
        val started = System.nanoTime()
        val result = fast.run(listOf("/bin/sleep", "5"))
        val elapsedMs = (System.nanoTime() - started) / 1_000_000
        assertTrue(result.timedOut)
        assertEquals(null, result.exitCode)
        assertTrue("took $elapsedMs ms", elapsedMs < 2_000)
    }

    @Test
    fun missingBinaryIsAFailureNotACrash() {
        val result = sh.run(listOf("/nonexistent/su", "-c", "id -u"))
        assertEquals(null, result.exitCode)
        assertFalse(result.timedOut)
        assertTrue(result.failedToStart)
    }
}
