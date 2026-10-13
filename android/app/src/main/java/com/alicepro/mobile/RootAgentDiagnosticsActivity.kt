package com.alicepro.mobile

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Bundle
import android.util.Base64
import android.util.Xml
import android.view.WindowManager
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import com.alicepro.mobile.rootagent.RootAgentClient
import com.alicepro.mobile.rootagent.RootAgentClient.Endpoint
import com.alicepro.mobile.rootagent.RootAgentService
import com.alicepro.mobile.rootagent.RootAgentTokenStore
import java.text.DateFormat
import java.util.Date
import java.util.concurrent.Executors
import org.json.JSONObject
import org.xmlpull.v1.XmlPullParser

/** Owner-operated diagnostics. No input, unlock, root grant or automatic screen requests. */
class RootAgentDiagnosticsActivity : AppCompatActivity() {
    private val worker = Executors.newSingleThreadExecutor()
    private val controls = mutableListOf<Button>()
    private val results = linkedMapOf<String, String>()
    private lateinit var status: TextView
    private lateinit var progress: ProgressBar
    private lateinit var output: TextView
    private var busy = false
    private var visible = false
    private var preview: AlertDialog? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        SystemInsets.applySafePadding(root, baseLeft = dp(16), baseTop = dp(12), baseRight = dp(16), baseBottom = dp(12))
        root.addView(TextView(this).apply { text = "RootAgent"; textSize = 26f })
        root.addView(TextView(this).apply {
            text = "Диагностика устройства · локальный API\nПроверки запускаются только по нажатию."
            textSize = 14f
            setPadding(0, dp(8), 0, dp(12))
        })
        status = TextView(this).apply {
            text = "Ещё не проверено"
            accessibilityLiveRegion = android.view.View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        root.addView(status)
        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            isIndeterminate = true
            visibility = android.view.View.GONE
        }
        root.addView(progress, LinearLayout.LayoutParams(-1, dp(4)))
        val content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(ScrollView(this).apply { addView(content) }, LinearLayout.LayoutParams(-1, 0, 1f))
        section(content, "Сервис")
        action(content, "Запустить сервис") {
            perform("Сервис") {
                val client = client()
                RootAgentService.start(applicationContext)
                val deadline = System.nanoTime() + 10_000_000_000L
                var response = client.request(Endpoint.HEALTH)
                while (response.code != 200 && System.nanoTime() < deadline) {
                    Thread.sleep(150)
                    response = client.request(Endpoint.HEALTH)
                }
                describe(Endpoint.HEALTH, response)
            }
        }
        action(content, "Остановить сервис") {
            AlertDialog.Builder(this).setTitle("Остановить RootAgent?")
                .setMessage("Текущие запросы RootAgent могут завершиться с ошибкой.")
                .setNegativeButton("Отмена", null)
                .setPositiveButton("Остановить") { _, _ ->
                    perform("Сервис") {
                        RootAgentService.stop(applicationContext)
                        UiResult("Запрос остановки отправлен. Закрытие порта ещё не проверено.")
                    }
                }.show()
        }
        section(content, "Проверки")
        action(content, "Проверить API и root") {
            perform("API и root") {
                val client = client()
                val health = describe(Endpoint.HEALTH, client.request(Endpoint.HEALTH))
                val root = describe(Endpoint.ROOT, client.request(Endpoint.ROOT))
                UiResult("API: ${health.text}\nRoot: ${root.text}")
            }
        }
        queryButton(content, "Проверить health", Endpoint.HEALTH)
        queryButton(content, "Проверить root", Endpoint.ROOT)
        queryButton(content, "Состояние блокировки", Endpoint.STATUS)
        queryButton(content, "Информация об устройстве", Endpoint.DEVICE)
        section(content, "Экран · по запросу")
        content.addView(TextView(this).apply {
            text = "При блокировке ожидается HTTP 423. XML и изображение не попадают в журнал или копируемый отчёт."
            textSize = 14f
        })
        queryButton(content, "Получить и проверить XML", Endpoint.TREE)
        queryButton(content, "Получить и показать PNG", Endpoint.CAPTURE)
        action(content, "Разблокировка: почему недоступна") {
            AlertDialog.Builder(this).setTitle("Разблокировка не реализована")
                .setMessage("В этой версии нет unlock endpoint и связи RootAgent с C-драйвером. Разрешение root само по себе не разблокирует экран.")
                .setPositiveButton("Понятно", null).show()
        }
        section(content, "Результаты")
        output = TextView(this).apply { text = "Результатов пока нет"; setTextIsSelectable(true) }
        content.addView(output)
        action(content, "Копировать отчёт без содержимого экрана") {
            val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            val version = packageManager.getPackageInfo(packageName, 0).versionName
            clipboard.setPrimaryClip(ClipData.newPlainText("RootAgent diagnostics", "Alice Pro $version\n${output.text}"))
            status.text = "Отчёт скопирован"
        }
        action(content, "Очистить результаты на экране") {
            results.clear()
            output.text = "Результатов пока нет"
            status.text = "Результаты очищены"
        }
        root.addView(Button(this).apply { text = "Назад к диагностике"; setOnClickListener { finish() } })
        setContentView(root)
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    private fun section(parent: LinearLayout, title: String) {
        parent.addView(TextView(this).apply {
            text = title
            textSize = 19f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(0, dp(20), 0, dp(6))
        })
    }

    private fun action(parent: LinearLayout, label: String, run: () -> Unit) {
        val button = Button(this).apply {
            text = label
            isAllCaps = false
            minHeight = dp(48)
            setOnClickListener { if (!busy) run() }
        }
        controls.add(button)
        parent.addView(button, LinearLayout.LayoutParams(-1, -2))
    }

