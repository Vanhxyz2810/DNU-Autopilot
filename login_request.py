#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
login_request.py — Đăng nhập SSO Microsoft 365 vào online.dainam.edu.vn
bằng HTTP thuần (requests), KHÔNG mở browser.

Ý tưởng (replay luồng interactive của Azure AD):
    1. GET /dnu-lms-sso  -> theo redirect tới trang login Microsoft, trong HTML
       có object JS `$Config` chứa flowToken (sFT) + canary + ctx + urlPost.
    2. POST email + password + các token đó tới urlPost của Microsoft.
    3. Nếu gặp màn "Stay signed in?" (KMSI) -> POST tiếp để xác nhận.
    4. Microsoft trả code -> gateway admin.dnu.edu.vn đổi code -> callback
       online.dainam.edu.vn/dnu-lms-sso/callback set cookie phiên Laravel.
    5. Lấy cookie header + meta csrf-token, lưu captures/creds.json (đúng định
       dạng mà run_flow.py / auto_complete.py đang dùng).

AN TOÀN:
    - Đọc email/mật khẩu từ .env (đã .gitignore).
    - KHÔNG BAO GIỜ in mật khẩu ra log.
    - Điều kiện hoạt động: tài khoản KHÔNG bật MFA/captcha. Nếu Microsoft trả
      về MFA/captcha, script dừng và khuyến nghị dùng login_auto.py (browser).

DÙNG:
    ./.venv/Scripts/python.exe login_request.py
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import random
import re
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlsplit, parse_qs

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ─────────────────────────── Cấu hình (không hardcode rải rác) ──────────────
@dataclass(frozen=True)
class Config:
    start_url: str = "https://online.dainam.edu.vn/dnu-lms-sso"
    dashboard_host: str = "online.dainam.edu.vn"
    aad_host: str = "login.microsoftonline.com"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    )
    timeout: int = 30
    max_retries: int = 3
    backoff_base: float = 1.5
    creds_path: str = "captures/creds.json"
    # Khoảng nghỉ "giống người" giữa các bước (giây)
    human_delay: tuple[float, float] = (0.4, 1.1)


CFG = Config()

# ─────────────────────────── Logging ───────────────────────────────────────
logger = logging.getLogger("login_request")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                      datefmt="%H:%M:%S"))
    logger.addHandler(_h)


class LoginError(Exception):
    """Lỗi nghiệp vụ trong quá trình đăng nhập (đã có thông điệp rõ ràng)."""


class MFARequiredError(LoginError):
    """Microsoft yêu cầu MFA/captcha -> không thể login thuần request."""


