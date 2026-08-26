plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.hos.ares"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.hos.ares"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0-alpha"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
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
    }
}

dependencies {
    // Compose
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")

    // 传输层（serve SSE 通道）
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    // rootfs 解压（Phase 5 全量装载；XZCompat 占位 GZIP，正式切 XZ）
    implementation("org.apache.commons:commons-compress:1.27.1")
    implementation("org.tukaani:xz:1.10")

    // AresGateway 网关模块（Phase 3 骨架）
    // round-11 实测：implementation(project(":ares-gateway")) 使 AGP 解析其自带 build.gradle.kts 的
    //   plugins 块（kotlin-jvm 2.0.21 等在父构建 classpath 已存在）→ "plugin already on classpath
    //   with unknown version"，assembleRelease 失败（round-10 build-apk 步）
    // 修复：移除该 project 依赖；网关职责由 rootfs 内 tools/mcp-compat-gw.py（mcp SDK 1.28.1
    //   双参派发适配）承担，Android 侧仅保留 :app 独立构建。
}