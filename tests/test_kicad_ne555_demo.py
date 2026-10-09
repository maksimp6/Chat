"""NE555 artifact regression tests; Python-only evidence and optional live KiCad checks.

Run: python -m unittest tests.test_kicad_ne555_demo -v
Refresh evidence with KiCad 9+: python tests/test_kicad_ne555_demo.py --refresh-evidence
The snapshot is bound to the editable schematic AND project by SHA-256. A fresh
KiCad export/ERC run is required after either input changes; CI never silently
accepts an old export. Live tests skip explicitly when kicad-cli is unavailable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


ARTIFACT = Path(__file__).resolve().parents[1] / "docs/artifacts/kicad/ne555-led-blinker"
SCHEMATIC = ARTIFACT / "NE555_LED_Blinker.kicad_sch"
PROJECT = SCHEMATIC.with_suffix(".kicad_pro")
EVIDENCE = ARTIFACT / "validation"
CLI = shutil.which("kicad-cli")
VALUES = {
    "R1": 10000.0,
    "R2": 68000.0,
    "R3": 1000.0,
    "C1": 1e-7,
    "C2": 1e-5,
    "C3": 1e-5,
    "C4": 1e-8,
}
# Compare pin membership, not auto-generated net names or numeric net codes.
NETS = {
    "supply_reset": {"J1.1", "U1.8", "U1.4", "R1.1", "C1.1", "C2.1"},
    "ground": {"J1.2", "U1.1", "C1.2", "C2.2", "C3.2", "C4.2", "D1.1"},
    "discharge": {"R1.2", "R2.1", "U1.7"},
    "timing": {"R2.2", "C3.1", "U1.2", "U1.6"},
    "control": {"U1.5", "C4.1"},
    "output": {"U1.3", "R3.1"},
    "led_anode": {"R3.2", "D1.2"},
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def circuit(root):
    components = {}
    for part in root.findall("components/comp"):
        ref = part.attrib["ref"]
        if ref in components:
            raise AssertionError(f"Duplicate component: {ref}")
        source = part.find("libsource")
        components[ref] = {
            "value": part.findtext("value"),
            "symbol": f"{source.attrib['lib']}:{source.attrib['part']}",
            "footprint": part.findtext("footprint"),
        }
    nets = []
    seen = set()
    for net in root.findall("nets/net"):
        members = set()
        for node in net.findall("node"):
            pin = f"{node.attrib['ref']}.{node.attrib['pin']}"
            if pin in seen:
                raise AssertionError(f"Pin appears more than once: {pin}")
            seen.add(pin)
            members.add(pin)
        nets.append(frozenset(members))
    return components, nets


def value_si(value):
    """Parse the explicit engineering values used in this artifact; reject garbage."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmunp]?)(?:F|[ΩR])?\s*", value)
    if not match:
        raise AssertionError(f"Unsupported component value: {value!r}")
    return (
        float(match[1]) * {"": 1, "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12}[match[2]]
    )


def check_values(data):
    components, _ = data
    for ref, expected in VALUES.items():
        actual = value_si(components[ref]["value"])
        if not math.isclose(actual, expected, rel_tol=1e-12):
            raise AssertionError(f"{ref}: expected {expected:g}, actual {actual:g}")


def check_connectivity(data):
    _, actual = data
    expected = {frozenset(net) for net in NETS.values()}
    if len(actual) != len(expected) or set(actual) != expected:
        missing = [sorted(net) for net in expected - set(actual)]
        extra = [sorted(net) for net in set(actual) - expected]
        raise AssertionError(f"Connectivity mismatch; missing={missing}; unexpected={extra}")


def timing(data):
    """TI NE555 Rev. K section 6.3.2; ideal steady state, not simulation."""
    components, _ = data
    ra, rb, cap = (value_si(components[ref]["value"]) for ref in ("R1", "R2", "C3"))
    high = math.log(2) * (ra + rb) * cap
    low = math.log(2) * rb * cap
    return {
        "high_s": high,
        "low_s": low,
        "period_s": high + low,
        "frequency_hz": 1 / (high + low),
        "duty_high": high / (high + low),
    }


def violations(report):
    return [item for sheet in report["sheets"] for item in sheet["violations"]]


class KiCadSession:
    """Use only temporary settings; never change the user's KiCad configuration."""

    def __init__(self, directory):
        if not CLI:
            raise RuntimeError("kicad-cli is required to refresh/live-test the schematic")
        self.directory = Path(directory)
        self.env = os.environ.copy()
        for variable, child in (
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_CACHE_HOME", "cache"),
            ("XDG_DATA_HOME", "data"),
        ):
            self.env[variable] = str(self.directory / child)
        self.version = self.run("version").stdout.strip()
        major = self.version.split(".")[0]
        if int(major) < 9:
            raise RuntimeError(f"KiCad 9+ required, found {self.version}")
        candidates = [
            os.environ.get("KICAD_TEST_SYMBOL_DIR", ""),
            os.environ.get(f"KICAD{major}_SYMBOL_DIR", ""),
            "/usr/share/kicad/symbols",
            "/usr/local/share/kicad/symbols",
            "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
        ]
        symbols = next(
            (Path(p) for p in candidates if p and (Path(p) / "Timer.kicad_sym").is_file()), None
        )
        if symbols is None:
            raise RuntimeError(
                "Install standard KiCad symbol libraries or set KICAD_TEST_SYMBOL_DIR"
            )
        config = self.directory / "config/kicad" / f"{major}.0"
        config.mkdir(parents=True, exist_ok=True)
        libraries = ("Timer", "Device", "Connector_Generic", "power")
        entries = [
            f'(lib (name "{name}")(type "KiCad")(uri "{(symbols / (name + ".kicad_sym")).as_posix()}")(options "")(descr ""))'
            for name in libraries
        ]
        (config / "sym-lib-table").write_text("(sym_lib_table\n" + "\n".join(entries) + "\n)\n")

    def run(self, *args, check=True):
        result = subprocess.run(
            [CLI, *map(str, args)], env=self.env, capture_output=True, text=True, timeout=60
        )
        if check and result.returncode:
            raise AssertionError(
                f"KiCad exit {result.returncode}: {' '.join(map(str, args))}\n{result.stdout}\n{result.stderr}"
            )
        return result

    def export(self, schematic, output):
        self.run("sch", "export", "netlist", "--format", "kicadxml", "--output", output, schematic)
        return ET.parse(output).getroot()

    def erc(self, schematic, output):
        result = self.run(
            "sch",
            "erc",
            "--format",
            "json",
            "--severity-all",
            "--exit-code-violations",
            "--output",
            output,
            schematic,
            check=False,
        )
        report = json.loads(Path(output).read_text())
        if result.returncode or violations(report):
            raise AssertionError(
                f"ERC exit {result.returncode}: {violations(report)}\n{result.stdout}\n{result.stderr}"
            )
        return report


def refresh_evidence():
    with tempfile.TemporaryDirectory(prefix="ne555-") as directory:
        session = KiCadSession(directory)
        root = session.export(SCHEMATIC, Path(directory) / "netlist.xml")
        data = circuit(root)
        check_values(data)
        check_connectivity(data)
        report = session.erc(SCHEMATIC, Path(directory) / "erc.json")
        # Remove only machine-local provenance; retain electrical data and tool version.
        root.find("design/source").text = SCHEMATIC.name
        EVIDENCE.mkdir(exist_ok=True)
        ET.indent(root, space="  ")
        ET.ElementTree(root).write(EVIDENCE / "netlist.xml", encoding="utf-8", xml_declaration=True)
        (EVIDENCE / "erc.json").write_text(json.dumps(report, indent=2) + "\n")
        inputs = {path.name: digest(path) for path in (SCHEMATIC, PROJECT)}
        outputs = {name: digest(EVIDENCE / name) for name in ("netlist.xml", "erc.json")}
        manifest = {
            "kicad_version": session.version,
            "inputs_sha256": inputs,
            "outputs_sha256": outputs,
            "timing_nominal": timing(data),
            "timing_method": "ideal RC calculation using ln(2); not SPICE or physical measurement",
        }
        (EVIDENCE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(manifest, indent=2))


class ArchivedCircuitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ET.parse(EVIDENCE / "netlist.xml").getroot()
        cls.data = circuit(cls.root)

    def test_evidence_matches_editable_source_project_and_outputs(self):
        manifest = json.loads((EVIDENCE / "manifest.json").read_text())
        for group, directory in (("inputs_sha256", ARTIFACT), ("outputs_sha256", EVIDENCE)):
            expected_names = (
                {SCHEMATIC.name, PROJECT.name}
                if group == "inputs_sha256"
                else {"netlist.xml", "erc.json"}
            )
            self.assertEqual(set(manifest[group]), expected_names)
            for name, expected in manifest[group].items():
                self.assertEqual(
                    digest(directory / name),
                    expected,
                    f"Stale {name}: rerun --refresh-evidence with KiCad",
                )

    def test_component_inventory_symbols_and_polarity(self):
        parts, _ = self.data
        expected = {
            "U1": "Timer:NE555P",
            "D1": "Device:LED",
            "J1": "Connector_Generic:Conn_01x02",
            "R1": "Device:R",
            "R2": "Device:R",
            "R3": "Device:R",
            "C1": "Device:C",
            "C2": "Device:C_Polarized",
            "C3": "Device:C_Polarized",
            "C4": "Device:C",
        }
        self.assertEqual({ref: part["symbol"] for ref, part in parts.items()}, expected)
        self.assertEqual(parts["U1"]["footprint"], "Package_DIP:DIP-8_W7.62mm")
        pins = self.root.findall("libparts/libpart[@part='LED']/pins/pin")
        self.assertEqual(
            {pin.attrib["num"]: pin.attrib["name"] for pin in pins}, {"1": "K", "2": "A"}
        )

    def test_all_nominal_component_values(self):
        check_values(self.data)

    def test_supply_reset_ground_and_bypass(self):
        for group in ("supply_reset", "ground"):
            self.assertIn(frozenset(NETS[group]), self.data[1])

    def test_timing_discharge_and_control_nets(self):
        for group in ("timing", "discharge", "control"):
            self.assertIn(frozenset(NETS[group]), self.data[1])

    def test_led_series_resistor_and_orientation(self):
        for group in ("output", "led_anode"):
            self.assertIn(frozenset(NETS[group]), self.data[1])
        ground = next(net for net in self.data[1] if "J1.2" in net)
        self.assertIn("D1.1", ground)
        self.assertNotIn("D1.2", ground)

    def test_all_pins_connected_with_no_extra_nets_or_shorts(self):
        check_connectivity(self.data)
        self.assertEqual(sum(map(len, self.data[1])), 26)

    def test_nominal_frequency_high_low_and_duty(self):
        result = timing(self.data)
        manifest = json.loads((EVIDENCE / "manifest.json").read_text())
        self.assertEqual(manifest["timing_nominal"], result)
        for key, expected in {
            "frequency_hz": 0.9881473,
            "high_s": 0.5406548,
            "low_s": 0.4713401,
            "period_s": 1.0119949,
            "duty_high": 0.5342466,
        }.items():
            self.assertAlmostEqual(result[key], expected, places=6, msg=key)

    def test_led_current_and_resistor_power_upper_bounds(self):
        resistance = value_si(self.data[0]["R3"]["value"])
        # Conservative ideal 5 V output, even with a shorted LED; no V_OH/V_F claim.
        self.assertLessEqual(5 / resistance, 0.005)
        self.assertLessEqual(5**2 / resistance, 0.025)

    def test_recorded_erc_has_no_violations_including_exclusions(self):
        report = json.loads((EVIDENCE / "erc.json").read_text())
        self.assertTrue(report["sheets"])
        self.assertEqual(violations(report), [])

    def test_negative_mutation_detects_disconnected_reset_pin(self):
        root = copy.deepcopy(self.root)
        supply = root.find("nets/net[@name='+5V']")
        supply.remove(supply.find("node[@ref='U1'][@pin='4']"))
        with self.assertRaisesRegex(AssertionError, "Connectivity mismatch"):
            check_connectivity(circuit(root))

    def test_negative_mutation_detects_reversed_led(self):
        root = copy.deepcopy(self.root)
        for node in root.findall("nets/net/node[@ref='D1']"):
            node.attrib["pin"] = {"1": "2", "2": "1"}[node.attrib["pin"]]
        with self.assertRaisesRegex(AssertionError, "Connectivity mismatch"):
            check_connectivity(circuit(root))

    def test_negative_mutation_detects_changed_resistor_value(self):
        root = copy.deepcopy(self.root)
        root.find("components/comp[@ref='R2']/value").text = "6.8 k"
        with self.assertRaisesRegex(AssertionError, "R2"):
            check_values(circuit(root))
        self.assertGreater(timing(circuit(root))["frequency_hz"], 6)


@unittest.skipUnless(CLI, "live KiCad checks require kicad-cli; archived evidence tests still run")
class LiveKiCadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="ne555-live-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.session = KiCadSession(cls.directory)

    def test_live_netlist_matches_archived_electrical_data(self):
        actual = circuit(self.session.export(SCHEMATIC, self.directory / "live.xml"))
        archived = circuit(ET.parse(EVIDENCE / "netlist.xml").getroot())
        self.assertEqual(actual[0], archived[0])
        self.assertEqual(set(actual[1]), set(archived[1]))
        check_values(actual)
        check_connectivity(actual)

    def test_live_erc_zero_errors_warnings_and_exclusions(self):
        self.session.erc(SCHEMATIC, self.directory / "live-erc.json")

    def mutated_export(self, name, pattern, replacement):
        directory = self.directory / name
        directory.mkdir()
        modified, count = re.subn(pattern, replacement, SCHEMATIC.read_text())
        self.assertEqual(count, 1, "Mutation must alter exactly one source item")
        schematic = directory / SCHEMATIC.name
        schematic.write_text(modified)
        shutil.copyfile(PROJECT, directory / PROJECT.name)
        return circuit(self.session.export(schematic, directory / "mutated.xml"))

    def test_live_mutation_detects_reset_supply_disconnection(self):
        data = self.mutated_export(
            "open-reset",
            r'\(label\s+"\+5V"(\s+\(at\s+120\.65\s+96\.52\b)',
            r'(label "RESET_DISCONNECTED"\1',
        )
        with self.assertRaisesRegex(AssertionError, "Connectivity mismatch"):
            check_connectivity(data)

    def test_live_mutation_detects_r2_value_change(self):
        data = self.mutated_export("wrong-r2", r'(\(property\s+"Value"\s+)"68 k"', r'\1"6.8 k"')
        with self.assertRaisesRegex(AssertionError, "R2"):
            check_values(data)
        self.assertGreater(timing(data)["frequency_hz"], 6)


if __name__ == "__main__":
    if sys.argv[1:] == ["--refresh-evidence"]:
        refresh_evidence()
    else:
        unittest.main()
