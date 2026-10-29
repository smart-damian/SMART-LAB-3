"""Tests for repeat suppression, the multi-timeframe alert and the state files.
No internet, no Telegram; the decision logic is pure so we just feed it dicts."""
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alerts import (BUY, SELL, Alert, empty_state, fresh_alerts, is_usable,
                    plan_alerts, zones_from_alerts)
from timeframes import Alert2Setting
from state import load_state, save_state, state_path

NOW = datetime(2026, 10, 26, 14, 5, tzinfo=timezone.utc)


def _alert(symbol="EUR", signal=SELL, multiple=1.0):
    return Alert(symbol, signal, 100.0, 99.0, multiple, 1.0, multiple)


def _partner(zones, minutes_ago=1):
    return {"updated": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
            "zones": zones, "confluence": {}}


# ---- state files

def test_missing_state_file_gives_empty_state():
    with tempfile.TemporaryDirectory() as d:
        assert load_state(state_path(d, "5m")) == empty_state()


def test_state_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        path = state_path(d, "15m")
        state = {"updated": NOW.isoformat(), "zones": {"EUR": {"SELL": 1.2}}, "confluence": {}}
        save_state(path, state)
        assert load_state(path) == state


def test_corrupt_state_file_does_not_crash():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "5m.json"
        path.write_text("{ not json")
        assert load_state(path) == empty_state()
        path.write_text('["a list, not an object"]')
        assert load_state(path) == empty_state()


# ---- repeat suppression (Alert 1)

def test_first_run_alerts_everything_near_a_level():
    plan = plan_alerts("1h", [_alert()], [], empty_state(), now=NOW)
    assert [(a.symbol, a.signal) for a in plan.new_alerts] == [("EUR", SELL)]
    assert plan.state["zones"] == {"EUR": {SELL: 1.0}}


def test_same_signal_is_not_repeated():
    first = plan_alerts("1h", [_alert()], [], empty_state(), now=NOW)
    second = plan_alerts("1h", [_alert()], [], first.state, now=NOW)
    assert second.new_alerts == []
    assert second.still_near == 1
    assert second.state["zones"] == {"EUR": {SELL: 1.0}}   # still remembered


def test_alerts_again_after_leaving_and_re_entering():
    first = plan_alerts("1h", [_alert()], [], empty_state(), now=NOW)
    left = plan_alerts("1h", [], [], first.state, now=NOW)
    assert left.state["zones"] == {}
    again = plan_alerts("1h", [_alert()], [], left.state, now=NOW)
    assert len(again.new_alerts) == 1


def test_a_different_signal_on_the_same_symbol_is_new():
    first = plan_alerts("1h", [_alert(signal=SELL)], [], empty_state(), now=NOW)
    second = plan_alerts("1h", [_alert(signal=BUY)], [], first.state, now=NOW)
    assert [a.signal for a in second.new_alerts] == [BUY]


def test_skipped_symbol_keeps_its_memory_so_it_does_not_repeat_later():
    first = plan_alerts("1h", [_alert()], [], empty_state(), now=NOW)
    during_outage = plan_alerts("1h", [], ["EUR"], first.state, now=NOW)
    assert during_outage.state["zones"] == {"EUR": {SELL: 1.0}}
    back = plan_alerts("1h", [_alert()], [], during_outage.state, now=NOW)
    assert back.new_alerts == []


def test_fresh_alerts_and_zones_helpers():
    zones = zones_from_alerts([_alert("A", SELL, 1.234), _alert("A", BUY, 0.5)])
    assert zones == {"A": {SELL: 1.23, BUY: 0.5}}
    assert fresh_alerts([_alert("A", SELL)], zones) == []
    assert len(fresh_alerts([_alert("B", SELL)], zones)) == 1


# ---- multi-timeframe alert (Alert 2): default setting = any 2 of all 6 timeframes

def _run(partners, previous=None, alerts=None, **kwargs):
    """One run of the 5m evaluator. By default 5m itself shows a SELL on EUR."""
    alerts = [_alert()] if alerts is None else alerts
    return plan_alerts("5m", alerts, kwargs.pop("skipped", []), previous or empty_state(),
                       partners, NOW, **kwargs)


def _says(signal=SELL, multiple=0.7, symbol="EUR", minutes_ago=1):
    return _partner({symbol: {signal: multiple}}, minutes_ago)


def test_two_timeframes_agreeing_fires_and_lists_them():
    plan = _run({"4h": _says(SELL, 0.7)}, last_prices={"EUR": 99.0})
    assert len(plan.confluences) == 1
    c = plan.confluences[0]
    assert (c.symbol, c.signal, c.last) == ("EUR", SELL, 99.0)
    assert c.multiples == {"5m": 1.0, "4h": 0.7}
    assert plan.state["confluence"] == {"EUR": {SELL: ["5m", "4h"]}}


