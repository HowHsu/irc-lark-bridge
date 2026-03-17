"""Native Lark WebSocket client (参考 Zeroclaw)，使用正确的 domain 和 locale。"""
import asyncio
import json
import logging
from urllib.parse import parse_qs, urlparse

import httpx
import websockets

logger = logging.getLogger(__name__)

# Zeroclaw: Lark 用 locale "en", Feishu 用 "zh"
# lark-oapi 硬编码 "zh" 导致 Lark 国际版报错 1000040351 Incorrect domain name
WS_ENDPOINT = "/callback/ws/endpoint"
DEVICE_ID = "device_id"
SERVICE_ID = "service_id"
HEADER_TYPE = "type"
MSG_TYPE_PING = "ping"
MSG_TYPE_PONG = "pong"
MSG_TYPE_EVENT = "event"
FRAME_CONTROL = 0
FRAME_DATA = 1


def _get_ws_endpoint(ws_base: str, locale: str, app_id: str, app_secret: str) -> tuple[str, dict]:
    """POST /callback/ws/endpoint 获取 WSS URL，使用正确的 locale。"""
    url = f"{ws_base.rstrip('/')}{WS_ENDPOINT}"
    with httpx.Client(timeout=15) as c:
        r = c.post(
            url,
            headers={"Content-Type": "application/json", "locale": locale},
            json={"AppID": app_id, "AppSecret": app_secret},
        )
        r.raise_for_status()
        data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark WS endpoint failed: {data}")
    ep = data.get("data") or {}
    wss_url = ep.get("URL") or ep.get("url")
    if not wss_url:
        raise RuntimeError("Lark WS endpoint: no URL in response")
    config = ep.get("ClientConfig") or {}
    return wss_url, config


async def run_lark_ws(
    ws_base: str,
    locale: str,
    app_id: str,
    app_secret: str,
    on_event: callable,
) -> None:
    """原生 WebSocket 循环，参考 Zeroclaw listen_ws。"""
    from lark_oapi.ws.pb.pbbp2_pb2 import Frame

    wss_url, config = _get_ws_endpoint(ws_base, locale, app_id, app_secret)
    u = urlparse(wss_url)
    qs = parse_qs(u.query)
    service_id = int(qs.get(SERVICE_ID, ["0"])[0])
    ping_interval = max(config.get("PingInterval", 120) or 120, 10)

    logger.info("Lark WS connecting to %s (service_id=%s)", wss_url, service_id)

    async with websockets.connect(wss_url) as ws:
        # 发送初始 ping（与 Zeroclaw 一致）
        ping = Frame()
        ping.SeqID = 1
        ping.LogID = 0
        ping.service = service_id
        ping.method = FRAME_CONTROL
        h = ping.headers.add()
        h.key, h.value = HEADER_TYPE, MSG_TYPE_PING
        await ws.send(ping.SerializeToString())
        logger.info("Lark WS connected")

        seq = 1
        frag_cache: dict[str, list] = {}

        async def send_ping():
            nonlocal seq
            seq += 1
            p = Frame()
            p.SeqID = seq
            p.LogID = 0
            p.service = service_id
            p.method = FRAME_CONTROL
            h = p.headers.add()
            h.key, h.value = HEADER_TYPE, MSG_TYPE_PING
            await ws.send(p.SerializeToString())

        async def ping_loop():
            while True:
                await asyncio.sleep(ping_interval)
                await send_ping()

        ping_task = asyncio.create_task(ping_loop())

        try:
            async for raw in ws:
                if not isinstance(raw, bytes):
                    continue
                frame = Frame()
                frame.ParseFromString(raw)
                if frame.method == FRAME_CONTROL:
                    # pong 等，忽略
                    continue
                if frame.method != FRAME_DATA:
                    continue

                def get_h(key: str) -> str:
                    for h in frame.headers:
                        if h.key == key:
                            return h.value
                    return ""

                msg_type = get_h(HEADER_TYPE)
                msg_id = get_h("message_id")
                sum_ = int(get_h("sum") or "1")
                seq_num = int(get_h("seq") or "0")

                # 分片重组
                payload = bytes(frame.payload) if frame.payload else b""
                if sum_ > 1 and msg_id:
                    if msg_id not in frag_cache:
                        frag_cache[msg_id] = [b""] * sum_
                    frag_cache[msg_id][seq_num] = payload
                    if all(frag_cache[msg_id]):
                        payload = b"".join(frag_cache.pop(msg_id))
                    else:
                        continue

                if msg_type != MSG_TYPE_EVENT:
                    continue

                try:
                    ev = json.loads(payload.decode())
                except Exception:
                    continue
                if ev.get("header", {}).get("event_type") != "im.message.receive_v1":
                    continue
                logger.debug("Lark WS: received im.message.receive_v1 chat_id=%s",
                             ev.get("event", {}).get("message", {}).get("chat_id"))

                # ACK（需在 3 秒内，参考 Zeroclaw）
                ack = Frame()
                ack.SeqID = frame.SeqID
                ack.LogID = frame.LogID
                ack.service = frame.service
                ack.method = frame.method
                for h in frame.headers:
                    nh = ack.headers.add()
                    nh.key, nh.value = h.key, h.value
                ack.payload = json.dumps({"code": 200, "headers": {}, "data": []}).encode()
                h = ack.headers.add()
                h.key, h.value = "biz_rt", "0"
                await ws.send(ack.SerializeToString())

                try:
                    on_event(ev)
                except Exception as e:
                    logger.exception("Lark event handler: %s", e)
        finally:
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
