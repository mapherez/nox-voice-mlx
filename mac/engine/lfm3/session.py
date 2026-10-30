"""Versioned audio-check protocol, with cancellable operations and no MCP execution."""

import asyncio
import base64
import binascii
import secrets
from contextlib import suppress
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sessionId: str = Field(min_length=1, max_length=64)


class StartTurn(Control):
    type: Literal["turn.start"]
    turnId: int = Field(ge=1, le=2**31 - 1)


class AudioPacket(StartTurn):
    type: Literal["input.audio"]
    audio: str = Field(min_length=1, max_length=4500)


class CommitTurn(StartTurn):
    type: Literal["turn.commit"]


class Speak(Control):
    type: Literal["speech.request"]
    playbackId: int = Field(ge=1, le=2**31 - 1)
    text: str = Field(min_length=1, max_length=2000)


class Interrupt(Control):
    type: Literal["speech.interrupt"]
    playbackId: int = Field(ge=1, le=2**31 - 1)


MESSAGE = TypeAdapter(
    Annotated[StartTurn | AudioPacket | CommitTurn | Speak | Interrupt, Field(discriminator="type")]
)


class AudioSession:
    def __init__(self, runtime, settings, send):
        self.runtime, self.settings, self.send = runtime, settings.model_copy(deep=True), send
        self.id = secrets.token_hex(16)
        self.sequence = 0
        self.turn = 0
        self.playback = 0
        self.operation = None
        self.operation_kind = None
        self.audio = None
        self.committed = False
        self.audio_samples = 0
        self.closed = False

    async def emit(self, kind, **value):
        if self.closed:
            return
        self.sequence += 1
        await self.send({"type": kind, "sessionId": self.id, "sequence": self.sequence, **value})

    async def ready(self):
        await self.emit(
            "session.ready",
            version=1,
            provider="lfm3-audio",
            audio={"inputRate": 16000, "outputRate": 24000, "format": "pcm16le"},
            capabilities={"transcription": True, "speech": True, "executor": False},
        )

    async def cancel_operation(self):
        task, kind = self.operation, self.operation_kind
        self.operation = self.operation_kind = None
        self.audio = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if kind == "speech":
            await self.emit("speech.interrupted", playbackId=self.playback)

    async def close(self):
        self.closed = True
        await self.cancel_operation()

    async def handle(self, value):
        message = MESSAGE.validate_python(value)
        if message.sessionId != self.id:
            raise ValueError("This message belongs to another audio session.")
        if isinstance(message, AudioPacket):
            if message.turnId < self.turn:
                return
            if message.turnId != self.turn or self.audio is None or self.committed:
                raise ValueError("Audio arrived outside its active turn.")
            try:
                packet = base64.b64decode(message.audio, validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("Invalid microphone encoding.") from None
            if not packet or len(packet) > 3200 or len(packet) % 2:
                raise ValueError("Invalid microphone PCM frame.")
            try:
                self.audio.put_nowait(message.audio)
            except asyncio.QueueFull:
                await self.cancel_operation()
                raise ValueError("The audio connection cannot keep up. Start a new turn.") from None
            self.audio_samples += len(packet) // 2
        elif isinstance(message, CommitTurn):
            if message.turnId < self.turn:
                return
            if message.turnId != self.turn or self.audio is None or self.committed:
                raise ValueError("This audio turn is not recording.")
            if not self.audio_samples:
                await self.cancel_operation()
                await self.emit("transcript.completed", turnId=self.turn, text="")
                return
            self.committed = True
            try:
                self.audio.put_nowait(None)
            except asyncio.QueueFull:
                await self.cancel_operation()
                raise ValueError("The audio connection cannot keep up. Start a new turn.") from None
            await self.emit("turn.committed", turnId=self.turn)
        elif isinstance(message, StartTurn):
            if message.turnId <= self.turn:
                raise ValueError("Audio turn identifiers must increase.")
            await self.cancel_operation()
            self.turn, self.audio_samples, self.committed = message.turnId, 0, False
            self.audio = asyncio.Queue(maxsize=32)
            self.operation_kind = "transcription"
            self.operation = asyncio.create_task(self.transcribe(self.turn, self.audio))
            await self.emit("turn.started", turnId=self.turn)
        elif isinstance(message, Speak):
            if message.playbackId <= self.playback:
                raise ValueError("Playback identifiers must increase.")
            if self.audio is not None and not self.committed:
                raise ValueError("Finish speaking before requesting playback.")
            await self.cancel_operation()
            self.playback = message.playbackId
            self.operation_kind = "speech"
            self.operation = asyncio.create_task(self.speak(self.playback, message.text))
        elif isinstance(message, Interrupt):
            if message.playbackId == self.playback and self.operation_kind == "speech":
                await self.cancel_operation()

    async def transcribe(self, turn, audio):
        try:
            text = await self.runtime.transcribe(audio, self.settings)
            if turn == self.turn:
                self.audio = None
                await self.emit("transcript.completed", turnId=turn, text=text)
        except asyncio.CancelledError:
            raise
        except Exception:
            if turn == self.turn:
                self.audio = None
                with suppress(Exception):
                    await self.emit(
                        "error",
                        turnId=turn,
                        code="TRANSCRIPTION_FAILED",
                        message="Transcription failed. Check the input language and try again.",
                    )
        finally:
            if self.operation is asyncio.current_task():
                self.operation = self.operation_kind = None

    async def speak(self, playback, text):
        try:
            await self.emit("speech.started", playbackId=playback)
            async for packet in self.runtime.synthesize(text, self.settings):
                if playback != self.playback or self.closed:
                    return
                await self.emit("speech.audio", playbackId=playback, audio=packet)
            await self.emit("speech.ended", playbackId=playback)
        except asyncio.CancelledError:
            raise
        except Exception:
            with suppress(Exception):
                await self.emit(
                    "error",
                    playbackId=playback,
                    code="SPEECH_FAILED",
                    message="Speech generation failed. Check the speech settings and try again.",
                )
        finally:
            if self.operation is asyncio.current_task():
                self.operation = self.operation_kind = None
