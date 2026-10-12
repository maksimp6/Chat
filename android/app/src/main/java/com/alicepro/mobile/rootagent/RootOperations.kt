package com.alicepro.mobile.rootagent

/**
 * The only operations the RootAgent core exposes so far (#650): read-only
 * device information, the lock-screen state, the UI tree and one harmless root probe. No shell,
 * input, package or process mutation is reachable from here.
 *
 * [runTree] runs the UI dump with a larger output bound than the small probes.
 */
class RootOperations(
    private val runRoot: (List<String>) -> RootShellResult,
    private val deviceInfo: () -> Map<String, String>,
    private val runTree: (List<String>) -> RootShellResult = runRoot,
) {
    sealed class Probe {
        data class Answered(val root: Boolean) : Probe()
        object TimedOut : Probe()
    }

    sealed class Status {
        data class Answered(val keyguardLocked: Boolean) : Status()
        object TimedOut : Status()
    }

    sealed class ScreenTree {
        data class Answered(val xml: String) : ScreenTree()
        object Unavailable : ScreenTree()
        object TimedOut : ScreenTree()
    }

    fun deviceInfo(): Map<String, String> = deviceInfo.invoke()

    /** The uiautomator hierarchy XML, or Unavailable; raw failure output is never returned. */
    fun screenTree(): ScreenTree {
        val result = runTree(TREE_ARGV)
        if (result.timedOut) return ScreenTree.TimedOut
        if (result.exitCode != 0 || result.truncated) return ScreenTree.Unavailable
        val start = result.output.indexOf("<?xml")
        val end = result.output.lastIndexOf(TREE_END)
        if (start < 0 || end < start || !result.output.contains("<hierarchy")) return ScreenTree.Unavailable
        return ScreenTree.Answered(result.output.substring(start, end + TREE_END.length))
    }

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
        val TREE_ARGV = listOf("su", "-c", "uiautomator dump /dev/tty")
        private const val TREE_END = "</hierarchy>"
        val STATUS_ARGV = listOf(
            "su", "-c",
            "dumpsys window | grep -E 'isKeyguardShowing|mShowingLockscreen|mKeyguardShowing'",
        )
    }
}
