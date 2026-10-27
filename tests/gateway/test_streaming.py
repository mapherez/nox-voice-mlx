import asyncio
from contextlib import asynccontextmanager

from nox_voice_gateway.sessions import COOKIE


class EchoStream:
    def __init__(self):
        self.queue = asyncio.Queue()

    async def send(self, value):
        await self.queue.put(value)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.queue.get()


def install_stream(remote):
    @asynccontextmanager
    async def conversation():
        yield EchoStream()

    remote.conversation = conversation


def test_authenticated_audio_roundtrip_and_logout_close(authenticated):
    _, client, remote, token, headers = authenticated
    install_stream(remote)
    ws_headers = {"Origin": "https://voice.test", "Cookie": f"{COOKIE}={token}"}
    with client.websocket_connect("wss://voice.test/ws", headers=ws_headers) as socket:
        pcm = b"\x00" * 7680
        socket.send_bytes(pcm)
        assert socket.receive_bytes() == pcm
        assert client.post("/auth/logout", headers=headers).status_code == 200
        assert socket.receive()["type"] == "websocket.close"


def test_invalid_audio_frame_is_rejected(authenticated):
    _, client, remote, token, _ = authenticated
    install_stream(remote)
    with client.websocket_connect(
        "wss://voice.test/ws",
        headers={"Origin": "https://voice.test", "Cookie": f"{COOKIE}={token}"},
    ) as socket:
        socket.send_bytes(b"invalid-frame")
        event = socket.receive()
        assert event["type"] == "websocket.close"
        assert event["code"] == 1003
