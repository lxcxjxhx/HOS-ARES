pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "hos-ares-android"
include(":app")
// round-10 实测：include(":ares-gateway") 将独立 JVM 工程（自带 settings/plugins 声明）拉入
//   android 构建 → assembleRelease 报 "plugin already on classpath with unknown version"。
// 修复（round-11）：android 侧仅构建 :app；网关职责由 rootfs 内 mcp-compat-gw.py 承担。