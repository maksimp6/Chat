package com.alicepro.mobile

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException

/**
 * Production-path unit tests for RootAgentService companion object utilities.
 *
 * Lifecycle, root execution, and HTTP-dispatch tests require Android context
 * (Robolectric or instrumented). Add Robolectric to build.gradle.kts and the
 * robolectric source set before enabling those.
 */
class RootAgentServiceTest {

    // -------------------------------------------------------------------------
    // Token generation
    // -------------------------------------------------------------------------

    @Test
    fun tokenFormat_is64CharLowercaseHex() {
        val token = RootAgentService.generateToken()
        assertEquals("token must be 64 chars", 64, token.length)
        assertTrue(
            "token must be lowercase hex",
            token.all { it in '0'..'9' || it in 'a'..'f' },
        )
    }

    @Test
    fun tokenGeneration_producesUniqueValues() {
        assertNotEquals(
            "consecutive tokens must differ",
            RootAgentService.generateToken(),
            RootAgentService.generateToken(),
        )
    }

    // -------------------------------------------------------------------------
    // Shell escaping – production method, not a re-implementation
    // -------------------------------------------------------------------------

    @Test
    fun shellEscape_singleQuote() {
        assertEquals("it'\\''s", RootAgentService.shellEscapeText("it's"))
    }

    @Test
    fun shellEscape_noSpecialChars() {
        assertEquals("hello world", RootAgentService.shellEscapeText("hello world"))
    }

    @Test
    fun shellEscape_empty() {
        assertEquals("", RootAgentService.shellEscapeText(""))
    }

    @Test
    fun shellEscape_multipleQuotes() {
        assertEquals("a'\\''b'\\''c", RootAgentService.shellEscapeText("a'b'c"))
    }

    // -------------------------------------------------------------------------
    // Auth check – production method verifies case-sensitive exact match
    // -------------------------------------------------------------------------

    @Test
    fun checkAuth_correctToken_returnsTrue() {
        assertTrue(RootAgentService.checkAuth("Bearer abc123", "abc123"))
    }

    @Test
    fun checkAuth_wrongToken_returnsFalse() {
        assertFalse(RootAgentService.checkAuth("Bearer wrong", "abc123"))
    }

    @Test
    fun checkAuth_emptyHeader_returnsFalse() {
        assertFalse(RootAgentService.checkAuth("", "abc123"))
    }

    @Test
    fun checkAuth_lowercaseBearer_returnsFalse() {
        assertFalse(RootAgentService.checkAuth("bearer abc123", "abc123"))
    }

    @Test
    fun checkAuth_schemeOnly_returnsFalse() {
        assertFalse(RootAgentService.checkAuth("Bearer ", "abc123"))
    }

    @Test
    fun checkAuth_noScheme_returnsFalse() {
        assertFalse(RootAgentService.checkAuth("abc123", "abc123"))
    }

    // -------------------------------------------------------------------------
    // HTTP line reader
    // -------------------------------------------------------------------------

    @Test
    fun readHttpLine_crlfStripped() {
        val stream = "Hello\r\n".byteInputStream()
        assertEquals("Hello", RootAgentService.readHttpLine(stream))
    }

    @Test
    fun readHttpLine_lfOnly() {
        val stream = "Hello\n".byteInputStream()
        assertEquals("Hello", RootAgentService.readHttpLine(stream))
    }

    @Test
    fun readHttpLine_multipleLines() {
        val stream = "First\r\nSecond\r\n".byteInputStream()
        assertEquals("First", RootAgentService.readHttpLine(stream))
        assertEquals("Second", RootAgentService.readHttpLine(stream))
        assertNull(RootAgentService.readHttpLine(stream))
    }

    @Test
    fun readHttpLine_emptyLine() {
        val stream = "\r\n".byteInputStream()
        assertEquals("", RootAgentService.readHttpLine(stream))
    }

    @Test
    fun readHttpLine_emptyStream_returnsNull() {
        assertNull(RootAgentService.readHttpLine("".byteInputStream()))
    }

