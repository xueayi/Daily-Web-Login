# Daily Web Login

macOS 菜单栏应用 -- 每日定时自动打开指定网页 + yngal 签到寻宝 + 飞书推送汇总。

## 功能

- 启动时立即打开所有配置的网址（不签到、不推送，避免打扰）
- 每日定时自动执行（默认 09:00，可修改）：
  1. 用默认浏览器打开 `urls` 列表中的网址
  2. yngal 自动登录 + 签到 + 寻宝（替代手动打开 fufugal.com）
  3. 飞书 webhook 推送汇总卡片（打开结果 + 签到结果 + 寻宝结果）
- macOS 菜单栏常驻，后台运行不占 Dock
- 支持通过菜单栏 GUI 管理网址、定时时间、yngal 寻宝开关
- 系统通知提示执行结果

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

依赖：`rumps`（菜单栏框架）、`schedule`（定时任务）、`requests`（HTTP 请求）、`python-dotenv`（环境变量加载）。

### 2. 配置凭证

复制 `.env.example` 为 `.env` 并填入真实凭证：

```bash
cp .env.example .env
```

`.env` 文件内容：

```
# yngal 账号（明文密码，程序内 MD5 后传输，不会记录原密码）
YNGAL_EMAIL=your_email@example.com
YNGAL_PASSWORD=your_plain_password

# 飞书 webhook URL
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
```

> `.env` 已加入 `.gitignore`，不会入库。飞书 webhook URL 可在飞书群「设置 → 群机器人 → 添加机器人 → 自定义机器人」获取。

### 3. 运行

```bash
# 后台运行（可以关闭终端）
./start.sh

# 或前台运行（调试用）
python3 main.py
```

运行后菜单栏会出现 🌐 图标，程序会**立即打开**所有配置的网址（不签到、不推送）。
每日定时任务会依次执行：打开网址 → yngal 签到寻宝 → 飞书推送汇总。

> 使用 `start.sh` 启动时可以安全关闭终端，程序继续在后台运行。

### 4. 配置文件

`config.json` 存放非敏感配置：

```json
{
  "schedule_time": "09:00",
  "urls": [
    "https://kp.m-team.cc/",
    "https://linux.do/"
  ],
  "yngal": {
    "enabled": true,
    "hunt_enabled": true
  },
  "feishu": {
    "notify_enabled": true
  }
}
```

| 字段 | 说明 |
|------|------|
| `schedule_time` | 每日定时执行时间，HH:MM 格式 |
| `urls` | 需要打开的网址列表 |
| `yngal.enabled` | 是否启用 yngal 签到 |
| `yngal.hunt_enabled` | 是否启用 yngal 寻宝（可在菜单切换） |
| `feishu.notify_enabled` | 是否启用飞书推送 |

## 菜单栏功能

| 菜单项 | 说明 |
|--------|------|
| 立即打开全部网址 | 手动触发打开所有网址 |
| 测试打开（首个网址） | 测试浏览器打开是否正常 |
| ⏰ 定时: HH:MM | 显示当前定时时间 |
| 网址列表 | 展开查看/单独打开某个网址 |
| 修改定时时间 | 弹窗修改每日定时时间 |
| 添加网址 | 弹窗添加新网址 |
| 删除网址 | 弹窗按编号删除网址 |
| 测试 yngal 签到 | 立即执行一次登录+签到+寻宝，弹窗显示结果 |
| ✓/☐ yngal 寻宝 | 切换寻宝开关（✓ 已启用 / ☐ 已禁用） |
| 测试飞书推送 | 发送测试消息到飞书 webhook |
| 退出 | 退出应用 |

## yngal 签到说明

yngal（www.yngal.com，与 fufugal.com 是同一网站）签到流程：

1. **登录**：POST `/sign`，密码经 MD5 摘要后传输（不传输明文）
2. **签到**：GET `/addJf`，领取每日访问奖励（VIP 2 硬币，普通用户 1 硬币）
3. **寻宝**：GET `/hunt`，每日寻宝（需先在网页设置守护灵出战位）

状态码处理：
- 签到：`0`=成功 / `10`=今日已签到 / `119`=登录失效（自动重新登录重试）
- 寻宝：`0/200`=成功 / `601`=登录失效 / `602`=未设置守护灵出战位 / `688`=今日已完成
- 寻宝奖励：`wrap==10` 时获得 5 硬币，其他值为积分

## 飞书推送说明

每日定时任务执行后，推送一张交互式卡片到飞书群，包含：
- 打开网址结果（成功数/总数）
- yngal 签到结果
- yngal 寻宝结果
- 执行时间

卡片颜色根据状态自动判定：
- 🟢 绿色：全部成功
- 🔵 蓝色：测试推送
- 🟡 黄色：警告（如未设置守护灵出战位）
- 🟠 橙色：部分失败
- 🔴 红色：全部失败

## 打包为独立 .app（可选）

```bash
pip install py2app
python setup.py py2app
```

生成的 `dist/DailyWebLogin.app` 可双击运行，无需终端。

**打包后 .env 配置**：py2app 打包后 `main.py` 在 `DailyWebLogin.app/Contents/Resources/` 下，需手动复制 `.env` 到该目录：

```bash
cp .env dist/DailyWebLogin.app/Contents/Resources/.env
```

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
- 系统 `/usr/bin/python3`（rumps/PyObjC 依赖系统 Python 的 GUI 框架，不要用 managed Python）

## 文件结构

```
Daily-Web-Login/
├── main.py             # 主程序（菜单栏 + 调度 + 整合 yngal/feishu）
├── yngal.py            # yngal 客户端（登录/签到/寻宝）
├── feishu.py           # 飞书 webhook 卡片推送
├── config.json         # 非敏感配置（定时/网址/开关）
├── .env                # 敏感凭证（不入库，需手动创建）
├── .env.example        # 凭证模板（入库）
├── requirements.txt    # 依赖清单
├── setup.py            # py2app 打包配置
├── start.sh            # 后台启动脚本
└── daily_web_login.log # 运行日志（运行时生成）
```

## 参考

- yngal 签到实现参考：[cyilin36/Check-in](https://github.com/cyilin36/Check-in)
- 飞书卡片 schema 参考：[飞书开放平台 - 卡片消息](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/feishu-cards/card-json-structure)
