import org.gradle.api.tasks.Sync

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "com.alicepro.mobile"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.alicepro.mobile"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }
}

val stagedPythonSources = layout.buildDirectory.dir("generated/python")

val stagePythonSources = tasks.register<Sync>("stagePythonSources") {
    from(rootProject.projectDir) {
        include("**/*.py")
        include("templates/**")
        include("static/**")
        exclude("android/**")
        exclude("frontend/node_modules/**")
        exclude("node_modules/**")
        exclude(".git/**")
        exclude("**/__pycache__/**")
        exclude("**/*.pyc")
    }
    into(stagedPythonSources)
}

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            install("-r", "../../requirements.txt")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir(stagedPythonSources)
        }
    }
}

// Chaquopy creates its merge task after plugin configuration. Wire the staged
// backend into that task lazily so the repository never becomes its own source.
tasks.matching { it.name == "mergeDebugPythonSources" }.configureEach {
    dependsOn(stagePythonSources)
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.12.1")
}
