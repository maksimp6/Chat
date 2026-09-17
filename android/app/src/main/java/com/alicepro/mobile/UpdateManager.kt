package com.alicepro.mobile

import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.FileProvider
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URLEncoder
import java.net.URL
import java.security.MessageDigest
import java.text.DateFormat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.zip.ZipInputStream

class UpdateManager(private val activity: Activity) {
    companion object {
        private const val OWNER = "maksimp6"
        private const val REPO = "Chat"
        private const val WORKFLOW = "ci.yml"
        private const val ARTIFACT_NAME = "alice-pro-debug-apk"
        private const val NIGHTLY_BASE = "https://nightly.link"
        private const val API_BASE = "https://api.github.com/repos/$OWNER/$REPO"
        private const val PREFS = "alice_pro_updates"
        private const val KEY_LAST_AUTO_CHECK = "last_auto_check"
        private const val AUTO_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000L
    }

    data class BuildInfo(
        val runId: Long,
        val runNumber: Int,
        val branch: String,
        val sha: String,
        val createdAt: String,
        val artifactId: Long,
        val artifactSize: Long,
        val artifactDigest: String,
        val downloadUrl: String,
    ) {
        fun label(): String {
            val date = try {
                SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssX", Locale.US).parse(createdAt)?.let {
                    DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(it)
                } ?: createdAt
            } catch (_: Exception) {
                createdAt
            }
            return "CI #$runNumber · $branch\n${sha.take(8)} · $date · ${formatBytes(artifactSize)}"
        }
    }

    fun autoCheck() {
        val prefs = activity.getSharedPreferences(PREFS, Activity.MODE_PRIVATE)
        val now = System.currentTimeMillis()
        val last = prefs.getLong(KEY_LAST_AUTO_CHECK, 0L)
        if (now - last < AUTO_CHECK_INTERVAL_MS) return
        prefs.edit().putLong(KEY_LAST_AUTO_CHECK, now).apply()

        Thread {
            try {
                val latest = findLatestSuccessfulBuild("master") ?: return@Thread
                val installed = installedVersionCode()
                if (latest.runNumber <= installed) return@Thread

                activity.runOnUiThread {
                    AlertDialog.Builder(activity)
                        .setTitle("Доступно обновление")
                        .setMessage(
                            "Найдена новая CI-сборка master.\n\n" +
                                "CI #${latest.runNumber}\n" +
                                "commit ${latest.sha.take(12)}\n" +
                                "размер ${formatBytes(latest.artifactSize)}"
                        )
                        .setNegativeButton("Позже", null)
                        .setPositiveButton("Обновить") { _, _ -> downloadAndInstall(latest) }
                        .show()
                }
            } catch (_: Exception) {
                // Silent auto-check: explicit updater UI remains available.
            }
        }.start()
    }

    fun openPicker() {
        showBranchPicker()
    }

    private fun showBranchPicker() {
        val content = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 24, 32, 16)
        }
        val status = TextView(activity).apply { text = "Загрузка веток…" }
        content.addView(status)
        val progress = ProgressBar(activity).apply { isIndeterminate = true }
        content.addView(progress)

        val dialog = AlertDialog.Builder(activity)
            .setTitle("Выбор APK из CI")
            .setView(content)
            .setNegativeButton("Закрыть", null)
            .create()
        dialog.show()

        Thread {
            try {
                val branches = fetchBranches()
                activity.runOnUiThread {
                    content.removeAllViews()
                    val hint = TextView(activity).apply {
                        text = "Выберите ветку. Показываются последние успешные CI-сборки с APK."
                        setPadding(0, 0, 0, 16)
                    }
                    content.addView(hint)
                    val scroll = ScrollView(activity)
                    val list = LinearLayout(activity).apply {
                        orientation = LinearLayout.VERTICAL
                    }
                    branches.forEach { branch ->
                        val button = Button(activity).apply {
                            text = if (branch == "master") "master (основная)" else branch
                            setOnClickListener { dialog.dismiss(); showBuildPicker(branch) }
                        }
                        list.addView(button)
                    }
                    scroll.addView(list)
                    content.addView(scroll, ViewGroup.LayoutParams(-1, 900))
                }
            } catch (error: Exception) {
                activity.runOnUiThread {
                    status.text = "Не удалось получить ветки: ${error.message ?: "ошибка сети"}"
                    progress.visibility = ProgressBar.GONE
                }
            }
        }.start()
    }

    private fun showBuildPicker(branch: String) {
        val content = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 24, 32, 16)
        }
        val status = TextView(activity).apply { text = "Поиск CI-сборок…" }
        content.addView(status)
        val progress = ProgressBar(activity).apply { isIndeterminate = true }
        content.addView(progress)

        val dialog = AlertDialog.Builder(activity)
            .setTitle("APK: $branch")
            .setView(content)
            .setNegativeButton("Назад") { _, _ -> showBranchPicker() }
            .create()
        dialog.show()

        Thread {
            try {
                val builds = fetchSuccessfulBuilds(branch)
                activity.runOnUiThread {
                    content.removeAllViews()
                    val hint = TextView(activity).apply {
                        text = if (builds.isEmpty()) {
                            "У ветки нет успешных CI-сборок с APK."
                        } else {
                            "Выберите нужную сборку. Первая обычно самая свежая."
                        }
                        setPadding(0, 0, 0, 16)
                    }
                    content.addView(hint)
                    val scroll = ScrollView(activity)
                    val list = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
                    builds.forEachIndexed { index, build ->
                        val button = Button(activity).apply {
                            text = if (index == 0) "★ Последняя\n${build.label()}" else build.label()
                            setOnClickListener { dialog.dismiss(); confirmInstall(build) }
                        }
                        list.addView(button)
                    }
                    scroll.addView(list)
                    content.addView(scroll, ViewGroup.LayoutParams(-1, 1000))
                }
            } catch (error: Exception) {
                activity.runOnUiThread {
                    status.text = "Не удалось получить CI: ${error.message ?: "ошибка сети"}"
                    progress.visibility = ProgressBar.GONE
                }
            }
        }.start()
    }

    private fun confirmInstall(build: BuildInfo) {
        AlertDialog.Builder(activity)
            .setTitle("Установить APK?")
            .setMessage(
                "Ветка: ${build.branch}\n" +
                    "CI #${build.runNumber}\n" +
                    "commit: ${build.sha}\n" +
                    "размер: ${formatBytes(build.artifactSize)}\n\n" +
                    "Источник: nightly.link → GitHub Actions"
            )
            .setNegativeButton("Отмена", null)
            .setPositiveButton("Скачать и установить") { _, _ -> downloadAndInstall(build) }
            .show()
    }

    private fun downloadAndInstall(build: BuildInfo) {
        val progress = ProgressBar(activity).apply { isIndeterminate = true }
        val dialog = AlertDialog.Builder(activity)
            .setTitle("Загрузка APK")
            .setMessage("Подготовка CI #${build.runNumber}…")
            .setView(progress)
            .setNegativeButton("Отмена") { d, _ -> d.dismiss() }
            .create()
        dialog.show()

        Thread {
            try {
                val zipFile = downloadArtifact(build)
                val apkFile = extractApk(zipFile, build.runNumber)
                verifyApk(apkFile, build)
                activity.runOnUiThread {
                    dialog.dismiss()
                    installApk(apkFile)
                }
            } catch (error: Exception) {
                activity.runOnUiThread {
                    dialog.dismiss()
                    AlertDialog.Builder(activity)
                        .setTitle("Обновление не выполнено")
                        .setMessage(error.message ?: "Неизвестная ошибка")
                        .setPositiveButton("OK", null)
                        .show()
                }
            }
        }.start()
    }

    private fun downloadArtifact(build: BuildInfo): File {
        val updateDir = File(activity.cacheDir, "updates").apply { mkdirs() }
        val zip = File(updateDir, "alice-pro-${build.runNumber}.zip")
        val connection = (URL(build.downloadUrl).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = 60_000
            instanceFollowRedirects = true
            requestMethod = "GET"
            setRequestProperty("User-Agent", "AlicePro/$OWNER-$REPO")
        }

        connection.connect()
        if (connection.responseCode !in 200..299) {
            throw IOException("Сервер загрузки вернул HTTP ${connection.responseCode}")
        }

        connection.inputStream.use { input ->
            FileOutputStream(zip).use { output -> input.copyTo(output) }
        }
        connection.disconnect()

        val digest = sha256(zip)
        if (build.artifactDigest.isNotBlank() && !digest.equals(build.artifactDigest, ignoreCase = true)) {
            zip.delete()
            throw SecurityException("Проверка checksum не пройдена")
        }
        return zip
    }

    private fun extractApk(zip: File, runNumber: Int): File {
        val outputDir = File(activity.cacheDir, "updates/apk").apply { mkdirs() }
        val target = File(outputDir, "alice-pro-$runNumber.apk")
        ZipInputStream(BufferedInputStream(FileInputStream(zip))).use { input ->
            var entry = input.nextEntry
            while (entry != null) {
                if (!entry.isDirectory && entry.name.lowercase(Locale.US).endsWith(".apk")) {
                    val safeName = File(entry.name).name
                    val candidate = File(outputDir, safeName)
                    BufferedOutputStream(FileOutputStream(candidate)).use { output -> input.copyTo(output) }
                    if (candidate.length() > 0) {
                        if (target.exists()) target.delete()
                        if (!candidate.renameTo(target)) {
                            candidate.copyTo(target, overwrite = true)
                            candidate.delete()
                        }
                    }
                }
                input.closeEntry()
                entry = input.nextEntry
            }
        }
        if (!target.exists()) throw IOException("В CI artifact не найден APK")
        return target
    }

    private fun verifyApk(apk: File, build: BuildInfo) {
        val flags = if (Build.VERSION.SDK_INT >= 28) {
            PackageManager.GET_SIGNING_CERTIFICATES
        } else {
            @Suppress("DEPRECATION") PackageManager.GET_SIGNATURES
        }
        val archive = activity.packageManager.getPackageArchiveInfo(apk.absolutePath, flags)
            ?: throw SecurityException("APK не распознаётся Android")
        if (archive.packageName != activity.packageName) {
            throw SecurityException("Неверный package name: ${archive.packageName}")
        }

        val installed = activity.packageManager.getPackageInfo(activity.packageName, flags)
        val installedDigest = signingDigest(installed)
        val archiveDigest = signingDigest(archive)
        if (!installedDigest.contentEquals(archiveDigest)) {
            throw SecurityException("Подпись APK не совпадает с установленной версией")
        }

        val installedVersion = installed.longVersionCodeCompat()
        val archiveVersion = archive.longVersionCodeCompat()
        if (archiveVersion <= installedVersion) {
            throw SecurityException("APK старше или той же версии (${archiveVersion} <= ${installedVersion})")
        }

        if (build.sha.length < 8) throw SecurityException("Некорректный commit SHA")
    }

    @Suppress("DEPRECATION")
    private fun signingDigest(info: PackageInfo): ByteArray {
        val signature = if (Build.VERSION.SDK_INT >= 28) {
            info.signingInfo.apkContentsSigners.firstOrNull()
        } else {
            info.signatures.firstOrNull()
        } ?: throw SecurityException("В APK отсутствует подпись")
        return MessageDigest.getInstance("SHA-256").digest(signature.toByteArray())
    }

    private fun installApk(apk: File) {
        val uri: Uri = FileProvider.getUriForFile(
            activity,
            "${activity.packageName}.fileprovider",
            apk,
        )
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        try {
            activity.startActivity(intent)
        } catch (_: ActivityNotFoundException) {
            if (Build.VERSION.SDK_INT >= 26) {
                activity.startActivity(
                    Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES).apply {
                        data = Uri.parse("package:${activity.packageName}")
                    }
                )
            } else {
                Toast.makeText(activity, "Не найден установщик APK", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun fetchBranches(): List<String> {
        val json = get("$API_BASE/branches?per_page=100")
        val array = JSONArray(json)
        val branches = mutableListOf<String>()
        for (i in 0 until array.length()) branches += array.getJSONObject(i).getString("name")
        branches.sortWith(compareByDescending<String> { it == "master" }.thenBy { it.lowercase(Locale.US) })
        return branches
    }

    private fun fetchSuccessfulBuilds(branch: String): List<BuildInfo> {
        val encodedBranch = URLEncoder.encode(branch, "UTF-8")
        val runsJson = get(
            "$API_BASE/actions/workflows/$WORKFLOW/runs?branch=$encodedBranch&status=success&per_page=10"
        )
        val runs = JSONObject(runsJson).getJSONArray("workflow_runs")
        val result = mutableListOf<BuildInfo>()
        for (i in 0 until runs.length()) {
            val run = runs.getJSONObject(i)
            val artifacts = fetchArtifacts(run.getLong("id"))
            val artifact = artifacts.firstOrNull {
                !it.optBoolean("expired", true) && it.optString("name") == ARTIFACT_NAME
            } ?: continue
            result += BuildInfo(
                runId = run.getLong("id"),
                runNumber = run.getInt("run_number"),
                branch = run.getString("head_branch"),
                sha = run.getString("head_sha"),
                createdAt = run.getString("created_at"),
                artifactId = artifact.getLong("id"),
                artifactSize = artifact.optLong("size_in_bytes", 0L),
                artifactDigest = artifact.optString("digest", "").removePrefix("sha256:"),
                downloadUrl = "$NIGHTLY_BASE/$OWNER/$REPO/actions/runs/${run.getLong("id")}/$ARTIFACT_NAME.zip",
            )
        }
        return result
    }

    private fun findLatestSuccessfulBuild(branch: String): BuildInfo? = fetchSuccessfulBuilds(branch).firstOrNull()

    private fun fetchArtifacts(runId: Long): List<JSONObject> {
        val json = get("$API_BASE/actions/runs/$runId/artifacts?per_page=30")
        val array = JSONObject(json).getJSONArray("artifacts")
        return (0 until array.length()).map { array.getJSONObject(it) }
    }

    private fun get(url: String): String {
        val connection = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 10_000
            readTimeout = 20_000
            requestMethod = "GET"
            setRequestProperty("Accept", "application/vnd.github+json")
            setRequestProperty("User-Agent", "AlicePro-Android-Updater")
            setRequestProperty("X-GitHub-Api-Version", "2022-11-28")
        }
        connection.connect()
        val code = connection.responseCode
        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
        val body = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
        connection.disconnect()
        if (code !in 200..299) throw IOException("GitHub API HTTP $code: ${body.take(180)}")
        return body
    }

    private fun installedVersionCode(): Long {
        val info = if (Build.VERSION.SDK_INT >= 33) {
            activity.packageManager.getPackageInfo(
                activity.packageName,
                PackageManager.PackageInfoFlags.of(0),
            )
        } else {
            @Suppress("DEPRECATION") activity.packageManager.getPackageInfo(activity.packageName, 0)
        }
        return info.longVersionCodeCompat()
    }

    private fun PackageInfo.longVersionCodeCompat(): Long =
        if (Build.VERSION.SDK_INT >= 28) longVersionCode else @Suppress("DEPRECATION") versionCode.toLong()

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { input ->
            val buffer = ByteArray(8192)
            while (true) {
                val read = input.read(buffer)
                if (read <= 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun formatBytes(value: Long): String {
        if (value <= 0) return "?"
        val units = arrayOf("B", "KB", "MB", "GB")
        var number = value.toDouble()
        var index = 0
        while (number >= 1024 && index < units.lastIndex) {
            number /= 1024
            index++
        }
        return String.format(Locale.US, "%.1f %s", number, units[index])
    }
}
