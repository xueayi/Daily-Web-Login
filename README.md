# Daily Web Login

macOS 菜单栏应用 -- 每日定时自动用默认浏览器打开指定网页。

## 功能

- 启动时立即打开所有配置的网址
- 每日定时自动打开（默认 09:00，可修改）
- macOS 菜单栏常驻，后台运行不占 Dock
- 支持通过菜单栏 GUI 管理网址和定时时间
- 系统通知提示执行结果

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 运行

```bash
# 后台运行（可以关闭终端）
./start.sh

# 或前台运行（调试用）
python3 main.py
```

运行后菜单栏会出现 🌐 图标，程序会**立即打开**所有配置的网址，之后每天在设定时间自动打开。
使用 `start.sh` 启动时可以安全关闭终端，程序继续在后台运行。

### 3. 配置

编辑 `config.json` 或通过菜单栏操作：

```json
{
  "schedule_time": "09:00",
  "urls": [
    "https://kp.m-team.cc/",
    "https://linux.do/",
    "https://www.fufugal.com/"
  ]
}
```

## 菜单栏功能

| 菜单项 | 说明 |
|--------|------|
| 立即打开全部网址 | 手动触发打开所有网址 |
| ⏰ 定时: HH:MM | 显示当前定时时间 |
| 网址列表 | 展开查看/单独打开某个网址 |
| 修改定时时间 | 弹窗修改每日定时时间 |
| 添加网址 | 弹窗添加新网址 |
| 删除网址 | 弹窗按编号删除网址 |
| 退出 | 退出应用 |

## 打包为独立 .app（可选）

```bash
pip install py2app
python setup.py py2app
```

生成的 `dist/DailyWebLogin.app` 可双击运行，无需终端。

## 开机自启

### 方法一：macOS 登录项（推荐）

1. 打开 **系统设置 → 通用 → 登录项**
2. 点击 **+**，选择 `dist/DailyWebLogin.app`（打包后）或创建一个 Automator 应用包装 `python main.py`

### 方法二：LaunchAgent

创建 `~/Library/LaunchAgents/com.dailyweblogin.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.dailyweblogin</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Volumes/WD_SN730/01_dev/Daily-Web-Login/main.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
```

加载：

```bash
launchctl load ~/Library/LaunchAgents/com.dailyweblogin.plist
```

## 系统要求

- macOS 10.12+
- Python 3.8+
