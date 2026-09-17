plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android { namespace = "com.alicepro.mobile"; compileSdk = 35
    defaultConfig { applicationId = "com.alicepro.mobile"; minSdk = 26; targetSdk = 35; versionCode = 1; versionName = "0.1.0" }
}

chaquopy { defaultConfig { version = "3.11" } }

dependencies { implementation("androidx.core:core-ktx:1.13.1"); implementation("androidx.appcompat:appcompat:1.7.0"); implementation("androidx.webkit:webkit:1.12.1") }
