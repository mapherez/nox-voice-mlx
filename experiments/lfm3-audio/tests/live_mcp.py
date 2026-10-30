"""Explicit test: own model process + own MCP app + synthetic speech; no real user apps."""

import base64
import contextlib
import http.client
import json
import secrets
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiment
from mcp_fixture import MCPFixture
from smoke import WebSocket


@contextlib.contextmanager
def local_app():
    """Allocate unique ports and retain the actual child handle for cleanup."""
    port, key = experiment.free_port(), secrets.token_urlsafe(32)
    data = experiment.manifest()["model"]["files"]
    model, projector = [str(experiment.local_path("models/" + item["name"])) for item in data]
    env = experiment.runtime_environment()
    env["LLAMA_API_KEY"] = key
    temporary = tempfile.TemporaryDirectory(prefix="mcp-live-", dir=experiment.local_path(".tmp"))
    with (Path(temporary.name) / "runtime.log").open("wb") as log:
        process = subprocess.Popen([str(experiment.runtime_binary()), "--model", model, "--mmproj", projector,
                                    "--host", "127.0.0.1", "--port", str(port), "--offline", "--ctx-size", "32768",
                                    "--parallel", "1", "--n-gpu-layers", "99", "--no-warmup", "--log-verbosity", "2"],
                                   cwd=experiment.ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        server, thread = None, None
        try:
            experiment.wait_ready(process, port, key)
            server = experiment.LocalServer(("127.0.0.1", 0), port, key)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            yield server.server_port
        finally:
            if server:
                server.close_connections()
                server.shutdown()
                server.server_close()
            if thread:
                thread.join()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    temporary.cleanup()


def post(port, path, body, timeout=40):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("POST", path, json.dumps(body), {
            "Content-Type": "application/json", "Origin": "http://127.0.0.1:" + str(port)})
        response = connection.getresponse()
        result = json.loads(response.read())
        assert response.status == 200, result
        return result
    finally:
        connection.close()


def main():
    with MCPFixture(sse=True) as fixture, local_app() as port:
        print("Own model and MCP fixture ready on temporary ports.", flush=True)
        question = WebSocket(port)
        try:
            question.session("Perform TTS in American English using a female voice.", ["audio"])
            question.send({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                          "content": [{"type": "input_text", "text": "Create a note called Voice test with the content Hello from audio."}]}})
            question.send({"type": "response.create"})
            _, audio = question.response()
            assert len(audio) > 24000
        finally:
            question.close()
        time.sleep(.25)
        profile = post(port, "/api/mcp/connect", {"url": fixture.url})
        client = WebSocket(port)
        try:
            assert client.receive()["type"] == "session.created"
            client.send({"type": "session.update", "session": {"type": "realtime",
                        "instructions": "Respond with function calls, and interleaved text and audio.\n\nList of tools:",
                        "tools": [tool["function"] for tool in profile["tools"]], "tool_choice": "auto", "max_output_tokens": 1024}})
            assert client.receive()["type"] == "session.updated"
            samples = struct.unpack("<" + "h" * (len(audio) // 2), audio)
            pcm = bytearray()
            for index in range(0, len(samples) - 2, 3):
                a, b, c = samples[index:index + 3]
                pcm.extend(struct.pack("<hh", round((2 * a + b) / 3), round((b + 2 * c) / 3)))
            for start in range(0, len(pcm), 3200):
                client.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[start:start + 3200]).decode()})
            client.send({"type": "response.create"})
            calls = 0
            for _ in range(7):
                text, reply_audio, response = client.response_details()
                pending = [item for item in response.get("output", []) if item["type"] == "function_call"]
                if not pending:
                    assert calls > 0, "The model answered without calling a tool: " + text
                    assert text.strip() and len(reply_audio) > 1000, "No spoken final response."
                    print("Spoken final reply: " + text, flush=True)
                    break
                for call in pending:
                    calls += 1
                    record = post(port, "/api/mcp/call", {"connectionId": profile["id"], "invocationId": "live:" + call["call_id"],
                                  "name": call["name"], "arguments": json.loads(call["arguments"])})
                    assert not record["isError"], record
                    print("Executed fixture tool:", record["name"], call["arguments"], flush=True)
                    client.send({"type": "conversation.item.create", "item": {"type": "function_call_output",
                                "call_id": call["call_id"], "output": json.dumps(record["result"])}})
                client.send({"type": "response.create"})
            else:
                raise AssertionError("Tool loop did not finish.")
            assert len(fixture.notes) == 1, fixture.notes
            assert "voice" in fixture.notes[0]["title"].lower(), fixture.notes
            assert "hello" in fixture.notes[0]["content"].lower(), fixture.notes
            print("PASS: synthetic speech -> model tool choice -> MCP write -> actual result -> spoken reply.", flush=True)
        finally:
            client.close()
            post(port, "/api/mcp/disconnect", {})
    print("Own model, MCP fixture and all test ports stopped. No notes or audio saved.", flush=True)


if __name__ == "__main__":
    main()
