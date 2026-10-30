"""Explicit live smoke test against this experiment only; no microphone or saved audio."""

import argparse
import base64
import contextlib
import hashlib
import json
import os
import socket
import struct
import time


class WebSocket:
    """Minimal test-only RFC6455 text client. The application uses the browser client."""

    def __init__(self, port):
        self.connection = socket.create_connection(("127.0.0.1", port), timeout=90)
        self.source = self.connection.makefile("rb")
        key = base64.b64encode(os.urandom(16)).decode()
        request = ("GET /realtime HTTP/1.1\r\nHost: localhost:{0}\r\n"
                   "Origin: http://localhost:{0}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {1}\r\n\r\n").format(port, key)
        self.connection.sendall(request.encode())
        status = self.source.readline().decode().strip()
        if "101" not in status:
            self.close()
            raise RuntimeError("WebSocket upgrade failed: " + status)
        headers = {}
        while True:
            line = self.source.readline().decode().strip()
            if not line:
                break
            name, value = line.split(":", 1)
            headers[name.lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            raise RuntimeError("Invalid WebSocket handshake.")

    def send_frame(self, payload, opcode=1):
        mask = os.urandom(4)
        size = len(payload)
        header = bytes([0x80 | opcode])
        if size < 126:
            header += bytes([0x80 | size])
        elif size < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", size)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", size)
        encoded = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.connection.sendall(header + mask + encoded)

    def send(self, event):
        self.send_frame(json.dumps(event).encode())

    def read(self, size):
        data = self.source.read(size)
        if len(data) != size:
            raise RuntimeError("The model connection closed unexpectedly.")
        return data

    def receive(self):
        fragments = bytearray()
        while True:
            first, second = self.read(2)
            size = second & 127
            if size == 126:
                size = struct.unpack("!H", self.read(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.read(8))[0]
            if size > 4_000_000:
                raise RuntimeError("Oversized test response.")
            payload = self.read(size)
            opcode = first & 15
            if opcode == 8:
                raise RuntimeError("The model closed its session.")
            if opcode == 9:
                self.send_frame(payload, 10)
                continue
            if opcode == 10:
                continue
            fragments.extend(payload)
            if first & 0x80:
                event = json.loads(fragments)
                if event.get("type") == "error":
                    raise RuntimeError(str(event.get("error")))
                return event

    def session(self, instructions, modalities):
        assert self.receive()["type"] == "session.created"
        self.send({"type": "session.update", "session": {
            "type": "realtime", "instructions": instructions,
            "output_modalities": modalities, "max_output_tokens": 512, "tool_choice": "none"}})
        assert self.receive()["type"] == "session.updated"

    def response_details(self):
        text, audio = "", bytearray()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            event = self.receive()
            if event["type"] == "response.output_text.delta":
                text += event["delta"]
            elif event["type"] == "response.output_audio.delta":
                audio.extend(base64.b64decode(event["delta"], validate=True))
            elif event["type"] == "response.done":
                assert event["response"]["status"] == "completed", event
                return text, bytes(audio), event["response"]
        raise RuntimeError("Live test timed out.")

    def response(self):
        text, audio, _ = self.response_details()
        return text, audio

    def close(self):
        with contextlib.suppress(OSError):
            self.send_frame(struct.pack("!H", 1000), 8)
            self.connection.shutdown(socket.SHUT_RDWR)
        self.source.close()
        self.connection.close()


def main(port):
    # Generate a question using this model, then feed that synthetic audio back as input.
    client = WebSocket(port)
    try:
        client.session("Perform TTS in American English using a female voice.", ["audio"])
        client.send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "What color is the sky on a clear day?"}]}})
        client.send({"type": "response.create"})
        _, question = client.response()
        assert len(question) > 24000, "No synthesized question audio."
        print("TTS generated {:.2f} seconds of synthetic question audio.".format(len(question) / 48000), flush=True)
    finally:
        client.close()
    time.sleep(.5)  # Let the runtime release its single conversation slot.
    client = WebSocket(port)
    try:
        client.session("Respond with interleaved text and audio.", ["text", "audio"])
        # Exact 3:2 area resampling for this runtime's 24 kHz -> 16 kHz format.
        samples = struct.unpack("<" + "h" * (len(question) // 2), question)
        pcm = bytearray()
        for index in range(0, len(samples) - 2, 3):
            a, b, c = samples[index:index + 3]
            pcm.extend(struct.pack("<hh", round((2 * a + b) / 3), round((b + 2 * c) / 3)))
        for start in range(0, len(pcm), 3200):
            client.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[start:start + 3200]).decode()})
        client.send({"type": "response.create"})
        text, audio = client.response()
        assert text.strip(), "No response text."
        assert len(audio) > 24000, "No spoken reply."
        print("Speech-to-speech reply: " + text, flush=True)
        print("Generated {:.2f} seconds of reply audio. No audio was saved.".format(len(audio) / 48000), flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    main(parser.parse_args().port)
