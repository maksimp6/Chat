package com.alicepro.mobile

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LogRedactorTest {
    @Test
    fun redactsBearerApiKeyAndEmail() {
        val value = "Authorization: Bearer abc.def.token api_key=secret-value user=max@example.com"
        val result = LogRedactor.redact(value)
        assertFalse(result.contains("abc.def.token"))
        assertFalse(result.contains("secret-value"))
        assertFalse(result.contains("max@example.com"))
        assertTrue(result.contains("<redacted>"))
        assertTrue(result.contains("<redacted-email>"))
    }

    @Test
    fun redactsJwt() {
        val jwt = "eyJhbGciOiJIUzI1NiJ9.payload-content.signature-content"
        val result = LogRedactor.redact("token=$jwt")
        assertFalse(result.contains(jwt))
        assertTrue(result.contains("<redacted-jwt>"))
    }
}
