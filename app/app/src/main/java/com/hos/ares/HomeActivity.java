package com.hos.ares;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;

/**
 * HOS-ARES 安全风信子主界面（PLAN3）。
 * 展示品牌与三工具状态；点「进入 Reasonix 终端」启动终端页。
 */
public class HomeActivity extends Activity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_home);

        // 手机端默认 rootfs 路径（与 TerminalActivity/ProotRuntime 统一）
        File rootfs = new File("/sdcard/data/.Ares");
        boolean envReady = new File(rootfs, "root/entry.sh").exists();
        boolean toolsReady = new File(rootfs, "root/tools/install.log").exists();

        setStatus(R.id.dot_reasonix, R.id.status_reasonix,
                envReady ? "环境就绪 · DeepSeek-Reasonix（源码构建）" : "首次启动将自动初始化", envReady);
        setStatus(R.id.dot_argus, R.id.status_argus,
                toolsReady ? "就绪 · MCP redteam_scan" : "预装 · 首次启动后自检", toolsReady);
        setStatus(R.id.dot_pentestgpt, R.id.status_pentestgpt,
                toolsReady ? "就绪 · MCP run_pentestgpt" : "预装 · 首次启动后自检", toolsReady);
        setStatus(R.id.dot_repoaudit, R.id.status_repoaudit,
                toolsReady ? "就绪 · MCP audit_repo" : "预装 · 首次启动后自检", toolsReady);

        // Root 检测：决定高级功能（frida 动态分析等）是否可用
        boolean rooted = isRooted();
        setStatus(R.id.dot_root, R.id.status_root,
                rooted ? "已检测 Root · 动态分析/系统级功能可用"
                       : "未检测 Root · 高级功能受限（frida 类不可用）", rooted);
        findViewById(R.id.card_root).setOnClickListener(v -> Toast.makeText(this,
                rooted ? "已检测到 Root：reasonix 内可调用系统级/动态分析命令"
                       : "未检测到 Root：frida 动态分析等需 root 的功能不可用（其余工具不受影响）",
                Toast.LENGTH_LONG).show());

        findViewById(R.id.card_reasonix).setOnClickListener(v -> enterTerminal());
        findViewById(R.id.btn_enter).setOnClickListener(v -> enterTerminal());
        findViewById(R.id.card_reasonix).setOnLongClickListener(v -> {
            Toast.makeText(this, "Reasonix TUI · 源码构建二进制", Toast.LENGTH_SHORT).show();
            return true;
        });
    }

    private void setStatus(int dotId, int textId, String text, boolean ready) {
        View dot = findViewById(dotId);
        dot.setBackgroundResource(ready ? R.drawable.bg_dot_ready : R.drawable.bg_dot_init);
        ((TextView) findViewById(textId)).setText(text);
    }

    /** Root 检测：常见 su/magisk 路径 + su 可执行性（决定 frida 等高级功能是否可用） */
    private boolean isRooted() {
        String[] markers = {
                "/system/bin/su", "/system/xbin/su", "/sbin/su", "/system/app/Superuser.apk",
                "/data/adb/magisk", "/system/bin/magisk", "/sbin/magisk"
        };
        for (String p : markers) {
            if (new File(p).exists()) return true;
        }
        try {
            Process p = new ProcessBuilder("su", "-c", "id").redirectErrorStream(true).start();
            byte[] buf = new byte[256];
            int n = p.getInputStream().read(buf);
            p.destroy();
            if (n > 0 && new String(buf, 0, n, "UTF-8").contains("uid=0")) return true;
        } catch (Exception ignored) {}
        return false;
    }

    private void enterTerminal() {
        if (TerminalActivity.needsStoragePermission(this)) {
            Toast.makeText(this, "需要「所有文件访问」权限部署 rootfs，请授权后重试",
                    Toast.LENGTH_LONG).show();
            pendingEnter = true; // 授权返回后 onResume 自动进入
            TerminalActivity.requestStoragePermission(this);
            return;
        }
        startActivity(new Intent(this, TerminalActivity.class));
    }

    /** API 30+ 授权返回后自动进入终端，减少一次手动点击。 */
    @Override
    protected void onResume() {
        super.onResume();
        if (pendingEnter && !TerminalActivity.needsStoragePermission(this)) {
            pendingEnter = false;
            startActivity(new Intent(this, TerminalActivity.class));
        }
    }

    private boolean pendingEnter = false;
}
