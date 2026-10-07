package com.alicepro.mobile

import android.content.Intent
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    private val updateManager by lazy { UpdateManager(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppLogger.initialize(this)
        AppLogger.info("MainActivity", "Native Android controller started")

        val padding = (20 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(padding, padding, padding, padding)
        }

        val title = TextView(this).apply {
            text = "Alice Pro"
            textSize = 28f
            gravity = Gravity.CENTER
        }
        root.addView(
            title,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ),
        )

        val subtitle = TextView(this).apply {
            text = "Native browser controller"
            textSize = 16f
            gravity = Gravity.CENTER
        }
        root.addView(
            subtitle,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = padding / 2 },
        )

        root.addView(actionButton("Open controlled browser") {
            startActivity(Intent(this, BrowserTakeoverActivity::class.java))
        })

        root.addView(actionButton("Diagnostics") {
            startActivity(Intent(this, DiagnosticsActivity::class.java))
        })

        root.addView(actionButton("Update APK") {
            updateManager.openPicker()
        })

        setContentView(root)
        updateManager.autoCheck()
    }

    private fun actionButton(label: String, action: () -> Unit): Button =
        Button(this).apply {
            text = label
            setOnClickListener { action() }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = (12 * resources.displayMetrics.density).toInt() }
        }
}
