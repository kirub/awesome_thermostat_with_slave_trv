"""Adds support for awesome thermostat units."""
import asyncio
import logging
import math

import voluptuous as vol

from homeassistant.components.climate import PLATFORM_SCHEMA, ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_PRESET_MODE,
    CURRENT_HVAC_COOL,
    CURRENT_HVAC_HEAT,
    CURRENT_HVAC_IDLE,
    CURRENT_HVAC_OFF,
    HVAC_MODE_COOL,
    HVAC_MODE_HEAT,
    HVAC_MODE_OFF,
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_HOME,
    PRESET_NONE,
    PRESET_SLEEP,
    SUPPORT_PRESET_MODE,
    SUPPORT_TARGET_TEMPERATURE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    CONF_NAME,
    CONF_UNIQUE_ID,
    EVENT_HOMEASSISTANT_START,
    PRECISION_HALVES,
    PRECISION_TENTHS,
    PRECISION_WHOLE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import DOMAIN as HA_DOMAIN, CoreState, callback
from homeassistant.exceptions import ConditionError
from homeassistant.helpers import condition, entity_registry as er
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
    async_call_later,
)
from homeassistant.helpers.reload import async_setup_reload_service
from homeassistant.helpers.restore_state import RestoreEntity
from voluptuous.schema_builder import Self

from . import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

DEFAULT_TOLERANCE = 0.3
DEFAULT_NAME = "Awesome Thermostat"

CONF_HEATER = "heater"
CONF_SENSOR = "target_sensor"
CONF_WINDOWS_SENSOR = "window_sensor"
CONF_CLIMATES = "slave_climates"
CONF_MOTION_SENSOR = "motion_sensor"
CONF_MOTION_MODE = "motion_mode"
CONF_NO_MOTION_MODE = "no_motion_mode"
CONF_MOTION_DELAY = "motion_delay"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_TARGET_TEMP = "target_temp"
CONF_AC_MODE = "ac_mode"
CONF_MIN_DUR = "min_cycle_duration"
CONF_COLD_TOLERANCE = "cold_tolerance"
CONF_HOT_TOLERANCE = "hot_tolerance"
CONF_KEEP_ALIVE = "keep_alive"
CONF_INITIAL_HVAC_MODE = "initial_hvac_mode"
CONF_PRECISION = "precision"


SUPPORT_FLAGS = SUPPORT_TARGET_TEMPERATURE

