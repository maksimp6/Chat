plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "com.alicepro.mobile"
    compileSdk = 35

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "com.alicepro.mobile"
        minSdk = 26
        targetSdk = 35
        val buildNumber = providers.gradleProperty("aliceBuildNumber").orElse("1").get().toIntOrNull() ?: 1
        val commitHash = providers.gradleProperty("aliceCommitHash").orElse("unknown").get()
        versionCode = buildNumber
        versionName = "0.1.0-${commitHash.take(7)}"
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
            install("-r", "../../requirements.txt")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.19.0")
    implementation("androidx.appcompat:appcompat:1.8.0")
    implementation("androidx.webkit:webkit:1.12.1")
    testImplementation("junit:junit:4.13.2")
}
