#!/bin/bash
# 后台启动 Daily Web Login，关闭终端后仍继续运行
DIR="$(cd "$(dirname "$0")" && pwd)"

# 使用 PATH 中的 python3（Homebrew 3.11，含全部依赖）
# 注意：/usr/bin/python3 是系统 3.9，缺少 beautifulsoup4 等包
PYTHON="$(command -v python3)"
if [ -z "$PYTHON" ]; then
    echo "错误: 找不到 python3，请确认已安装 Python 3.11+" >&2
    exit 1
fi

# 杀掉已运行的旧实例（避免 rumps 菜单栏项冲突）
pkill -f "main.py" 2>/dev/null
sleep 1

# 不使用 nohup（nohup 的 stdin 重定向会阻断 rumps 的 Cocoa WindowServer 连接），
# 改用 disown 方式让进程脱离当前 shell
# stderr 追加到日志文件，方便排查启动失败
"$PYTHON" "$DIR/main.py" >> "$DIR/daily_web_login.log" 2>&1 &
PID=$!
disown $PID

# 等待 1 秒后检查进程是否存活
sleep 1
if ! kill -0 $PID 2>/dev/null; then
    echo "⚠️  进程启动后立即退出 (PID: $PID)，请检查日志:" >&2
    echo "    tail -20 $DIR/daily_web_login.log" >&2
    exit 1
fi

echo "Daily Web Login 已在后台启动 (PID: $PID)"
echo "菜单栏会出现 🌐 图标，可通过菜单退出"
