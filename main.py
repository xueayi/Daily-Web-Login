#!/usr/bin/env python3
"""Daily Web Login - macOS 菜单栏应用，每日定时用默认浏览器打开配置的网址。"""

import json
import os
import webbrowser
from datetime import datetime

import rumps
import schedule

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# ── helpers ──────────────────────────────────────────────────────────────────


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def open_all_urls(urls=None):
    """用默认浏览器逐个打开所有网址。"""
    if urls is None:
        urls = load_config().get("urls", [])
    for url in urls:
        webbrowser.open_new_tab(url)


# ── app ──────────────────────────────────────────────────────────────────────


class DailyWebLoginApp(rumps.App):

    def __init__(self):
        cfg = load_config()
        self.schedule_time = cfg.get("schedule_time", "09:00")
        self.urls = cfg.get("urls", [])

        super().__init__(
            name="DailyWebLogin",
            title="🌐",
            menu=self._build_menu(),
            quit_button=None,
        )

        self._register_schedule()
        self._run_immediately()

    # ── menu ─────────────────────────────────────────────────────────────

    def _build_menu(self):
        items = []

        items.append(rumps.MenuItem("立即打开全部网址", callback=self._on_open_all))
        items.append(rumps.separator)

        time_item = rumps.MenuItem(f"⏰ 定时: {self.schedule_time}")
        time_item.set_callback(None)
        items.append(time_item)
        items.append(rumps.separator)

        url_submenu = rumps.MenuItem("网址列表")
        for url in self.urls:
            url_submenu.add(rumps.MenuItem(url, callback=self._on_open_single))
        items.append(url_submenu)
        items.append(rumps.separator)

        items.append(rumps.MenuItem("修改定时时间", callback=self._on_change_time))
        items.append(rumps.MenuItem("添加网址", callback=self._on_add_url))
        items.append(rumps.MenuItem("删除网址", callback=self._on_remove_url))
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
        webbrowser.open_new_tab(sender.title)

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

    def _on_quit(self, _):
        rumps.quit_application()

    # ── schedule ─────────────────────────────────────────────────────────

    def _register_schedule(self):
        schedule.clear()
        schedule.every().day.at(self.schedule_time).do(self._scheduled_open)

    def _scheduled_open(self):
        open_all_urls(self.urls)
        rumps.notification(
            "Daily Web Login",
            "定时任务已执行",
            f"已打开 {len(self.urls)} 个网址 ({self.schedule_time})",
        )

    @rumps.timer(30)
    def _tick(self, _):
        schedule.run_pending()

    def _run_immediately(self):
        open_all_urls(self.urls)

    # ── persistence ──────────────────────────────────────────────────────

    def _save_current_config(self):
        save_config({"schedule_time": self.schedule_time, "urls": self.urls})


# ── entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    DailyWebLoginApp().run()
