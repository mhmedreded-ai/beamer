import java.io.File

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.chaquo.python")
}

val versionFile = File(rootProject.projectDir, "../VERSION")
val repoVersion = if (versionFile.exists()) versionFile.readText().trim() else "1.5.18"
val resolvedVersionName = (project.findProperty("versionName") as String?) ?: repoVersion

android {
    namespace = "uk.co.kalkman.beamer"
    compileSdk = 35

    defaultConfig {
        applicationId = "uk.co.kalkman.beamer"
        minSdk = 26
        targetSdk = 35
        versionCode = 10518
        versionName = resolvedVersionName

        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        create("releaseConfig") {
            val keystoreFile = file("release-keystore.jks")
            if (keystoreFile.exists() && !System.getenv("ANDROID_KEYSTORE_PASSWORD").isNullOrEmpty()) {
                storeFile = keystoreFile
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            val keystoreFile = file("release-keystore.jks")
            signingConfig = if (keystoreFile.exists() && !System.getenv("ANDROID_KEYSTORE_PASSWORD").isNullOrEmpty()) {
                signingConfigs.getByName("releaseConfig")
            } else {
                signingConfigs.getByName("debug")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            install("segno==1.6.6")
        }
        pyc {
            src = false
        }
    }
    sourceSets {
        getByName("main") {
            srcDirs("..", "src/main/python")
            include("core/**/*.py", "nacl/**/*.py", "beamer_android_bridge.py")
            exclude("core/tests/**")
        }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.10.01")
    implementation(composeBom)
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material3:material3-window-size-class")
    implementation("androidx.window:window:1.3.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
}
