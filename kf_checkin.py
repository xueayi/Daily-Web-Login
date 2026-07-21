"""绯月论坛（bbs.kfpromax.com）每日签到客户端。

移植自 https://github.com/cyilin36/Check-in (checkin.py)，适配本项目架构：
- 同步架构（requests.Session），匹配 Daily-Web-Login 主线程同步模型
- 异常分类（AuthenticationError/ForumError/NetworkError），带 retryable 标记
- URL 安全校验，防开放重定向
- GBK/GB18030 编码处理

论坛背景：
- 运行在传统 GBK/PHP 环境，requests 字典默认 UTF-8 会导致中文用户名变乱码
- 因此登录表单体需显式以 gb18030 编码
- HTML 响应也以 gb18030 解码
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

KF_BASE = "https://bbs.kfpromax.com/"

# ── 异常层次 ─────────────────────────────────────────────────────────────────


class KfError(Exception):
    """绯月签到业务异常基类。"""

    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class AuthenticationError(KfError):
    """登录失败（密码错 / 登录状态失效）。不可重试。"""

    def __init__(self, message):
        super().__init__(message, retryable=False)


class ForumError(KfError):
    """业务不可重试错误（如无法识别页面状态、URL 不安全）。"""

    def __init__(self, message):
        super().__init__(message, retryable=False)


class NetworkError(KfError):
    """网络异常 / 超时 / 非 200 响应。可重试。"""

    def __init__(self, message):
        super().__init__(message, retryable=True)


# ── 辅助函数 ─────────────────────────────────────────────────────────────────


def _validate_kf_url(url):
    """校验 URL 必须 https + hostname==bbs.kfpromax.com + port in (None, 443) + 无 userinfo。"""
    p = urlparse(url)
    if p.scheme != "https":
        raise ValueError(f"非法 scheme: {p.scheme}")
    if p.hostname != "bbs.kfpromax.com":
        raise ValueError(f"非法 hostname: {p.hostname}")
    if p.port not in (None, 443):
        raise ValueError(f"非法 port: {p.port}")
    if p.username or p.password:
        raise ValueError("URL 不允许携带 userinfo")


def _decode_html(response):
    """该站声明 GBK；GB18030 是其兼容超集。"""
    return response.content.decode("gb18030", errors="replace")


def _normalize_text(value):
    return re.sub(r"\s+", " ", value).strip()


def _is_login_page(html):
    """通过检查密码输入框判断是否还在登录页面。"""
    soup = BeautifulSoup(html, "html.parser")
    return soup.find("input", attrs={"name": "pwpwd"}) is not None


def _find_account_url(html, page_url):
    """从首页 HTML 中定位用户账户链接（通过 KFB 余额文本）。"""
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        text = _normalize_text(anchor.get_text(" ", strip=True))
        if re.search(r"\d[\d,.]*\s*KFB\b", text, flags=re.IGNORECASE):
            candidate = urljoin(page_url, anchor["href"])
            # 安全校验
            try:
                _validate_kf_url(candidate)
                return candidate
            except ValueError:
                raise ForumError(
                    f"账户入口链接不是论坛 HTTPS 同源链接，已拒绝访问: {candidate}"
                )
    return None


def _parse_reward_page(html, page_url):
    """解析奖励页面，返回 (state, claim_url, reward_text)。

    state: "available" | "claimed" | "unknown"
    """
    soup = BeautifulSoup(html, "html.parser")
    full_text = _normalize_text(soup.get_text(" ", strip=True))

    # 检查是否已领取
    already_patterns = (
        r"(?:今日|今天).{0,12}(?:已经|已).{0,4}领(?:取|过)",
        r"(?:已经|已).{0,4}领(?:取|过).{0,12}(?:今日|今天)",
        r"(?:今日|今天).{0,10}领取完毕",
    )
    if any(re.search(p, full_text) for p in already_patterns):
        return "claimed", None, None

    # 查找可领取链接
    for anchor in soup.find_all("a", href=True):
        anchor_text = _normalize_text(anchor.get_text(" ", strip=True))
        container = anchor.find_parent(["td", "div", "p", "li"]) or anchor.parent
        context = (
            _normalize_text(container.get_text(" ", strip=True))
            if container
            else anchor_text
        )
        if "可以领取" not in context:
            continue
        if "点击这里" not in anchor_text and "领取" not in anchor_text:
            continue

        claim_url = urljoin(page_url, anchor["href"])
        try:
            _validate_kf_url(claim_url)
        except ValueError:
            raise ForumError(
                f"奖励领取链接不是论坛 HTTPS 同源链接，已拒绝访问: {claim_url}"
            )

        reward_match = re.search(r"可以领取\s*(.*?)\s*请点击这里", context)
        reward_text = (
            _normalize_text(reward_match.group(1))
            if reward_match
            else context[:160]
        )
        return "available", claim_url, reward_text

    return "unknown", None, None


# ── 客户端 ───────────────────────────────────────────────────────────────────


class KfClient:
    """绯月论坛签到客户端。requests.Session 维持会话状态。"""

    LOGIN_URL = urljoin(KF_BASE, "login.php")
    INDEX_URL = urljoin(KF_BASE, "index.php")
    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, username, password, timeout=30):
        # 启动时校验所有 URL
        _validate_kf_url(self.LOGIN_URL)
        _validate_kf_url(self.INDEX_URL)

        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9",
        })

    def _get(self, url):
        """GET 请求，自动 raise_for_status。"""
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    def login(self):
        """登录绯月论坛。成功后返回 (index_html, account_url)。

        注意：论坛运行在传统 GBK/PHP 环境，中文用户名需以 GBK 编码提交。
        """
        # 先访问登录页获取 cookie
        self._get(self.LOGIN_URL)

        payload = {
            "forward": "",
            "jumpurl": self.INDEX_URL,
            "step": "2",
            "lgt": "1",
            "hideid": "0",
            "cktime": "31536000",
            "pwuser": self.username,
            "pwpwd": self.password,
            "submit": "登录",
        }
        # 显式生成 GBK 表单体（requests 字典默认 UTF-8）
        encoded_payload = urlencode(payload, encoding="gb18030", errors="strict")

        try:
            resp = self.session.post(
                self.LOGIN_URL,
                data=encoded_payload,
                headers={
                    "Referer": self.LOGIN_URL,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise NetworkError(f"登录网络异常: {e}")

        # 以首页作为权威认证状态
        try:
            index_resp = self._get(self.INDEX_URL)
        except requests.RequestException as e:
            raise NetworkError(f"获取首页失败: {e}")

        index_html = _decode_html(index_resp)
        account_url = _find_account_url(index_html, index_resp.url)

        if _is_login_page(index_html) or account_url is None:
            raise AuthenticationError("登录失败，或登录后的账户入口无法识别")

        logger.info("绯月登录成功, account_url=%s", account_url)
        return index_html, account_url

    def checkin(self):
        """执行签到流程：登录 → 解析奖励页 → 领取 → 验证。返回结构化结果 dict。"""
        # 1. 登录
        try:
            _, account_url = self.login()
        except KfError:
            raise

        # 2. 读取账户页
        try:
            account_resp = self._get(account_url)
        except requests.RequestException as e:
            raise NetworkError(f"获取账户页失败: {e}")

        account_html = _decode_html(account_resp)
        if _is_login_page(account_html):
            raise AuthenticationError("读取奖励页面时登录状态已失效")

        # 3. 解析奖励状态
        state, claim_url, reward_text = _parse_reward_page(
            account_html, account_resp.url
        )

        if state == "claimed":
            return {
                "success": True,
                "message": "今日已领取",
                "already_done": True,
                "reward": None,
            }
        if state == "unknown" or not claim_url:
            raise ForumError(
                "无法在账户页面识别领取状态；为避免误操作，本次未点击任何链接"
            )

        logger.info("检测到可领取奖励：%s", reward_text or "金额未解析")

        # 4. 点击领取
        try:
            claim_resp = self._get(claim_url)
        except requests.RequestException as e:
            raise NetworkError(f"领取链接访问失败: {e}")

        if _is_login_page(_decode_html(claim_resp)):
            raise AuthenticationError("领取过程中登录状态已失效")

        # 5. 验证结果
        try:
            verify_resp = self._get(account_url)
        except requests.RequestException as e:
            raise NetworkError(f"验证页面访问失败: {e}")

        verified_state, _, _ = _parse_reward_page(
            _decode_html(verify_resp), verify_resp.url
        )

        if verified_state == "claimed":
            return {
                "success": True,
                "message": f"奖励领取成功{f' ({reward_text})' if reward_text else ''}",
                "already_done": False,
                "reward": reward_text,
            }
        if verified_state == "available":
            raise ForumError("领取请求完成，但页面仍显示可以领取")
        raise ForumError("领取后无法从账户页面确认结果")

    def run_all(self):
        """执行 登录 → 签到，返回汇总 dict。

        异常被捕获并填入 result，不会抛出。
        调用方只需检查 result['error'] 是否非空即可判断整体成败。

        返回结构：
        {
            login_ok: bool, login_msg: str,
            sign_ok: bool, sign_msg: str, sign_already_done: bool,
            reward: str | None,
            error: str | None,
        }
        """
        result = {
            "login_ok": False,
            "login_msg": "",
            "sign_ok": False,
            "sign_msg": "",
            "sign_already_done": False,
            "reward": None,
            "error": None,
        }

        try:
            cr = self.checkin()
            result["login_ok"] = True
            result["login_msg"] = "登录成功"
            result["sign_ok"] = cr["success"]
            result["sign_msg"] = cr["message"]
            result["sign_already_done"] = cr.get("already_done", False)
            result["reward"] = cr.get("reward")
        except AuthenticationError as e:
            result["login_msg"] = ""
            result["sign_msg"] = ""
            result["error"] = f"登录失败: {e}"
        except ForumError as e:
            result["login_ok"] = True
            result["login_msg"] = "登录成功"
            result["sign_msg"] = str(e)
            result["error"] = f"签到失败: {e}"
        except NetworkError as e:
            result["sign_msg"] = str(e)
            result["error"] = f"网络异常: {e}"
        except Exception as e:
            logger.exception("绯月签到未知异常")
            result["error"] = f"未知异常: {e}"

        return result
