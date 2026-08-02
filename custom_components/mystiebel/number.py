"""Number platform for MyStiebel integration."""

import logging

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import EntityCategory

from .const import (
    DOMAIN,
    ESSENTIAL_CONTROLS,
    EXCLUDED_INDIVIDUAL_SENSORS,
    HOT_WATER_PLUS_DEFAULT_DURATION_HOURS,
    HOT_WATER_PLUS_MAX_DURATION_HOURS,
    HOT_WATER_PLUS_MIN_DURATION_HOURS,
    NUMERIC_CONTROL_TYPES,
)
from .sensor import MyStiebelBaseEntity, normalize_unit

_LOGGER = logging.getLogger(__name__)


def _setup_number_entities(coordinator):
    params_to_check, fields_to_create = (
        coordinator.parameters,
        coordinator.active_fields,
    )
    numbers = []
    for idx in fields_to_create:
        # Skip excluded sensors (e.g., those combined into other entities)
        if idx in EXCLUDED_INDIVIDUAL_SENSORS:
            continue

        param = params_to_check.get(idx)
        group_id = param.get("group_id", "") if param else ""
        if (
            param
            and "read_write" in param.get("access", [])
            and param.get("data_type") in NUMERIC_CONTROL_TYPES
            and not bool(param.get("choices"))
            and "RUNNING_TIMES" not in group_id
        ):
            min_val, max_val = param.get("min"), param.get("max")
            if isinstance(min_val, (int, float)) and isinstance(max_val, (int, float)):
                numbers.append(MyStiebelNumber(coordinator, idx, param))
    return numbers


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    numbers = await hass.async_add_executor_job(_setup_number_entities, coordinator)

    # This one has no corresponding device register - see the module docstring
    # on MyStiebelHotWaterPlusDuration for why it's needed.
    numbers.append(MyStiebelHotWaterPlusDuration(coordinator))

    async_add_entities(numbers, True)


class MyStiebelNumber(MyStiebelBaseEntity, NumberEntity):
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, register_index, param) -> None:
        super().__init__(coordinator, param)
        self._register_index = register_index
        self._param = param
        self._attr_unique_id = f"mystiebel_{register_index}_number"
        self._attr_name = param.get("display_name")
        self._attr_mode = NumberMode.BOX
        self._attr_native_min_value, self._attr_native_max_value = (
            param.get("min"),
            param.get("max"),
        )
        scale = int(param.get("scale", 0))
        self._attr_native_step = 10**scale if scale < 0 else 1
        unit, data_type = normalize_unit(param.get("unit")), param.get("data_type")
        self._attr_device_class = None
        if unit is None:
            if data_type in ("DurationDays", "DurationHours", "Minute", "Second"):
                unit = {
                    "DurationDays": "d",
                    "DurationHours": "h",
                    "Minute": "min",
                    "Second": "s",
                }[data_type]
            elif data_type in ("WWK_LuminosityLevel", "Percentage"):
                unit = "%"
        if unit == "%" and data_type not in ["WWK_LuminosityLevel", "Percentage"]:
            self._attr_device_class = SensorDeviceClass.HUMIDITY
        self._attr_native_unit_of_measurement = unit
        self._attr_entity_category = EntityCategory.CONFIG
        if register_index in ESSENTIAL_CONTROLS:
            self._attr_entity_registry_enabled_default = True

    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.data.get(self._register_index)
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_value(self._register_index, value)


class MyStiebelHotWaterPlusDuration(RestoreNumber):
    """Locally-stored duration (hours) for activating Hot Water Plus from HA.

    Unlike every other entity in this integration, this one has no
    corresponding device register - the WWK-I only exposes an *absolute*
    end-timestamp (register 2394), never a duration. The stock MyStiebel app
    lets the user pick a duration and converts it to that absolute timestamp
    itself before sending it to the device.

    This entity stores that duration on the HA side (persisted across
    restarts via RestoreNumber) so that MyStiebelHotWaterPlusSwitch
    (see switch.py) can read it and compute register 2394 = current device
    clock (register 2391) + duration, mirroring what the app does.

    NOTE: this entity is intentionally NOT built from `coordinator.parameters`
    like every other entity in this file, since it doesn't map to a real
    register - it has no register index and its value is never pushed by the
    coordinator. It deliberately does NOT inherit MyStiebelBaseEntity /
    CoordinatorEntity: combining CoordinatorEntity's __init__ chain with
    RestoreNumber/RestoreEntity's via multiple inheritance is untested and
    an unnecessary risk here, since this entity never needs push-updates from
    the coordinator anyway. Instead, `device_info` below is a direct copy of
    MyStiebelBaseEntity.device_info (sensor.py) so this still shows up
    grouped under the same WWK device in HA.

    Availability is intentionally left as the Entity default (always
    available) rather than tied to `coordinator.last_update_success`: this
    lets the user pre-configure a duration even while the WWK is briefly
    offline. Change this if you'd rather it match the rest of the device's
    entities.
    """

    _attr_has_entity_name = True
    _attr_name = "Warmwasser Plus Dauer"
    _attr_icon = "mdi:timer-plus-outline"
    _attr_native_min_value = HOT_WATER_PLUS_MIN_DURATION_HOURS
    _attr_native_max_value = HOT_WATER_PLUS_MAX_DURATION_HOURS
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "h"
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = (
            f"mystiebel_{coordinator.installation_id}_hot_water_plus_duration"
        )
        self._attr_native_value = HOT_WATER_PLUS_DEFAULT_DURATION_HOURS

    @property
    def device_info(self):
        # Deliberate copy of MyStiebelBaseEntity.device_info (sensor.py) -
        # see the class docstring for why this isn't inherited instead.
        return {
            "identifiers": {(DOMAIN, self.coordinator.installation_id)},
            "name": self.coordinator.device_name,
            "manufacturer": "Stiebel Eltron",
            "model": self.coordinator.model,
            "sw_version": self.coordinator.sw_version,
            "connections": {(CONNECTION_NETWORK_MAC, self.coordinator.mac_address)},
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data is not None and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        # Make the chosen duration reachable from the switch platform without
        # needing a registry/entity lookup from there.
        self.coordinator.hot_water_plus_duration_hours = self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.hot_water_plus_duration_hours = value
        self.async_write_ha_state()
