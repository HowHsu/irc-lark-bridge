#!/usr/bin/env python3
"""IRC-Lark bridge: #bitcoin-core-dev <-> Lark group."""
import asyncio
import logging
import os
import signal
import sys

from config import Config
from irc_client import IrcClient
from lark_client import LarkClient

logging.basicConfig(
    level=logging.DEBUG if os.getenv("DEBUG") else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


irc_ref: IrcClient | None = None


async def run_bridge(cfg: Config, lark: LarkClient) -> None:
    global irc_ref

    def on_irc_privmsg(sender: str, target: str, msg: str) -> None:
        """IRC -> Lark: forward channel messages to all mapped Lark groups."""
        try:
            chat_ids = cfg.chat_ids_for_channel(target)
            if not chat_ids:
                logger.debug("IRC: no bridge for channel %s", target)
                return
            from irc_client import strip_irc_codes
            clean = strip_irc_codes(msg)
            if clean:
                for cid in chat_ids:
                    lark.send_text(cid, f"[IRC] <{sender}> {clean}")
        except Exception as e:
            logger.exception("Lark send: %s", e)

    irc_ref = IrcClient(
        server=cfg.irc_server,
        port=cfg.irc_port,
        nick=cfg.irc_nick,
        channels=cfg.irc_channels(),
        sasl_password=cfg.irc_sasl_password,
        on_privmsg=on_irc_privmsg,
    )

    def handle_lark_message(chat_id: str, text: str) -> bool:
        """处理 Lark 消息：若以 / 开头则作为 IRC 命令执行，返回 True；否则返回 False。"""
        t = text.strip()
        if not t.startswith("/"):
            return False
        parts = t[1:].split(None, 1)  # 去掉首 /，按空白分割最多 2 段
        cmd = (parts[0] or "").upper()
        rest = (parts[1] or "").strip()
        if not cmd:
            return False
        irc_ch = cfg.channel_for_chat_id(chat_id) or cfg.irc_channels()[0] if cfg.irc_channels() else ""
        # 常用 IRC 命令
        if cmd == "NICK":
            irc_ref.send_raw(f"NICK {rest}" if rest else "NICK")
        elif cmd == "JOIN":
            irc_ref.send_raw(f"JOIN {rest}" if rest else "JOIN")
        elif cmd == "PART":
            irc_ref.send_raw(f"PART {rest}" if rest else f"PART {irc_ch}")
        elif cmd == "QUIT":
            irc_ref.send_raw(f"QUIT :{rest}" if rest else "QUIT")
        elif cmd == "MSG":
            sp = rest.split(None, 1)
            if len(sp) >= 2:
                irc_ref.send_raw(f"PRIVMSG {sp[0]} :{sp[1]}")
            elif sp:
                irc_ref.send_raw(f"PRIVMSG {sp[0]} :")
        elif cmd == "ME":
            irc_ref.send_raw(f"PRIVMSG {irc_ch} :\x01ACTION {rest}\x01")
        elif cmd == "RAW":
            irc_ref.send_raw(rest)
        else:
            irc_ref.send_raw(f"{cmd} {rest}" if rest else cmd)
        return True

    async def drain_lark_to_irc() -> None:
        """Lark -> IRC: forward @mention replies; / 开头作为 IRC 命令执行。"""
        while True:
            for chat_id, _open_id, text in lark.drain_received():
                try:
                    irc_ch = cfg.channel_for_chat_id(chat_id)
                    if not irc_ch:
                        logger.warning("No IRC channel for Lark chat %s", chat_id)
                        continue
                    if not handle_lark_message(chat_id, text):
                        await irc_ref.send_privmsg(irc_ch, text)
                except Exception as e:
                    logger.exception("IRC send: %s", e)
            await asyncio.sleep(0.2)

    async def run_irc() -> None:
        await irc_ref.run()

    await asyncio.gather(run_irc(), drain_lark_to_irc())


def main() -> int:
    cfg = Config.from_env()
    errs = cfg.validate()
    if errs:
        for e in errs:
            logger.error("%s", e)
        return 1
    cfg.resolve_chat_names()
    failed = [b for b in cfg.bridges if not b.lark_chat_id.startswith("oc_")]
    if failed:
        for b in failed:
            logger.error("Could not resolve Lark group: %r (check name or use chat_id)", b.lark_chat_id)
        return 1

    lark = LarkClient(
        app_id=cfg.lark_app_id,
        app_secret=cfg.lark_app_secret,
        chat_ids=cfg.chat_ids(),
        use_feishu=cfg.lark_use_feishu,
        lark_domain=cfg.lark_domain,
        mention_only=cfg.lark_mention_only,
    )
    # 必须在主事件循环启动之前启动 Lark 线程，否则 lark-oapi 会获取到主循环
    lark.run_ws_thread()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def stop(*_):
        if irc_ref:
            irc_ref.stop()
        loop.stop()

    try:
        task = loop.create_task(run_bridge(cfg, lark))
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        loop.run_until_complete(task)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
