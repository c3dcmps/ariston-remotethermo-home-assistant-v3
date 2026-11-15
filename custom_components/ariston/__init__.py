"""The Ariston integration."""

from __future__ import annotations

import asyncio
import logging
from functools import partial

import voluptuous as vol

from ariston import Ariston, DeviceAttribute, SystemType
from ariston.base_device import AristonBaseDevice
from ariston.const import ARISTON_API_URL, ARISTON_USER_AGENT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_ID,
    CONF_DEVICE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util.unit_system import METRIC_SYSTEM

from .const import (
    API_URL_SETTING,
    API_USER_AGENT,
    BSB_DATA_COORDINATOR,
    BSB_DATA_REQUEST_TIMEOUT_SECONDS,
    BSB_DATA_POINT_ADDRESSES,
    BUS_ERRORS_COORDINATOR,
    BUS_ERRORS_SCAN_INTERVAL,
    CONF_BRAND,
    COORDINATOR,
    DEFAULT_BSB_DATA_SCAN_INTERVAL_SECONDS,
    DEFAULT_BUS_ERRORS_SCAN_INTERVAL_SECONDS,
    DEFAULT_ENERGY_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    ELCO_API_URL,
    ENERGY_COORDINATOR,
    ENERGY_REQUEST_TIMEOUT_SECONDS,
    ENERGY_SCAN_INTERVAL,
    R2_CLIENT,
)
from .coordinator import DeviceDataUpdateCoordinator
from .r2 import R2Client

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]

SERVICE_SET_ITEM_BY_ID = "set_item_by_id"
ATTR_ITEM_ID = "item_id"
ATTR_ZONE = "zone"
ATTR_VALUE = "value"

SET_ITEM_BY_ID_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_ITEM_ID): cv.string,
        vol.Required(ATTR_ZONE): vol.All(cv.positive_int, vol.Range(min=1)),
        vol.Required(ATTR_VALUE): vol.Coerce(float),
    }
)


async def _async_update_energy(device: AristonBaseDevice) -> None:
    """Update the energy data with a bounded request time."""
    async with asyncio.timeout(ENERGY_REQUEST_TIMEOUT_SECONDS):
        await device.async_update_energy()


def _r2_base_url(api_url: str) -> str:
    """Return the web app base URL matching the configured API URL."""
    return api_url.split("/api/")[0].rstrip("/")