def test_one_timeframe_alone_is_not_enough():
    assert _run({}).confluences == []
    assert _run({"4h": _partner({})}).confluences == []


def test_different_signals_do_not_agree():
    assert _run({"4h": _says(BUY)}).confluences == []


def test_agreement_does_not_have_to_include_the_base_timeframe():
    plan = _run({"1h": _says(SELL, 0.4), "4h": _says(SELL, 1.1)}, alerts=[])
    assert [(c.symbol, c.signal) for c in plan.confluences] == [("EUR", SELL)]
    assert list(plan.confluences[0].multiples) == ["1h", "4h"]


def test_all_agreeing_timeframes_are_listed_fastest_first():
    partners = {"1d": _says(), "30m": _says(), "15m": _says()}
    assert list(_run(partners).confluences[0].multiples) == ["5m", "15m", "30m", "1d"]


def test_not_repeated_while_the_same_group_stays_put():
    first = _run({"4h": _says()})
    second = _run({"4h": _says()}, previous=first.state)
    assert second.confluences == []
    assert second.state["confluence"] == {"EUR": {SELL: ["5m", "4h"]}}


def test_a_timeframe_joining_the_group_alerts_again_with_the_full_list():
    first = _run({"4h": _says()})
    second = _run({"4h": _says(), "1h": _says()}, previous=first.state)
    assert len(second.confluences) == 1
    assert list(second.confluences[0].multiples) == ["5m", "1h", "4h"]


def test_alerts_again_after_the_agreement_broke_and_came_back():
    first = _run({"4h": _says()})
    broken = _run({"4h": _partner({})}, previous=first.state)      # 4h no longer near a level
    assert broken.state["confluence"] == {}
    again = _run({"4h": _says()}, previous=broken.state)
    assert len(again.confluences) == 1


def test_min_agree_can_be_raised():
    setting = Alert2Setting(("5m", "15m", "30m"), min_agree=3)
    two = _run({"15m": _says()}, setting=setting)
    three = _run({"15m": _says(), "30m": _says()}, setting=setting)
    assert two.confluences == []
    assert len(three.confluences) == 1


def test_custom_timeframe_set_uses_its_fastest_as_evaluator():
    setting = Alert2Setting(("1h", "4h", "1d"), min_agree=2)
    partners = {"4h": _says(minutes_ago=30), "1d": _says(minutes_ago=600)}
    plan = plan_alerts("1h", [_alert()], [], empty_state(), partners, NOW, setting=setting)
    assert list(plan.confluences[0].multiples) == ["1h", "4h", "1d"]
    assert plan_alerts("5m", [_alert()], [], empty_state(), partners, NOW,
                       setting=setting).confluences == []


def test_stale_timeframe_is_not_counted_but_the_rest_still_work():
    partners = {"15m": _says(), "4h": _says(minutes_ago=2000)}      # 4h limit is 4 * 240 = 960 min
    plan = _run(partners)
    assert list(plan.confluences[0].multiples) == ["5m", "15m"]
    assert any("4h" in note for note in plan.notes)


def test_when_it_cannot_be_evaluated_the_memory_is_kept():
    previous = {"updated": None, "zones": {}, "confluence": {"EUR": {SELL: ["5m", "4h"]}}}
    plan = _run({}, previous=previous)
    assert plan.confluences == []
    assert plan.state["confluence"] == {"EUR": {SELL: ["5m", "4h"]}}
    assert any("skipped" in note for note in plan.notes)


def test_only_the_base_run_evaluates_alert_2():
    plan = plan_alerts("15m", [_alert()], [], empty_state(), {"4h": _says()}, NOW)
    assert plan.confluences == []
    assert "confluence" not in plan.state


def test_old_memory_format_is_read_without_crashing():
    previous = {"updated": None, "zones": {}, "confluence": {"EUR": ["SELL"]}}   # older file format
    plan = _run({"4h": _says()}, previous=previous)
    assert len(plan.confluences) == 1     # one re-alert at migration, then normal


def test_symbol_skipped_on_the_base_keeps_its_vote_and_does_not_repeat():
    previous = {"updated": None, "zones": {"EUR": {SELL: 1.0}},
                "confluence": {"EUR": {SELL: ["5m", "4h"]}}}
    plan = _run({"4h": _says()}, previous=previous, alerts=[], skipped=["EUR"])
    assert plan.confluences == []
    assert plan.state["confluence"] == {"EUR": {SELL: ["5m", "4h"]}}


def test_is_usable_checks_age_and_format():
    assert is_usable(_partner({}, minutes_ago=10), "15m", NOW)       # limit 60
    assert not is_usable(_partner({}, minutes_ago=61), "15m", NOW)
    assert not is_usable(empty_state(), "15m", NOW)
    assert not is_usable({"updated": "garbage"}, "15m", NOW)
