package com.alicepro.mobile

import android.content.ClipboardManager
import android.content.Context
import android.graphics.Bitmap
import android.os.Looper
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import android.widget.ScrollView
import androidx.appcompat.app.AlertDialog
import com.alicepro.mobile.rootagent.RootAgentClient
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import org.junit.After
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows
import org.robolectric.android.controller.ActivityController
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.shadows.ShadowDialog

/** Host Android simulation only. No service, Keystore, root command or device is used. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [30])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class RootAgentDiagnosticsActivityTest {
    private val controllers = mutableListOf<ActivityController<RootAgentDiagnosticsActivity>>()

    @Test
    @Config(qualifiers = "w320dp-h640dp")
    fun narrowScreenWithDoubleFontKeepsEveryButtonLabelUnclipped() {
        RuntimeEnvironment.setFontScale(2f)
        val activity = panel { error("Layout must not make a request") }.get()
        layout(activity, 320, 640)
        val buttons = views(activity.window.decorView).filterIsInstance<Button>()
        assertEquals(13, buttons.size)
        for (button in buttons) {
            assertTrue(button.text.toString(), button.height >= (48 * activity.resources.displayMetrics.density).toInt())
            assertNotNull(button.layout)
            assertTrue(button.text.toString(), button.height - button.compoundPaddingTop - button.compoundPaddingBottom >= button.layout.height)
        }
        assertTrue(button(activity, "Копировать отчёт без содержимого экрана").layout.lineCount > 1)
    }

    @Test
    @Config(qualifiers = "w640dp-h320dp-land")
    fun shortLandscapeWithLargeFontKeepsNavigationInsideScrollableContent() {
        RuntimeEnvironment.setFontScale(2f)
        val activity = panel { error("Layout must not make a request") }.get()
        layout(activity, 640, 320)
        val scroll = views(activity.window.decorView).filterIsInstance<ScrollView>().single()
        assertTrue(scroll.height > 0)
        assertTrue(scroll.getChildAt(0).height > scroll.height)
        var parent: android.view.ViewParent? = button(activity, "Назад к диагностике").parent
        while (parent != null && parent !== scroll) parent = parent.parent
        assertSame(scroll, parent)
    }

    @Test
    fun completingRequestShowsUsefulResultAtTopWithoutManualSearch() {
        val connection = Connection("{\"status\":\"ok\"}")
        val activity = panel { RootAgentClient(8766, connectionFactory = { connection }) { "synthetic-token" } }.get()
        layout(activity, 320, 640)
        button(activity, "Проверить связь").performClick()
        await { text(activity).contains("API отвечает") }
        val summary = views(activity.window.decorView).filterIsInstance<TextView>().first {
            it.accessibilityLiveRegion == View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        assertTrue(summary.text.toString().contains("API отвечает"))
        assertTrue(summary.top < button(activity, "Запустить сервис").top)
        assertFalse(summary.text.toString().contains("synthetic-token"))
    }

    private fun layout(activity: RootAgentDiagnosticsActivity, widthDp: Int, heightDp: Int) {
        val density = activity.resources.displayMetrics.density
        val width = (widthDp * density).toInt()
        val height = (heightDp * density).toInt()
        val decor = activity.window.decorView
        decor.measure(View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(height, View.MeasureSpec.EXACTLY))
        decor.layout(0, 0, width, height)
        Shadows.shadowOf(Looper.getMainLooper()).idle()
    }

    private class Connection(private val body: String, private val blocked: Boolean = false, private val code: Int = 200) : HttpURLConnection(URL("http://127.0.0.1/")) {
        val reading = CountDownLatch(1)
        val disconnected = CountDownLatch(1)
        override fun connect() = Unit
        override fun usingProxy() = false
        override fun disconnect() { disconnected.countDown() }
        override fun getResponseCode() = code
        override fun getErrorStream(): InputStream = getInputStream()
        override fun getInputStream(): InputStream {
            if (!blocked) return body.byteInputStream()
            return object : InputStream() {
                override fun read(): Int {
                    reading.countDown()
                    disconnected.await(3, TimeUnit.SECONDS)
                    return -1
                }
            }
        }
    }

    private fun panel(factory: () -> RootAgentClient): ActivityController<RootAgentDiagnosticsActivity> {
        val controller = Robolectric.buildActivity(RootAgentDiagnosticsActivity::class.java)
        controller.get().clientFactory = factory
        controllers.add(controller)
        return controller.create().start().resume().visible()
    }

    private fun views(view: View): List<View> = listOf(view) + if (view is ViewGroup) {
        (0 until view.childCount).flatMap { views(view.getChildAt(it)) }
    } else emptyList()

    private fun button(activity: RootAgentDiagnosticsActivity, label: String): Button =
        views(activity.window.decorView).filterIsInstance<Button>().first { it.text.toString() == label }

    private fun text(activity: RootAgentDiagnosticsActivity): String =
        views(activity.window.decorView).filterIsInstance<TextView>().joinToString("\n") { it.text.toString() }

    private fun await(condition: () -> Boolean) {
        val deadline = System.nanoTime() + 5_000_000_000L
        while (!condition() && System.nanoTime() < deadline) {
            Shadows.shadowOf(Looper.getMainLooper()).idle()
            Thread.sleep(10)
        }
        assertTrue("UI result did not arrive", condition())
    }

    @After
    fun cleanup() {
        controllers.forEach { controller ->
            if (!controller.get().isDestroyed) controller.pause().stop().destroy()
        }
        Shadows.shadowOf(Looper.getMainLooper()).idle()
    }

    @Test
    fun openingPanelRunsNothingAndUnlockOnlyExplainsAbsence() {
        val requests = AtomicInteger()
        val activity = panel {
            requests.incrementAndGet()
            error("Opening panel must not create a client")
        }.get()
        button(activity, "Почему разблокировка недоступна").performClick()
        assertEquals(0, requests.get())
        val dialog = ShadowDialog.getLatestDialog() as AlertDialog
        assertTrue(dialog.isShowing)
        dialog.dismiss()
    }

    @Test
    fun validPngActuallyReachesSecurePreviewAndIsExcludedFromClipboard() {
        val bitmap = Bitmap.createBitmap(720, 1600, Bitmap.Config.ARGB_8888)
        val bytes = ByteArrayOutputStream().also { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()
        bitmap.recycle()
        val encoded = android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP)
        val connection = Connection("{\"format\":\"png\",\"png_base64\":\"$encoded\"}")
        val controller = panel { RootAgentClient(8766, connectionFactory = { connection }) { "synthetic-token" } }
        val activity = controller.get()
        button(activity, "Сделать снимок экрана").performClick()
        await { text(activity).contains("PNG декодирован") }
        val dialog = ShadowDialog.getLatestDialog() as AlertDialog
        assertTrue(dialog.isShowing)
        assertTrue(dialog.window!!.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE != 0)
        assertTrue(views(dialog.window!!.decorView).filterIsInstance<ImageView>().any { it.drawable != null })
        dialog.dismiss()
        button(activity, "Копировать отчёт без содержимого экрана").performClick()
        val clipboard = activity.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val report = clipboard.primaryClip!!.getItemAt(0).text.toString()
        assertFalse(report.contains(encoded))
        assertFalse(report.contains("synthetic-token"))
        button(activity, "Сделать снимок экрана").performClick()
        await { (ShadowDialog.getLatestDialog() as AlertDialog).isShowing }
        val reopened = ShadowDialog.getLatestDialog() as AlertDialog
        controller.pause().stop().destroy()
        assertFalse(reopened.isShowing)
    }

    @Test
    fun stopCancelsBlockedHttpAndReopenedPanelCanRunNextRequest() {
        val connection = Connection("", blocked = true)
        val first = panel { RootAgentClient(8766, connectionFactory = { connection }) { "synthetic-token" } }
        button(first.get(), "Состояние блокировки").performClick()
        assertTrue(connection.reading.await(2, TimeUnit.SECONDS))
        first.pause().stop().destroy()
        assertTrue(connection.disconnected.await(2, TimeUnit.SECONDS))
        await { !first.get().hasRunningOperation }
        val next = Connection("{\"status\":\"ok\"}")
        val second = panel { RootAgentClient(8766, connectionFactory = { next }) { "synthetic-token" } }
        // Cancellation must release global ownership before a replacement request can execute.
        button(second.get(), "Проверить связь").performClick()
        await { text(second.get()).contains("API отвечает") }
        assertFalse(text(second.get()).contains("Предыдущая проверка ещё завершается"))
    }

    @Test
    fun invalidPngDoesNotProducePreviewOrSuccessfulResult() {
        val connection = Connection("{\"format\":\"png\",\"png_base64\":\"AAAA\"}")
        val activity = panel { RootAgentClient(8766, connectionFactory = { connection }) { "synthetic-token" } }.get()
        button(activity, "Сделать снимок экрана").performClick()
        await { text(activity).contains("Данные не подтверждены") }
        assertFalse(text(activity).contains("PNG декодирован"))
    }
}
