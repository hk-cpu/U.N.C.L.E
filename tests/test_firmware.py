"""Build and run the ESP32 firmware's decode tests as part of the suite.

The firmware's arithmetic is plain C with no ESP32 dependency precisely so it
can be checked here. Running it from pytest means it cannot quietly rot while
the Python side moves on - and it is the only part of the firmware that can be
verified without the board in hand.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

FIRMWARE = Path(__file__).resolve().parent.parent / "firmware"
TEST_DIR = FIRMWARE / "test"

needs_cc = pytest.mark.skipif(
    shutil.which("cc") is None and shutil.which("gcc") is None,
    reason="no C compiler available to build the firmware tests",
)


@needs_cc
def test_firmware_decoding_matches_the_vehicle_reference(tmp_path):
    binary = tmp_path / "test_obd"
    compiler = shutil.which("cc") or shutil.which("gcc")

    build = subprocess.run(
        [compiler, "-std=c99", "-Wall", "-Wextra", "-Werror", "-O1",
         "-o", str(binary),
         str(TEST_DIR / "test_obd.c"),
         str(FIRMWARE / "cardiag-esp32" / "obd.c"), "-lm"],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, f"firmware did not compile:\n{build.stderr}"
    assert not build.stderr.strip(), f"firmware compiled with warnings:\n{build.stderr}"

    run = subprocess.run([str(binary)], capture_output=True, text=True)
    assert run.returncode == 0, f"firmware decode tests failed:\n{run.stdout}"
    assert "0 failures" in run.stdout


@needs_cc
def test_the_sketch_only_depends_on_obd_h_for_decoding():
    """Keep the testable half testable.

    If decoding drifts into the .ino it stops being checkable here, because
    that file needs the Arduino toolchain. The split is the whole reason any
    of this firmware can be verified before it reaches a car.
    """
    sketch = (FIRMWARE / "cardiag-esp32" / "cardiag-esp32.ino").read_text()

    for formula in ("* 256", "/ 4.0", "- 40.0", "* 100.0 / 255.0"):
        assert formula not in sketch, (
            f"decoding arithmetic ({formula!r}) has leaked into the sketch, "
            "where it cannot be tested"
        )


def test_the_firmware_ships_a_self_test_that_needs_no_hardware():
    sketch = (FIRMWARE / "cardiag-esp32" / "cardiag-esp32.ino").read_text()
    assert "#define SELF_TEST 1" in sketch, (
        "the sketch should arrive in self-test mode, so a first upload proves "
        "the board before anything is wired"
    )
