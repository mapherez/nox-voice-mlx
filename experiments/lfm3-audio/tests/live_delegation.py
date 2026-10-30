"""Own voice process + explicit LLM delegation + disposable MCP app. No real notes."""

import argparse
import base64
import contextlib
import json
import struct
import subprocess
import time
from types import SimpleNamespace

from live_mcp import local_app, post
from llm_fixture import LLMFixture
from mcp_fixture import MCPFixture
from smoke import WebSocket


def run(url=None, model=None):
    context = contextlib.nullcontext(SimpleNamespace(url=url)) if url else LLMFixture()
    with context as llm, MCPFixture(sse=True, name="NoteX") as fixture, local_app() as port:
        print("Own voice model and disposable MCP app ready.", flush=True)
        question = WebSocket(port)
        try:
            question.session("Perform TTS in American English using a female voice.", ["audio"])
            question.send({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                          "content": [{"type": "input_text", "text": "Use the local LLM to create a note in No Tex. The exact title must be Backup Guide. The content should explain backups with headings and a short checklist."}]}})
            question.send({"type": "response.create"})
            _, audio = question.response()
        finally:
            question.close()
        time.sleep(.25)
        profile = post(port, "/api/mcp/connect", {"url": fixture.url})
        settings = post(port, "/api/delegation/configure", {"url": llm.url, "model": model or "fixture-model",
                        "connectionId": profile["id"], "target": "create_note"})
        # Use the production tool schema and prompt, not a separate test approximation.
        script = ('import {readFileSync} from "node:fs"; '
                  'import {sessionSettings,delegatedProfile} from "./app/tools.js"; '
                  'const d=JSON.parse(readFileSync(0,"utf8")); '
                  'process.stdout.write(JSON.stringify(sessionSettings(delegatedProfile(d.profile,d.settings))));')
        result = subprocess.run(["node", "--input-type=module", "-e", script],
                                input=json.dumps({"profile": profile, "settings": settings}),
                                capture_output=True, text=True, check=True)
        session = json.loads(result.stdout)
        notice_script = ('import {readFileSync} from "node:fs"; '
                         'import {delegationNoticeSession,delegationMessages} from "./app/tools.js"; '
                         'process.stdout.write(JSON.stringify({session:delegationNoticeSession(),messages:delegationMessages(JSON.parse(readFileSync(0,"utf8")))}));')
        notice_config = json.loads(subprocess.run(["node", "--input-type=module", "-e", notice_script],
                                                 input=json.dumps(profile), capture_output=True, text=True, check=True).stdout)
        for kind, phrase in notice_config["messages"].items():
            notice = WebSocket(port)
            try:
                assert notice.receive()["type"] == "session.created"
                notice.send({"type": "session.update", "session": notice_config["session"]})
                assert notice.receive()["type"] == "session.updated"
                notice.send({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                             "content": [{"type": "input_text", "text": phrase}]}})
                notice.send({"type": "response.create"})
                notice_text, notice_audio = notice.response()
                assert len(notice_audio) > 24000, "Spoken notice must contain playable speech"
                assert len(notice_audio) <= 480000, "Spoken notice must fit the preparation limit"
                if not url:
                    assert not llm.requests
                assert fixture.calls == []
                print("Prepared", kind, repr(phrase), "(%.2f seconds)" % (len(notice_audio) / 48000), flush=True)
            finally:
                notice.close()
        client = WebSocket(port)
        try:
            assert client.receive()["type"] == "session.created"
            client.send({"type": "session.update", "session": session})
            assert client.receive()["type"] == "session.updated"
            samples = struct.unpack("<" + "h" * (len(audio) // 2), audio)
            pcm = bytearray()
            for index in range(0, len(samples) - 2, 3):
                a, b, c = samples[index:index + 3]
                pcm.extend(struct.pack("<hh", round((2 * a + b) / 3), round((b + 2 * c) / 3)))
            for start in range(0, len(pcm), 3200):
                client.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[start:start + 3200]).decode()})
            client.send({"type": "response.create"})
            _, _, response = client.response_details()
            calls = [item for item in response.get("output", []) if item["type"] == "function_call"]
            assert len(calls) == 1 and calls[0]["name"] == "delegate_local_llm", response
            call = calls[0]
            task = json.loads(call["arguments"])["task"]
            assert "notex" in task.lower().replace(" ", ""), "The app reference must use the connected NoteX vocabulary: " + task
            print("Voice selected delegation:", task, flush=True)
            draft = post(port, "/api/delegation/generate", {"configurationId": settings["id"], "connectionId": profile["id"],
                         "invocationId": "live-delegation:" + call["call_id"], "task": task}, timeout=180)
            assert fixture.notes == [], "Generating must not write a note"
            record = post(port, "/api/delegation/execute", {"draftId": draft["draftId"]})
            assert not record["isError"], record
            assert fixture.notes[0]["content"] == record["arguments"]["content"]
            assert "\n" in fixture.notes[0]["content"]
            receipt_script = ('import {readFileSync} from "node:fs"; '
                              'import {toolResultForModel} from "./app/tools.js"; '
                              'process.stdout.write(toolResultForModel(JSON.parse(readFileSync(0,"utf8"))));')
            receipt = subprocess.run(["node", "--input-type=module", "-e", receipt_script],
                                     input=json.dumps(record), capture_output=True, text=True, check=True).stdout
            client.send({"type": "conversation.item.create", "item": {"type": "function_call_output",
                        "call_id": call["call_id"], "output": receipt}})
            completion_script = ('import {silentDelegationCompletion} from "./app/tools.js"; '
                                 'process.stdout.write(JSON.stringify(silentDelegationCompletion()));')
            completion = json.loads(subprocess.run(["node", "--input-type=module", "-e", completion_script],
                                                  capture_output=True, text=True, check=True).stdout)
            client.send(completion)
            text, reply_audio, final = client.response_details()
            assert not reply_audio, "Model continuation must be silent: the app plays the short completion clip"
            assert not any(item["type"] == "function_call" for item in final.get("output", []))
            assert len(fixture.notes) == 1 and len(fixture.calls) == 1
            client.send({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": "Create another note in NoteX titled Second Guide about backups."}]}})
            client.send({"type": "response.create"})
            _, _, followup = client.response_details()
            assert any(item["type"] == "function_call" and item["name"] == "delegate_local_llm"
                       for item in followup.get("output", [])), "The next turn must still support delegation"
            print("PASS: No Tex speech -> NoteX delegate -> exact generated arguments -> MCP; short completion clip, silent model continuation, next turn available.", flush=True)
        finally:
            client.close()
            post(port, "/api/mcp/disconnect", {})
    print("Own processes stopped. No audio or real notes saved; external LLM server left running.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Explicit LM Studio /api/v1/chat URL; otherwise uses a fixture.")
    parser.add_argument("--model", help="Model ID at that explicitly supplied endpoint.")
    args = parser.parse_args()
    if bool(args.url) != bool(args.model):
        parser.error("supply both --url and --model, or neither")
    run(args.url, args.model)
