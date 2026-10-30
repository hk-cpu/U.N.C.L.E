"""Generate examples/demo.xdf and examples/demo.bin for `cardiag bin`.

A tiny, SYNTHETIC calibration — not a real Charger tune, just enough to show
what the read-only bin viewer does with no files of your own: a 2x3 spark table
and a rev-limiter constant. Re-run to regenerate.

    python tools/make_example_xdf.py
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "examples"

XDF = """<!-- SYNTHETIC demo, not a real calibration. For `cardiag bin`. -->
<XDFFORMAT version="1.80">
  <XDFHEADER>
    <BASEOFFSET offset="0x0" subtract="0" />
  </XDFHEADER>
  <XDFTABLE>
    <title>Spark Advance</title>
    <XDFAXIS id="x">
      <units>RPM</units>
      <LABEL index="0" value="800" />
      <LABEL index="1" value="1600" />
      <LABEL index="2" value="2400" />
    </XDFAXIS>
    <XDFAXIS id="y">
      <units>kPa</units>
      <LABEL index="0" value="30" />
      <LABEL index="1" value="100" />
    </XDFAXIS>
    <XDFAXIS id="z">
      <units>deg</units>
      <EMBEDDEDDATA mmedaddress="0x10" mmedelementsizebits="8"
                    mmedrowcount="2" mmedcolcount="3" mmedtypeflags="0x00" />
      <MATH equation="X*0.5" />
      <decimalpl>1</decimalpl>
    </XDFAXIS>
  </XDFTABLE>
  <XDFCONSTANT>
    <title>Rev Limiter</title>
    <units>RPM</units>
    <EMBEDDEDDATA mmedaddress="0x20" mmedelementsizebits="16"
                  mmedtypeflags="0x00" />
    <MATH equation="X" />
  </XDFCONSTANT>
</XDFFORMAT>
"""


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    (HERE / "demo.xdf").write_text(XDF)

    data = bytearray(64)
    # Spark table at 0x10: 2 rows x 3 cols, 8-bit, scaled X*0.5.
    #   low load  (30 kPa): 20, 22, 24 deg -> raw 40, 44, 48
    #   high load (100 kPa): 12, 14, 16 deg -> raw 24, 28, 32
    data[0x10:0x16] = bytes([40, 44, 48, 24, 28, 32])
    # Rev limiter at 0x20: 16-bit big-endian, 5600 rpm = 0x15E0.
    data[0x20:0x22] = (5600).to_bytes(2, "big")
    (HERE / "demo.bin").write_bytes(bytes(data))
    print(f"wrote {HERE/'demo.xdf'} and {HERE/'demo.bin'}")


if __name__ == "__main__":
    main()
