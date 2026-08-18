package com.hos.ares;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream;
import org.apache.commons.compress.compressors.gzip.GzipCompressorInputStream;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * HOS-ARES · Reasonix 终端页（自研）。
 * 解压预装 rootfs → 启动 proot（Alpine）→ pty-bridge 提供 PTY → entry.sh → reasonix TUI。
 * WebView + xterm.js 渲染终端；JS-Java 桥完成输入输出与窗口尺寸同步。
 */
public class TerminalActivity extends Activity {
    private static final String TAG = "HOSARES";

    private WebView webView;
    private Process proot;
    private OutputStream guestIn;
    private File rootfs;
    private volatile boolean alive = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_terminal);

        rootfs = new File(getFilesDir(), "rootfs");
        try {
            ensureEnvironment();
            setupWebView();
            startProot();
        } catch (Exception e) {
            Log.e(TAG, "启动失败", e);
            pushOutput("[HOS-ARES] 启动失败: " + e.getMessage() + "\r\n");
        }
    }

    /* ---------- 环境准备：解压预装 rootfs + 部署 assets ---------- */
    private void ensureEnvironment() throws IOException {
        // rootfs 已解压（存在 etc + bin/sh）则跳过 tar 解压，只补部署 assets；
        // 与 ProotRuntime 共用 /sdcard/data/.Ares，避免两处重复解压 455MB。
        boolean rootfsReady = new File(rootfs, "etc").exists() && new File(rootfs, "bin/sh").exists();
        if (!rootfsReady) {
            if (!rootfs.exists() && !rootfs.mkdirs()) {
                throw new IOException("无法创建 rootfs 目录: " + rootfs.getAbsolutePath());
            }
            extractRootfs();
            Log.i(TAG, "rootfs 解压完成 → " + rootfs.getAbsolutePath());
        }
        deploy("usr/bin/reasonix", new File(rootfs, "usr/local/bin/reasonix"));
        deploy("usr/bin/pty-bridge", new File(rootfs, "usr/bin/pty-bridge"));
        deploy("root/entry.sh", new File(rootfs, "root/entry.sh"));
        new File(rootfs, "root/.hos-ares").mkdirs();
    }

    private void extractAsset(String name, File dst) throws IOException {
        dst.getParentFile().mkdirs();
        try (InputStream in = getAssets().open(name); FileOutputStream out = new FileOutputStream(dst)) {
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        }
    }

    private void deploy(String asset, File dst) throws IOException {
        extractAsset(asset, dst);
        dst.setExecutable(true, false);
    }

    /**
     * 将内置 rootfs 解包到 rootfs 路径。优先使用完整预装环境 rootfs.dat（与
     * ProotRuntime 一致），缺失时回退 alpine-minirootfs.tar.gz → alpine-minirootfs.tar。
     * 正确处理 symlink：minirootfs 中 /bin/sh 等是指向 busybox 的软链，/sdcard 上
     * 直接 tar -xzf 可能丢链或失效，故对 symlink entry 单独以 ln -s 或 NIO 创建。
     */
    private void extractRootfs() throws IOException {
        String datName = "rootfs.dat";       // 预装 + gzip（.dat 扩展名避免 AGP 解压）
        String fullName = "rootfs.tar";       // 预装 + 裸 tar
        String gzName = "alpine-minirootfs.tar.gz";  // 精简 fallback
        String rawName = "alpine-minirootfs.tar";    // 精简 fallback
        String assetName;
        if (assetsContains(datName)) {
            assetName = datName;
        } else if (assetsContains(fullName)) {
            assetName = fullName;
        } else if (assetsContains(gzName)) {
            assetName = gzName;
        } else {
            assetName = rawName;
        }
        try (InputStream in = getAssets().open(assetName)) {
            // rootfs.dat / .tar.gz 均为 gzip；.tar 裸格式不压缩。
            boolean isGz = assetName.endsWith(".gz") || assetName.endsWith(".dat");
            InputStream stream = isGz ? new GzipCompressorInputStream(in) : in;
            try (TarArchiveInputStream tar = new TarArchiveInputStream(stream)) {
                TarArchiveEntry entry;
                while ((entry = tar.getNextTarEntry()) != null) {
                    String name = entry.getName().replaceFirst("^\\./", "");
                    File dest = new File(rootfs, name);
                    if (entry.isSymbolicLink()) {
                        dest.getParentFile().mkdirs();
                        createSymlink(entry.getLinkName(), dest);
                    } else if (entry.isDirectory()) {
                        dest.mkdirs();
                    } else if (entry.isFile()) {
                        dest.getParentFile().mkdirs();
                        try (FileOutputStream out = new FileOutputStream(dest)) {
                            byte[] buf = new byte[65536];
                            int n;
                            while ((n = tar.read(buf)) > 0) out.write(buf, 0, n);
                        }
                    }
                    // hardlink / 其他类型：忽略（minirootfs 无关键 hardlink）
                }
            }
        }
    }

    /** 判断某个顶层资产是否存在。 */
    private boolean assetsContains(String name) {
        try (InputStream in = getAssets().open(name)) {
            return true;
        } catch (IOException e) {
            return false;
        }
    }

    /** 创建符号链接：优先 toybox ln（/sdcard 上 Java NIO 在 API 26 前不可用），失败则尝试 NIO 兜底。 */
    private void createSymlink(String linkName, File dest) {
        boolean ok = false;
        try {
            Process p = new ProcessBuilder("/system/bin/ln", "-s", linkName, dest.getAbsolutePath())
                    .redirectErrorStream(true).start();
            p.waitFor();
            // NIO Files 仅 API 26+ 可用；API 24-25 乐观假设 ln -s 已成功
            if (android.os.Build.VERSION.SDK_INT >= 26) {
                ok = java.nio.file.Files.isSymbolicLink(dest.toPath());
            } else {
                ok = true;
            }
        } catch (Exception ignored) {
            // 走 NIO 兜底
        }
        if (!ok && android.os.Build.VERSION.SDK_INT >= 26) {
            try {
                java.nio.file.Files.createSymbolicLink(
                        dest.toPath(), java.nio.file.Paths.get(linkName)
                );
                ok = java.nio.file.Files.isSymbolicLink(dest.toPath());
            } catch (Exception e) {
                Log.w(TAG, "创建 symlink 失败: " + dest + " -> " + linkName + " (" + e.getMessage() + ")");
            }
        }
    }

    /* ---------- 终端 WebView ---------- */
    private void setupWebView() {
        webView = findViewById(R.id.terminal_view);
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.setWebViewClient(new WebViewClient());
        webView.addJavascriptInterface(new Bridge(), "Android");
        webView.loadUrl("file:///android_asset/web/index.html");
    }

    private class Bridge {
        @JavascriptInterface
        public void onData(String data) {
            if (guestIn != null && alive) {
                try { guestIn.write(data.getBytes("UTF-8")); guestIn.flush(); }
                catch (IOException ignored) {}
            }
        }

        @JavascriptInterface
        public void onResize(int cols, int rows) {
            File f = new File(rootfs, "root/.win-size");
            try (FileOutputStream out = new FileOutputStream(f)) {
                out.write((cols + " " + rows).getBytes());
            } catch (IOException ignored) {}
        }
    }

    /* ---------- proot 进程 ---------- */
    private void startProot() throws IOException {
        File prootBin = new File(getApplicationInfo().nativeLibraryDir, "proot.so");
        File loader = new File(getApplicationInfo().nativeLibraryDir, "loader.so");
        if (!prootBin.exists() || !loader.exists()) {
            pushOutput("[HOS-ARES] 缺少 proot 组件（jniLibs）\r\n");
            return;
        }
        ProcessBuilder pb = new ProcessBuilder(
                prootBin.getAbsolutePath(),
                "-0", "-r", rootfs.getAbsolutePath(),
                "-b", "/dev", "-b", "/proc", "-b", "/sys",
                "-b", "/sdcard:/sdcard",
                "-w", "/root",
                "/bin/sh", "-c", "/usr/bin/pty-bridge /bin/sh /root/entry.sh");
        pb.environment().put("PROOT_LOADER", loader.getAbsolutePath());
        pb.environment().put("TERM", "xterm-256color");
        pb.redirectErrorStream(true);
        proot = pb.start();
        guestIn = proot.getOutputStream();
        alive = true;
        readerThread();
        watcherThread();
    }

    private void readerThread() {
        Thread t = new Thread(() -> {
            byte[] buf = new byte[8192];
            try (InputStream in = proot.getInputStream()) {
                int n;
                while (alive && (n = in.read(buf)) > 0) {
                    final String s = new String(buf, 0, n, "UTF-8");
                    runOnUiThread(() -> pushOutput(s));
                }
            } catch (IOException ignored) {}
        }, "hos-pt-reader");
        t.setDaemon(true);
        t.start();
    }

    private void watcherThread() {
        Thread t = new Thread(() -> {
            try {
                int code = proot.waitFor();
                alive = false;
                runOnUiThread(() -> webView.evaluateJavascript("window.notifyExit()", null));
                Log.i(TAG, "proot 退出码 " + code);
            } catch (InterruptedException ignored) {}
        }, "hos-pt-watcher");
        t.setDaemon(true);
        t.start();
    }

    private void pushOutput(String s) {
        if (webView == null) return;
        String quoted = JSONObject.quote(s);
        webView.evaluateJavascript("window.writeData(" + quoted + ")", null);
    }

    /* ---------- 生命周期 ---------- */
    @Override
    protected void onDestroy() {
        super.onDestroy();
        alive = false;
        if (proot != null) { proot.destroy(); }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    /* 存储授权（/sdcard 映射给 guest） */
    @SuppressWarnings("deprecation")
    public static boolean needsStoragePermission(Activity a) {
        return android.os.Build.VERSION.SDK_INT >= 30
                && !Environment.isExternalStorageManager();
    }
    public static void requestStoragePermission(Activity a) {
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            try {
                a.startActivity(new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        android.net.Uri.parse("package:" + a.getPackageName())));
            } catch (Exception e) {
                a.startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
            }
        }
    }
}
