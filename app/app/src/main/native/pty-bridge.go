// pty-bridge —— HOS-ARES 自研：guest 内创建 PTY 供 reasonix TUI 使用（Go 纯 syscall 版）。
// 交叉编译: CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -o pty-bridge pty-bridge.go
package main

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

const sizeFile = "/root/.win-size"

func readWinsize() (int, int, error) {
	b, err := os.ReadFile(sizeFile)
	if err != nil {
		return 0, 0, err
	}
	parts := strings.Fields(string(b))
	if len(parts) != 2 {
		return 0, 0, fmt.Errorf("bad size file")
	}
	c, err1 := strconv.Atoi(parts[0])
	r, err2 := strconv.Atoi(parts[1])
	if err1 != nil || err2 != nil || c <= 0 || r <= 0 {
		return 0, 0, fmt.Errorf("bad size")
	}
	return c, r, nil
}

func main() {
	// 1) 打开 pty master
	master, err := os.OpenFile("/dev/ptmx", os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		fmt.Fprintln(os.Stderr, "open /dev/ptmx:", err)
		os.Exit(1)
	}
	defer master.Close()

	// 2) 获取 slave 编号并打开
	n, err := unix.IoctlGetInt(int(master.Fd()), unix.TIOCGPTN)
	if err != nil {
		fmt.Fprintln(os.Stderr, "TIOCGPTN:", err)
		os.Exit(1)
	}
	slavePath := fmt.Sprintf("/dev/pts/%d", n)
	slave, err := os.OpenFile(slavePath, os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		fmt.Fprintln(os.Stderr, "open slave:", err)
		os.Exit(1)
	}
	defer slave.Close()

	// 3) 子进程：sh 会话（setsid），stdin/out/err 接 slave
	shell := "/bin/sh"
	cmd := exec.Command(shell)
	cmd.Stdin = slave
	cmd.Stdout = slave
	cmd.Stderr = slave
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		fmt.Fprintln(os.Stderr, "start sh:", err)
		os.Exit(1)
	}

	// 4) 初始尺寸
	cols, rows := 80, 24
	if c, r, err := readWinsize(); err == nil {
		cols, rows = c, r
		_ = unix.IoctlSetWinsize(int(master.Fd()), unix.TIOCSWINSZ, &unix.Winsize{Row: uint16(rows), Col: uint16(cols)})
	}

	// 5) 双向转发 + 尺寸轮询
	done := make(chan struct{})
	go func() {
		buf := make([]byte, 8192)
		for {
			n, err := master.Read(buf)
			if n > 0 {
				os.Stdout.Write(buf[:n])
			}
			if err != nil {
				close(done)
				return
			}
		}
	}()
	go func() {
		buf := make([]byte, 8192)
		for {
			n, err := os.Stdin.Read(buf)
			if n > 0 {
				master.Write(buf[:n])
			}
			if err != nil {
				close(done)
				return
			}
		}
	}()

	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-done:
			cmd.Process.Kill()
			return
		case <-ticker.C:
			if c, r, err := readWinsize(); err == nil && (c != cols || r != rows) {
				_ = unix.IoctlSetWinsize(int(master.Fd()), unix.TIOCSWINSZ, &unix.Winsize{Row: uint16(r), Col: uint16(c)})
				cmd.Process.Signal(syscall.SIGWINCH)
				cols, rows = c, r
			}
		}
	}
}
