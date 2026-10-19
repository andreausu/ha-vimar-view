"""Vimar Cloud light platform."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import VimarCloudClient, VimarConnectionError
from .const import DOMAIN, SFE_STATE_BRIGHTNESS, SFE_STATE_ONOFF
from .device_info import device_info_for_idsf

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    client: VimarCloudClient = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for idsf, device in client.devices.items():
        dt = device.get("device_type")
        if dt == "light":
            entities.append(VimarLight(client, idsf, device, entry))
        elif dt == "dimmer":
            entities.append(VimarDimmer(client, idsf, device, entry))
    async_add_entities(entities)


class VimarLight(LightEntity):
    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, client: VimarCloudClient, idsf: int, device: dict, entry: ConfigEntry) -> None:
        self._client = client
        self._idsf = idsf
        self._attr_name = None  # Main feature: inherit the device name without duplicating it
        self._attr_unique_id = f"{DOMAIN}_{idsf}_light"
        self._attr_device_info = device_info_for_idsf(client, idsf, entry)

    async def async_added_to_hass(self) -> None:
        self._client.register_state_callback(self._on_update)

    async def async_will_remove_from_hass(self) -> None:
        self._client.unregister_state_callback(self._on_update)

    @callback
    def _on_update(self, updated: list[int]) -> None:
        if self._idsf in updated:
            self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._client.get_state(self._idsf, SFE_STATE_ONOFF) == "On"

    async def _async_light_command(self, command: str, *args: Any) -> None:
        """Expose transport failures as recoverable HA service errors.

        Home Assistant continue_on_error only handles HomeAssistantError.
        Keep retries in the automation and never report success on failure.
        """
        try:
            await getattr(self._client, command)(self._idsf, *args)
        except (VimarConnectionError, aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Vimar light {self._idsf}: {command} failed ({err})"
            ) from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_light_command("turn_on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_light_command("turn_off")


class VimarDimmer(VimarLight):
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, client: VimarCloudClient, idsf: int, device: dict, entry: ConfigEntry) -> None:
        super().__init__(client, idsf, device, entry)
        self._attr_unique_id = f"{DOMAIN}_{idsf}_dimmer"

    @property
    def brightness(self) -> int | None:
        value = self._client.get_state(self._idsf, SFE_STATE_BRIGHTNESS)
        try:
            return round(int(value) * 255 / 100) if value is not None else None
        except (ValueError, TypeError):
            return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            vimar_brightness = round(kwargs[ATTR_BRIGHTNESS] * 100 / 255)
            await self._async_light_command("turn_on")
            await self._async_light_command("set_brightness", vimar_brightness)
        else:
            await self._async_light_command("turn_on")
