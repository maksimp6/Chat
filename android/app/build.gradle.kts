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
        versionCode = buildNumber
        versionName = "0.1.0.${buildNumber}"
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
    }

    buildTypes {
        getByName("debug") {
            // Debug builds keep the standard debug signing configuration.
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
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.12.1")
    testImplementation("junit:junit:4.13.2")
}
