package com.alicepro.mobile.rootagent

/**
 * The only operations the first RootAgent slice exposes (#650): read-only
 * device information, the lock-screen state and one harmless root probe. No shell, input, package or
 * process mutation is reachable from here.
 */
class RootOperations(
    private val runRoot: (List<String>) -> RootShellResult,
    private val deviceInfo: () -> Map<String, String>,
) {
    sealed class Probe {
        data class Answered(val root: Boolean) : Probe()
        object TimedOut : Probe()
    }

    sealed class Status {
        data class Answered(val keyguardLocked: Boolean) : Status()
        object TimedOut : Status()
    }

    fun deviceInfo(): Map<String, String> = deviceInfo.invoke()

    /** Lock-screen state only; foreground app and window names are not exposed. */
    fun status(): Status {
        val result = runRoot(STATUS_ARGV)
        if (result.timedOut) return Status.TimedOut
        val trusted = result.exitCode == 0 && !result.truncated
        return Status.Answered(keyguardLocked = !trusted || KeyguardState.locked(result.output))
    }

    /** `su -c "id -u"` must print exactly 0; anything else means no root. */
    fun rootProbe(): Probe {
        val result = runRoot(PROBE_ARGV)
        if (result.timedOut) return Probe.TimedOut
        return Probe.Answered(result.exitCode == 0 && !result.truncated && result.output.trim() == "0")
    }

    companion object {
        val PROBE_ARGV = listOf("su", "-c", "id -u")
        val STATUS_ARGV = listOf(
            "su", "-c",
            "dumpsys window | grep -E 'isKeyguardShowing|mShowingLockscreen|mKeyguardShowing'",
        )
    }
}
