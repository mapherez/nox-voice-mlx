"""Installed provider definitions. Catalogue access never imports an inference runtime."""

from dataclasses import dataclass
from pathlib import Path

from engine.lfm3 import assets as lfm3_assets
from engine.lfm3.runtime import INPUT_LANGUAGES, OUTPUT_LANGUAGES, validate_settings
from pydantic import BaseModel

from .config import VOICE_NAMES, ControllerConfig, VoiceSettings
from .model_files import asset_root as personaplex_asset_root
from .model_files import missing_files
from .profiles import LFM3Settings, ProviderId, VoiceConfiguration


@dataclass(frozen=True)
class Provider:
    id: ProviderId
    name: str
    model_id: str
    runtime: str
    worker_module: str | None
    settings_model: type[BaseModel]
    profile_key: str
    native_conversation: bool = False
    legacy_audio: bool = False
    reason: str = ""

    @property
    def implemented(self) -> bool:
        return self.worker_module is not None

    def settings(self, configuration: VoiceConfiguration) -> BaseModel:
        return getattr(configuration.profiles, self.profile_key)

    def worker_settings_file(self, config: ControllerConfig) -> Path:
        return config.data_dir / "workers" / f"{self.id}-settings.json"

    def assets_dir(self, config: ControllerConfig) -> Path:
        return (
            lfm3_assets.asset_root(config.weights_dir)
            if self.id == "lfm3-audio"
            else personaplex_asset_root(config.weights_dir)
        )

    def missing_assets(self, config: ControllerConfig) -> list[str]:
        try:
            return (
                lfm3_assets.missing_assets(self.assets_dir(config))
                if self.id == "lfm3-audio"
                else missing_files(self.assets_dir(config))
            )
        except (OSError, ValueError):
            return ["invalid installation"]

    def validate(self, configuration: VoiceConfiguration) -> None:
        if self.id == "lfm3-audio":
            validate_settings(self.settings(configuration))

    def phase_labels(self) -> dict[str, str]:
        return {
            "verifying": "Verifying model files",
            "loading": f"Loading {self.name}",
            "quantizing": "Applying quantization",
            "codec": "Loading audio codec",
            "warming": "Warming up the model",
            "voice": "Preparing voice",
            "ready": "Ready",
        }

    def describe(self, config: ControllerConfig) -> dict:
        implemented = self.implemented
        missing = self.missing_assets(config)
        schema = self.settings_model.model_json_schema()
        if self.id == "personaplex":
            schema["properties"]["voice"].update(
                title="Assistant voice",
                enumLabels={name: voice_label(name) for name in VOICE_NAMES},
            )
            schema["properties"]["prompt"].update(title="Persona", format="textarea")
            schema["properties"]["bits"].update(
                title="Model precision",
                enumLabels={
                    str(bits): f"{bits}-bit" if bits else "Full precision"
                    for bits in (0, 4, 5, 6, 8)
                },
            )
        else:
            schema["properties"]["inputLanguage"].update(
                title="Input language", enum=list(INPUT_LANGUAGES)
            )
            schema["properties"]["outputLanguage"].update(
                title="Speech language", enum=list(OUTPUT_LANGUAGES)
            )
            schema["properties"]["vocabulary"].update(title="Vocabulary hints", format="textarea")
        return {
            "id": self.id,
            "modelId": self.model_id,
            "name": self.name,
            "runtime": self.runtime,
            "implemented": implemented,
            "selectable": implemented,
            "availability": "not_implemented"
            if not implemented
            else "preparation_required"
            if missing
            else "files_present",
            "reason": self.reason
            or (
                "Prepare model files before starting."
                if missing
                else "Model files are verified when the engine starts."
            ),
            "capabilities": {
                "nativeConversation": self.native_conversation,
                "legacyAudio": self.legacy_audio,
                "executor": False,
                "audioChecks": self.id == "lfm3-audio" and implemented,
                "pushToTalk": self.id == "lfm3-audio" and implemented,
                "transcription": self.id == "lfm3-audio" and implemented,
                "speech": self.id == "lfm3-audio" and implemented,
                "assetPreparation": implemented,
            },
            "audio": {
                "inputRate": 24000,
                "outputRate": 24000,
                "format": "float32le",
                "frameSamples": 1920,
            }
            if self.legacy_audio
            else {
                "inputRate": 16000,
                "outputRate": 24000,
                "format": "pcm16le",
                "packetSamples": 1600,
            },
            "settingsSchema": schema,
            "settingsApply": {
                "voice": "next_conversation",
                "prompt": "next_conversation",
                "bits": "worker_reload",
            }
            if self.id == "personaplex"
            else {key: "next_conversation" for key in schema["properties"]},
            "languageNotice": "The official conversational model supports English. Other languages are experimental."
            if self.id == "personaplex"
            else "Audio checks are available. The conversation executor is not integrated yet. Portuguese speech quality is experimental.",
        }


def voice_label(name: str) -> str:
    groups = {
        "NATF": "Natural female",
        "NATM": "Natural male",
        "VARF": "Variety female",
        "VARM": "Variety male",
    }
    return f"{groups[name[:-1]]} {int(name[-1]) + 1}" + (" · Kendra" if name == "NATF0" else "")


PROVIDERS = {
    "personaplex": Provider(
        "personaplex",
        "PersonaPlex 7B",
        "nvidia/personaplex-7b-v1",
        "MLX",
        "engine.server",
        VoiceSettings,
        "personaplex",
        native_conversation=True,
        legacy_audio=True,
    ),
    "lfm3-audio": Provider(
        "lfm3-audio",
        "LFM3-Audio-2B",
        "LiquidAI/LFM3-Audio-2B-GGUF",
        "llama.cpp",
        "engine.lfm3.server",
        LFM3Settings,
        "lfm3_audio",
    ),
}


def provider(provider_id: ProviderId) -> Provider:
    return PROVIDERS[provider_id]
