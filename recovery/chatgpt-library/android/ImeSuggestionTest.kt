package example.ime

import android.os.SystemClock
import android.view.accessibility.AccessibilityEvent
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiDevice
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Black-box smoke/regression. Run on the affected real device.
 * Arguments: TARGET_PACKAGE, UNDERLINED_WORD, WORD_X, WORD_Y.
 * WORD_X/Y are screen pixels at the underlined glyph, not the text-field centre.
 * Uses system input_method dump for visibility (OEM dependent); evidence is saved
 * in instrumentation output. A transient IME hide is considered a failure.
 */
@RunWith(AndroidJUnit4::class)
class ImeSuggestionTest {
    private val inst = InstrumentationRegistry.getInstrumentation()
    private val device = UiDevice.getInstance(inst)
    private val args = InstrumentationRegistry.getArguments()
    private fun arg(name: String) = args.getString(name) ?: error("Missing $name")

    private fun imeState(): String {
        val p = Runtime.getRuntime().exec(arrayOf("sh", "-c", "dumpsys input_method"))
        val output = p.inputStream.bufferedReader().readText()
        p.waitFor()
        return output.lineSequence().filter {
            it.contains("mInputShown") || it.contains("mShowRequested") ||
            it.contains("mImeWindowVis") || it.contains("mCurFocusedWindow")
        }.joinToString(" | ")
    }
    private fun shown(state: String): Boolean? {
        val match = Regex("mInputShown=(true|false)").find(state)
        return match?.groupValues?.get(1)?.toBoolean()
    }

    @Test fun underlinedWordDoesNotTriggerImeHide() {
        val pkg = arg("TARGET_PACKAGE")
        assertTrue("Wrong foreground app: ${device.currentPackageName}", device.currentPackageName == pkg)
        val x = arg("WORD_X").toInt()
        val y = arg("WORD_Y").toInt()
        require(x >= 0 && y >= 0)
        val evidence = mutableListOf<String>()
        val before = imeState()
        evidence += "before=$before"
        // Do not call waitForIdle: it can conceal short IME hide/show transitions.
        device.click(x, y)
        val states = mutableListOf<Boolean?>()
        repeat(30) { i ->
            val state = imeState()
            states += shown(state)
            evidence += "${SystemClock.elapsedRealtime()} sample=$i $state"
            SystemClock.sleep(75)
        }
        println("IME_TRACE_START\n${evidence.joinToString("\n")}\nIME_TRACE_END")
        assertTrue("IME visibility unsupported on this Android build; inspect trace", states.any { it != null })
        assertTrue("IME never observed as visible: $evidence", states.any { it == true })
        val firstVisible = states.indexOfFirst { it == true }
        assertTrue("IME hidden after becoming visible: $evidence", states.drop(firstVisible).none { it == false })
    }
}