async def _async_update_bsb_data_points(
    device: AristonBaseDevice, client: R2Client
) -> dict[int, dict[str, Any]]:
    """Read the BSB energy counters through the web app."""
    async with asyncio.timeout(BSB_DATA_REQUEST_TIMEOUT_SECONDS):
        return await client.async_read_data_points(
            device.gw, list(BSB_DATA_POINT_ADDRESSES)
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ariston from a config entry."""
    ariston = Ariston()
    try:
        brand = entry.data.get(CONF_BRAND, "ariston")
        default_api_url = ELCO_API_URL if brand == "elco" else ARISTON_API_URL
        api_url_setting = entry.data.get(API_URL_SETTING) or default_api_url

        api_user_agent = entry.data.get(API_USER_AGENT, ARISTON_USER_AGENT)

        reponse = await ariston.async_connect(
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            api_url_setting,
            api_user_agent,
        )
        if not reponse:
            _LOGGER.error(
                "Failed to connect to Ariston with device: %s",
                entry.data[CONF_DEVICE].get(DeviceAttribute.NAME),
            )
            raise ConfigEntryAuthFailed

        device = await ariston.async_hello(
            entry.data[CONF_DEVICE].get(DeviceAttribute.GW),
            hass.config.units is METRIC_SYSTEM,
        )
        if device is None:
            return False

        await device.async_get_features()

        scan_interval_seconds = entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS
        )
        coordinator = DeviceDataUpdateCoordinator(
            hass,
            device,
            scan_interval_seconds,
            COORDINATOR,
            device.async_update_state,
            config_entry=entry,
        )
        coordinator.brand = brand

        hass.data.setdefault(DOMAIN, {}).setdefault(
            entry.unique_id, {COORDINATOR: {}, ENERGY_COORDINATOR: {}}
        )
        hass.data[DOMAIN][entry.unique_id][COORDINATOR] = coordinator

        await coordinator.async_config_entry_first_refresh()

        bus_errors_scan_interval_seconds = entry.options.get(
            BUS_ERRORS_SCAN_INTERVAL, DEFAULT_BUS_ERRORS_SCAN_INTERVAL_SECONDS
        )
        bus_errors_coordinator = DeviceDataUpdateCoordinator(
            hass,
            device,
            bus_errors_scan_interval_seconds,
            BUS_ERRORS_COORDINATOR,
            device.async_get_bus_errors,
            config_entry=entry,
        )
        bus_errors_coordinator.brand = brand
        hass.data[DOMAIN][entry.unique_id][BUS_ERRORS_COORDINATOR] = (
            bus_errors_coordinator
        )
        await bus_errors_coordinator.async_refresh()

        if device.has_metering:
            energy_interval_minutes = entry.options.get(
                ENERGY_SCAN_INTERVAL, DEFAULT_ENERGY_SCAN_INTERVAL_MINUTES
            )
            energy_coordinator = DeviceDataUpdateCoordinator(
                hass,
                device,
                energy_interval_minutes * 60,
                ENERGY_COORDINATOR,
                partial(_async_update_energy, device),
                config_entry=entry,
            )
            energy_coordinator.brand = brand
            hass.data[DOMAIN][entry.unique_id][ENERGY_COORDINATOR] = energy_coordinator
            try:
                await energy_coordinator.async_config_entry_first_refresh()
            except Exception as err:  # noqa: BLE001 - energy data is optional
                _LOGGER.warning(
                    "Energy consumption data not available for %s: %s",
                    device.name,
                    err,
                )

        if device.system_type == SystemType.BSB:
            r2_client = R2Client(
                _r2_base_url(api_url_setting),
                entry.data[CONF_USERNAME],
                entry.data[CONF_PASSWORD],
            )
            hass.data[DOMAIN][entry.unique_id][R2_CLIENT] = r2_client
            bsb_data_coordinator = DeviceDataUpdateCoordinator(
                hass,
                device,
                DEFAULT_BSB_DATA_SCAN_INTERVAL_SECONDS,
                BSB_DATA_COORDINATOR,
                partial(_async_update_bsb_data_points, device, r2_client),
                config_entry=entry,
            )
            bsb_data_coordinator.brand = brand
            hass.data[DOMAIN][entry.unique_id][BSB_DATA_COORDINATOR] = (
                bsb_data_coordinator
            )
            await bsb_data_coordinator.async_refresh()

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        entry.async_on_unload(entry.add_update_listener(update_listener))

        if device.system_type == SystemType.GALEVO:

            async def async_set_item_by_id_service(service_call):
                """Create a vacation on the target device."""
                device_id = service_call.data.get(ATTR_DEVICE_ID)
                item_id = service_call.data.get(ATTR_ITEM_ID)
                zone = service_call.data.get(ATTR_ZONE)
                value = service_call.data.get(ATTR_VALUE)

                device_registry = dr.async_get(hass)
                device = device_registry.devices[device_id]

                entry = hass.config_entries.async_get_entry(
                    next(iter(device.config_entries))
                )
                coordinator: DeviceDataUpdateCoordinator = hass.data[DOMAIN][
                    entry.unique_id
                ][COORDINATOR]
                await coordinator.device.async_set_item_by_id(item_id, value, zone)

            hass.services.async_register(
                DOMAIN,
                SERVICE_SET_ITEM_BY_ID,
                async_set_item_by_id_service,
                schema=SET_ITEM_BY_ID_SCHEMA,
            )
    except ConfigEntryAuthFailed:
        raise
    except Exception as error:
        _LOGGER.exception("")
        raise ConfigEntryNotReady from error

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        r2_client = hass.data[DOMAIN][entry.unique_id].get(R2_CLIENT)
        if r2_client is not None:
            await r2_client.async_close()
        hass.data[DOMAIN].pop(entry.unique_id)

    return unload_ok
