package com.alicepro.mobile

import android.app.Activity
import android.content.Intent
import android.content.pm.ResolveInfo
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.format.DateFormat
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.GridView
import android.widget.ImageView
import android.widget.TextView
import java.util.Date

/** Minimal home screen: clock, Alice Pro shortcut and a grid of launchable apps. */
class LauncherActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var clock: TextView

    private val tick = object : Runnable {
        override fun run() {
            clock.text = DateFormat.format("HH:mm  EEE, d MMM", Date())
            handler.postDelayed(this, 30_000L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_launcher)
        clock = findViewById(R.id.clock)

        val apps = loadApps()
        val grid = findViewById<GridView>(R.id.apps)
        grid.adapter = AppAdapter(apps)
        grid.setOnItemClickListener { _, _, position, _ ->
            packageManager.getLaunchIntentForPackage(apps[position].activityInfo.packageName)
                ?.let { startActivity(it) }
        }

        findViewById<TextView>(R.id.alice).setOnClickListener {
            startActivity(Intent(this, MainActivity::class.java))
        }
    }

    override fun onResume() {
        super.onResume()
        handler.post(tick)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(tick)
    }

    private fun loadApps(): List<ResolveInfo> {
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        return packageManager.queryIntentActivities(intent, 0)
            .filter { it.activityInfo.packageName != packageName }
            .sortedBy { it.loadLabel(packageManager).toString().lowercase() }
    }

    private inner class AppAdapter(private val items: List<ResolveInfo>) : BaseAdapter() {
        override fun getCount(): Int = items.size
        override fun getItem(position: Int): ResolveInfo = items[position]
        override fun getItemId(position: Int): Long = position.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val view = convertView
                ?: LayoutInflater.from(this@LauncherActivity).inflate(R.layout.item_app, parent, false)
            val info = items[position]
            view.findViewById<ImageView>(R.id.icon).setImageDrawable(info.loadIcon(packageManager))
            view.findViewById<TextView>(R.id.label).text = info.loadLabel(packageManager)
            return view
        }
    }
}
