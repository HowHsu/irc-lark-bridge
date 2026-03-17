"""Lark WebSocket client + send API. Uses lark-oapi for WS, httpx for HTTP."""
import asyncio
import json
import logging
import re
import threading
from queue import Queue
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

# Lark @提及占位符 @_user_N
_RE_LARK_AT = re.compile(r"@_user_\d+\s*")


def _strip_lark_at_placeholders(text: str) -> str:
    """去除 Lark 群聊中的 @_user_N 占位符。"""
    return _RE_LARK_AT.sub("", text).strip()


# Lark International: open.larksuite.com
# Feishu CN: open.feishu.cn
LARK_BASE = "https://open.larksuite.com/open-apis"
FEISHU_BASE = "https://open.feishu.cn/open-apis"
LARK_WS_BASE = "https://open.larksuite.com"
FEISHU_WS_BASE = "https://open.feishu.cn"


class LarkClient:
    """Lark WebSocket receive + HTTP send. Runs WS in separate thread."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        chat_id: str,
        use_feishu: bool = False,
        lark_domain: str = "",
        mention_only: bool = True,
        on_message: Callable[[str, str], None] | None = None,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.chat_id = chat_id
        self.use_feishu = use_feishu
        self._domain_override = lark_domain
        self._mention_only = mention_only
        self._base = FEISHU_BASE if use_feishu else LARK_BASE
        self._ws_base = FEISHU_WS_BASE if use_feishu else LARK_WS_BASE
        self._locale = "zh" if use_feishu else "en"
        self.on_message = on_message or (lambda n, t: None)
        self._token: str | None = None
        self._token_expire = 0.0
        self._received_queue: Queue[tuple[str, str]] = Queue()
        self._ws_thread: threading.Thread | None = None

    def _get_token(self) -> str:
        import time

        now = time.time()
        if self._token and now < self._token_expire - 60:
            return self._token
        url = f"{self._base}/auth/v3/tenant_access_token/internal"
        with httpx.Client(timeout=10) as c:
            r = c.post(
                url,
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Lark token failed: {data}")
        self._token = data["tenant_access_token"]
        self._token_expire = now + data.get("expire", 7200)
        return self._token

    def _get_bot_open_id(self) -> str | None:
        url = f"{self._base}/bot/v3/info"
        with httpx.Client(timeout=10) as c:
            r = c.post(
                url,
                headers={
                    "Authorization": f"Bearer {self._get_token()}",
                    "Content-Type": "application/json",
                },
            )
            r.raise_for_status()
            data = r.json()
        if data.get("code") != 0:
            return None
        bot = data.get("bot") or data.get("data", {}).get("bot") or {}
        return bot.get("open_id")

    def send_text(self, text: str) -> None:
        """Send text to the configured chat."""
        url = f"{self._base}/im/v1/messages?receive_id_type=chat_id"
        with httpx.Client(timeout=10) as c:
            r = c.post(
                url,
                headers={
                    "Authorization": f"Bearer {self._get_token()}",
                    "Content-Type": "application/json",
                },
                json={
                    "receive_id": self.chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}),
                },
            )
            r.raise_for_status()
            data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Lark send failed: {data}")

    def drain_received(self) -> list[tuple[str, str]]:
        """Drain queued messages received from Lark (to forward to IRC)."""
        out = []
        while True:
            try:
                out.append(self._received_queue.get_nowait())
            except Exception:
                break
        return out

    def run_ws_thread(self) -> None:
        """Start Lark WebSocket. 使用原生实现（正确 locale），参考 Zeroclaw。"""
        bot_open_id = self._get_bot_open_id()
        logger.info("Lark bot open_id: %s, chat_id: %s", bot_open_id or "(none)", self.chat_id)

        def _is_mention(ev: dict) -> bool:
            if not bot_open_id:
                return True
            msg = ev.get("event", {}).get("message", {})
            mentions = msg.get("mentions", [])
            for m in mentions:
                oid = (
                    (m.get("id") or {}).get("open_id")
                    or m.get("open_id")
                    or (m.get("id") if isinstance(m.get("id"), str) else None)
                )
                if oid == bot_open_id:
                    return True
            # post 富文本中的 @
            content = msg.get("content", "{}")
            if isinstance(content, str):
                try:
                    c = json.loads(content)
                    for elem in (c.get("elements") or []):
                        for run in (elem.get("elements") or []):
                            if run.get("text_run", {}).get("style", {}).get("link", {}).get("url", "").startswith("https://open.feishu.cn/client/contact/user/"):
                                return True
                except json.JSONDecodeError:
                    pass
            return False

        def _on_event(ev: dict) -> None:
            try:
                ev_body = ev.get("event", {})
                msg = ev_body.get("message", {})
                chat_id = msg.get("chat_id", "")
                chat_type = msg.get("chat_type", "")
                logger.debug("Lark event: chat_id=%s (expect %s), chat_type=%s", chat_id, self.chat_id, chat_type)
                if chat_id != self.chat_id:
                    logger.debug("Lark: skip (chat_id mismatch)")
                    return
                if self._mention_only and chat_type == "group" and not _is_mention(ev):
                    mids = [(m.get("id") or {}).get("open_id") or m.get("open_id") for m in msg.get("mentions", [])]
                    logger.info("Lark: skip (need @mention bot, bot=%s, mentions=%s, or LARK_MENTION_ONLY=0)",
                                bot_open_id, mids)
                    return
                content = msg.get("content", "{}")
                if isinstance(content, str):
                    try:
                        c = json.loads(content)
                        text = (c.get("text") or "").strip()
                    except json.JSONDecodeError:
                        text = content
                else:
                    text = str(content)
                # 去除 Lark @提及占位符 @_user_N
                text = _strip_lark_at_placeholders(text)
                if not text:
                    logger.debug("Lark: skip (empty text)")
                    return
                sender = ev_body.get("sender", {})
                sid = sender.get("sender_id", {})
                open_id = sid.get("open_id", "?")
                logger.info("Lark -> IRC: forwarding %r", text[:50])
                self._received_queue.put((open_id, text))
                self.on_message(open_id, text)
            except Exception as e:
                logger.exception("Lark event handler: %s", e)

        def _run():
            from lark_ws_native import run_lark_ws

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            while True:
                try:
                    loop.run_until_complete(
                        run_lark_ws(
                            ws_base=self._ws_base,
                            locale=self._locale,
                            app_id=self.app_id,
                            app_secret=self.app_secret,
                            on_event=_on_event,
                        )
                    )
                except Exception as e:
                    logger.warning("Lark WS disconnected: %s, reconnecting in 5s", e)
                loop.run_until_complete(asyncio.sleep(5))

        self._ws_thread = threading.Thread(target=_run, daemon=True)
        self._ws_thread.start()
