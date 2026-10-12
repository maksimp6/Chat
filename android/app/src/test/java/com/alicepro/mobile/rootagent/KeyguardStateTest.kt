package com.alicepro.mobile.rootagent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class KeyguardStateTest {
    @Test
    fun explicitFalseMeansUnlocked() {
        assertFalse(KeyguardState.locked("  isKeyguardShowing=false\n  mCurrentFocus=Window{1 u0 app}"))
        assertFalse(KeyguardState.locked("mShowingLockscreen=false mKeyguardShowing: false"))
    }

    @Test
    fun anyTrueMeansLocked() {
        assertTrue(KeyguardState.locked("isKeyguardShowing=false\nmShowingLockscreen=true"))
        assertTrue(KeyguardState.locked("mKeyguardShowing=TRUE"))
    }

    @Test
    fun missingOrUnexpectedFormatFailsClosed() {
        assertTrue(KeyguardState.locked(""))
        assertTrue(KeyguardState.locked("mCurrentFocus=Window{1 u0 app}"))
        assertTrue(KeyguardState.locked("isKeyguardShowing=maybe"))
        assertTrue(KeyguardState.locked("xisKeyguardShowing=false"))
    }

    @Test
    fun statusUsesRootDumpAndFailsClosedOnShellErrors() {
        var seen: List<String>? = null
        val unlocked = RootOperations(
            runRoot = {
                seen = it
                RootShellResult(0, "isKeyguardShowing=false\n", timedOut = false, truncated = false)
            },
            deviceInfo = { emptyMap() },
        )
        assertEquals(RootOperations.Status.Answered(keyguardLocked = false), unlocked.status())
        assertEquals(RootOperations.STATUS_ARGV, seen)

        val failing = RootOperations(
            runRoot = { RootShellResult(1, "isKeyguardShowing=false", timedOut = false, truncated = false) },
            deviceInfo = { emptyMap() },
        )
        assertEquals(RootOperations.Status.Answered(keyguardLocked = true), failing.status())

        val truncated = RootOperations(
            runRoot = { RootShellResult(0, "isKeyguardShowing=false", timedOut = false, truncated = true) },
            deviceInfo = { emptyMap() },
        )
        assertEquals(RootOperations.Status.Answered(keyguardLocked = true), truncated.status())

        val slow = RootOperations(
            runRoot = { RootShellResult(null, "", timedOut = true, truncated = false) },
            deviceInfo = { emptyMap() },
        )
        assertEquals(RootOperations.Status.TimedOut, slow.status())
    }
}
