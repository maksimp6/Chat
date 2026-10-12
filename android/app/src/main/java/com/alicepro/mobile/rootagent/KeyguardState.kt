package com.alicepro.mobile.rootagent

/**
 * Reads the lock-screen state from `dumpsys window` text. Only explicit
 * `isKeyguardShowing` / `mShowingLockscreen` / `mKeyguardShowing` booleans count;
 * a missing or unexpected field is treated as locked (fail closed).
 */
object KeyguardState {
    private val FIELD = Regex(
        "(?<![A-Za-z])(?:isKeyguardShowing|mShowingLockscreen|mKeyguardShowing)\\s*[=:]\\s*(\\w+)",
    )

    fun locked(dump: String): Boolean {
        val values = FIELD.findAll(dump).map { it.groupValues[1].lowercase() }.toList()
        if (values.isEmpty() || values.any { it != "true" && it != "false" }) return true
        return values.any { it == "true" }
    }
}
