"""Switch platform for MyStiebel integration."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .const import (
    DEVICE_CLOCK_REGISTER,
    DOMAIN,
    ESSENTIAL_CONTROLS,
    EXCLUDED_INDIVIDUAL_SENSORS,
    HOT_WATER_PLUS_DEFAULT_DURATION_HOURS,
    HOT_WATER_PLUS_END_TIME_REGISTER,
    HOT_WATER_PLUS_SWITCH_REGISTER,
)
from .sensor import MyStiebelBaseEntity

_LOGGER = logging.getLogger(__name__)


def _setup_switch_entities(coordinator):
    params_to_check, fields_to_create = (
        coordinator.parameters,
        coordinator.active_fields,
    )
    switches = []
    for idx in fields_to_create:
        # Skip excluded sensors (e.g., those combined into other entities)
        if idx in EXCLUDED_INDIVIDUAL_SENSORS:
            continue

        param = params_to_check.get(idx)
        if (
            param
            and "read_write" in param.get("access", [])
            and param.get("choicelist_id") == "State_on_off"
        ):
            if idx == HOT_WATER_PLUS_SWITCH_REGISTER:
                switches.append(MyStiebelHotWaterPlusSwitch(coordinator, idx, param))
            else:
                switches.append(MyStiebelSwitch(coordinator, idx, param))
    return switches


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    switches = await hass.async_add_executor_job(_setup_switch_entities, coordinator)
    async_add_entities(switches, True)


class MyStiebelSwitch(MyStiebelBaseEntity, SwitchEntity):
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, register_index, param) -> None:
        super().__init__(coordinator, param)
        self._register_index = register_index
        self._attr_unique_id = f"mystiebel_{register_index}_switch"
        self._attr_name = param.get("display_name")
        self._attr_icon = "mdi:toggle-switch"
        self._attr_entity_category = EntityCategory.CONFIG
        if register_index in ESSENTIAL_CONTROLS:
            self._attr_entity_registry_enabled_default = True

    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self):
        try:
            return float(self.coordinator.data.get(self._register_index)) == 1.0
        except (ValueError, TypeError, AttributeError):
            return False

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_value(self._register_index, 1)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_value(self._register_index, 0)


class MyStiebelHotWaterPlusSwitch(MyStiebelSwitch):
    """Hot Water Plus ("Warmwasser Plus") switch.

    The device only honours activation if register 2394 - an absolute
    end-timestamp, in the same clock/epoch base as register 2391 (the
    device's live "now" register) - has already been set to a value in the
    future. The stock MyStiebel app always writes 2394 a few dozen
    milliseconds *before* flipping register 2487 (the on/off flag).

    Simply writing register 2487 alone, which is what the base
    MyStiebelSwitch class (and this integration, until now) does, leaves the
    device without a valid end time. The device accepts the flag for a
    moment and then silently reverts it back to 0 within a few seconds -
    this is the bug reported against this integration.

    This subclass writes 2394 first (computed from the current value of
    register 2391 plus a user-configurable duration, see
    MyStiebelHotWaterPlusDuration in number.py), then 2487, mirroring the
    app's own sequence. async_turn_off is inherited unchanged - turning the
    switch off directly via register 2487 = 0 was confirmed working reliably
    in testing.
    """

    async def async_turn_on(self, **kwargs):
        now_device_clock = self.coordinator.data.get(DEVICE_CLOCK_REGISTER)
        if now_device_clock is None:
            _LOGGER.warning(
                "MyStiebel: could not read device clock (register %d); "
                "activating Hot Water Plus without setting an end time. "
                "This may silently revert after a few seconds - see "
                "register %d in the debug log.",
                DEVICE_CLOCK_REGISTER,
                HOT_WATER_PLUS_END_TIME_REGISTER,
            )
            await super().async_turn_on(**kwargs)
            return

        duration_hours = getattr(
            self.coordinator,
            "hot_water_plus_duration_hours",
            HOT_WATER_PLUS_DEFAULT_DURATION_HOURS,
        )

        # SET_VALUE_MSG (websocket_client.py) sends this value verbatim as
        # "displayValue" - no server-side type coercion happens on our end.
        # The app's own successful write used a whole-number float
        # (displayValue: 1785687900.0), so a plain float matches observed
        # real-world behaviour; no int() cast needed.
        target_timestamp = float(now_device_clock) + float(duration_hours) * 3600

        await self.coordinator.async_set_value(
            HOT_WATER_PLUS_END_TIME_REGISTER, target_timestamp
        )
        await self.coordinator.async_set_value(self._register_index, 1)
