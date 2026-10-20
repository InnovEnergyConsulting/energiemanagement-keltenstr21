"""MQTT-Anbindung mit Wiederverbindung und Verfügbarkeitsmeldung (Last Will).

Jeder Dienst meldet sich mit eigenem Konto an (Broker-ACL). Bei Verbindungsabbruch
versucht paho selbst neu zu verbinden (1 s … 120 s, wachsend) – Startreihenfolge ersetzt
keine Wiederverbindungslogik (Anforderungen Kap. 3).
"""
import json
import logging
import threading

import paho.mqtt.client as mqtt

from .config import env, secret
from .timeutil import iso, utcnow

log = logging.getLogger("ems.mqtt")


def availability_payload(status: str, reason: str = "") -> str:
    return json.dumps({"status": status, "since": iso(utcnow()), "reason": reason})


class Bus:
    def __init__(self, client_id: str, user: str, availability_topic: str | None = None,
                 subscriptions: list[str] | None = None, on_message=None):
        self.availability_topic = availability_topic
        self.subscriptions = subscriptions or []
        self.connected = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id,
                                  clean_session=True)
        self.client.username_pw_set(user, secret(f"mqtt_{user}"))
        self.client.reconnect_delay_set(min_delay=1, max_delay=120)
        if availability_topic:
            self.client.will_set(availability_topic, availability_payload("offline", "connection lost"),
                                 qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        if on_message:
            self.client.on_message = lambda c, u, msg: on_message(msg.topic, msg.payload)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT-Anmeldung abgelehnt: %s", reason_code)
            return
        log.info("MQTT verbunden")
        for topic in self.subscriptions:
            client.subscribe(topic, qos=1)
        if self.availability_topic:
            client.publish(self.availability_topic, availability_payload("online"), qos=1, retain=True)
        self.connected.set()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected.clear()
        log.warning("MQTT getrennt (%s) – Wiederverbindung läuft", reason_code)

    def start(self):
        self.client.connect_async(env("EMS_MQTT_HOST", "mosquitto"), int(env("EMS_MQTT_PORT", "1883")),
                                  keepalive=30)
        self.client.loop_start()
        return self

    def publish_json(self, topic: str, payload, qos: int = 1, retain: bool = False):
        return self.client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=qos, retain=retain)

    def set_availability(self, status: str, reason: str = ""):
        if self.availability_topic:
            self.client.publish(self.availability_topic, availability_payload(status, reason), qos=1, retain=True)
