# Example logs

Sample data so the tuning analysis is usable with no car and no adapter.

## `charger-pull.csv`

A representative wide-open-throttle pull for the 2006 Charger R/T: a short
cruise, a full-throttle run with the revs climbing from ~1,800 to the limiter,
then a lift. The columns are what `cardiag live ... --log` records — an epoch
timestamp and one column per channel.

Analyse it:

```bash
cardiag pull examples/charger-pull.csv
```

You get the pull read off the way a tuner reads one — timing eased back as the
revs climb, intake air heat-soaking, fuel trims at wide-open throttle — and an
RPM × load table of timing advance. Add `--json` to script it, or
`--channel LONG_FUEL_TRIM_1` to table a different channel.

Regenerate it with `python tools/make_example_log.py`.

## Recording your own

On the car, record the channels a pull is read from while you make the run:

```bash
cardiag live RPM INTAKE_PRESSURE THROTTLE_POS TIMING_ADVANCE INTAKE_TEMP \
    LONG_FUEL_TRIM_1 LONG_FUEL_TRIM_2 --log mypull.csv
cardiag pull mypull.csv
```

The browser app's **Tuning** tab does the same capture-and-analyse without the
command line: `cardiag app`, open Tuning, **Start capture**, make the pull.

Nothing here writes to the car — it reads, records and analyses, which is the
whole job that surrounds a tune.
