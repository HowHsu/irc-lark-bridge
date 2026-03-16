"""Lark WebSocket client + send API. Uses lark-oapi for WS, httpx for HTTP."""
import asyncio
import json
import logging
import threading
from queue import Queue
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

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
        on_message: Callable[[str, str], None] | None = None,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.chat_id = chat_id
        self.use_feishu = use_feishu
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
        """Start Lark WebSocket in a background thread."""
        try:
            import lark_oapi as lark
            from lark_oapi import EventDispatcherHandler, ws
        except ImportError:
            logger.error("lark-oapi requires: pip install lark-oapi")
            raise

        bot_open_id = self._get_bot_open_id()
        logger.info("Lark bot open_id: %s", bot_open_id or "(none)")

        def _is_mention(data: dict) -> bool:
            if not bot_open_id:
                return True  # no filter
            msg = data.get("event", {}).get("message", {})
            mentions = msg.get("mentions", [])
            for m in mentions:
                oid = m.get("id", {}).get("open_id") or m.get("open_id")
                if oid == bot_open_id:
                    return True
            content = msg.get("content", "{}")
            if isinstance(content, str):
                try:
                    c = json.loads(content)
                    if "post" in c:
                        return True  # post may have mentions
                except json.JSONDecodeError:
                    pass
            return False

        def _to_dict(obj) -> dict:
            if isinstance(obj, dict):
                return obj
            if hasattr(obj, "__dict__"):
                return obj.__dict__
            if hasattr(obj, "model_dump"):
                return obj.model_dump()
            return {}

        def _handler(data) -> None:
            try:
                d = _to_dict(data)
                ev = d.get("event", {})
                if isinstance(ev, object) and not isinstance(ev, dict):
                    ev = _to_dict(ev)
                msg = ev.get("message", {}) if isinstance(ev, dict) else {}
                if isinstance(msg, object) and not isinstance(msg, dict):
                    msg = _to_dict(msg)
                if not isinstance(msg, dict):
                    return
                chat_id = msg.get("chat_id", "")
                if chat_id != self.chat_id:
                    return
                chat_type = msg.get("chat_type", "")
                if chat_type == "group" and not _is_mention(d):
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
                if not text:
                    return
                sender = ev.get("sender", {}) if isinstance(ev, dict) else {}
                if isinstance(sender, object) and not isinstance(sender, dict):
                    sender = _to_dict(sender)
                sid = sender.get("sender_id", {}) if isinstance(sender, dict) else {}
                if isinstance(sid, object) and not isinstance(sid, dict):
                    sid = _to_dict(sid)
                open_id = sid.get("open_id", "?") if isinstance(sid, dict) else "?"
                self._received_queue.put((open_id, text))
                self.on_message(open_id, text)
            except Exception as e:
                logger.exception("Lark handler: %s", e)

        def _run():
            # lark-oapi 使用 asyncio，必须在独立线程中创建自己的事件循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            handler = (
                EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(_handler)
                .build()
            )
            cli = ws.Client(
                self.app_id,
                self.app_secret,
                event_handler=handler,
            )
            if self.use_feishu:
                cli.domain = "https://open.feishu.cn"
            logger.info("Lark WS starting...")
            cli.start()

        self._ws_thread = threading.Thread(target=_run, daemon=True)
        self._ws_thread.start()
