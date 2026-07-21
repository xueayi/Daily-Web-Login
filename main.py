#!/usr/bin/env python3
"""Daily Web Login - macOS 菜单栏应用，每日定时打开网址 + yngal 签到寻宝 + 飞书推送。"""

import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone

import rumps
import schedule
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
from dotenv import load_dotenv

from yngal import YngalClient, YngalError
from kf_checkin import KfClient, KfError
import feishu

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_web_login.log")
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

# 启动时加载 .env 凭证文件（YNGAL_EMAIL/PASSWORD、FEISHU_WEBHOOK_URL）
# py2app 打包后 __file__ 在 Contents/Resources/ 下，.env 也需放在那里
load_dotenv(ENV_FILE)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)

# ── helpers ──────────────────────────────────────────────────────────────────


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _open_url(url):
    """用系统默认浏览器打开 url，返回是否成功。

    优先使用 /usr/bin/open（Launch Services，最可靠）；失败时回退到
    AppleScript 的 `open location`。之前的 webbrowser.open_new_tab 在
    本运行环境（系统 python3 + accessory 模式 + nohup 后台）下会静默
    失败，因此改为直接调用系统命令。
    """
    try:
        r = subprocess.run(["/usr/bin/open", url], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            logging.info("打开成功: %s", url)
            return True
        logging.warning("open 失败 rc=%s: %s", r.returncode, r.stderr.strip())
    except Exception:
        logging.exception("open 异常 %s", url)

    # 回退：AppleScript
    try:
        script = f'open location "{url}"'
        r = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            logging.info("AppleScript 打开成功: %s", url)
            return True
        logging.warning("AppleScript 失败: %s", r.stderr.strip())
    except Exception:
        logging.exception("AppleScript 异常 %s", url)

    return False


def open_all_urls(urls=None):
    """用默认浏览器逐个打开所有网址，返回成功打开的数量。"""
    if urls is None:
        urls = load_config().get("urls", [])
    ok = 0
    for url in urls:
        ok += 1 if _open_url(url) else 0
    logging.info("本次共打开 %d/%d 个网址", ok, len(urls))
    return ok


# ── app ──────────────────────────────────────────────────────────────────────


class DailyWebLoginApp(rumps.App):

    def __init__(self):
        cfg = load_config()
        self.schedule_time = cfg.get("schedule_time", "09:00")
        self.urls = cfg.get("urls", [])
        # yngal/feishu 非敏感配置走 config.json
        self.yngal_enabled = cfg.get("yngal", {}).get("enabled", True)
        self.yngal_hunt_enabled = cfg.get("yngal", {}).get("hunt_enabled", True)
        self.feishu_notify_enabled = cfg.get("feishu", {}).get("notify_enabled", True)
        self.kf_enabled = cfg.get("kf", {}).get("enabled", True)
        # 敏感凭证走 .env 环境变量
        self.yngal_email = os.getenv("YNGAL_EMAIL", "")
        self.yngal_password = os.getenv("YNGAL_PASSWORD", "")
        self.feishu_webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "")
        self.kf_username = os.getenv("KF_USERNAME", "")
        self.kf_password = os.getenv("KF_PASSWORD", "")

        logging.info(
            "DailyWebLogin 启动, 定时=%s, 网址数=%d, yngal=%s, hunt=%s, kf=%s, feishu=%s",
            self.schedule_time, len(self.urls),
            self.yngal_enabled, self.yngal_hunt_enabled,
            self.kf_enabled, self.feishu_notify_enabled,
        )

        super().__init__(
            name="DailyWebLogin",
            title="🌐",
            menu=self._build_menu(),
            quit_button=None,
        )

        self._register_schedule()
        self._run_immediately()

    def run(self, **options):
        # 以后台 accessory 方式运行（运行时等价 LSUIElement），对 python3 直跑
        # 和 start.sh 都生效。sharedApplication() 是单例，super().run() 复用同一实例。
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
        super().run(**options)

    # ── menu ─────────────────────────────────────────────────────────────

    def _build_menu(self):
        items = []

        # ── 网址操作区 ──
        items.append(rumps.MenuItem("立即打开全部网址", callback=self._on_open_all))
        items.append(rumps.MenuItem("测试打开（首个网址）", callback=self._on_test_open))
        items.append(rumps.separator)

        url_submenu = rumps.MenuItem("网址列表")
        for url in self.urls:
            url_submenu.add(rumps.MenuItem(url, callback=self._on_open_single))
        items.append(url_submenu)
        items.append(rumps.separator)

        # 特定站点网页打开
        items.append(rumps.MenuItem("🌐 打开绯月网页", callback=self._on_open_kf))
        items.append(rumps.MenuItem("🌐 打开 yngal 网页", callback=self._on_open_yngal))
        items.append(rumps.separator)

        # ── 配置管理区 ──
        items.append(rumps.MenuItem("添加网址", callback=self._on_add_url))
        items.append(rumps.MenuItem("删除网址", callback=self._on_remove_url))
        items.append(rumps.separator)

        # ── 签到/推送操作区 ──
        items.append(rumps.MenuItem("测试 yngal 签到", callback=self._on_test_yngal))
        hunt_label = ("✓ " if self.yngal_hunt_enabled else "☐ ") + "yngal 寻宝"
        items.append(rumps.MenuItem(hunt_label, callback=self._on_toggle_hunt))
        items.append(rumps.MenuItem("测试绯月签到", callback=self._on_test_kf))
        kf_label = ("✓ " if self.kf_enabled else "☐ ") + "绯月签到"
        items.append(rumps.MenuItem(kf_label, callback=self._on_toggle_kf))
        items.append(rumps.MenuItem("测试飞书推送", callback=self._on_test_feishu))
        items.append(rumps.separator)

        # ── 底部：定时与退出 ──
        time_item = rumps.MenuItem(f"⏰ 定时: {self.schedule_time}")
        time_item.set_callback(None)
        items.append(time_item)
        items.append(rumps.MenuItem("修改定时时间", callback=self._on_change_time))
        items.append(rumps.separator)

        items.append(rumps.MenuItem("退出", callback=self._on_quit))

        return items

    def _refresh_menu(self):
        """重新构建菜单以反映最新配置。"""
        self.menu.clear()
        for item in self._build_menu():
            self.menu.add(item)

    # ── callbacks ────────────────────────────────────────────────────────

    def _on_open_all(self, _):
        open_all_urls(self.urls)
        rumps.notification(
            "Daily Web Login",
            "已打开全部网址",
            f"共 {len(self.urls)} 个",
        )

    def _on_open_single(self, sender):
        ok = _open_url(sender.title)
        rumps.notification(
            "Daily Web Login",
            "打开" + ("成功" if ok else "失败"),
            sender.title,
        )

    def _on_test_open(self, _):
        if not self.urls:
            rumps.alert("提示", "网址列表为空")
            return
        ok = _open_url(self.urls[0])
        rumps.notification(
            "Daily Web Login",
            "测试打开" + ("成功" if ok else "失败"),
            f"{self.urls[0]}\n日志: {LOG_FILE}",
        )

    def _on_change_time(self, _):
        resp = rumps.Window(
            title="修改定时时间",
            message="请输入每日定时时间 (HH:MM 格式，24小时制):",
            default_text=self.schedule_time,
            ok="确定",
            cancel="取消",
        ).run()

        if not resp.clicked:
            return

        new_time = resp.text.strip()
        try:
            datetime.strptime(new_time, "%H:%M")
        except ValueError:
            rumps.alert("格式错误", f'"{new_time}" 不是有效的 HH:MM 格式')
            return

        self.schedule_time = new_time
        self._save_current_config()
        self._register_schedule()
        self._refresh_menu()
        rumps.notification("Daily Web Login", "定时已更新", f"每日 {new_time} 自动打开")

    def _on_add_url(self, _):
        resp = rumps.Window(
            title="添加网址",
            message="请输入要添加的网址:",
            default_text="https://",
            ok="添加",
            cancel="取消",
        ).run()

        if not resp.clicked:
            return

        url = resp.text.strip()
        if not url or url == "https://":
            return

        self.urls.append(url)
        self._save_current_config()
        self._refresh_menu()
        rumps.notification("Daily Web Login", "网址已添加", url)

    def _on_remove_url(self, _):
        if not self.urls:
            rumps.alert("提示", "网址列表为空")
            return

        numbered = "\n".join(f"{i + 1}. {u}" for i, u in enumerate(self.urls))
        resp = rumps.Window(
            title="删除网址",
            message=f"请输入要删除的编号:\n\n{numbered}",
            default_text="",
            ok="删除",
            cancel="取消",
        ).run()

        if not resp.clicked:
            return

        try:
            idx = int(resp.text.strip()) - 1
            if 0 <= idx < len(self.urls):
                removed = self.urls.pop(idx)
                self._save_current_config()
                self._refresh_menu()
                rumps.notification("Daily Web Login", "网址已删除", removed)
            else:
                rumps.alert("错误", "编号超出范围")
        except ValueError:
            rumps.alert("错误", "请输入有效的数字编号")

    def _on_test_yngal(self, _):
        """立即执行一次 yngal 登录+签到+寻宝，结果打印到日志（不弹窗）。"""
        if not self.yngal_email or not self.yngal_password:
            logging.warning("yngal 测试：未配置 YNGAL_EMAIL / YNGAL_PASSWORD，请检查 .env")
            rumps.notification("Daily Web Login", "yngal 签到", "未配置凭证，请检查 .env")
            return
        logging.info("yngal 测试：开始执行 登录+签到+寻宝")
        try:
            client = YngalClient(self.yngal_email, self.yngal_password)
            result = client.run_all(hunt_enabled=self.yngal_hunt_enabled)
        except YngalError as e:
            logging.error("yngal 测试失败: %s (retryable=%s, code=%s)", e, e.retryable, e.code)
            rumps.notification("Daily Web Login", "yngal 签到", "失败，详情见日志")
            return
        except Exception as e:
            logging.exception("yngal 测试未知异常: %s", e)
            rumps.notification("Daily Web Login", "yngal 签到", "异常，详情见日志")
            return

        # 详细结果全部打印到日志
        logging.info("yngal 测试结果: 登录=%s | 签到=%s | 寻宝=%s | 错误=%s",
                     result.get("login_msg"),
                     result.get("sign_msg"),
                     result.get("hunt_msg"),
                     result.get("error"))
        # 简短系统通知（不弹窗，不需点击确认）
        sign_short = result.get("sign_msg", "")
        rumps.notification("Daily Web Login", "yngal 签到完成", sign_short)

    def _on_toggle_hunt(self, sender):
        """切换 yngal 寻宝开关，持久化到 config.json 并刷新菜单。"""
        self.yngal_hunt_enabled = not self.yngal_hunt_enabled
        self._save_current_config()
        self._refresh_menu()
        rumps.notification(
            "Daily Web Login",
            "yngal 寻宝",
            "已启用" if self.yngal_hunt_enabled else "已禁用",
        )

    def _on_test_feishu(self, _):
        """发送一条测试消息到飞书 webhook，结果打印到日志（不弹窗）。"""
        if not self.feishu_webhook_url:
            logging.warning("飞书测试：未配置 FEISHU_WEBHOOK_URL，请检查 .env")
            rumps.notification("Daily Web Login", "飞书推送", "未配置 webhook，请检查 .env")
            return
        now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        markdown = f"**测试推送**\n\n这是来自 Daily-Web-Login 的测试消息。\n\n时间：{now} (UTC+8)"
        logging.info("飞书测试：开始发送测试消息")
        ok, err = feishu.send_feishu_card(
            self.feishu_webhook_url,
            "Daily Web Login 测试",
            markdown,
            color=feishu.COLOR_BLUE,
        )
        if ok:
            logging.info("飞书测试：推送成功，请检查飞书群")
            rumps.notification("Daily Web Login", "飞书推送", "成功，请检查飞书群")
        else:
            logging.error("飞书测试：推送失败: %s", err)
            rumps.notification("Daily Web Login", "飞书推送", "失败，详情见日志")

    def _on_open_kf(self, _):
        """在浏览器中打开绯月论坛首页。"""
        ok = _open_url("https://bbs.kfpromax.com/")
        rumps.notification(
            "Daily Web Login",
            "打开绯月网页" + ("成功" if ok else "失败"),
            "https://bbs.kfpromax.com/",
        )

    def _on_open_yngal(self, _):
        """在浏览器中打开 yngal 首页。"""
        ok = _open_url("https://www.yngal.com/")
        rumps.notification(
            "Daily Web Login",
            "打开 yngal 网页" + ("成功" if ok else "失败"),
            "https://www.yngal.com/",
        )

    def _on_test_kf(self, _):
        """立即执行一次绯月签到，结果打印到日志（不弹窗）。"""
        if not self.kf_username or not self.kf_password:
            logging.warning("绯月测试：未配置 KF_USERNAME / KF_PASSWORD，请检查 .env")
            rumps.notification("Daily Web Login", "绯月签到", "未配置凭证，请检查 .env")
            return
        logging.info("绯月测试：开始执行 登录+签到")
        try:
            client = KfClient(self.kf_username, self.kf_password)
            result = client.run_all()
        except Exception as e:
            logging.exception("绯月测试未知异常: %s", e)
            rumps.notification("Daily Web Login", "绯月签到", "异常，详情见日志")
            return

        logging.info(
            "绯月测试结果: 登录=%s | 签到=%s | 奖励=%s | 错误=%s",
            result.get("login_msg"),
            result.get("sign_msg"),
            result.get("reward"),
            result.get("error"),
        )
        sign_short = result.get("sign_msg") or result.get("error") or "未知"
        rumps.notification("Daily Web Login", "绯月签到完成", sign_short)

    def _on_toggle_kf(self, sender):
        """切换绯月签到开关，持久化到 config.json 并刷新菜单。"""
        self.kf_enabled = not self.kf_enabled
        self._save_current_config()
        self._refresh_menu()
        rumps.notification(
            "Daily Web Login",
            "绯月签到",
            "已启用" if self.kf_enabled else "已禁用",
        )

    def _on_quit(self, _):
        rumps.quit_application()

    # ── schedule ─────────────────────────────────────────────────────────

    def _register_schedule(self):
        schedule.clear()
        schedule.every().day.at(self.schedule_time).do(self._scheduled_task)

    def _scheduled_task(self):
        """每日定时任务：1) 打开 urls 网址 → 2) yngal 签到+寻宝 → 3) 飞书推送汇总。

        串行执行，总耗时 < 10s。打开网址失败不阻塞后续 yngal 签到。
        """
        # 1. 打开网址
        opened = open_all_urls(self.urls)

        # 2. yngal 签到+寻宝
        if self.yngal_enabled and self.yngal_email and self.yngal_password:
            try:
                client = YngalClient(self.yngal_email, self.yngal_password)
                yngal_result = client.run_all(hunt_enabled=self.yngal_hunt_enabled)
            except Exception as e:
                logging.exception("yngal 执行异常")
                yngal_result = {
                    "login_ok": False, "login_msg": "",
                    "sign_ok": False, "sign_msg": f"执行异常: {e}",
                    "hunt_ok": False, "hunt_msg": "执行异常", "hunt_no_action": False,
                    "error": str(e),
                }
        else:
            yngal_result = {
                "login_ok": False, "login_msg": "",
                "sign_ok": False, "sign_msg": "未启用" if not self.yngal_enabled else "未配置凭证",
                "hunt_ok": False, "hunt_msg": "未启用", "hunt_no_action": False,
                "error": None,
            }

        # 3. 绯月签到
        if self.kf_enabled and self.kf_username and self.kf_password:
            try:
                kf_client = KfClient(self.kf_username, self.kf_password)
                kf_result = kf_client.run_all()
            except Exception as e:
                logging.exception("绯月签到异常")
                kf_result = {
                    "login_ok": False, "login_msg": "",
                    "sign_ok": False, "sign_msg": f"执行异常: {e}",
                    "sign_already_done": False, "reward": None,
                    "error": str(e),
                }
        else:
            kf_result = {
                "login_ok": False, "login_msg": "",
                "sign_ok": False, "sign_msg": "未启用" if not self.kf_enabled else "未配置凭证",
                "sign_already_done": False, "reward": None,
                "error": None,
            }

        # 4. 飞书推送汇总
        if self.feishu_notify_enabled and self.feishu_webhook_url:
            markdown, color = feishu.build_daily_summary(
                opened, len(self.urls), yngal_result, self.yngal_hunt_enabled,
                kf_result,
            )
            ok, err = feishu.send_feishu_card(
                self.feishu_webhook_url,
                "Daily Web Login 每日任务",
                markdown,
                color=color,
            )
            logging.info("飞书推送: %s (%s)", "成功" if ok else "失败", err or "")
        else:
            logging.info("飞书推送已禁用或未配置，跳过")

        rumps.notification(
            "Daily Web Login",
            "定时任务已执行",
            f"打开 {opened}/{len(self.urls)} 网址, "
            f"yngal: {yngal_result.get('sign_msg', '')}, "
            f"绯月: {kf_result.get('sign_msg', '')}",
        )

    @rumps.timer(300)
    def _tick(self, _):
        """每 5 分钟检查一次 schedule 库是否有到期任务。

        相比 30s 间隔，唤醒频率降低 10 倍；schedule 库的 catch-up 机制
        保证错过定时点后下次 tick 立即补触发。配合 accessory 策略，
        App Nap 仍可在 tick 间隙生效。
        """
        schedule.run_pending()

    def _run_immediately(self):
        """启动时立即打开网址（避免每次启动都签到+推送打扰用户）。

        yngal 签到和飞书推送只在每日定时任务 _scheduled_task 中执行。
        用户需要手动测试时，可用「测试 yngal 签到」「测试飞书推送」菜单项。
        """
        open_all_urls(self.urls)

    # ── persistence ──────────────────────────────────────────────────────

    def _save_current_config(self):
        """读取现有配置 → 更新已知字段 → 写回，保护未知字段不丢失。

        重要：原实现是全量覆写 save_config({"schedule_time":..., "urls":...})，
        会丢失任何新增字段（yngal/feishu 段）。重构后先读旧配置再更新，
        确保添加/删除网址、修改定时、切换寻宝开关时不会擦掉 yngal/feishu 配置。
        此函数被 4 处调用：_on_change_time / _on_add_url / _on_remove_url / _on_toggle_hunt。
        """
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            cfg = {}
        cfg["schedule_time"] = self.schedule_time
        cfg["urls"] = self.urls
        cfg["yngal"] = {
            "enabled": self.yngal_enabled,
            "hunt_enabled": self.yngal_hunt_enabled,
        }
        cfg["feishu"] = {
            "notify_enabled": self.feishu_notify_enabled,
        }
        cfg["kf"] = {
            "enabled": self.kf_enabled,
        }
        save_config(cfg)


# ── entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    DailyWebLoginApp().run()
