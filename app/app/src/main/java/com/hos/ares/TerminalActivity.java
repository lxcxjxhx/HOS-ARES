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

    /** 手机端默认 rootfs 路径（用户指定）：解压/运行均以此为根。 */
    private static final String ROOTFS_PATH = "/sdcard/data/.Ares";

    private WebView webView;
    private Process proot;
    private OutputStream guestIn;
    private File rootfs;
    private volatile boolean alive = false;
    /** 初始化是否已发起（防止 onCreate/onResume 双跑） */
    private volatile boolean bootStarted = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_terminal);

        // 顶部返回（规范页2）
        findViewById(R.id.btnTermBack).setOnClickListener(v -> finish());

        rootfs = new File(ROOTFS_PATH);
        // 写 /sdcard 必须先获得存储权限；未授权时引导用户开启后返回重进
        if (needsStoragePermission(this)) {
            android.widget.Toast.makeText(this,
                    "需要「所有文件访问」权限才能部署 rootfs 到 " + ROOTFS_PATH + "，请授权后重进终端",
                    android.widget.Toast.LENGTH_LONG).show();
            requestStoragePermission(this);
            return;
        }
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
            File tar = new File(getFilesDir(), "rootfs.tar");
            extractAsset("rootfs.tar", tar);
            if (!rootfs.exists() && !rootfs.mkdirs()) {
                throw new IOException("无法创建 rootfs 目录: " + rootfs.getAbsolutePath());
            }
            Process p = new ProcessBuilder("/system/bin/tar", "-xzf", tar.getAbsolutePath(), "-C", rootfs.getAbsolutePath())
                    .redirectErrorStream(true).start();
            try { p.waitFor(); } catch (InterruptedException ignored) {}
            tar.delete();
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
        bootStarted = true; // 标记初始化已发起，防止 onCreate/onResume/权限回调双跑
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
        // Android 宿主默认 /tmp 不可写，proot 建 glue rootfs 临时目录会报
        // "can't create temporary directory"，必须指向 App 私有可写目录
        File prootTmp = new File(getCacheDir(), "proot-tmp");
        if (!prootTmp.exists() && !prootTmp.mkdirs()) {
            pushOutput("[HOS-ARES] 警告: 无法创建 proot 临时目录，可能仍会报 tmp 错误\r\n");
        }
        pb.environment().put("PROOT_TMP_DIR", prootTmp.getAbsolutePath());
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

    /** API 30+ 从「所有文件访问」设置页返回后，若已授权则继续初始化。 */
    @Override
    protected void onResume() {
        super.onResume();
        if (bootStarted || alive || proot != null) return; // 已在运行/已初始化
        if (!needsStoragePermission(this)) {
            try {
                ensureEnvironment();
                setupWebView();
                startProot();
            } catch (Exception e) {
                Log.e(TAG, "授权后启动失败", e);
                pushOutput("[HOS-ARES] 启动失败: " + e.getMessage() + "\r\n");
            }
        }
    }

    /** API 24-29 运行时权限回调：授权成功后自动继续初始化。 */
    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        // 请求顺序为 [READ, WRITE]；WRITE 才是 /sdcard 部署的关键权限
        boolean writeGranted = false;
        for (int i = 0; i < permissions.length && i < grantResults.length; i++) {
            if (android.Manifest.permission.WRITE_EXTERNAL_STORAGE.equals(permissions[i])
                    && grantResults[i] == android.content.pm.PackageManager.PERMISSION_GRANTED) {
                writeGranted = true;
                break;
            }
        }
        if (requestCode == 1001 && writeGranted) {
            if (bootStarted) return; // 已在初始化
            try {
                ensureEnvironment();
                setupWebView();
                startProot();
            } catch (Exception e) {
                Log.e(TAG, "授权后启动失败", e);
                pushOutput("[HOS-ARES] 启动失败: " + e.getMessage() + "\r\n");
            }
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    /* 存储授权（rootfs 部署到 /sdcard/data/.Ares 必须） */
    @SuppressWarnings("deprecation")
    public static boolean needsStoragePermission(Activity a) {
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            return !Environment.isExternalStorageManager();
        }
        // API 24-29：传统运行时权限（Manifest 已声明 READ/WRITE_EXTERNAL_STORAGE）
        return a.checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != android.content.pm.PackageManager.PERMISSION_GRANTED;
    }

    @SuppressWarnings("deprecation")
    public static void requestStoragePermission(Activity a) {
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            try {
                a.startActivity(new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        android.net.Uri.parse("package:" + a.getPackageName())));
            } catch (Exception e) {
                a.startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
            }
        } else {
            // API 24-29：直接请求运行时权限
            a.requestPermissions(
                    new String[]{android.Manifest.permission.READ_EXTERNAL_STORAGE,
                            android.Manifest.permission.WRITE_EXTERNAL_STORAGE}, 1001);
        }
    }
}
