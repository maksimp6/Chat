package com.alicepro.mobile

import org.junit.Assert.assertEquals
import org.junit.Test

class DiagnosticLogQueryTest {
    private val now = 1_000_000L
    private val entries = listOf(
        AppLogger.LogEntry(now - 1_000L, AppLogger.LogLevel.ERROR, "Network", "request failed"),
        AppLogger.LogEntry(now - 20 * 60 * 1000L, AppLogger.LogLevel.INFO, "UI", "screen opened"),
        AppLogger.LogEntry(now - 2 * 60 * 60 * 1000L, AppLogger.LogLevel.WARNING, "Sync", "retry scheduled"),
    )

    @Test
    fun filtersByLevel() {
        val result = DiagnosticLogQuery.filter(
            entries, AppLogger.LogLevel.ERROR, DiagnosticTimeRange.ALL, "", now
        )
        assertEquals(listOf(entries[0]), result)
    }

    @Test
    fun filtersByTimeAndText() {
        val result = DiagnosticLogQuery.filter(
            entries, null, DiagnosticTimeRange.LAST_HOUR, "screen", now
        )
        assertEquals(listOf(entries[1]), result)
    }

    @Test
    fun filtersByContext() {
        val contextual = entries + AppLogger.LogEntry(
            now - 500L,
            AppLogger.LogLevel.INFO,
            "HTTP",
            "completed",
            mapOf("request_id" to "req-123"),
        )
        val result = DiagnosticLogQuery.filter(
            contextual, null, DiagnosticTimeRange.ALL, "req-123", now
        )
        assertEquals(listOf(contextual.last()), result)
    }
}
