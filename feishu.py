"""飞书 webhook 交互式卡片推送（同步版，基于 requests）。

参考实现：/Volumes/WD_SN730/01_dev/vrchat_status_push/src/feishu_card.py
原项目用 aiohttp（异步），本模块改为 requests（同步）以匹配 Daily-Web-Login 架构。

飞书卡片 schema 2.0 文档：
https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/feishu-cards/card-json-structure

消息体结构：
{
    "msg_type": "interactive",
    "card": {
        "schema": "2.0",
        "config": {"update_multi": true},
        "header": {
            "title": {"tag": "plain_text", "content": "标题"},
            "template": "green"  # green/blue/yellow/orange/red
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 12px 12px",
            "elements": [
                {"tag": "markdown", "content": "正文", "text_align": "left", "text_size": "normal_v2"}
            ]
        }
    }
}

飞书业务码校验：HTTP 200 但响应 JSON 中 code != 0 也算失败。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 30

# 状态 → 卡片 header 颜色（template 字段）
COLOR_GREEN = "green"    # 全部成功
COLOR_BLUE = "blue"      # 信息 / 测试推送
COLOR_YELLOW = "yellow"  # 警告（如未设置守护灵出战位）
COLOR_ORANGE = "orange"  # 部分失败
COLOR_RED = "red"        # 全部失败


def build_card(title, markdown_content, color=COLOR_GREEN):
    """构建飞书 schema 2.0 交互式卡片 payload。

    Args:
        title: 卡片标题
        markdown_content: markdown 正文（支持飞书 markdown 语法）
        color: header 颜色，取值见 COLOR_* 常量

    Returns:
        飞书 webhook 请求体 dict
    """
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "elements": [
                    {
                        "tag": "markdown",
                        "content": markdown_content,
                        "text_align": "left",
                        "text_size": "normal_v2",
                    }
                ],
            },
        },
    }


def send_feishu_card(webhook_url, title, markdown_content, color=COLOR_GREEN, timeout=TIMEOUT):
    """发送飞书卡片消息。

    双重校验：HTTP 200 + 响应 JSON 中 code == 0。
    不重试（同步主线程调用，重试会卡 UI）。

    Args:
        webhook_url: 飞书 webhook 完整 URL
        title: 卡片标题
        markdown_content: markdown 正文
        color: header 颜色
        timeout: HTTP 超时秒数

    Returns:
        (success: bool, error_msg: str) - 成功时 error_msg 为空字符串
    """
    if not webhook_url:
        return False, "FEISHU_WEBHOOK_URL 未配置"

    payload = build_card(title, markdown_content, color)
    try:
        resp = requests.post(webhook_url, json=payload, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("飞书推送 HTTP 异常: %s", e)
        return False, f"HTTP 异常: {e}"

    try:
        result = resp.json()
    except ValueError:
        logger.error("飞书响应非 JSON: %s", resp.text[:200])
        return False, f"响应非 JSON: {resp.text[:200]}"

    # 飞书业务码校验：HTTP 200 但 code != 0 也算失败
    if result.get("code", -1) != 0:
        msg = f"飞书业务码错误 code={result.get('code')} msg={result.get('msg')}"
        logger.error(msg)
        return False, msg

    logger.info("飞书推送成功: %s (响应: %s)", title, resp.text[:200])
    return True, ""


def build_daily_summary(opened_count, total_urls, yngal_result, hunt_enabled,
                       kf_result=None):
    """构建每日汇总卡片的 markdown 正文 + 颜色。

    Args:
        opened_count: 成功打开的网址数
        total_urls: 总网址数
        yngal_result: yngal.YngalClient.run_all() 的返回值
        hunt_enabled: 是否启用了寻宝
        kf_result: kf_checkin.KfClient.run_all() 的返回值（可选）

    Returns:
        (markdown_content: str, color: str)
    """
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"**打开网址：{opened_count}/{total_urls}**",
        "",
        "**yngal 签到**",
        yngal_result.get("sign_msg") or "未执行",
        "",
    ]

    if hunt_enabled:
        lines.append("**yngal 寻宝**")
        lines.append(yngal_result.get("hunt_msg") or "未执行")
        lines.append("")

    # 绯月签到
    if kf_result is not None:
        lines.append("**绯月签到**")
        kf_msg = kf_result.get("sign_msg") or "未执行"
        # 只有在 sign_msg 尚未包含奖励信息时才追加
        reward = kf_result.get("reward")
        if reward and reward not in kf_msg:
            kf_msg = f"{kf_msg} ({reward})"
        lines.append(kf_msg)
        lines.append("")

    # 汇总错误
    all_errors = []
    if yngal_result.get("error"):
        all_errors.append(f"yngal: {yngal_result['error']}")
    if kf_result and kf_result.get("error"):
        all_errors.append(f"绯月: {kf_result['error']}")
    if all_errors:
        lines.append("**错误**")
        for err in all_errors:
            lines.append(err)
        lines.append("")

    lines.append(f"执行时间：{now} (UTC+8)")

    # 颜色判定逻辑
    yngal_error = bool(yngal_result.get("error"))
    kf_error = bool(kf_result and kf_result.get("error"))
    has_error = yngal_error or kf_error

    yngal_sign_failed = (
        not yngal_result.get("sign_ok")
        and not yngal_result.get("sign_msg", "").startswith("今日已签到")
    )
    hunt_failed = (
        hunt_enabled
        and not yngal_result.get("hunt_ok")
        and not yngal_result.get("hunt_no_action")
    )
    kf_sign_failed = (
        kf_result is not None
        and not kf_result.get("sign_ok")
        and not kf_result.get("sign_msg", "").startswith("今日已领取")
        and not kf_error  # kf_error 已在 has_error 中考虑
    )
    open_failed = opened_count < total_urls

    if has_error or (yngal_sign_failed and kf_sign_failed):
        color = COLOR_RED
    elif yngal_sign_failed or hunt_failed or kf_sign_failed or open_failed:
        color = COLOR_ORANGE
    elif yngal_result.get("hunt_no_action"):
        color = COLOR_YELLOW
    else:
        color = COLOR_GREEN

    return "\n".join(lines), color