    @Test
    fun readHttpLine_tooLong_throwsIOException() {
        val longLine = "a".repeat(RootAgentService.MAX_HEADER_LINE_BYTES + 1) + "\n"
        try {
            RootAgentService.readHttpLine(longLine.byteInputStream())
            fail("expected IOException for oversized header line")
        } catch (e: IOException) {
            // expected
        }
    }

    @Test
    fun readHttpLine_exactlyAtLimit_doesNotThrow() {
        // A line of exactly MAX_HEADER_LINE_BYTES chars followed by \n must not throw
        val line = "a".repeat(RootAgentService.MAX_HEADER_LINE_BYTES) + "\n"
        val result = RootAgentService.readHttpLine(line.byteInputStream())
        assertEquals(RootAgentService.MAX_HEADER_LINE_BYTES, result!!.length)
    }

    // -------------------------------------------------------------------------
    // Exact-byte reader
    // -------------------------------------------------------------------------

    @Test
    fun readExactBytes_fullRead() {
        val data = byteArrayOf(1, 2, 3, 4, 5)
        assertArrayEquals(data, RootAgentService.readExactBytes(ByteArrayInputStream(data), 5))
    }

    @Test
    fun readExactBytes_shortRead_returnsAvailable() {
        val data = byteArrayOf(1, 2, 3)
        val result = RootAgentService.readExactBytes(ByteArrayInputStream(data), 5)
        assertEquals(3, result.size)
        assertArrayEquals(data, result)
    }

    @Test
    fun readExactBytes_zeroLength() {
        assertEquals(0, RootAgentService.readExactBytes("anything".byteInputStream(), 0).size)
    }

    // -------------------------------------------------------------------------
    // Bounded stream collector
    // -------------------------------------------------------------------------

    @Test
    fun collectBounded_underLimit_notTruncated() {
        val (result, truncated) = RootAgentService.collectBounded("hello".byteInputStream(), 100)
        assertEquals("hello", result)
        assertFalse(truncated)
    }

    @Test
    fun collectBounded_exactLimit_notTruncated() {
        val text = "hello"
        val (result, truncated) = RootAgentService.collectBounded(text.byteInputStream(), text.length)
        assertEquals(text, result)
        assertFalse(truncated)
    }

    @Test
    fun collectBounded_overLimit_truncatesAndDrains() {
        val text = "abcdefghij"   // 10 bytes
        val stream = text.byteInputStream()
        val (result, truncated) = RootAgentService.collectBounded(stream, 5)
        assertEquals("abcde", result)
        assertTrue(truncated)
        // Verify the stream was fully drained (no bytes buffered that would block a process)
        assertEquals(0, stream.available())
    }

    @Test
    fun collectBounded_largerThan64KiB_drainsCompletely() {
        // Regression: old code silently truncated PNG data at 64 KiB.
        // New code drains past the text limit; screenshot limit is separate and larger.
        val limit = 128 * 1024   // 128 KiB, well above old 64 KiB
        val data = ByteArray(200 * 1024) { 65 }  // 200 KiB of ASCII 'A'
        val stream = ByteArrayInputStream(data)
        val (result, truncated) = RootAgentService.collectBounded(stream, limit)
        assertEquals(limit, result.length)
        assertTrue(truncated)
        assertEquals(0, stream.available())
    }

    @Test
    fun collectBounded_emptyStream() {
        val (result, truncated) = RootAgentService.collectBounded("".byteInputStream(), 100)
        assertEquals("", result)
        assertFalse(truncated)
    }

    // -------------------------------------------------------------------------
    // Exception types
    // -------------------------------------------------------------------------

    @Test
    fun screenshotTooLargeException_isException() {
        val ex = RootAgentService.ScreenshotTooLargeException()
        assertTrue(ex is Exception)
    }

    @Test
    fun rootDeniedException_isException() {
        val ex = RootAgentService.RootDeniedException()
        assertTrue(ex is Exception)
    }

    // -------------------------------------------------------------------------
    // Limits / constants sanity
    // -------------------------------------------------------------------------

    @Test
    fun screenshotLimit_largerThanTextLimit() {
        assertTrue(
            "screenshot limit must exceed text output limit",
            RootAgentService.MAX_SCREENSHOT_BYTES > RootAgentService.MAX_TEXT_OUTPUT_BYTES,
        )
    }
}
