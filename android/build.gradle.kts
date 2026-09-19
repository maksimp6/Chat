plugins {
    id("com.android.application") version "9.3.1" apply false
    id("com.chaquo.python") version "17.0.0" apply false
}

buildscript {
    dependencies {
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.4.20")
    }
}
