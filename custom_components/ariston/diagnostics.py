"""Diagnostics support for the Ariston integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import BSB_DATA_COORDINATOR, COORDINATOR, DOMAIN

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, "sn", "loc"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinators: dict[str, Any] = hass.data.get(DOMAIN, {}).get(entry.unique_id, {})
    device = getattr(coordinators.get(COORDINATOR), "device", None)

    diagnostics: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinators": {
            name: {
                "last_update_success": coordinator.last_update_success,
                "last_exception": str(coordinator.last_exception),
            }
            for name, coordinator in coordinators.items()
            if hasattr(coordinator, "last_update_success")
        },
    }

    if device is None:
        diagnostics["error"] = "The device is not available"
        return async_redact_data(diagnostics, TO_REDACT)

    diagnostics["device"] = {
        "system_type": device.system_type.name,
        "whe_type": device.whe_type.name,
        "model": device.whe_model_type,
        "firmware": device.firmware_version,
        "has_metering": device.has_metering,
        "features": dict(device.features),
        "custom_features": dict(device.custom_features),
        "attributes": dict(device.attributes),
    }
    diagnostics["plant_data"] = device.data
    diagnostics["consumptions_sequences"] = device.consumptions_sequences
    diagnostics["bus_errors"] = device.bus_errors_list

    bsb_coordinator = coordinators.get(BSB_DATA_COORDINATOR)
    if bsb_coordinator is not None:
        diagnostics["bsb_data"] = bsb_coordinator.data

    return async_redact_data(diagnostics, TO_REDACT)
