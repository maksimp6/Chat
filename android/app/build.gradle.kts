plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

android {
    namespace = "com.alicepro.mobile"
    compileSdk = 37
    testOptions {
        unitTests.isIncludeAndroidResources = true

    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "com.alicepro.mobile"
        minSdk = 26
        targetSdk = 35
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        val buildNumber = providers.gradleProperty("aliceBuildNumber").orElse("1").get().toIntOrNull() ?: 1
        val commitHash = providers.gradleProperty("aliceCommitHash").orElse("unknown").get()
        val resolvedVersionName = providers.gradleProperty("aliceVersionName")
            .orElse("0.1.0-" + commitHash.take(7))
            .get()
        versionCode = buildNumber
        versionName = resolvedVersionName
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        create("release") {
            val keystorePath = System.getenv("ALICE_RELEASE_KEYSTORE_PATH")
                ?: "release-signing.keystore"
            storeFile = file(keystorePath)
            storePassword = System.getenv("ALICE_RELEASE_STORE_PASSWORD")
                ?: providers.gradleProperty("aliceReleaseStorePassword").orNull
            keyAlias = System.getenv("ALICE_RELEASE_KEY_ALIAS")
                ?: providers.gradleProperty("aliceReleaseKeyAlias").orNull
            keyPassword = System.getenv("ALICE_RELEASE_KEY_PASSWORD")
                ?: providers.gradleProperty("aliceReleaseKeyPassword").orNull
        }

        val debugKeystorePath = System.getenv("ALICE_DEBUG_KEYSTORE_PATH")
            ?: "debug-signing.keystore"
        val debugKeystore = file(debugKeystorePath)
        val debugStorePassword = System.getenv("ALICE_DEBUG_STORE_PASSWORD")
        val debugKeyAlias = System.getenv("ALICE_DEBUG_KEY_ALIAS")
        val debugKeyPassword = System.getenv("ALICE_DEBUG_KEY_PASSWORD")
        if (debugKeystore.isFile && !debugStorePassword.isNullOrBlank()
            && !debugKeyAlias.isNullOrBlank() && !debugKeyPassword.isNullOrBlank()) {
            create("customDebug") {
                storeFile = debugKeystore
                storePassword = debugStorePassword
                keyAlias = debugKeyAlias
                keyPassword = debugKeyPassword
            }
        }
    }

    buildTypes {
        getByName("debug") {
            val customDebug = signingConfigs.findByName("customDebug")
            if (customDebug != null) {
                signingConfig = customDebug
            }
        }
        getByName("release") {
            signingConfig = signingConfigs.getByName("release")
        }
    }
}

kotlin {
    jvmToolchain(17)
}

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            install("-r", "../requirements.txt")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.appcompat:appcompat:1.8.0")
    implementation("androidx.webkit:webkit:1.12.1")
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test:runner:1.6.2")
    testImplementation("org.robolectric:robolectric:4.17")
}

// AGP 9 exposes JVM arguments on Gradle Test tasks, not the Android unit-test DSL.
tasks.withType<org.gradle.api.tasks.testing.Test>().configureEach {
    testLogging.events("passed", "failed", "skipped")
    jvmArgs("--add-opens=java.base/java.lang=ALL-UNNAMED", "--add-opens=java.base/java.util=ALL-UNNAMED", "--add-opens=java.base/java.io=ALL-UNNAMED", "--add-opens=java.base/java.net=ALL-UNNAMED", "--add-opens=java.base/java.security=ALL-UNNAMED", "--add-opens=java.base/java.text=ALL-UNNAMED")
}
