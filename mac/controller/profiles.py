"""Versioned provider settings; load without side effects and migrate on explicit save."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import ControllerConfig, VoiceSettings, write_private

ProviderId = Literal["personaplex", "lfm3-audio"]


class LFM3Settings(BaseModel):
    """Independent configuration reserved for the LFM3 adapter, not a runnable provider yet."""

    model_config = ConfigDict(extra="forbid", strict=True)
    inputLanguage: str = Field(default="auto", min_length=1, max_length=80)
    vocabulary: str = Field(default="", max_length=4096)
    outputLanguage: str = Field(default="American English", min_length=1, max_length=80)
    voice: Literal["female", "male"] = "female"


class ProviderProfiles(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    personaplex: VoiceSettings = Field(default_factory=VoiceSettings)
    lfm3_audio: LFM3Settings = Field(default_factory=LFM3Settings, alias="lfm3-audio")


class VoiceConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schemaVersion: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    selectedProvider: ProviderId = "personaplex"
    profiles: ProviderProfiles = Field(default_factory=ProviderProfiles)

    def document(self) -> dict:
        return self.model_dump(mode="json", by_alias=True)


class ProfilesUpdate(ProviderProfiles):
    personaplex: VoiceSettings = Field(...)
    lfm3_audio: LFM3Settings = Field(..., alias="lfm3-audio")


class ConfigurationUpdate(VoiceConfiguration):
    """An API write must carry the full document and an explicit expected revision."""

    schemaVersion: Literal[1] = Field(...)
    revision: int = Field(..., ge=0)
    selectedProvider: ProviderId = Field(...)
    profiles: ProfilesUpdate = Field(...)


def load_profiles(config: ControllerConfig) -> VoiceConfiguration:
    if not config.settings_file.exists():
        return VoiceConfiguration()
    value = json.loads(config.settings_file.read_text())
    if isinstance(value, dict) and "schemaVersion" in value:
        return VoiceConfiguration.model_validate(value)
    legacy = VoiceSettings.model_validate(value)
    return VoiceConfiguration(profiles=ProviderProfiles(personaplex=legacy))


def save_profiles(config: ControllerConfig, value: VoiceConfiguration) -> None:
    """The old flat document is retained byte-for-byte before its first replacement."""
    if config.settings_file.exists():
        previous = config.settings_file.read_bytes()
        parsed = json.loads(previous)
        if isinstance(parsed, dict) and "schemaVersion" not in parsed:
            VoiceSettings.model_validate(parsed)
            if not config.legacy_settings_backup.exists():
                write_private(config.legacy_settings_backup, previous)
    write_private(config.settings_file, json.dumps(value.document(), indent=2) + "\n")
