"""Security schema must match the keyboard C driver's key whitelist."""
import pytest
from alice_mouse.security.grants import _validate_action_payload, GrantError

@pytest.mark.parametrize('key',[113,114,115,116])
def test_system_volume_and_power_keys_never_authorized(key):
    with pytest.raises(GrantError):
        _validate_action_payload('key_down',{'key':key})

@pytest.mark.parametrize('key',[87,88,30,29,42,54,97,100,125,126])
def test_physical_keyboard_supported_keys_authorized(key):
    _validate_action_payload('key_down',{'key':key})
    _validate_action_payload('key_up',{'key':key})

@pytest.mark.parametrize('key',range(69,87))
def test_supported_numeric_keypad_keys_authorized(key):
    _validate_action_payload('key_down',{'key':key})
    _validate_action_payload('key_up',{'key':key})
