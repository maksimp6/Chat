"""All inventoried 3D models require a passport and fail-closed G-code checks."""
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'printing3d/assets'
SPEC = importlib.util.spec_from_file_location('gcode_audit', ROOT / 'printing3d/gcode_audit.py')
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def passports():
    return {p.stem: json.loads(p.read_text()) for p in (ASSETS / 'passports').glob('*.json')}


def test_every_model_has_exactly_one_passport():
    inventory = json.loads((ASSETS / 'models_manifest.json').read_text())['models']
    ps = passports()
    assert set(ps) == {model['id'] for model in inventory}
    for model in inventory:
        passport = ps[model['id']]
        assert passport['id'] == model['id']
        assert passport['source_sha256'] == model['source_archive_sha256']
        assert passport['parts'] == model['quantity']
        assert passport['gcode_status'] == 'not_built'
        assert AUDIT.audit_passport(passport)['status'] == 'SOURCE_ONLY_NOT_PRINT_READY'
        assert passport['firmware_open_verified'] is False
        assert passport['physical_fit_verified'] is False


def test_missing_source_provenance_rejected():
    p = next(iter(passports().values())).copy()
    p['source_sha256'] = ''
    with pytest.raises(ValueError, match='provenance'):
        AUDIT.audit_passport(p)


def test_fake_fit_verification_rejected():
    p = next(iter(passports().values())).copy()
    p['fit_status'] = 'verified'
    with pytest.raises(ValueError, match='evidence'):
        AUDIT.audit_passport(p)


def test_source_only_3mf_rejected_as_print_job(tmp_path):
    p = next(iter(passports().values()))
    source = tmp_path / 'model.3mf'
    source.write_bytes(b'not sliced')
    with pytest.raises(ValueError, match='sliced'):
        AUDIT.audit_passport(p, source)


def test_corrupt_sliced_archive_rejected(tmp_path):
    p = next(iter(passports().values()))
    source = tmp_path / 'model.gcode.3mf'
    source.write_bytes(b'not a zip')
    with pytest.raises(Exception):
        AUDIT.audit_passport(p, source)
