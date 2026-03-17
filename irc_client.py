"""Minimal IRC client with SASL (Libera)."""
import asyncio
import base64
import re
import ssl
import logging
from typing import Callable, Coroutine, Any

logger = logging.getLogger(__name__)

# IRC line format: [:prefix] command [params] [:trailing]
RE_IRC = re.compile(
    r"^(?::([^\s]+)\s+)?(\S+)(?:\s+(.*?))?(?:\s+:(.+))?$"
)


def encode_sasl_plain(nick: str, password: str) -> str:
    return base64.b64encode(f"\0{nick}\0{password}".encode()).decode()


def parse_irc(line: str) -> tuple[str | None, str, list[str]] | None:
    line = line.strip()
    if not line:
        return None
    m = RE_IRC.match(line)
    if not m:
        return None
    prefix, cmd, params, trailing = m.groups()
    parts = params.split() if params else []
    if trailing is not None:
        parts.append(trailing)
    return (prefix, cmd.upper(), parts)


def nick_from_prefix(prefix: str | None) -> str | None:
    if not prefix:
        return None
    return prefix.split("!")[0] if "!" in prefix else prefix


def strip_irc_codes(text: str) -> str:
    """去除 IRC 颜色和格式码，保留空格、括号等正常字符。"""
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\x03":  # 颜色
            i += 1
            if i < n and text[i].isdigit():
                i += 1
                if i < n and text[i].isdigit():
                    i += 1
            if i < n and text[i] == ",":
                i += 1
                if i < n and text[i].isdigit():
                    i += 1
                    if i < n and text[i].isdigit():
                        i += 1
            continue
        if c == "\x04":  # 十六进制颜色 RRGGBB
            i += 1
            for _ in range(6):
                if i < n and text[i] in "0123456789abcdefABCDEF":
                    i += 1
                else:
                    break
            continue
        if c in "\x02\x0f\x11\x16\x1d\x1e\x1f":  # 粗体/重置/等宽/反色/斜体/删除线/下划线
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out).strip()


class IrcClient:
    def __init__(
        self,
        server: str,
        port: int,
        nick: str,
        channels: list[str],
        sasl_password: str,
        on_privmsg: Callable[[str, str, str], Coroutine[Any, Any, None]],
    ):
        self.server = server
        self.port = port
        self.nick = nick
        self.channels = list(channels) if channels else []
        self.sasl_password = sasl_password
        self.on_privmsg = on_privmsg
        self._writer: asyncio.StreamWriter | None = None
        self._running = False

    def _send(self, line: str) -> None:
        if self._writer:
            self._writer.write(f"{line}\r\n".encode())
            logger.debug("IRC> %s", line)

    def send_raw(self, line: str) -> None:
        """发送原始 IRC 命令（如 NICK、JOIN 等）。"""
        line = line.strip()
        if line:
            self._send(line)

    async def send_privmsg(self, target: str, text: str) -> None:
        # IRC max 512 bytes; keep ~64 for prefix
        max_len = 400
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            while line:
                chunk = line[:max_len] if len(line) > max_len else line
                line = line[max_len:] if len(line) > max_len else ""
                self._send(f"PRIVMSG {target} :{chunk}")

    async def run(self) -> None:
        self._running = True
        ssl_ctx = ssl.create_default_context()
        while self._running:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.server, self.port, ssl=ssl_ctx),
                    timeout=30,
                )
                self._writer = writer
                logger.info("IRC connected to %s:%s", self.server, self.port)

                if self.sasl_password:
                    self._send("CAP REQ :sasl")

                self._send(f"NICK {self.nick}")
                self._send(f"USER {self.nick} 0 * :irc-lark-bridge")

                sasl_pending = bool(self.sasl_password)
                registered = False

                while self._running:
                    line = await asyncio.wait_for(reader.readline(), timeout=300)
                    if not line:
                        break
                    raw = line.decode(errors="replace").strip()
                    parsed = parse_irc(raw)
                    if not parsed:
                        continue

                    prefix, cmd, params = parsed
                    logger.debug("IRC< %s", raw)

                    if cmd == "PING":
                        token = params[0] if params else ""
                        self._send(f"PONG :{token}")

                    elif cmd == "CAP":
                        if sasl_pending and any("sasl" in p for p in params):
                            if any("ACK" in p for p in params):
                                self._send("AUTHENTICATE PLAIN")
                            elif any("NAK" in p for p in params):
                                self._send("CAP END")
                                sasl_pending = False

                    elif cmd == "AUTHENTICATE" and params and params[0] == "+":
                        if sasl_pending and self.sasl_password:
                            enc = encode_sasl_plain(self.nick, self.sasl_password)
                            self._send(f"AUTHENTICATE {enc}")
                        sasl_pending = False

                    elif cmd in ("903", "904", "905", "906", "907"):
                        self._send("CAP END")
                        sasl_pending = False
                        if cmd != "903":
                            logger.warning("IRC SASL failed: %s", cmd)

                    elif cmd == "001":
                        registered = True
                        logger.info("IRC registered as %s", self.nick)
                        for ch in self.channels:
                            if ch:
                                self._send(f"JOIN {ch}")

                    elif cmd == "PRIVMSG" and len(params) >= 2:
                        target, msg = params[0], params[1]
                        sender = nick_from_prefix(prefix)
                        if sender and sender != self.nick:
                            await self.on_privmsg(sender, target, msg)

            except asyncio.TimeoutError:
                logger.warning("IRC read timeout")
            except (ConnectionError, OSError) as e:
                logger.warning("IRC connection error: %s", e)
            except Exception as e:
                logger.exception("IRC error: %s", e)
            finally:
                self._writer = None
                if self._running:
                    await asyncio.sleep(5)

    def stop(self) -> None:
        self._running = False
