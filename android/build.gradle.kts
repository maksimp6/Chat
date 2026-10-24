plugins {
    id("com.android.application") version "9.4.1" apply false
    id("com.chaquo.python") version "17.1.0" apply false
}

buildscript {
    dependencies {
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.4.21")
    }
}
