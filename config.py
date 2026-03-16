"""Configuration from environment or config file."""
import os
from dataclasses import dataclass


@dataclass
class Config:
    # IRC
    irc_server: str = "irc.libera.chat"
    irc_port: int = 6697
    irc_nick: str = ""
    irc_channel: str = "#bitcoin-core-dev"
    irc_sasl_password: str = ""

    # Lark
    lark_app_id: str = ""
    lark_app_secret: str = ""
    lark_chat_id: str = ""  # Lark group chat_id to send to
    lark_use_feishu: bool = False  # True = open.feishu.cn

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            irc_server=os.getenv("IRC_SERVER", "irc.libera.chat"),
            irc_port=int(os.getenv("IRC_PORT", "6697")),
            irc_nick=os.getenv("IRC_NICK", "howhsu_bitcoin_dev"),
            irc_channel=os.getenv("IRC_CHANNEL", "#bitcoin-core-dev"),
            irc_sasl_password=os.getenv("IRC_SASL_PASSWORD", ""),
            lark_app_id=os.getenv("LARK_APP_ID", ""),
            lark_app_secret=os.getenv("LARK_APP_SECRET", ""),
            lark_chat_id=os.getenv("LARK_CHAT_ID", ""),
            lark_use_feishu=os.getenv("LARK_USE_FEISHU", "0").lower() in ("1", "true", "yes"),
        )

    def validate(self) -> list[str]:
        errs = []
        if not self.lark_app_id or not self.lark_app_secret:
            errs.append("LARK_APP_ID and LARK_APP_SECRET required")
        if not self.lark_chat_id:
            errs.append("LARK_CHAT_ID required (target group chat_id)")
        if not self.irc_sasl_password:
            errs.append("IRC_SASL_PASSWORD required (Libera requires login to send)")
        return errs
