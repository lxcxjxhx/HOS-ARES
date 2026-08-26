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
        versionCode = 2
        versionName = "0.5.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // 侧载发布（自托管 APK）：CI 无正式 keystore → 复用 AGP 自动生成的 debug 签名，
            // 保证 release 产物可直接安装。正式上架时替换为签名 keystore（偏差记录见 11 文 §11.5）。
            signingConfig = signingConfigs.getByName("debug")
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

    // round-16：rootfs 资产（rootfs.tar.xz，实测烘烤产物 697.7MB）经 AAPT2 的
    // compressReleaseAssets 二次 deflate 时 ZipFlinger 堆爆炸（round-14/15 实测
    // "Java heap space"，-Xmx4g 仍不足）。rootfs.tar.xz 本身已是 xz 压缩（不可再压），
    // 按 STORED 直接入包：既能免去重复压缩（体积不变）又把内存占用降为流式常数。
    androidResources {
        noCompress += "xz"
    }

    // AresGateway 骨架（Phase 3，根模块 ares-gateway/）——源码集直引，单一源码真源：
    //   · round-10 实测：include(":ares-gateway") 将独立 JVM 工程（自带 plugins 声明）拉入
    //     android 构建 → "plugin already on classpath with unknown version"；
    //   · round-11 移除依赖后 :app 编译失败（AresViewModel/AresHomeScreen 引用 gateway 包）；
    //   · round-14 修复：java.srcDirs 直接挂接根模块 Kotlin 源码（纯 Kotlin + coroutines +
    //     org.json，无 JVM-only API），既绕开工程/插件冲突，又保持 AresGateway 唯一实现。
    sourceSets {
        getByName("main") {
            java.srcDirs("../../ares-gateway/src/main/kotlin")
        }
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

    // rootfs 解压（Phase 5 全量装载；XZ 正式切流，见 RootfsInstaller.XZCompat）
    implementation("org.apache.commons:commons-compress:1.27.1")
    implementation("org.tukaani:xz:1.10")

    // AresGateway 网关源码经 sourceSets 直引（见上方 android.sourceSets），无需 project 依赖；
    // 网关运行职责由 rootfs 内 tools/mcp-compat-gw.py（mcp SDK 1.28.1 双参派发适配）承担。
}