"""企业微信机器人消息解析与文件解密测试。

覆盖 WeComBotMessage 的字段解析（text/image/官方字段/空消息）以及
企业微信媒体文件 AES-CBC 解密的往返一致性。
"""

from src.platform.channels.wecom import WeComBotMessage


def test_message_parsing():
    text_msg_data = {
        "msgtype": "text",
        "from_userid": "zhangsan",
        "to_userid": "bot_xxxxx",
        "chatid": "chat_xxxxx",
        "chattype": "single",
        "aibotid": "bot_xxxxx",
        "msgid": "1234567890",
        "text": {"content": "你好，帮我查一下今天的日程"},
    }

    msg = WeComBotMessage(text_msg_data)
    assert msg.msg_type == "text"
    assert msg.from_user == "zhangsan"
    assert msg.chat_id == "chat_xxxxx"
    assert msg.chat_type == "single"
    assert msg.aibot_id == "bot_xxxxx"
    assert msg.content == "你好，帮我查一下今天的日程"

    official_shape = WeComBotMessage({
        "msgtype": "text",
        "from": {"userid": "lisi"},
        "msgid": "m2",
        "text": {"content": "官方字段"},
    })
    assert official_shape.from_user == "lisi"

    empty_msg_data = {}
    empty_msg = WeComBotMessage(empty_msg_data)
    assert empty_msg.msg_type == "text"
    assert empty_msg.content == ""

    image_msg = WeComBotMessage({
        "msgtype": "image",
        "from_userid": "zhangsan",
        "msgid": "img1",
        "image": {"media_id": "media_xxx", "size": 123},
    })
    assert image_msg.msg_type == "image"
    assert image_msg.content == ""
    assert image_msg.raw["image"]["media_id"] == "media_xxx"


def test_wecom_file_decrypt_roundtrip():
    import base64
    import os

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from src.platform.channels.wecom import decrypt_wecom_file

    key = os.urandom(32)
    aes_key = base64.b64encode(key).decode("ascii").rstrip("=")
    iv = key[:16]
    plain = b"hello wecom media"
    pad_len = 16 - (len(plain) % 16)
    padded = plain + bytes([pad_len]) * pad_len
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    assert decrypt_wecom_file(encrypted, aes_key) == plain
