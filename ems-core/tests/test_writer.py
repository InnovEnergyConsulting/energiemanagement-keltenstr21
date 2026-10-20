import json

from ems.writer import convert
from ems.writer.buffer import Buffer
from ems.writer.service import Writer

TEL = {"point_id": "haus.heizung.controme_raum_2.room_climate.temperature", "asset_id": "controme_raum_2",
       "name": "Küche", "medium": "room_climate", "quantity": "temperature", "role": "room_actual", "unit": "degC",
       "measurement_kind": "measured", "circuit_id": "unassigned", "direction": "none", "value": 20.64,
       "measured_at": "2026-10-20T10:06:00.000Z", "received_at": "2026-10-20T10:10:00.000Z", "age_s": 240.0,
       "source": "controme", "mapping_version": 1, "floor": "Erdgeschoss"}


def test_line_protocol():
    bucket, line = convert.telemetry(TEL)
    assert bucket == "ems_detail"
    assert line.startswith("telemetry,")
    assert "asset_id=controme_raum_2" in line and "value=20.64" in line
    from datetime import datetime, timezone
    ms = int(datetime(2026, 10, 20, 10, 6, tzinfo=timezone.utc).timestamp() * 1000)
    assert line.endswith(f" {ms}")                                # Messzeit in ms


def test_buffer_limit_counts_dropped(tmp_path):
    b = Buffer(str(tmp_path / "b.sqlite"), max_rows=3)
    b.add([("ems_detail", f"l{i}") for i in range(5)])
    assert b.count() == 3 and b.dropped == 2
    assert [r[2] for r in b.peek(10)] == ["l2", "l3", "l4"]        # die ältesten wurden verworfen


def test_db_outage_is_buffered_and_replayed_in_order(tmp_path):
    written, state = [], {"up": False}

    def write_fn(bucket, lines):
        if not state["up"]:
            raise ConnectionError("influx down")
        written.extend(lines)

    w = Writer(write_fn, Buffer(str(tmp_path / "b.sqlite"), 1000))
    for i in range(3):
        w.handle(f"ems/v1/telemetry/a/temperature", json.dumps({**TEL, "value": float(i),
                 "measured_at": f"2026-10-20T10:0{i}:00.000Z"}).encode())
    w.flush()
    assert written == [] and w.buffer.count() == 3
    w.handle("ems/v1/telemetry/a/temperature", json.dumps({**TEL, "value": 9.0}).encode())
    state["up"] = True
    w.flush()
    assert w.buffer.count() == 0
    assert [l.split("value=")[1].split(" ")[0] for l in written] == ["0", "1", "2", "9"]   # Float ohne "i"-Suffix


def test_garbage_message_is_ignored(tmp_path):
    w = Writer(lambda b, l: None, Buffer(str(tmp_path / "b.sqlite"), 10))
    w.handle("ems/v1/telemetry/x/y", b"nicht json")
    w.handle("ems/v1/telemetry/x/y", json.dumps({"value": 1}).encode())
    assert w.pending == []


def test_permanent_rejection_is_not_buffered(tmp_path):
    from influxdb_client.rest import ApiException

    def write_fn(bucket, lines):
        raise ApiException(status=422, reason="Unprocessable Entity")

    w = Writer(write_fn, Buffer(str(tmp_path / "b.sqlite"), 100))
    w.handle("ems/v1/telemetry/a/temperature", json.dumps(TEL).encode())
    w.flush()
    assert w.buffer.count() == 0          # nicht gepuffert, sonst blockiert der Puffer für immer