# ─────────────────────────── Tiện ích ──────────────────────────────────────
def load_env(path: str = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def _human_pause() -> None:
    time.sleep(random.uniform(*CFG.human_delay))


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except Exception:
        return ""


# ─────────────────────────── Client Azure AD ───────────────────────────────
@dataclass
class AADConfig:
    """Trích từ object `$Config` trên trang login Microsoft."""
    flow_token: str
    canary: str
    ctx: str
    url_post: str          # tuyệt đối
    ppft_name: str = "flowToken"


class AADClient:
    """Trừu tượng hoá thao tác với Azure AD (login.microsoftonline.com).

    Mỗi method chỉ làm một việc để dễ test/bảo trì.
    """

    # Greedy tới `};` CUỐI: object $Config có nhiều object lồng nhau nên
    # non-greedy sẽ cắt ngay `}` đầu tiên và làm mất sFT/sCtx.
    # $Config chứa object lồng nhau + có thể có string chứa '{' '}', nên
    # regex đơn thuần không đáng tin. Dùng đếm ngoặc cân bằng (bỏ qua ngoặc
    # nằm trong chuỗi) để lấy chính xác object.
    _CONFIG_START = re.compile(r"\$Config\s*=\s*{", re.S)

    def __init__(self, session: requests.Session):
        self._s = session

    @staticmethod
    def _extract_config_object(page_html: str) -> Optional[str]:
        m = AADClient._CONFIG_START.search(page_html)
        if not m:
            return None
        start = m.end() - 1  # vị trí dấu '{'
        depth = 0
        in_str = False
        quote = ""
        escaped = False
        for i in range(start, len(page_html)):
            ch = page_html[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    in_str = False
                continue
            if ch in ('"', "'"):
                in_str = True
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return page_html[start:i + 1]
        return None

    @staticmethod
    def parse_config(page_url: str, page_html: str) -> AADConfig:
        """Bóc `$Config` -> AADConfig. Ném LoginError nếu không parse được."""
        raw = AADClient._extract_config_object(page_html)
        if not raw:
            # Có thể là trang MFA/captcha hoặc lỗi -> chẩn đoán
            if re.search(r"(captcha|arkose|funcaptcha)", page_html, re.I):
                raise MFARequiredError("Microsoft yêu cầu CAPTCHA.")
            raise LoginError("Không tìm thấy $Config trên trang login Microsoft.")
        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError as e:
            raise LoginError(f"$Config không phải JSON hợp lệ: {e}") from e

        url_post = cfg.get("urlPost") or ""
        if url_post and not url_post.startswith("http"):
            url_post = urljoin(page_url, url_post)
        if not url_post:
            raise LoginError("Thiếu urlPost trong $Config.")
        # LƯU Ý: trang 'sso_reload' đầu tiên CHƯA có sFT/sCtx (chúng chỉ xuất
        # hiện ở trang login thật sau khi GET urlPost). Cho phép rỗng ở đây;
        # DNUSSOClient.authenticate sẽ reload cho tới khi có đủ token.
        return AADConfig(
            flow_token=cfg.get("sFT") or "",
            canary=cfg.get("canary") or "",
            ctx=cfg.get("sCtx") or "",
            url_post=url_post,
            ppft_name=cfg.get("sFTName", "flowToken"),
        )

    def get_credential_type(self, email: str, flow_token: str, canary: str,
                            ctx: str) -> dict:
        """Gọi GetCredentialType.

        QUAN TRỌNG: response trả về một `FlowToken` MỚI — đây mới là token
        hợp lệ cho bước submit_password() tiếp theo, KHÔNG phải sFT ban đầu
        lấy từ trang login (đã xác nhận bằng capture thật: flowToken trong
        POST /login trùng khớp chính xác với response['FlowToken'] ở đây,
        khác hoàn toàn với sFT gốc).
        """
        url = f"https://{CFG.aad_host}/common/GetCredentialType?mkt=en-US"
        payload = {
            "username": email,
            "isOtherIdpSupported": True,
            "checkPhones": True,
            "isRemoteNGCSupported": True,
            "isCookieBannerShown": False,
            "isFidoSupported": True,
            "originalRequest": ctx,
            "flowToken": flow_token,
        }
        headers = {"Content-Type": "application/json; charset=UTF-8",
                   "canary": canary}
        try:
            r = self._s.post(url, json=payload, headers=headers, timeout=CFG.timeout)
            if r.ok:
                return r.json()
        except Exception as e:  # noqa: BLE001 - best effort, không chặn luồng
            logger.debug("GetCredentialType bỏ qua (%s)", e)
        return {}

    def submit_password(self, aad: AADConfig, email: str, password: str) -> requests.Response:
        """POST email + mật khẩu tới urlPost. Trả về response sau redirect."""
        payload = {
            "login": email,
            "loginfmt": email,
            "passwd": password,
            "ctx": aad.ctx,
            "flowToken": aad.flow_token,
            "canary": aad.canary,
            "hpgrequestid": "",
            "LoginOptions": "3",
            "type": "11",
            "NewUser": "1",
            "i13": "0",
            "i19": str(random.randint(2000, 9000)),
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded",
                   "Referer": aad.url_post}
        # KHÔNG log payload (chứa mật khẩu)
        r = self._s.post(aad.url_post, data=payload, headers=headers,
                         timeout=CFG.timeout, allow_redirects=True)
        return r

    def follow_auto_post(self, resp: requests.Response) -> tuple[requests.Response, bool]:
        """Mô phỏng JS auto-submit của các trang trung gian AAD.

        Sau khi POST mật khẩu, AAD thường trả về trang 'BssoInterrupt' /
        'Redirecting' (title=Redirecting) chứa $Config.oPostParams + một form
        <input> được JS tự submit tới urlPost. Vì requests không chạy JS, ta
        tự đọc oPostParams rồi POST lại.

        Trả về (response_mới, da_repost). da_repost=False nếu không phải
        trang auto-post (để vòng lặp biết dừng).
        """
        if _host(resp.url) != CFG.aad_host:
            return resp, False
        raw = self._extract_config_object(resp.text)
        if not raw:
            return resp, False
        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError:
            return resp, False
        post_params = cfg.get("oPostParams")
        url_post = cfg.get("urlPost") or ""
        if not (isinstance(post_params, dict) and url_post):
            return resp, False
        if not url_post.startswith("http"):
            url_post = urljoin(resp.url, url_post)
        # oPostParams là payload hoàn chỉnh (đã có token mới). Giữ nguyên.
        headers = {"Content-Type": "application/x-www-form-urlencoded",
                   "Referer": resp.url}
        r = self._s.post(url_post, data=post_params, headers=headers,
                         timeout=CFG.timeout, allow_redirects=True)
        return r, True

    def maybe_confirm_kmsi(self, resp: requests.Response) -> requests.Response:
        """Nếu đang ở màn 'Stay signed in?' -> POST xác nhận để lấy code."""
        if _host(resp.url) != CFG.aad_host:
            return resp  # đã rời AAD, không phải KMSI
        body = resp.text
        if "kmsi" not in body.lower() and "Stay signed in" not in body:
            return resp
        try:
            aad = self.parse_config(resp.url, body)
        except LoginError:
            return resp
        payload = {
            "LoginOptions": "1",       # 1 = Yes, giữ đăng nhập
            "ctx": aad.ctx,
            "flowToken": aad.flow_token,
            "canary": aad.canary,
            "type": "28",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded",
                   "Referer": resp.url}
        logger.info("→ Xác nhận 'Stay signed in?' (KMSI)...")
        return self._s.post(aad.url_post, data=payload, headers=headers,
                            timeout=CFG.timeout, allow_redirects=True)


# ─────────────────────────── Client SSO DNU ────────────────────────────────
class DNUSSOClient:
    """Điều phối toàn bộ luồng SSO end-to-end."""

    def __init__(self):
        self._s = requests.Session()
        self._s.headers.update({
            "User-Agent": CFG.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
        })
        self._aad = AADClient(self._s)

    # -- các bước --------------------------------------------------------
    def _get_with_retry(self, url: str) -> requests.Response:
        last: Optional[Exception] = None
        for attempt in range(1, CFG.max_retries + 1):
            try:
                return self._s.get(url, timeout=CFG.timeout, allow_redirects=True)
            except requests.RequestException as e:
                last = e
                wait = CFG.backoff_base ** attempt
                logger.warning("GET lỗi (lần %d/%d): %s — thử lại sau %.1fs",
                               attempt, CFG.max_retries, e, wait)
                time.sleep(wait)
        raise LoginError(f"Không GET được {url}: {last}")

    def start_sso(self) -> requests.Response:
        """GET điểm khởi đầu, theo redirect tới trang login Microsoft."""
        logger.info("→ Khởi tạo SSO: %s", CFG.start_url)
        resp = self._get_with_retry(CFG.start_url)
        if CFG.dashboard_host in _host(resp.url) and "/dnu-lms-sso" not in resp.url:
            logger.info("✅ Đã có phiên sẵn — không cần đăng nhập lại.")
            return resp
        if _host(resp.url) != CFG.aad_host:
            raise LoginError(f"Không tới được trang login Microsoft (đang ở {resp.url}).")
        return resp

    def _resolve_login_form(self, page: requests.Response) -> AADConfig:
        """Từ trang AAD đầu tiên (thường là 'sso_reload' chưa có token), lặp
        GET urlPost cho tới khi lấy được trang login thật có sFT + sCtx.

        Azure AD dùng trang trung gian sso_reload để thử Desktop-SSO ngầm;
        trang này chỉ chứa urlPost trỏ tới chính nó, GET lại sẽ ra form login.
        """
        aad = self._aad.parse_config(page.url, page.text)
        for attempt in range(1, 6):
            if aad.flow_token and aad.ctx:
                return aad
            logger.info("→ Trang trung gian (sso_reload) — tải trang login thật (lần %d)...",
                        attempt)
            _human_pause()
            resp = self._s.get(aad.url_post, timeout=CFG.timeout, allow_redirects=True)
            if CFG.dashboard_host in _host(resp.url):
                raise LoginError("Bị chuyển về dashboard giữa chừng bất ngờ.")
            aad = self._aad.parse_config(resp.url, resp.text)
        raise LoginError("Không lấy được flowToken/ctx sau nhiều lần thử (có thể AAD đổi luồng).")

    def authenticate(self, email: str, password: str) -> requests.Response:
        """Thực hiện đăng nhập, trả về response cuối (đã ở dashboard DNU)."""
        login_page = self.start_sso()
        # Đã đăng nhập sẵn?
        if CFG.dashboard_host in _host(login_page.url) and "/dnu-lms-sso" not in login_page.url:
            return login_page

        aad = self._resolve_login_form(login_page)
        logger.info("→ Đã lấy flowToken/canary từ trang login Microsoft.")
        _human_pause()

        cred_type = self._aad.get_credential_type(email, aad.flow_token, aad.canary, aad.ctx)
        fresh_flow_token = cred_type.get("FlowToken")
        if fresh_flow_token:
            aad = replace(aad, flow_token=fresh_flow_token)
        else:
            logger.warning("GetCredentialType không trả FlowToken mới — dùng tạm sFT gốc "
                           "(bước sau có thể bị Microsoft từ chối).")
        _human_pause()

        if not password:
            raise LoginError("DNU_PASSWORD trong .env đang TRỐNG.")

        logger.info("→ Gửi email + mật khẩu tới Microsoft...")
        resp = self._aad.submit_password(aad, email, password)
        self._check_password_error(resp)

        # Sau khi POST mật khẩu, AAD có thể trả nhiều trang trung gian tự
        # submit (BssoInterrupt -> KMSI -> ...). Lặp mô phỏng cho tới khi
        # rời login.microsoftonline.com (về gateway DNU -> callback).
        for _ in range(8):
            if CFG.dashboard_host in _host(resp.url):
                break
            self._check_password_error(resp)
            resp = self._aad.maybe_confirm_kmsi(resp)
            if CFG.dashboard_host in _host(resp.url):
                break
            resp, reposted = self._aad.follow_auto_post(resp)
            if not reposted:
                # không còn trang auto-post -> thử form callback thủ công
                resp = self._follow_callback(resp)
                break
            _human_pause()

        if CFG.dashboard_host not in _host(resp.url):
            resp = self._follow_callback(resp)

        if CFG.dashboard_host not in _host(resp.url):
            raise LoginError(
                f"Kết thúc nhưng chưa về {CFG.dashboard_host} (đang ở {resp.url}).")
        logger.info("✅ Đăng nhập SSO thành công: %s", resp.url)
        return resp

    def _follow_callback(self, resp: requests.Response) -> requests.Response:
        """Phòng khi form KMSI trả HTML auto-submit thay vì 302 thuần."""
        # Trường hợp AAD trả form <input name=code ...> tự submit
        m = re.search(r'<form[^>]+action="([^"]+)"[^>]*>(.*?)</form>',
                      resp.text, re.S | re.I)
        if not m:
            return resp
        action = html_lib.unescape(m.group(1))
        fields = dict(re.findall(r'name="([^"]+)"\s+value="([^"]*)"', m.group(2)))
        if not action.startswith("http"):
            action = urljoin(resp.url, action)
        logger.info("→ Auto-submit form callback tới %s", _host(action))
        return self._s.post(action, data=fields, timeout=CFG.timeout,
                            allow_redirects=True)

    @staticmethod
    def _check_password_error(resp: requests.Response) -> None:
        if _host(resp.url) != CFG.aad_host:
            return  # đã rời AAD -> không có lỗi mật khẩu
        body = resp.text
        # Mã lỗi AADSTS phổ biến
        m = re.search(r'"sErrorCode"\s*:\s*"(\d+)"', body)
        if re.search(r"(captcha|arkose|funcaptcha)", body, re.I):
            raise MFARequiredError("Microsoft yêu cầu CAPTCHA — dùng login_auto.py.")
        if re.search(r'"urlBeginAuth".*"(?:BeginAuth|SAS/BeginAuth)"', body) or \
                "SAS/ProcessAuth" in body:
            raise MFARequiredError("Tài khoản yêu cầu xác thực 2 lớp (MFA).")
        if m:
            code = m.group(1)
            if code in ("50126", "50053", "50055"):
                raise LoginError(f"Sai email/mật khẩu hoặc bị khoá (AADSTS{code}).")
            raise LoginError(f"Microsoft trả lỗi AADSTS{code}.")

    # -- lưu phiên -------------------------------------------------------
    def build_creds(self, resp: requests.Response) -> Optional[dict]:
        """Dựng dict creds (cookie + csrf + user-agent) TRONG BỘ NHỚ.

        Tách riêng khỏi việc ghi đĩa để tầng web (FastAPI) có thể lấy creds
        theo từng phiên người dùng mà KHÔNG đụng tới file dùng chung
        captures/creds.json (tránh race condition khi nhiều người dùng cùng
        lúc). Trả None nếu không đủ cookie/CSRF.
        """
        cookies = self._s.cookies.get_dict(domain=CFG.dashboard_host) or \
            {c.name: c.value for c in self._s.cookies}
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items() if k)

        csrf = self._extract_csrf(resp.text)
        if not csrf:
            # Tải lại dashboard để chắc chắn lấy được meta csrf
            page = self._get_with_retry(f"https://{CFG.dashboard_host}/student-dashboard")
            csrf = self._extract_csrf(page.text)
            resp = page

        if not (cookie_header and csrf):
            logger.warning("Không lấy đủ cookie/CSRF (cookie=%s, csrf=%s).",
                           bool(cookie_header), bool(csrf))
            return None

        return {
            "cookie": cookie_header,
            "x-csrf-token": csrf,
            "user-agent": CFG.user_agent,
            "current_url": resp.url,
            "saved_at": _now_iso(),
        }

    def save_creds(self, resp: requests.Response) -> bool:
        """Ghi cookie + csrf ra captures/creds.json (định dạng cũ)."""
        creds = self.build_creds(resp)
        if not creds:
            return False
        Path("captures").mkdir(exist_ok=True)
        Path(CFG.creds_path).write_text(
            json.dumps(creds, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    @staticmethod
    def _extract_csrf(page_html: str) -> Optional[str]:
        m = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"',
                      page_html, re.I)
        return html_lib.unescape(m.group(1)) if m else None


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat()


# ─────────────────────────── Entrypoint ────────────────────────────────────
def main() -> int:
    print("=" * 70)
    print("  LOGIN_REQUEST — đăng nhập SSO Microsoft 365 (thuần HTTP, no browser)")
    print("=" * 70)

    env = load_env()
    email = env.get("DNU_EMAIL", "")
    password = env.get("DNU_PASSWORD", "")
    if not email:
        logger.error("Thiếu DNU_EMAIL trong .env.")
        return 2

    client = DNUSSOClient()
    try:
        resp = client.authenticate(email, password)
    except MFARequiredError as e:
        logger.error("⛔ %s", e)
        logger.error("   → Chạy: ./.venv/Scripts/python.exe login_auto.py")
        return 3
    except LoginError as e:
        logger.error("⛔ Đăng nhập thất bại: %s", e)
        return 1
    except requests.RequestException as e:
        logger.error("⛔ Lỗi mạng: %s", e)
        return 1

    if client.save_creds(resp):
        logger.info("💾 Đã lưu phiên vào %s", CFG.creds_path)
        logger.info("   Giờ chạy: ./.venv/Scripts/python.exe run_flow.py")
        return 0
    logger.warning("⚠️ Không lấy được cookie/CSRF — thử lại hoặc dùng login_auto.py.")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