CONF_PRESETS = {
    p: f"{p}_temp"
    for p in (
        PRESET_ECO,
        PRESET_AWAY,
        PRESET_BOOST,
        PRESET_COMFORT,
        PRESET_HOME,
        PRESET_SLEEP,
    )
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HEATER): cv.entity_id,
        vol.Required(CONF_SENSOR): cv.entity_id,
        vol.Optional(CONF_WINDOWS_SENSOR): cv.entity_id,
        vol.Optional(CONF_CLIMATES): cv.entity_ids,
        vol.Optional(CONF_MOTION_SENSOR): cv.entity_id,
        vol.Optional(CONF_MOTION_MODE): cv.string,
        vol.Optional(CONF_NO_MOTION_MODE): cv.string,
        vol.Optional(CONF_MOTION_DELAY): cv.positive_time_period,
        vol.Optional(CONF_AC_MODE): cv.boolean,
        vol.Optional(CONF_MAX_TEMP): vol.Coerce(float),
        vol.Optional(CONF_MIN_DUR): cv.positive_time_period,
        vol.Optional(CONF_MIN_TEMP): vol.Coerce(float),
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_COLD_TOLERANCE, default=DEFAULT_TOLERANCE): vol.Coerce(float),
        vol.Optional(CONF_HOT_TOLERANCE, default=DEFAULT_TOLERANCE): vol.Coerce(float),
        vol.Optional(CONF_TARGET_TEMP): vol.Coerce(float),
        vol.Optional(CONF_KEEP_ALIVE): cv.positive_time_period,
        vol.Optional(CONF_INITIAL_HVAC_MODE): vol.In(
            [HVAC_MODE_COOL, HVAC_MODE_HEAT, HVAC_MODE_OFF]
        ),
        vol.Optional(CONF_PRECISION): vol.In(
            [PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE]
        ),
        vol.Optional(CONF_UNIQUE_ID): cv.string,
    }
).extend({vol.Optional(v): vol.Coerce(float) for (k, v) in CONF_PRESETS.items()})


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the awesome thermostat platform."""

    await async_setup_reload_service(hass, DOMAIN, PLATFORMS)

    name = config.get(CONF_NAME)
    heater_entity_id = config.get(CONF_HEATER)
    temperature_entity_id = config.get(CONF_SENSOR)
    windows_entity_id = config.get(CONF_WINDOWS_SENSOR)
    slave_climate_entities_id = config.get(CONF_CLIMATES)
    min_temp = config.get(CONF_MIN_TEMP)
    max_temp = config.get(CONF_MAX_TEMP)
    target_temp = config.get(CONF_TARGET_TEMP)
    ac_mode = config.get(CONF_AC_MODE)
    min_cycle_duration = config.get(CONF_MIN_DUR)
    cold_tolerance = config.get(CONF_COLD_TOLERANCE)
    hot_tolerance = config.get(CONF_HOT_TOLERANCE)
    keep_alive = config.get(CONF_KEEP_ALIVE)
    initial_hvac_mode = config.get(CONF_INITIAL_HVAC_MODE)
    presets = {}
    for (key, value) in CONF_PRESETS.items():
        if value in config:
            presets[key] = config.get(value)
    motion_entity_id = config.get(CONF_MOTION_SENSOR)
    motion_mode = config.get(CONF_MOTION_MODE)
    no_motion_mode = config.get(CONF_NO_MOTION_MODE)
    motion_delay = config.get(CONF_MOTION_DELAY)
    precision = config.get(CONF_PRECISION)
    unit = hass.config.units.temperature_unit
    unique_id = config.get(CONF_UNIQUE_ID)

    async_add_entities(
        [
            AwesomeThermostat(
                name,
                heater_entity_id,
                temperature_entity_id,
                windows_entity_id,
                slave_climate_entities_id,
                motion_entity_id,
                motion_mode,
                no_motion_mode,
                motion_delay,
                min_temp,
                max_temp,
                target_temp,
                ac_mode,
                min_cycle_duration,
                cold_tolerance,
                hot_tolerance,
                keep_alive,
                initial_hvac_mode,
                presets,
                precision,
                unit,
                unique_id,
            )
        ]
    )


class AwesomeThermostat(ClimateEntity, RestoreEntity):
    """Representation of a Awesome Thermostat device."""

    def __init__(
        self,
        name,
        heater_entity_id,
        temperature_entity_id,
        windows_entity_id,
        slave_climate_entities_id,
        motion_entity_id,
        motion_mode,
        no_motion_mode,
        motion_delay,
        min_temp,
        max_temp,
        target_temp,
        ac_mode,
        min_cycle_duration,
        cold_tolerance,
        hot_tolerance,
        keep_alive,
        initial_hvac_mode,
        presets,
        precision,
        unit,
        unique_id,
    ):
        """Initialize the thermostat."""
        self._name = name
        self.heater_entity_id = heater_entity_id
        self.temperature_entity_id = temperature_entity_id
        self.windows_entity_id = windows_entity_id

        entity_reg = er.async_get_registry(self.hass)
        self.slave_climate_entities_id = slave_climate_entities_id
        self.slave_climates = []
        for climate_entity_id in self.slave_climate_entities_id:
            climate_object = entity_reg.async_get(climate_entity_id)
            self.slave_climates.append(climate_object)

        self.motion_entity_id = motion_entity_id
        self.motion_mode = motion_mode
        self.no_motion_mode = no_motion_mode
        self.motion_delay = motion_delay
        self.support_motion_control = False
        if (
            self.motion_entity_id
            and self.motion_mode
            and self.motion_mode in presets.keys()
            and self.no_motion_mode
            and self.no_motion_mode in presets.keys()
            and self.motion_delay
        ):
            self.support_motion_control = True
            presets[PRESET_ACTIVITY] = presets[no_motion_mode]
        self.ac_mode = ac_mode
        self.min_cycle_duration = min_cycle_duration
        self._cold_tolerance = cold_tolerance
        self._hot_tolerance = hot_tolerance
        self._keep_alive = keep_alive
        self._hvac_mode = initial_hvac_mode
        self._saved_hvac_mode = self._hvac_mode
        self._saved_target_temp = target_temp or next(iter(presets.values()), None)
        self._temp_precision = precision
        if self.ac_mode:
            self._hvac_list = [HVAC_MODE_COOL, HVAC_MODE_OFF]
        else:
            self._hvac_list = [HVAC_MODE_HEAT, HVAC_MODE_OFF]
        self._active = False
        self._cur_temp = None
        self._temp_lock = asyncio.Lock()
        self._min_temp = min_temp
        self._max_temp = max_temp
        self._attr_preset_mode = PRESET_NONE
        self._target_temp = target_temp
        self._unit = unit
        self._unique_id = unique_id
        self._support_flags = SUPPORT_FLAGS
        if len(presets):
            self._support_flags = SUPPORT_FLAGS | SUPPORT_PRESET_MODE
            self._attr_preset_modes = [PRESET_NONE] + list(presets.keys())
        else:
            self._attr_preset_modes = [PRESET_NONE]
        self._presets = presets

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Add listener
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.temperature_entity_id], self._async_temperature_changed
            )
        )
        if self.windows_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self.windows_entity_id], self._async_windows_changed
                )
            )
        if self.support_motion_control:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self.motion_entity_id], self._async_motion_changed
                )
            )
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.heater_entity_id], self._async_switch_changed
            )
        )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self.slave_climate_entities_id],
                self._async_slave_climates_changed,
            )
        )

        if self._keep_alive:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass, self._async_control_heating, self._keep_alive
                )
            )

        @callback
        def _async_startup(*_):
            """Init on startup."""
            temperature_state = self.hass.states.get(self.temperature_entity_id)
            if temperature_state and temperature_state.state not in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                self._async_update_temp(temperature_state)
                self.async_write_ha_state()
            switch_state = self.hass.states.get(self.heater_entity_id)
            if switch_state and switch_state.state not in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                self.hass.create_task(self._check_switch_initial_state())

        if self.hass.state == CoreState.running:
            _async_startup()
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _async_startup)

        # Check If we have an old state
        old_state = await self.async_get_last_state()
        if old_state is not None:
            # If we have no initial temperature, restore
            if self._target_temp is None:
                # If we have a previously saved temperature
                if old_state.attributes.get(ATTR_TEMPERATURE) is None:
                    if self.ac_mode:
                        self._target_temp = self.max_temp
                    else:
                        self._target_temp = self.min_temp
                    _LOGGER.warning(
                        "Undefined target temperature, falling back to %s",
                        self._target_temp,
                    )
                else:
                    self._target_temp = float(old_state.attributes[ATTR_TEMPERATURE])
            if old_state.attributes.get(ATTR_PRESET_MODE) in self._attr_preset_modes:
                self._attr_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)
            if not self._hvac_mode and old_state.state:
                self._hvac_mode = old_state.state

        else:
            # No previous state, try and restore defaults
            if self._target_temp is None:
                if self.ac_mode:
                    self._target_temp = self.max_temp
                else:
                    self._target_temp = self.min_temp
            _LOGGER.warning(
                "No previously saved temperature, setting to %s", self._target_temp
            )

        # Set default state to off
        if not self._hvac_mode:
            self._hvac_mode = HVAC_MODE_OFF

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name of the thermostat."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique id of this thermostat."""
        return self._unique_id

    @property
    def precision(self):
        """Return the precision of the system."""
        if self._temp_precision is not None:
            return self._temp_precision
        return super().precision

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        # Since this integration does not yet have a step size parameter
        # we have to re-use the precision as the step size for now.
        return self.precision

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        return self._cur_temp

    @property
    def hvac_mode(self):
        """Return current operation."""
        return self._hvac_mode

    @property
    def hvac_action(self):
        """Return the current running hvac operation if supported.

        Need to be one of CURRENT_HVAC_*.
        """
        if self._hvac_mode == HVAC_MODE_OFF:
            return CURRENT_HVAC_OFF
        if not self._is_device_active:
            return CURRENT_HVAC_IDLE
        if self.ac_mode:
            return CURRENT_HVAC_COOL
        return CURRENT_HVAC_HEAT

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temp

    @property
    def hvac_modes(self):
        """List of available operation modes."""
        return self._hvac_list

    def get_slave_climate(self, idx):
        """returns climate entity at idx index."""
        return self.slave_climates[idx]

    async def async_set_hvac_mode(self, hvac_mode):
        """Set hvac mode."""
        if hvac_mode == HVAC_MODE_HEAT:
            self._hvac_mode = HVAC_MODE_HEAT
            await self._async_control_heating(force=True)
        elif hvac_mode == HVAC_MODE_COOL:
            self._hvac_mode = HVAC_MODE_COOL
            await self._async_control_heating(force=True)
        elif hvac_mode == HVAC_MODE_OFF:
            self._hvac_mode = HVAC_MODE_OFF
            if self._is_device_active:
                await self._async_heater_turn_off()
        else:
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return

        for climate in self.slave_climates:
            if climate._hvac_mode != self._hvac_mode:
                climate.async_set_hvac_mode(self._hvac_mode)

        # Ensure we update the current operation after changing the mode
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        self._target_temp = temperature
        self._attr_preset_mode = PRESET_NONE
        await self._async_control_heating(force=True)

        for climate in self.slave_climates:
            climate.async_set_temperature(kwargs)

        self.async_write_ha_state()

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        if self._min_temp is not None:
            return self._min_temp

        # get default temp from super class
        return super().min_temp

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        if self._max_temp is not None:
            return self._max_temp

        # Get default temp from super class
        return super().max_temp

    async def _async_temperature_changed(self, event):
        """Handle temperature changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        self._async_update_temp(new_state)
        await self._async_control_heating()
        self.async_write_ha_state()

    async def _async_windows_changed(self, event):
        """Handle window changes."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or old_state is None or new_state.state == old_state.state:
            return
        if not self._saved_hvac_mode:
            self._saved_hvac_mode = self._hvac_mode
        if new_state.state == STATE_OFF:
            await self.async_set_hvac_mode(self._saved_hvac_mode)
        elif new_state.state == STATE_ON:
            self._saved_hvac_mode = self._hvac_mode
            await self.async_set_hvac_mode(HVAC_MODE_OFF)
        else:
            return

    async def _async_motion_changed(self, event):
        """Handle motion changes."""
        if self._attr_preset_mode != PRESET_ACTIVITY:
            return
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state not in (STATE_OFF, STATE_ON):
            return

        if self.motion_delay:
            if new_state.state == STATE_ON:
                self._target_temp = self._presets[self.motion_mode]
                await self._async_control_heating()
                self.async_write_ha_state()
            else:

                async def try_no_motion_condition(_):
                    if self._attr_preset_mode != PRESET_ACTIVITY:
                        return
                    try:
                        long_enough = condition.state(
                            self.hass,
                            self.motion_entity_id,
                            STATE_OFF,
                            self.motion_delay,
                        )
                    except ConditionError:
                        long_enough = False
                    if long_enough:
                        self._target_temp = self._presets[self.no_motion_mode]
                        await self._async_control_heating()
                        self.async_write_ha_state()

                async_call_later(self.hass, self.motion_delay, try_no_motion_condition)

    async def _check_switch_initial_state(self):
        """Prevent the device from keep running if HVAC_MODE_OFF."""
        if self._hvac_mode == HVAC_MODE_OFF and self._is_device_active:
            _LOGGER.warning(
                "The climate mode is OFF, but the switch device is ON. Turning off device %s",
                self.heater_entity_id,
            )
            await self._async_heater_turn_off()

    @callback
    def _async_switch_changed(self, event):
        """Handle heater switch state changes."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return
        if old_state is None:
            self.hass.create_task(self._check_switch_initial_state())
        self.async_write_ha_state()

    @callback
    def _async_slave_climates_changed(self, event):
        """Handle climates state changes."""
        new_state = event.data.get("new_state")

        if new_state != self._hvac_mode:
            self.async_set_hvac_mode(new_state)

    @callback
    def _async_update_temp(self, state):
        """Update thermostat with latest state from sensor."""
        try:
            cur_temp = float(state.state)
            if math.isnan(cur_temp) or math.isinf(cur_temp):
                raise ValueError(f"Sensor has illegal state {state.state}")
            self._cur_temp = cur_temp
        except ValueError as ex:
            _LOGGER.error("Unable to update from sensor: %s", ex)

    async def _async_control_heating(self, time=None, force=False):
        """Check if we need to turn heating on or off."""
        async with self._temp_lock:
            if not self._active and None not in (
                self._cur_temp,
                self._target_temp,
            ):
                self._active = True
                _LOGGER.info(
                    "Obtained current and target temperature. "
                    "Awesome thermostat active. %s, %s",
                    self._cur_temp,
                    self._target_temp,
                )

            if not self._active or self._hvac_mode == HVAC_MODE_OFF:
                return

            # If the `force` argument is True, we
            # ignore `min_cycle_duration`.
            # If the `time` argument is not none, we were invoked for
            # keep-alive purposes, and `min_cycle_duration` is irrelevant.
            if not force and time is None and self.min_cycle_duration:
                if self._is_device_active:
                    current_state = STATE_ON
                else:
                    current_state = HVAC_MODE_OFF
                try:
                    long_enough = condition.state(
                        self.hass,
                        self.heater_entity_id,
                        current_state,
                        self.min_cycle_duration,
                    )
                except ConditionError:
                    long_enough = False

                if not long_enough:
                    return

            too_cold = self._target_temp >= self._cur_temp + self._cold_tolerance
            too_hot = self._cur_temp >= self._target_temp + self._hot_tolerance
            if self._is_device_active:
                if (self.ac_mode and too_cold) or (not self.ac_mode and too_hot):
                    _LOGGER.info("Turning off heater %s", self.heater_entity_id)
                    await self._async_heater_turn_off()
                elif time is not None:
                    # The time argument is passed only in keep-alive case
                    _LOGGER.info(
                        "Keep-alive - Turning on heater heater %s",
                        self.heater_entity_id,
                    )
                    await self._async_heater_turn_on()
            else:
                if (self.ac_mode and too_hot) or (not self.ac_mode and too_cold):
                    _LOGGER.info("Turning on heater %s", self.heater_entity_id)
                    await self._async_heater_turn_on()
                elif time is not None:
                    # The time argument is passed only in keep-alive case
                    _LOGGER.info(
                        "Keep-alive - Turning off heater %s", self.heater_entity_id
                    )
                    await self._async_heater_turn_off()

    @property
    def _is_device_active(self):
        """If the toggleable device is currently active."""
        if not self.hass.states.get(self.heater_entity_id):
            return None

        return self.hass.states.is_state(self.heater_entity_id, STATE_ON)

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return self._support_flags

    async def _async_heater_turn_on(self):
        """Turn heater toggleable device on."""
        data = {ATTR_ENTITY_ID: self.heater_entity_id}
        await self.hass.services.async_call(
            HA_DOMAIN, SERVICE_TURN_ON, data, context=self._context
        )

    async def _async_heater_turn_off(self):
        """Turn heater toggleable device off."""
        data = {ATTR_ENTITY_ID: self.heater_entity_id}
        await self.hass.services.async_call(
            HA_DOMAIN, SERVICE_TURN_OFF, data, context=self._context
        )

    async def async_set_preset_mode(self, preset_mode: str):
        """Set new preset mode."""
        if preset_mode not in (self._attr_preset_modes or []):
            raise ValueError(
                f"Got unsupported preset_mode {preset_mode}. Must be one of {self._attr_preset_modes}"
            )

        # I don't think we need to call async_write_ha_state if we didn't change the state
        state_changed = preset_mode != self._attr_preset_mode
        if state_changed:
            if preset_mode == PRESET_NONE:
                self._attr_preset_mode = PRESET_NONE
                self._target_temp = self._saved_target_temp
                await self._async_control_heating(force=True)
            elif preset_mode == PRESET_ACTIVITY:
                self._attr_preset_mode = PRESET_ACTIVITY
                self._target_temp = self._presets[self.no_motion_mode]
                await self._async_control_heating(force=True)
            else:
                if self._attr_preset_mode == PRESET_NONE:
                    self._saved_target_temp = self._target_temp
                self._attr_preset_mode = preset_mode
                self._target_temp = self._presets[preset_mode]
                await self._async_control_heating(force=True)

        for climate in self.slave_climates:
            if climate.preset_mode != self._attr_preset_mode:
                climate.async_set_temperature(self._target_temp)

        if state_changed:
            self.async_write_ha_state()
