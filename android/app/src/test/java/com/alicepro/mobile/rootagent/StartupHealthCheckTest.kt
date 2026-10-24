package com.alicepro.mobile.rootagent

import org.junit.Assert.*
import org.junit.Test

class StartupHealthCheckTest {
    @Test
    fun eightSecondFailureLeavesOnlyRemainingBudgetForRetry() {
        var time = 0L
        val budgets = mutableListOf<Long>()
        val response = StartupHealthCheck.await(
            probe = { budget ->
                budgets.add(budget)
                time += minOf(8000L, budget)
                RootAgentClient.Response(null, "", "timeout")
            },
            nowMs = { time }, pause = { time += it },
        )
        assertEquals(listOf(10_000L, 1850L), budgets)
        assertEquals(10_000L, time)
        assertEquals("timeout", response.error)
        assertEquals("", response.body)
    }

    @Test
    fun finalPauseCannotStartAnExtraRequestAfterDeadline() {
        var time = 0L
        var attempts = 0
        val response = StartupHealthCheck.await(
            probe = { attempts++; time += 9950; RootAgentClient.Response(null, "", "unreachable") },
            nowMs = { time }, pause = { time += it },
        )
        assertEquals(1, attempts)
        assertEquals(10_000L, time)
        assertEquals("timeout", response.error)
    }

    @Test
    fun cancellationAndHealthyResponseDoNotRetry() {
        for (expected in listOf(RootAgentClient.Response(null, "", "cancelled"), RootAgentClient.Response(200, "{\"status\":\"ok\"}", null))) {
            var attempts = 0
            val result = StartupHealthCheck.await(
                probe = { attempts++; expected }, nowMs = { 0L },
                pause = { error("Unexpected retry pause") },
            )
            assertSame(expected, result)
            assertEquals(1, attempts)
        }
    }
}
