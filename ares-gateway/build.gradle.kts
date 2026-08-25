plugins {
    kotlin("jvm") version "2.0.21"
    application
}

group = "com.hos.ares"
version = "0.1.0"

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0")
    implementation("org.json:json:20240303")
    // Phase 4（Android 端）：okhttp + okhttp-sse 用于 reasonix serve 通道
    // implementation("com.squareup.okhttp3:okhttp:4.12.0")
    // implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")

    testImplementation(kotlin("test"))
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.9.0")
}

tasks.test { useJUnitPlatform() }

application {
    mainClass.set("com.hos.ares.gateway.MainKt")
}