import yaml

from ems.common.config import mapping_dir
from ems.normalizer import controme

MAP = yaml.safe_load((mapping_dir() / "controme_vtr.yaml").read_text(encoding="utf-8"))


def raw(payload):
    return {"source": "controme", "stream": "vtr", "received_at": "2026-10-20T12:00:00.000Z", "payload": payload}


def test_empty_answer_gives_nothing():
    # Stand 20.10.2026: keine Vorlaufregelung eingerichtet -> {} ist normal, kein Fehler
    assert controme.normalize_vtr(raw({}), MAP) == ([], [], [])


def test_documented_fields_are_mapped():
    tel, _, q = controme.normalize_vtr(raw({"3": {"name": "Heizkreis FBH", "ist": 31.5, "soll": 33.0,
                                                  "offset": -1.5}}), MAP)
    assert q == []
    got = {t["quantity"]: (t["value"], t["unit"]) for t in tel}
    assert got == {"flow_temperature": (31.5, "degC"), "flow_temperature_setpoint": (33.0, "degC"),
                   "flow_temperature_offset": (-1.5, "K")}
    assert all(t["asset_id"] == "controme_vorlauf_3" and t["medium"] == "heat" for t in tel)


def test_missing_value_and_unknown_key():
    tel, _, q = controme.normalize_vtr(raw({"1": {"name": "X", "ist": None, "soll": 30, "neu": 1}}), MAP)
    assert [t["quantity"] for t in tel] == ["flow_temperature_setpoint"]
    assert q and q[0]["kind"] == "unknown_keys"
