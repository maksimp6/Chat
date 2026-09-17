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

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            install("-r", "../../requirements.txt")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("../..")
        }
    }
}

// Gradle 8.9 validates task inputs across the repository-root Python source set.
// Make the generated Python merge explicitly depend on Chaquopy/AGP outputs it reads.
tasks.named("mergeDebugPythonSources") {
    dependsOn("mergeDebugNativeDebugMetadata")
    dependsOn("installDebugPythonRequirements")
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.12.1")
}
