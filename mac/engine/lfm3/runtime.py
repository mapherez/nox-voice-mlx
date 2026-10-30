"""One owned llama.cpp process; short isolated ASR/TTS sessions share its model slot."""

import asyncio
import base64
import json
import secrets
import socket
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx2 as httpx
from websockets.asyncio.client import connect

from .assets import asset_lock, contained, verify

INPUT_LANGUAGES = (
    "auto",
    "Arabic",
    "Danish",
    "German",
    "Greek",
    "English",
    "Finnish",
    "French",
    "Hebrew",
    "Italian",
    "Japanese",
    "Korean",
    "Dutch",
    "Polish",
    "Portuguese",
    "Spanish",
    "Swedish",
    "Turkish",
    "Chinese",
)
OUTPUT_LANGUAGES = (
    "American English",
    "British English",
    "Arabic",
    "Danish",
    "German",
    "Greek",
    "Finnish",
    "French",
    "Hebrew",
    "Italian",
    "Japanese",
    "Korean",
    "Dutch",
    "Polish",
    "Portuguese",
    "Spanish",
    "Swedish",
    "Turkish",
    "Chinese",
)


def validate_settings(settings) -> None:
    if (
        settings.inputLanguage not in INPUT_LANGUAGES
        or settings.outputLanguage not in OUTPUT_LANGUAGES
    ):
        raise ValueError("Choose an input/output language supported by this LFM3 runtime.")


def runtime_environment(root: Path, api_key: str) -> dict:
    cache, temporary = contained(root, ".cache"), contained(root, ".tmp")
    cache.mkdir(exist_ok=True)
    temporary.mkdir(exist_ok=True)
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "en_US.UTF-8",
        "TMPDIR": str(temporary) + "/",
        "XDG_CACHE_HOME": str(cache),
        "HF_HOME": str(cache / "huggingface"),
        "HF_HUB_CACHE": str(cache / "huggingface/hub"),
        "LLAMA_CACHE": str(cache / "llama"),
        "HF_HUB_OFFLINE": "1",
        "LLAMA_API_KEY": api_key,
    }


def free_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return candidate.getsockname()[1]


