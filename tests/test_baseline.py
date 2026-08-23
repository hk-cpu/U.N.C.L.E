"""Baseline storage and comparison."""

import pytest

from cardiag import baseline as baseline_module, report as report_module
from cardiag.baseline import BaselineStore
from cardiag.cli import main
from cardiag.session import Session


@pytest.fixture
def store(tmp_path):
    with BaselineStore(tmp_path / "baselines.db") as opened:
        yield opened


def snapshot_of(profile: str) -> dict:
    with Session(f"sim://?profile={profile}") as car:
        return report_module.build(car).to_dict()


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr().out


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def test_saving_and_reading_back(store):
    saved = store.save(snapshot_of("charger"), "before-plugs")
    assert saved.label == "before-plugs"
    assert saved.vin == "2B3KA53H66H123456"

    loaded = store.get(saved.id)
    assert loaded.label == saved.label
    assert loaded.payload["headline"] == "No faults found"


def test_listing_is_newest_first(store):
    store.save(snapshot_of("charger"), "first")
    store.save(snapshot_of("charger"), "second")
    assert [s.label for s in store.list()] == ["second", "first"]


def test_listing_can_be_filtered_by_vin(store):
    store.save(snapshot_of("charger"), "charger")
    store.save(snapshot_of("default"), "other-car")

    charger = store.list(vin="2B3KA53H66H123456")
    assert [s.label for s in charger] == ["charger"]


def test_resolve_accepts_ids_labels_and_keywords(store):
    first = store.save(snapshot_of("charger"), "oldest")
    last = store.save(snapshot_of("charger"), "newest")

    assert store.resolve(str(first.id)).id == first.id
    assert store.resolve("oldest").id == first.id
    assert store.resolve("latest").id == last.id
    assert store.resolve("first").id == first.id


def test_resolve_rejects_an_unknown_label(store):
    store.save(snapshot_of("charger"), "real")
    with pytest.raises(KeyError):
        store.resolve("imaginary")


def test_resolve_on_an_empty_store(store):
    with pytest.raises(KeyError, match="no snapshots"):
        store.resolve("latest")


def test_deleting(store):
    saved = store.save(snapshot_of("charger"), "temp")
    assert store.delete(saved.id) is True
    assert store.delete(saved.id) is False
    assert store.list() == []


def test_missing_snapshot_raises(store):
    with pytest.raises(KeyError):
        store.get(999)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def test_identical_snapshots_show_no_change(store):
    payload = snapshot_of("charger")
    before = store.save(payload, "a")
    after = store.save(payload, "b")
    assert baseline_module.compare(before, after) == []
    assert "Nothing measurably changed" in baseline_module.summarise([])


def test_degradation_is_reported_as_worse(store):
    before = store.save(snapshot_of("charger"), "healthy")
    after = store.save(snapshot_of("charger-wear"), "worn")

    changes = baseline_module.compare(before, after)
    assert changes
    assert all(c.direction == "worse" for c in changes if c.category == "misfire")

    misfires = {c.label: c for c in changes if c.category == "misfire"}
    assert "Misfire counts, cylinder 4" in misfires
    # Worse changes sort to the front.
    assert changes[0].direction == "worse"


def test_repair_is_reported_as_better(store):
    before = store.save(snapshot_of("charger-wear"), "worn")
    after = store.save(snapshot_of("charger"), "fixed")

    changes = baseline_module.compare(before, after)
    assert any(c.direction == "better" for c in changes)


def test_new_codes_are_flagged(store):
    before = store.save(snapshot_of("charger"), "clean")
    after = store.save(snapshot_of("charger-misfire"), "broken")

    changes = baseline_module.compare(before, after)
    new_codes = next(c for c in changes if c.label == "New trouble codes")
    assert new_codes.direction == "worse"
    assert "P0304" in new_codes.after


def test_a_reflash_is_detected(store):
    payload = snapshot_of("charger")
    before = store.save(payload, "stock")

    reflashed = snapshot_of("charger")
    reflashed["calibration"] = {
        "calibration_ids": ["68XX9999ZZ"],
        "verification_numbers": ["DEADBEEF"],
    }
    after = store.save(reflashed, "tuned")

    change = next(c for c in baseline_module.compare(before, after)
                  if c.category == "calibration")
    assert "68RT0057AA" in change.before
    assert "68XX9999ZZ" in change.after
    assert "reflashed" in change.note


def test_misfires_are_not_double_reported(store):
    """The counters and the monitor headroom describe the same thing."""
    before = store.save(snapshot_of("charger"), "healthy")
    after = store.save(snapshot_of("charger-wear"), "worn")

    changes = baseline_module.compare(before, after)
    monitor_labels = [c.label for c in changes if c.category == "monitor"]
    assert not any("Misfire cylinder" in label for label in monitor_labels)


def test_summarise_counts_directions():
    changes = [
        baseline_module.Change("x", "a", "1", "2", direction="worse"),
        baseline_module.Change("x", "b", "1", "2", direction="better"),
        baseline_module.Change("x", "c", "1", "2", direction="neutral"),
    ]
    summary = baseline_module.summarise(changes)
    assert "1 worse" in summary and "1 better" in summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_save_list_and_compare(capsys, tmp_path):
    db = str(tmp_path / "b.db")

    code, out = run(capsys, "--sim", "--sim-profile", "charger",
                    "baseline", "before", "--store", db)
    assert code == 0
    assert "Saved snapshot #1" in out

    code, out = run(capsys, "--sim", "baseline", "--list", "--store", db)
    assert code == 0
    assert "before" in out

    # Compare a healthy baseline against a worn car: a regression, so exit 1.
    code, out = run(capsys, "--sim", "--sim-profile", "charger-wear",
                    "compare", "before", "--store", db)
    assert code == 1
    assert "Misfire counts, cylinder 4" in out


def test_cli_compare_with_no_change_exits_zero(capsys, tmp_path):
    db = str(tmp_path / "b.db")
    run(capsys, "--sim", "--sim-profile", "charger", "baseline", "x", "--store", db)

    code, out = run(capsys, "--sim", "--sim-profile", "charger",
                    "compare", "x", "--store", db)
    assert code == 0
    assert "Nothing measurably changed" in out


def test_cli_compare_rejects_an_unknown_snapshot(capsys, tmp_path):
    db = str(tmp_path / "b.db")
    code, _ = run(capsys, "--sim", "compare", "nope", "--store", db)
    assert code == 2


def test_cli_delete(capsys, tmp_path):
    db = str(tmp_path / "b.db")
    run(capsys, "--sim", "--sim-profile", "charger", "baseline", "x", "--store", db)

    code, out = run(capsys, "--sim", "baseline", "--delete", "1", "--store", db)
    assert code == 0
    assert "Deleted snapshot #1" in out


def test_cli_empty_list(capsys, tmp_path):
    code, out = run(capsys, "--sim", "baseline", "--list",
                    "--store", str(tmp_path / "empty.db"))
    assert code == 0
    assert "No snapshots saved yet" in out
