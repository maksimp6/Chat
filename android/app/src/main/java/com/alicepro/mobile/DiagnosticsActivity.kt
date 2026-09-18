package com.alicepro.mobile

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.view.Window
import android.view.WindowManager
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat

class DiagnosticsActivity : AppCompatActivity() {
    private lateinit var list: ListView
    private lateinit var search: EditText
    private lateinit var levelSpinner: Spinner
    private lateinit var timeSpinner: Spinner
    private lateinit var summary: TextView

    private var entries: List<AppLogger.LogEntry> = emptyList()
    private var filtered: List<AppLogger.LogEntry> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppLogger.info("DiagnosticsActivity", "Diagnostics UI opened")

        WindowCompat.setDecorFitsSystemWindows(window, false)
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16, 16, 16, 16)
        }

        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
            )
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            view.setPadding(
                16 + bars.left,
                16 + bars.top,
                16 + bars.right,
                16 + maxOf(bars.bottom, ime.bottom),
            )
            insets
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
        }
        header.addView(TextView(this).apply {
            text = "Диагностика"
            textSize = 22f
            setTextIsSelectable(true)
            layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
        })
        header.addView(Button(this).apply {
            text = "Закрыть"
            setOnClickListener { finish() }
        })
        root.addView(header)

        search = EditText(this).apply {
            hint = "Поиск по сообщению, тегу или контексту"
            inputType = InputType.TYPE_CLASS_TEXT
            isSingleLine = true
        }
        root.addView(search, LinearLayout.LayoutParams(-1, -2))

        val filterRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
        }

        levelSpinner = Spinner(this)
        levelSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            listOf("Все", *AppLogger.LogLevel.entries.map { it.label }.toTypedArray()),
        )
        filterRow.addView(levelSpinner, LinearLayout.LayoutParams(0, -2, 1f))

        timeSpinner = Spinner(this)
        timeSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            DiagnosticTimeRange.entries.map { it.label },
        )
        filterRow.addView(timeSpinner, LinearLayout.LayoutParams(0, -2, 1f))
        root.addView(filterRow)

        summary = TextView(this).apply {
            setPadding(0, 8, 0, 8)
        }
        root.addView(summary)

        list = ListView(this)
        root.addView(list, LinearLayout.LayoutParams(-1, 0, 1f))

        val buttons = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
        }
        buttons.addView(Button(this).apply {
            text = "Обновить"
            setOnClickListener { refresh() }
        }, LinearLayout.LayoutParams(0, -2, 1f))
        buttons.addView(Button(this).apply {
            text = "Копировать"
            setOnClickListener { copyVisible() }
        }, LinearLayout.LayoutParams(0, -2, 1f))
        buttons.addView(Button(this).apply {
            text = "Экспорт"
            setOnClickListener { share() }
        }, LinearLayout.LayoutParams(0, -2, 1f))
        buttons.addView(Button(this).apply {
            text = "Очистить"
            setOnClickListener { confirmClear() }
        }, LinearLayout.LayoutParams(0, -2, 1f))
        root.addView(buttons)

        setContentView(root)

        search.addTextChangedListener(object : android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = applyFilter()
            override fun afterTextChanged(s: android.text.Editable?) = Unit
        })
        levelSpinner.onItemSelectedListener = spinnerListener { applyFilter() }
        timeSpinner.onItemSelectedListener = spinnerListener { applyFilter() }

        list.setOnItemClickListener { _, _, position, _ ->
            showDetails(filtered[position])
        }

        ViewCompat.requestApplyInsets(root)
        refresh()
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun refresh() {
        entries = AppLogger.readEntries()
        applyFilter()
    }

    private fun applyFilter() {
        val selectedLevel = levelSpinner.selectedItemPosition.takeIf { it > 0 }?.let {
            AppLogger.LogLevel.entries[it - 1]
        }
        val range = DiagnosticTimeRange.entries.getOrElse(timeSpinner.selectedItemPosition) {
            DiagnosticTimeRange.ALL
        }
        filtered = DiagnosticLogQuery
            .filter(entries, selectedLevel, range, search.text?.toString().orEmpty())
            .reversed()

        summary.text = "Показано ${filtered.size} из ${entries.size} событий"
        list.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_list_item_2,
            android.R.id.text1,
            filtered.map { entry ->
                "${AppLogger.formatTimestamp(entry.timestamp)}  ${entry.level.label}  ${entry.tag}\n" +
                    entry.message.replace('\n', ' ')
            },
        )
    }

    private fun showDetails(entry: AppLogger.LogEntry) {
        val contextText = if (entry.context.isEmpty()) {
            "Контекст отсутствует"
        } else {
            entry.context.entries.joinToString("\n") { "${it.key}: ${it.value}" }
        }
        val details = "${AppLogger.formatTimestamp(entry.timestamp)}\n" +
            "${entry.level.label} · ${entry.tag}\n\n" +
            "${entry.message}\n\nКонтекст:\n${contextText}"

        AlertDialog.Builder(this)
            .setTitle("Событие")
            .setMessage(details)
            .setNegativeButton("Закрыть", null)
            .setPositiveButton("Копировать") { _, _ -> copyText(details) }
            .show()
    }

    private fun copyVisible() {
        val text = filtered.joinToString("\n") { entry ->
            "${AppLogger.formatTimestamp(entry.timestamp)} ${entry.level.label} ${entry.tag}: ${entry.message}"
        }
        copyText(text.ifBlank { "Нет диагностических событий" })
    }

    private fun copyText(text: String) {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("Alice Pro diagnostics", text))
        AppLogger.debug("DiagnosticsActivity", "Diagnostic data copied", mapOf("chars" to text.length.toString()))
    }

    private fun share() {
        startActivity(Intent.createChooser(AppLogger.createShareIntent(this), "Экспорт диагностики"))
        AppLogger.info("DiagnosticsActivity", "Diagnostic export started")
    }

    private fun confirmClear() {
        AlertDialog.Builder(this)
            .setTitle("Очистить диагностику?")
            .setMessage("Будут удалены сохранённые диагностические логи этого приложения.")
            .setNegativeButton("Отмена", null)
            .setPositiveButton("Очистить") { _, _ ->
                AppLogger.clear()
                AppLogger.info("DiagnosticsActivity", "Diagnostic log cleared")
                refresh()
            }
            .show()
    }

    private fun spinnerListener(onSelected: () -> Unit) =
        object : android.widget.AdapterView.OnItemSelectedListener {
            override fun onItemSelected(
                parent: android.widget.AdapterView<*>?,
                view: View?,
                position: Int,
                id: Long,
            ) = onSelected()

            override fun onNothingSelected(parent: android.widget.AdapterView<*>?) = onSelected()
        }
}
