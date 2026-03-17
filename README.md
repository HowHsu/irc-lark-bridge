# IRC-Lark Bridge

将 Libera IRC `#bitcoin-core-dev` 与 Lark 群聊双向桥接：
- **IRC → Lark**：频道消息转发到 Lark 群
- **Lark → IRC**：群里 @机器人 的回复转发到 IRC

使用 Lark WebSocket 长连接，无需公网地址。

## 依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 配置

通过环境变量：

| 变量 | 说明 | 示例 |
|------|------|------|
| `IRC_SERVER` | IRC 服务器 | `irc.libera.chat` |
| `IRC_PORT` | 端口 (TLS) | `6697` |
| `IRC_NICK` | Bot 昵称 | `irc-lark-bridge` |
| `IRC_CHANNEL` | 频道 | `#bitcoin-core-dev` |
| `IRC_SASL_PASSWORD` | Libera 账号密码（必填，否则无法发消息） | |
| `LARK_APP_ID` | 飞书应用 App ID | |
| `LARK_APP_SECRET` | 飞书应用 App Secret | |
| `LARK_CHAT_ID` | 目标 Lark 群聊 chat_id | |
| `LARK_USE_FEISHU` | 1=使用飞书国内版 (open.feishu.cn) | `0` |
| `LARK_DOMAIN` | 可选，覆盖域名，如 `https://open.feishu.cn` | |
| `LARK_MENTION_ONLY` | 1=仅转发 @机器人 的消息，0=转发群内所有消息 | `1` |
| `DEBUG` | 1=开启 DEBUG 日志 | |

### 获取 LARK_CHAT_ID

1. 将机器人加入群聊
2. 在群设置中查看，或通过 [获取群信息 API](https://open.larksuite.com/document/server-docs/im-v1/chat/get) 获取

### Libera 账号

1. 在 Libera 注册：`/msg NickServ REGISTER 密码 邮箱`
2. 验证邮箱
3. 将密码填入 `IRC_SASL_PASSWORD`

### Lark 应用

1. [Lark 开放平台](https://open.larksuite.com/) 创建企业自建应用
2. 权限：`im:message`、`im:message:send_as_bot`、`im:message.group_at_msg`
3. 事件订阅：`im.message.receive_v1`，选择 **使用长连接接收事件**
4. 将机器人加入目标群

### 在 Lark 中 @IRC 频道里的人

IRC 没有原生 @提及。在 Lark 发消息时，若要指定某位 IRC 用户，可在消息中直接写其昵称，例如：`willy: 你好` 或 `willy, 你的问题...`。该用户会在 IRC 频道中看到自己的昵称被提及。

### Lark @机器人 无法转发到 IRC

1. 确认 Lark 应用权限已开启「群聊中 @机器人 时接收消息」
2. 确认机器人已加入目标群
3. 临时设置 `LARK_MENTION_ONLY=0` 测试（转发群内所有消息）
4. 开启 `DEBUG=1` 查看日志中的 chat_id、mentions 是否匹配

### 报错 "Incorrect domain name"

应用所在平台与域名不一致。飞书国内版应用需设置 `LARK_USE_FEISHU=1`，或显式指定 `LARK_DOMAIN=https://open.feishu.cn`。

## 运行

```bash
.venv/bin/python main.py
```

### Docker

```bash
docker build -t irc-lark-bridge .
# 强制重新 clone 最新代码（保留 apt/pip 缓存）:
# docker build --build-arg CACHE_BUST=$(date +%s) -t irc-lark-bridge .
docker run --rm \
  -e IRC_NICK=irc-lark-bridge \
  -e IRC_SASL_PASSWORD=xxx \
  -e LARK_APP_ID=cli_xxx \
  -e LARK_APP_SECRET=xxx \
  -e LARK_CHAT_ID=oc_xxx \
  irc-lark-bridge
```

或：

```bash
export IRC_SASL_PASSWORD=xxx
export LARK_APP_ID=cli_xxx
export LARK_APP_SECRET=xxx
export LARK_CHAT_ID=oc_xxx
.venv/bin/python main.py
```
