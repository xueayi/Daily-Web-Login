#!/bin/bash
# 后台启动 Daily Web Login，关闭终端后仍继续运行
DIR="$(cd "$(dirname "$0")" && pwd)"
nohup /usr/bin/python3 "$DIR/main.py" > /dev/null 2>&1 &
echo "Daily Web Login 已在后台启动 (PID: $!)"
echo "菜单栏会出现 🌐 图标，可通过菜单退出"