    private fun client(): RootAgentClient {
        val token = RootAgentTokenStore(applicationContext).loadOrCreate()
        return RootAgentClient(RootAgentService.PORT) { token }
    }

    private fun queryButton(parent: LinearLayout, label: String, endpoint: Endpoint) {
        action(parent, label) { perform(label) { describe(endpoint, client().request(endpoint)) } }
    }

    private class UiResult(val text: String, val image: Bitmap? = null)

    private fun perform(label: String, operation: () -> UiResult) {
        busy = true
        controls.forEach { it.isEnabled = false }
        progress.visibility = android.view.View.VISIBLE
        status.text = "$label · выполняется"
        worker.execute {
            val result = try { operation() } catch (_: Exception) { UiResult("Проверка не завершена. Данные не подтверждены.") }
            runOnUiThread {
                if (isFinishing || isDestroyed) {
                    result.image?.recycle()
                    return@runOnUiThread
                }
                busy = false
                controls.forEach { it.isEnabled = true }
                progress.visibility = android.view.View.GONE
                status.text = "$label · завершено"
                results[label] = "${DateFormat.getTimeInstance().format(Date())}\n${result.text}"
                output.text = results.entries.joinToString("\n\n") { "${it.key}\n${it.value}" }
                result.image?.let { if (visible) showImage(it) else it.recycle() }
            }
        }
    }

    private fun describe(endpoint: Endpoint, response: RootAgentClient.Response): UiResult {
        response.error?.let { return UiResult(failure(it)) }
        if (response.code == 423) return UiResult("HTTP 423 · экран заблокирован или его состояние неизвестно. Содержимое не показано.")
        if (response.code != 200) return UiResult(when (response.code) {
            401 -> "HTTP 401 · авторизация отклонена"
            403 -> "HTTP 403 · запрос запрещён"
            502 -> "HTTP 502 · данные недоступны или не прошли проверку"
            504 -> "HTTP 504 · команда превысила время ожидания"
            else -> "HTTP ${response.code ?: "—"} · проверка не подтверждена"
        })
        val body = JSONObject(response.body)
        return when (endpoint) {
            Endpoint.HEALTH -> {
                require(body.getString("status") == "ok")
                UiResult("HTTP 200 · API отвечает")
            }
            Endpoint.ROOT -> UiResult("HTTP 200 · root ${if (body.getBoolean("root")) "подтверждён, uid 0" else "не подтверждён"}")
            Endpoint.STATUS -> UiResult("HTTP 200 · экран ${if (body.getBoolean("keyguard_locked")) "заблокирован / состояние не доверено" else "разблокирован"}")
            Endpoint.DEVICE -> UiResult("HTTP 200 · ${body.getString("manufacturer")} ${body.getString("model")}\nAndroid ${body.getString("release")} · SDK ${body.getString("sdk")}")
            Endpoint.TREE -> {
                val parser = Xml.newPullParser()
                parser.setFeature(XmlPullParser.FEATURE_PROCESS_DOCDECL, false)
                parser.setInput(body.getString("xml").reader())
                var root: String? = null
                var nodes = 0
                while (parser.nextToken() != XmlPullParser.END_DOCUMENT) {
                    require(parser.eventType != XmlPullParser.DOCDECL)
                    if (parser.eventType == XmlPullParser.START_TAG) {
                        if (root == null) root = parser.name
                        if (parser.name == "node") nodes++
                    }
                }
                require(root == "hierarchy" && nodes > 0)
                UiResult("HTTP 200 · XML разобран\nУзлов интерфейса: $nodes")
            }
            Endpoint.CAPTURE -> {
                require(body.getString("format") == "png")
                val bytes = Base64.decode(body.getString("png_base64"), Base64.NO_WRAP)
                require(bytes.size <= 9 * 1024 * 1024 && bytes.size >= 8)
                require(bytes.copyOf(8).contentEquals(byteArrayOf(0x89.toByte(), 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a)))
                val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
                require(options.outWidth in 1..8192 && options.outHeight in 1..8192)
                require(options.outWidth.toLong() * options.outHeight <= 16 * 1024 * 1024)
                val width = options.outWidth
                val height = options.outHeight
                options.inJustDecodeBounds = false
                while (maxOf(width, height) / options.inSampleSize > 1024) options.inSampleSize *= 2
                val bitmap = requireNotNull(BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options))
                UiResult("HTTP 200 · PNG декодирован\nРазмер: $width × $height · ${bytes.size} байт\nПредпросмотр уменьшен; это снимок на момент запроса.", bitmap)
            }
        }
    }

    private fun failure(error: String) = when (error) {
        "timeout", "root_probe_timeout" -> "Время ожидания истекло"
        "response_too_large" -> "Ответ превышает допустимый размер"
        "unreachable" -> "API недоступен. Запустите сервис и повторите проверку."
        else -> "Проверка не подтверждена"
    }

    private fun showImage(bitmap: Bitmap) {
        preview?.dismiss()
        val image = ImageView(this).apply { setImageBitmap(bitmap); adjustViewBounds = true; contentDescription = "Скриншот по запросу" }
        val dialog = AlertDialog.Builder(this).setTitle("Снимок экрана")
            .setView(image).setPositiveButton("Закрыть", null).create()
        dialog.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        dialog.setOnDismissListener { image.setImageDrawable(null); bitmap.recycle(); preview = null }
        preview = dialog
        dialog.show()
        dialog.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
    }

    override fun onStop() {
        visible = false
        preview?.dismiss()
        super.onStop()
    }

    override fun onStart() {
        super.onStart()
        visible = true
    }

    override fun onDestroy() {
        worker.shutdownNow()
        preview?.dismiss()
        super.onDestroy()
    }
}
