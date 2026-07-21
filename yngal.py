"""yngal 同步客户端 - 登录、签到、寻宝。

参考实现：https://github.com/cyilin36/Check-in
- 登录接口 POST /sign，表单 email + password（MD5 hexdigest）
- 签到接口 GET /addJf，header X-Auth-Token
- 寻宝接口 GET /hunt，header X-Auth-Token

设计要点：
- 同步架构（requests.Session），匹配 Daily-Web-Login 主线程同步模型
- 异常分类（AuthenticationError/ForumError/NetworkError），带 retryable 标记
- URL 安全校验（https + hostname + port + 无 userinfo），防开放重定向
- 登录失效（code 119/601）自动重新登录 + 重试一次，覆盖瞬时失效
- 不实现 5/15/30 分钟自动重试（菜单栏 App 用户可手动重试）
"""

from __future__ import annotations

import hashlib
import logging
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

YNGAL_BASE = "https://www.yngal.com/"


# ── 异常层次 ─────────────────────────────────────────────────────────────────


class YngalError(Exception):
    """yngal 业务异常基类。"""

    def __init__(self, message, *, retryable=False, code=None):
        super().__init__(message)
        self.retryable = retryable
        self.code = code


class AuthenticationError(YngalError):
    """登录失效（密码错 / token 过期）。不可重试。"""

    def __init__(self, message, code=None):
        super().__init__(message, retryable=False, code=code)


class ForumError(YngalError):
    """业务不可重试错误（如未知状态码、响应格式异常）。"""

    def __init__(self, message, code=None):
        super().__init__(message, retryable=False, code=code)


class NetworkError(YngalError):
    """网络异常 / 超时 / 非 200 响应。可重试。"""

    def __init__(self, message, code=None):
        super().__init__(message, retryable=True, code=code)


# ── 辅助函数 ─────────────────────────────────────────────────────────────────


def _validate_yngal_url(url):
    """校验 URL 必须 https + hostname==www.yngal.com + port in (None, 443) + 无 userinfo。

    防止开放重定向攻击，参考 cyilin36 的 is_safe_yngal_url。
    """
    p = urlparse(url)
    if p.scheme != "https":
        raise ValueError(f"非法 scheme: {p.scheme}")
    if p.hostname != "www.yngal.com":
        raise ValueError(f"非法 hostname: {p.hostname}")
    if p.port not in (None, 443):
        raise ValueError(f"非法 port: {p.port}")
    if p.username or p.password:
        raise ValueError("URL 不允许携带 userinfo")


def _code_is(code, *values):
    """兼容 int/str 的状态码匹配。

    yngal 接口可能返回整数或字符串类型的 code，统一用字符串比较。
    """
    for v in values:
        if str(code) == str(v):
            return True
    return False


# ── 客户端 ───────────────────────────────────────────────────────────────────


