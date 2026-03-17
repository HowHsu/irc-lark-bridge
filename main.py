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
        """IRC -> Lark: forward channel messages."""
        try:
            from irc_client import strip_irc_codes
            clean = strip_irc_codes(msg)
            if clean:
                lark.send_text(f"[IRC] <{sender}> {clean}")
        except Exception as e:
            logger.exception("Lark send: %s", e)

    irc_ref = IrcClient(
        server=cfg.irc_server,
        port=cfg.irc_port,
        nick=cfg.irc_nick,
        channel=cfg.irc_channel,
        sasl_password=cfg.irc_sasl_password,
        on_privmsg=on_irc_privmsg,
    )

    async def drain_lark_to_irc() -> None:
        """Lark -> IRC: forward @mention replies."""
        while True:
            for _open_id, text in lark.drain_received():
                try:
                    await irc_ref.send_privmsg(cfg.irc_channel, text)
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

    lark = LarkClient(
        app_id=cfg.lark_app_id,
        app_secret=cfg.lark_app_secret,
        chat_id=cfg.lark_chat_id,
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
