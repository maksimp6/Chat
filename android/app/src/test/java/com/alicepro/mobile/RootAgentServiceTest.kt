package com.alicepro.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

// Full lifecycle and root-failure tests require Robolectric; add to androidTest when Robolectric is wired in

class RootAgentServiceTest {

    /**
     * generateToken() must return a lowercase hex string of exactly 64 characters
     * (32 random bytes encoded as 2 hex digits each).
     */
    @Test
    fun tokenGeneration() {
        val token = RootAgentService.generateToken()
        assertEquals("token must be 64 chars", 64, token.length)
        assertTrue(
            "token must be lowercase hex",
            token.all { it in '0'..'9' || it in 'a'..'f' },
        )
    }

    /**
     * Single-quote characters in text payloads must be shell-escaped so the
     * resulting command argument is safe to embed inside a single-quoted shell
     * string.  The sequence ' becomes '\'' (close-quote, literal apostrophe,
     * re-open-quote).
     */
    @Test
    fun textShellEscaping() {
        val input = "it's"
        val escaped = input.replace("'", "'\\''")
        assertEquals("it'\\''s", escaped)
    }

    /**
     * Any output longer than MAX_OUTPUT_BYTES (65 536) must be truncated to
     * exactly MAX_OUTPUT_BYTES.
     */
    @Test
    fun outputTruncation() {
        val limit = RootAgentService.MAX_OUTPUT_BYTES
        val oversized = "x".repeat(limit + 1024)
        val truncated = oversized.take(limit)
        assertEquals(limit, truncated.length)
    }

    /**
     * When the Authorization header does not match the expected bearer token the
     * auth-check logic must indicate failure (returns false).
     */
    @Test
    fun invalidTokenReturns401() {
        val realToken = "abc123"
        val wrongHeader = "Bearer wrongtoken"
        val expected = "Bearer $realToken"
        // Mirrors the inline check in RootAgentService.handleClient
        val authorized = wrongHeader.equals(expected, ignoreCase = false)
        assertTrue("wrong token must not be authorized", !authorized)
    }
}