class YngalClient:
    """yngal 同步客户端。requests.Session 维持会话状态。"""

    LOGIN_URL = "https://www.yngal.com/sign"
    SIGN_URL = "https://www.yngal.com/addJf"
    HUNT_URL = "https://www.yngal.com/hunt"

    def __init__(self, email, password, timeout=30):
        # 启动时校验所有 URL（防止配置被篡改）
        _validate_yngal_url(self.LOGIN_URL)
        _validate_yngal_url(self.SIGN_URL)
        _validate_yngal_url(self.HUNT_URL)

        self.email = email
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        # 关键：yngal 服务器根据 Accept 头决定返回 JSON 还是 HTML 首页。
        # 不设置 Accept 头时 /addJf 和 /hunt 会返回 HTML 首页（SPA fallback），
        # 导致 JSON 解析失败。必须设置 Accept: application/json。
        # User-Agent 也需要设置为浏览器 UA，避免被服务器拒绝。
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.yngal.com/",
        })
        self.token = None
        self.is_vip = False

    def _md5_password(self):
        """密码 MD5 摘要（yngal 协议要求客户端计算 MD5）。

        usedforsecurity=False 标记避免 FIPS 环境审计警告，
        参考cyilin36/checkin.py 实现。
        """
        return hashlib.md5(
            self.password.encode("utf-8"), usedforsecurity=False
        ).hexdigest()

    def login(self):
        """登录，成功后设置 self.token 和 self.is_vip。"""
        try:
            resp = self.session.post(
                self.LOGIN_URL,
                data={"email": self.email, "password": self._md5_password()},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise NetworkError(f"登录网络异常: {e}")
        except ValueError as e:
            raise NetworkError(f"登录响应非 JSON: {e}")

        if not _code_is(data.get("code"), 0):
            raise AuthenticationError(
                f"登录失败 code={data.get('code')} msg={data.get('msg')}",
                code=data.get("code"),
            )

        obj = data.get("obj") or {}
        self.token = obj.get("token")
        if not self.token or not isinstance(self.token, str):
            raise AuthenticationError("登录成功但未返回有效 token")

        # vstatus 为 1/"1" 是 VIP，奖励 2 硬币，普通用户 1 硬币
        self.is_vip = _code_is(obj.get("vstatus"), 1)
        logger.info("yngal 登录成功, VIP=%s", self.is_vip)
        return self.token

    def _auth_get(self, url, *, label):
        """带 X-Auth-Token 的 GET 请求，返回 (code, obj, msg)。

        若 token 不存在则先登录。label 用于日志标识。
        """
        if not self.token:
            self.login()
        headers = {"X-Auth-Token": self.token}
        try:
            resp = self.session.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise NetworkError(f"{label} 网络异常: {e}")
        except ValueError as e:
            raise NetworkError(f"{label} 响应非 JSON: {e}")
        return data.get("code"), data.get("obj"), data.get("msg")

    def sign(self):
        """签到。返回 dict: {success, message, already_done}。

        状态码：
        - 0: 签到成功
        - 10: 今日已签到
        - 119: 登录失效（重新登录后重试一次）
        """
        code, _obj, msg = self._auth_get(self.SIGN_URL, label="签到")

        if _code_is(code, 0):
            coins = 2 if self.is_vip else 1
            return {"success": True, "message": f"签到成功 +{coins} 硬币", "already_done": False}
        if _code_is(code, 10):
            return {"success": True, "message": "今日已签到", "already_done": True}
        if _code_is(code, 119):
            # 登录失效，重新登录后重试一次
            logger.warning("签到 token 失效，重新登录重试")
            self.token = None
            self.login()
            code2, _obj2, msg2 = self._auth_get(self.SIGN_URL, label="签到(重试)")
            if _code_is(code2, 0):
                coins = 2 if self.is_vip else 1
                return {"success": True, "message": f"签到成功(重试) +{coins} 硬币", "already_done": False}
            if _code_is(code2, 10):
                return {"success": True, "message": "今日已签到(重试)", "already_done": True}
            raise ForumError(f"签到重试仍失败 code={code2} msg={msg2}", code=code2)
        raise ForumError(f"签到失败 code={code} msg={msg}", code=code)

    def hunt(self):
        """寻宝。返回 dict: {success, message, reward, no_action}。

        状态码：
        - 0/200: 寻宝成功
        - 601: 登录失效（重新登录后重试一次）
        - 602: 未设置守护灵出战位（不算失败也不算成功，no_action=True）
        - 688: 今日寻宝已完成

        奖励解析：
        - wrap 字段兼容 int/str，排除 bool（Python 中 True==1, False==0）
        - wrap==10 时奖励"硬币 +5"，其他值为"积分 +{amount}"
        """
        code, obj, msg = self._auth_get(self.HUNT_URL, label="寻宝")

        if _code_is(code, 0, 200):
            wrap = obj.get("wrap") if isinstance(obj, dict) else None
            # wrap 兼容 int/str，排除 bool（Python 中 True==1, False==0）
            if isinstance(wrap, bool):
                reward = None
            else:
                try:
                    wrap_int = int(wrap)
                except (TypeError, ValueError):
                    reward = None
                else:
                    if wrap_int < 0:
                        reward = None
                    else:
                        reward = "硬币 +5" if wrap_int == 10 else f"积分 +{wrap_int}"
            message = f"寻宝成功 {reward}".strip() if reward else "寻宝成功"
            return {"success": True, "message": message, "reward": reward, "no_action": False}

        if _code_is(code, 601):
            logger.warning("寻宝 token 失效，重新登录重试")
            self.token = None
            self.login()
            code2, obj2, msg2 = self._auth_get(self.HUNT_URL, label="寻宝(重试)")
            if _code_is(code2, 0, 200):
                return {"success": True, "message": "寻宝成功(重试)", "reward": None, "no_action": False}
            raise ForumError(f"寻宝重试仍失败 code={code2} msg={msg2}", code=code2)

        if _code_is(code, 602):
            # 未设置守护灵出战位：不算失败也不算成功
            return {
                "success": False,
                "message": "未设置守护灵出战位，跳过",
                "reward": None,
                "no_action": True,
            }

        if _code_is(code, 688):
            return {"success": True, "message": "今日寻宝已完成", "reward": None, "no_action": False}

        raise ForumError(f"寻宝失败 code={code} msg={msg}", code=code)

    def run_all(self, *, hunt_enabled=True):
        """执行 登录 → 签到 → (可选)寻宝，返回汇总 dict。

        任何步骤的异常都被捕获并填入 result，不会抛出。
        调用方只需检查 result['error'] 是否非空即可判断整体成败。

        返回结构：
        {
            login_ok: bool, login_msg: str,
            sign_ok: bool, sign_msg: str,
            hunt_ok: bool, hunt_msg: str, hunt_no_action: bool,
            error: str | None,
        }
        """
        result = {
            "login_ok": False, "login_msg": "",
            "sign_ok": False, "sign_msg": "",
            "hunt_ok": False, "hunt_msg": "", "hunt_no_action": False,
            "error": None,
        }

        # 1. 登录
        try:
            self.login()
            result["login_ok"] = True
            result["login_msg"] = f"登录成功 (VIP={self.is_vip})"
        except YngalError as e:
            result["error"] = f"登录失败: {e}"
            return result

        # 2. 签到
        try:
            s = self.sign()
            result["sign_ok"] = s["success"]
            result["sign_msg"] = s["message"]
        except YngalError as e:
            result["sign_msg"] = f"签到异常: {e}"

        # 3. 寻宝（可选）
        if hunt_enabled:
            try:
                h = self.hunt()
                result["hunt_ok"] = h["success"]
                result["hunt_msg"] = h["message"]
                result["hunt_no_action"] = h["no_action"]
            except YngalError as e:
                result["hunt_msg"] = f"寻宝异常: {e}"
        else:
            result["hunt_msg"] = "已关闭"

        return result
