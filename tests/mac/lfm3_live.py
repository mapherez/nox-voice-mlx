"""Opt-in ASR/TTS/lifecycle check using explicitly supplied experiment assets.

Creates an isolated disposable installation, reuses immutable model inodes and
owns both test processes. Never connects to an existing controller or MCP app.
"""

import argparse
import asyncio
import base64
import json
import os
import shutil
import struct
import tempfile
import time
from pathlib import Path

import psutil
from controller.config import ControllerConfig, write_private
from controller.profiles import VoiceConfiguration
from controller.supervisor import Supervisor
from engine.lfm3.assets import asset_root, checked, inventory, manifest
from engine.lfm3.runtime import free_port
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[2]


async def event(wire, kind, **matches):
    async with asyncio.timeout(120):
        while True:
            value = json.loads(await wire.recv())
            if value["type"] == "error":
                raise RuntimeError(value["message"])
            if value["type"] == kind and all(
                value.get(key) == expected for key, expected in matches.items()
            ):
                return value


def resample(audio):
    samples = struct.unpack("<" + "h" * (len(audio) // 2), audio)
    result = bytearray()
    for index in range(0, len(samples) - 2, 3):
        a, b, c = samples[index : index + 3]
        result.extend(struct.pack("<hh", round((2 * a + b) / 3), round((b + 2 * c) / 3)))
    return result


async def exercise(config, language):
    manager = Supervisor(config)
    manager.configuration = VoiceConfiguration(selectedProvider="lfm3-audio")
    manager.configuration.profiles.lfm3_audio.inputLanguage = language
    manager.configuration.profiles.lfm3_audio.outputLanguage = (
        "American English" if language == "English" else "Portuguese"
    )
    owned_children = []
    started = time.monotonic()
    try:
        await manager.start()
        async with asyncio.timeout(210):
            while manager.state == "starting":
                await asyncio.sleep(0.2)
        assert manager.state == "ready", manager.status()["error"]
        owned_children = psutil.Process(manager.process.pid).children(recursive=True)
        print(
            f"Worker ready in {time.monotonic() - started:.2f}s; {len(owned_children)} owned native child.",
            flush=True,
        )
        async with connect(
            f"ws://127.0.0.1:{config.worker_port}/voice/v1/ws",
            additional_headers=manager.headers,
            proxy=None,
            max_size=65536,
        ) as wire:
            await wire.send('{"type":"session.start","version":1}')
            ready = await event(wire, "session.ready")
            session = ready["sessionId"]

            async def send(kind, **value):
                await wire.send(json.dumps({"type": kind, "sessionId": session, **value}))

            phrase = (
                "Find all notes with the title Hello World and delete the results."
                if language == "English"
                else "Lista todos os recursos e apaga os resultados."
            )
            started = time.monotonic()
            await send("speech.request", playbackId=1, text=phrase)
            audio = bytearray()
            first = None
            async with asyncio.timeout(90):
                while True:
                    message = json.loads(await wire.recv())
                    if message["type"] == "error":
                        raise RuntimeError(message["message"])
                    if message["type"] == "speech.audio":
                        if first is None:
                            first = time.monotonic() - started
                        audio.extend(base64.b64decode(message["audio"]))
                    elif message["type"] == "speech.ended":
                        break
            assert len(audio) > 24000
            print(
                f"TTS first packet {first:.2f}s; {len(audio) / 48000:.2f}s synthetic speech.",
                flush=True,
            )
            pcm = resample(audio)

            async def transcribe(turn):
                started = time.monotonic()
                await send("turn.start", turnId=turn)
                await event(wire, "turn.started", turnId=turn)
                for index in range(0, len(pcm), 3200):
                    await send(
                        "input.audio",
                        turnId=turn,
                        audio=base64.b64encode(pcm[index : index + 3200]).decode(),
                    )
                    # Production capture sends 100 ms packets at the microphone rate.
                    await asyncio.sleep(0.01)
                await send("turn.commit", turnId=turn)
                result = await event(wire, "transcript.completed", turnId=turn)
                expected = (
                    ("hello world", "delete") if language == "English" else ("recursos", "apaga")
                )
                assert all(word in result["text"].lower() for word in expected), result
                print(
                    f"ASR turn {turn}: {time.monotonic() - started:.2f}s; {result['text']}",
                    flush=True,
                )

            await transcribe(1)
            await send(
                "speech.request",
                playbackId=2,
                text="This deliberately longer sentence is used to interrupt generation and immediately capture a new instruction in the same audio session.",
            )
            await event(wire, "speech.audio", playbackId=2)
            await send("speech.interrupt", playbackId=2)
            await event(wire, "speech.interrupted", playbackId=2)
            await transcribe(2)
        print(
            "Two separate ASR turns and speech interruption passed; no executor/MCP was contacted.",
            flush=True,
        )
    finally:
        await manager.close()
        assert all(not child.is_running() for child in owned_children), (
            "Owned native runtime survived Stop"
        )
        print("Owned worker/runtime stopped; temporary installation is being removed.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--language", choices=["English", "Portuguese"], default="English")
    args = parser.parse_args()
    source = args.experiment.resolve()
    data = manifest()
    temporary = ROOT / ".tmp"
    temporary.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lfm3-live-", dir=temporary) as folder:
        config = ControllerConfig(
            data_dir=Path(folder) / "data",
            weights_dir=Path(folder) / "weights",
            worker_port=free_port(),
        )
        root = asset_root(config.weights_dir)
        (root / "models").mkdir(parents=True)
        for item in data["model"]["files"]:
            original = source / "models" / item["name"]
            assert checked(original, item), "Explicitly supplied source model failed verification"
            os.link(original, root / "models" / item["name"])
        runtime = root / "runtime"
        shutil.copytree(source / "runtime" / "llama-b11960", runtime, symlinks=True)
        write_private(
            root / "installed.json",
            json.dumps(
                {
                    "schemaVersion": 1,
                    "artifacts": data,
                    "binary": "llama-server",
                    "runtimeFiles": inventory(runtime),
                }
            ),
        )
        asyncio.run(exercise(config, args.language))


if __name__ == "__main__":
    main()
