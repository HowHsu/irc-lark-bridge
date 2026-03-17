"""Configuration from environment or config file."""
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BridgePair:
    """单对桥接：Lark 群 chat_id <-> IRC 频道"""
    lark_chat_id: str
    irc_channel: str


@dataclass
class Config:
    # IRC
    irc_server: str = "irc.libera.chat"
    irc_port: int = 6697
    irc_nick: str = ""
    irc_sasl_password: str = ""

    # Lark
    lark_app_id: str = ""
    lark_app_secret: str = ""
    lark_use_feishu: bool = False  # True = open.feishu.cn
    lark_domain: str = ""  # 可选覆盖，如 https://open.feishu.cn
    lark_mention_only: bool = True  # False=转发群内所有消息，True=仅 @机器人 时

    # 桥接对：支持多对 (Lark 群, IRC 频道)
    # BRIDGE_PAIRS=chat_id1:channel1,chat_id2:channel2 或沿用 LARK_CHAT_ID+IRC_CHANNEL
    bridges: list[BridgePair] = None

    def __post_init__(self):
        if self.bridges is None:
            self.bridges = []

    @classmethod
    def from_env(cls) -> "Config":
        pairs_raw = os.getenv("BRIDGE_PAIRS", "").strip()
        bridges: list[BridgePair] = []
        if pairs_raw:
            for part in pairs_raw.split(","):
                part = part.strip()
                if ":" in part:
                    # 从最后一个 : 分割，右侧为 IRC 频道（以 # 开头）
                    idx = part.rfind(":")
                    lid, chan = part[:idx].strip(), part[idx + 1 :].strip()
                    if lid and chan:
                        bridges.append(BridgePair(lark_chat_id=lid, irc_channel=chan))
        if not bridges:
            # 向后兼容：单对用 LARK_CHAT_ID + IRC_CHANNEL
            chat_id = os.getenv("LARK_CHAT_ID", "")
            channel = os.getenv("IRC_CHANNEL", "#bitcoin-core-dev")
            if chat_id:
                bridges.append(BridgePair(lark_chat_id=chat_id, irc_channel=channel))
        return cls(
            irc_server=os.getenv("IRC_SERVER", "irc.libera.chat"),
            irc_port=int(os.getenv("IRC_PORT", "6697")),
            irc_nick=os.getenv("IRC_NICK", "howhsu_bitcoin_dev"),
            irc_sasl_password=os.getenv("IRC_SASL_PASSWORD", ""),
            lark_app_id=os.getenv("LARK_APP_ID", ""),
            lark_app_secret=os.getenv("LARK_APP_SECRET", ""),
            lark_use_feishu=os.getenv("LARK_USE_FEISHU", "0").lower() in ("1", "true", "yes"),
            lark_domain=os.getenv("LARK_DOMAIN", ""),
            lark_mention_only=os.getenv("LARK_MENTION_ONLY", "1").lower() in ("1", "true", "yes"),
            bridges=bridges,
        )

    def validate(self) -> list[str]:
        errs = []
        if not self.lark_app_id or not self.lark_app_secret:
            errs.append("LARK_APP_ID and LARK_APP_SECRET required")
        if not self.bridges:
            errs.append("BRIDGE_PAIRS or (LARK_CHAT_ID + IRC_CHANNEL) required")
        if not self.irc_sasl_password:
            errs.append("IRC_SASL_PASSWORD required (Libera requires login to send)")
        return errs

    def resolve_chat_names(self) -> None:
        """将 bridges 中的群名解析为 chat_id（需 im:chat 或 im:chat:readonly 权限）。"""
        if not self.lark_app_id or not self.lark_app_secret:
            return
        base = "https://open.feishu.cn/open-apis" if self.lark_use_feishu else "https://open.larksuite.com/open-apis"
        if self.lark_domain:
            base = self.lark_domain.rstrip("/")
            if not base.endswith("/open-apis"):
                base = f"{base}/open-apis"
        from lark_client import resolve_chat_name_to_id
        for b in self.bridges:
            if b.lark_chat_id.startswith("oc_"):
                continue
            cid = resolve_chat_name_to_id(b.lark_chat_id, base, self.lark_app_id, self.lark_app_secret)
            if cid:
                logger.info("Resolved Lark group %r -> %s", b.lark_chat_id, cid)
                b.lark_chat_id = cid
            else:
                logger.warning("Could not resolve Lark group name %r to chat_id", b.lark_chat_id)

    def chat_ids(self) -> set[str]:
        return {b.lark_chat_id for b in self.bridges}

    def irc_channels(self) -> list[str]:
        return list(dict.fromkeys(b.irc_channel for b in self.bridges))

    def chat_ids_for_channel(self, irc_channel: str) -> list[str]:
        """返回桥接到该 IRC 频道的所有 Lark chat_id（同频道可对应多群）"""
        return [b.lark_chat_id for b in self.bridges if b.irc_channel == irc_channel]

    def channel_for_chat_id(self, lark_chat_id: str) -> str | None:
        for b in self.bridges:
            if b.lark_chat_id == lark_chat_id:
                return b.irc_channel
        return None