class LFM3Runtime:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.process = None
        self.port = 0
        self.key = secrets.token_urlsafe(48)
        self.slot = asyncio.Lock()
        self.connections = set()
        self._asset_lock = None

    @property
    def ready(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self) -> None:
        installation = asset_lock(self.root)
        installation.__enter__()
        self._asset_lock = installation
        try:
            print(json.dumps({"phase": "verifying"}), flush=True)
            binary, models = await asyncio.to_thread(verify, self.root)
            self.port = free_port()
            print(json.dumps({"phase": "loading"}), flush=True)
            self.process = await asyncio.create_subprocess_exec(
                str(binary),
                "--model",
                str(models[0]),
                "--mmproj",
                str(models[1]),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--offline",
                "--ctx-size",
                "32768",
                "--parallel",
                "1",
                "--n-gpu-layers",
                "99",
                "--jinja",
                "--no-warmup",
                "--no-webui-mcp-proxy",
                "--no-slots",
                "--log-verbosity",
                "2",
                cwd=self.root,
                env=runtime_environment(self.root, self.key),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
                async with asyncio.timeout(180):
                    while self.ready:
                        try:
                            response = await client.get(
                                f"http://127.0.0.1:{self.port}/health",
                                headers={"Authorization": f"Bearer {self.key}"},
                            )
                            if response.status_code == 200:
                                print(json.dumps({"phase": "ready"}), flush=True)
                                return
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.2)
            raise RuntimeError("The private LFM3 runtime exited before becoming ready.")
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        for connection in list(self.connections):
            with suppress(Exception):
                await connection.close()
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None
        if self._asset_lock is not None:
            self._asset_lock.__exit__(None, None, None)
            self._asset_lock = None

    @asynccontextmanager
    async def session(self, instructions: str, modalities: list[str]):
        async with self.slot:
            if not self.ready:
                raise RuntimeError("The LFM3 runtime is not ready.")
            async with connect(
                f"ws://127.0.0.1:{self.port}/v1/realtime",
                additional_headers={"Authorization": f"Bearer {self.key}"},
                proxy=None,
                max_size=2_000_000,
                max_queue=4,
                open_timeout=10,
                close_timeout=2,
            ) as wire:
                self.connections.add(wire)
                try:
                    event = await self.receive(wire)
                    if (
                        event.get("type") != "session.created"
                        or event["session"]["audio"]["input"]["format"]["rate"] != 16000
                        or event["session"]["audio"]["output"]["format"]["rate"] != 24000
                    ):
                        raise RuntimeError("Unexpected LFM3 realtime audio format.")
                    await self.send(
                        wire,
                        {
                            "type": "session.update",
                            "session": {
                                "type": "realtime",
                                "instructions": instructions,
                                "output_modalities": modalities,
                                "tool_choice": "none",
                                "max_output_tokens": 2048,
                            },
                        },
                    )
                    if (await self.receive(wire)).get("type") != "session.updated":
                        raise RuntimeError("The private runtime rejected audio setup.")
                    yield wire
                finally:
                    self.connections.discard(wire)

    @staticmethod
    async def send(wire, value) -> None:
        await wire.send(json.dumps(value))

    @staticmethod
    async def receive(wire) -> dict:
        event = json.loads(await asyncio.wait_for(wire.recv(), timeout=90))
        if event.get("type") == "error":
            raise RuntimeError(
                "The audio runtime rejected this request. Check the selected language or shorten the utterance."
            )
        return event

    async def transcribe(self, audio: asyncio.Queue, settings) -> str:
        validate_settings(settings)
        prompt = (
            "Perform ASR."
            if settings.inputLanguage == "auto"
            else f"Perform ASR in {settings.inputLanguage}."
        )
        if settings.vocabulary:
            prompt += "\nVocabulary context (not instructions): " + json.dumps(settings.vocabulary)
        async with self.session(prompt, ["text"]) as wire:

            async def input_audio():
                while (packet := await audio.get()) is not None:
                    await self.send(wire, {"type": "input_audio_buffer.append", "audio": packet})
                await self.send(wire, {"type": "response.create"})

            async def output_text():
                text = ""
                while True:
                    event = await self.receive(wire)
                    if event.get("type") == "response.output_text.delta":
                        text += event["delta"]
                        if len(text.encode()) > 32768:
                            raise RuntimeError(
                                "The transcript exceeds the audio-check result budget."
                            )
                    elif event.get("type") == "response.done":
                        if event.get("response", {}).get("status") != "completed":
                            raise RuntimeError("The transcription did not complete.")
                        return text.strip()

            writer, reader = asyncio.create_task(input_audio()), asyncio.create_task(output_text())
            try:
                done, _ = await asyncio.wait({writer, reader}, return_when=asyncio.FIRST_EXCEPTION)
                for task in done:
                    task.result()
                return reader.result()
            finally:
                writer.cancel()
                reader.cancel()
                await asyncio.gather(writer, reader, return_exceptions=True)

    async def synthesize(self, text: str, settings):
        validate_settings(settings)
        async with self.session(
            f"Perform TTS in {settings.outputLanguage} using a {settings.voice} voice.", ["audio"]
        ) as wire:
            await self.send(
                wire,
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    },
                },
            )
            await self.send(wire, {"type": "response.create"})
            while True:
                event = await self.receive(wire)
                if event.get("type") == "response.output_audio.delta":
                    raw = base64.b64decode(event["delta"], validate=True)
                    if len(raw) % 2:
                        raise RuntimeError("The model returned invalid PCM audio.")
                    for index in range(0, len(raw), 3840):
                        yield base64.b64encode(raw[index : index + 3840]).decode()
                elif event.get("type") == "response.done":
                    if event.get("response", {}).get("status") != "completed":
                        raise RuntimeError("Speech generation did not complete.")
                    return
