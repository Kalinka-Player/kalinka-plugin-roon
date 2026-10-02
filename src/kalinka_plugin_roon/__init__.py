"""Roon Bridge input plugin; the proprietary audio engine is fetched on enable."""

from pathlib import Path
from typing import ClassVar

from kalinka_plugin_sdk import paths
from kalinka_plugin_sdk.config_feedback import ConfigOption
from kalinka_plugin_sdk.datamodel import EmptyList
from kalinka_plugin_sdk.dynamic_fields import DynamicFieldDecl
from kalinka_plugin_sdk.inputmodule import InputModule
from kalinka_plugin_sdk.module_config import ModuleConfig
from kalinka_plugin_sdk.module_health import ModuleHealthState, ModuleState
from kalinka_plugin_sdk.plugin import InputModulePlugin
from pydantic import Field

from .supervisor import Supervisor


class RoonConfig(ModuleConfig):
    __module_icon__: ClassVar[str] = "speaker_outlined"
    __module_icon_color__: ClassVar[str] = "#7565AD"

    name: str = Field(
        default="roon",
        title="Roon Bridge",
        frozen=True,
        exclude=True,
        description="Use this Linux device as a Roon endpoint.",
    )
    enabled: bool = Field(
        default=False,
        title="Enable Roon Bridge",
        description="Downloads and runs the official Roon Bridge. A Roon Server and subscription are required.",
        json_schema_extra={"importance": "simple"},
    )
    output_id: str = Field(
        default="",
        title="This device's Roon output",
        description="Enable the local audio output in Roon, authorize the Kalinka extension, then select that output here. Do not select another device.",
        json_schema_extra={"dynamic_options": True, "importance": "simple"},
    )
    alsa_hw_params: str = Field(
        default="",
        title="ALSA output details (optional)",
        pattern=r"^$|^/proc/asound/card[0-9]+/pcm[0-9]+p/sub[0-9]+/hw_params$",
        description="hw_params for the DAC selected in Roon, e.g. /proc/asound/card0/pcm0p/sub0/hw_params. Reports output rate/channels; Roon does not expose source format.",
    )


class RoonInput(InputModule):
    def __init__(self, plugin):
        self.plugin = plugin

    def module_name(self):
        return "roon"

    def display_name(self):
        return "Roon Bridge"

    async def browse(self, entity_id, offset=0, limit=50, filter=None):
        return EmptyList(offset, limit)

    async def search(self, type, query, offset=0, limit=50):
        return EmptyList(offset, limit)

    async def get_track_info(self, track_ids):
        return []

    async def get_content_info(self, asset_id):
        service = self.plugin.service
        return await service.content(asset_id) if service else None


class KalinkaPluginRoon(InputModulePlugin):
    PLUGIN_ID = "roon"
    REQUIRES_SDK = ">=3.7,<4"
    CONFIG_MODEL = RoonConfig
    DYNAMIC_FIELDS: ClassVar[dict[str, DynamicFieldDecl]] = {
        "connection_status": DynamicFieldDecl(
            section_id="", label="Roon status", widget="text"
        ),
    }

    def __init__(self):
        self.supervisor = None
        self.interface = RoonInput(self)

    @property
    def service(self):
        return self.supervisor.service if self.supervisor else None

    def get_interface(self):
        return self.interface

    async def setup(self, context):
        config = RoonConfig(**context.config.model_dump())
        if not config.enabled:
            return
        playback = getattr(context, "external_playback", None)
        if playback is None:
            raise RuntimeError(
                "Roon Bridge requires Kalinka external playback support (SDK 3.7+ and matching server)"
            )
        self.supervisor = Supervisor(playback, config, Path(paths.state_dir()) / "roon")
        self.supervisor.start()

    async def resolve_options(self, path):
        if path != "output_id":
            raise KeyError(path)
        return (
            [
                ConfigOption(value=key, label=f"{name} ({key})")
                for key, name in sorted(self.service.outputs.items())
            ]
            if self.service
            else []
        )

    async def resolve_dynamic_field(self, path):
        if path != "connection_status":
            raise KeyError(path)
        if not self.supervisor:
            return "Disabled"
        return self.supervisor.error or (
            self.service.status if self.service else self.supervisor.status
        )

    async def get_state(self):
        state = ModuleHealthState.DISABLED
        if self.supervisor:
            state = (
                ModuleHealthState.ERROR
                if self.supervisor.error
                else ModuleHealthState.WARNING
            )
            if not self.supervisor.error and self.service and self.service.zone:
                state = ModuleHealthState.READY
        return ModuleState(
            state=state, message=await self.resolve_dynamic_field("connection_status")
        )

    async def shutdown(self):
        if self.supervisor:
            await self.supervisor.stop()
            self.supervisor = None
