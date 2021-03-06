import contextlib
import struct
import time
import traceback
from datetime import datetime
from typing import List

import paho.mqtt.client as mqtt
import yaml
from bluepy.btle import UUID, BTLEDisconnectError, BTLEException, Peripheral


class Sensor:
    name: str
    uuid: str
    format_type: str
    scale: float

    def __init__(self, name, uuid, format_type, scale):
        self.name = name
        self.uuid = uuid
        self.format_type = format_type
        self.scale = scale

    def read(self, p: Peripheral):
        ch = p.getCharacteristics(uuid=self.uuid)[0]
        if not ch.supportsRead():
            return None

        val = struct.unpack(self.format_type, ch.read())
        return str(val[0] * self.scale)


SENSORS_V1 = [
    Sensor("temperature", UUID(0x2A6E), "h", 1.0/100.0),
    Sensor("humidity", UUID(0x2A6F), "H", 1.0/100.0),
    Sensor("radon_short", "b42e01aa-ade7-11e4-89d3-123b93f75cba", "H", 1.0),
    Sensor("radon_long", "b42e0a4c-ade7-11e4-89d3-123b93f75cba", "H", 1.0)
]


def peripheral_with_retries(addr: str, retries: int) -> Peripheral:
    exc = None

    for _ in range(retries):
        try:
            return Peripheral(addr)
        except BTLEException as e:
            if exc is not None:
                e.__cause__ = exc

            exc = e

        print(f"retrying connection to {addr}")
        time.sleep(1)

    raise exc


class Wave:
    name: str
    addr: str

    def __init__(self, name, addr) -> None:
        self.name = name
        self.addr = addr

    def __str__(self) -> str:
        return f"{self.name} [{self.addr}]"

    @contextlib.contextmanager
    def with_peripheral(self):
        p = peripheral_with_retries(self.addr, 3)
        try:
            yield p
        finally:
            p.disconnect()

    def get_readings(self):
        readings = {}
        with self.with_peripheral() as p:
            for sensor in SENSORS_V1:
                readings[sensor.name] = sensor.read(p)

        return readings


class WavePlus(Wave):
    def get_readings(self):
        with self.with_peripheral() as p:
            val = p.readCharacteristic(0x000d)

        humidity, light, sh_rad, lo_rad, temp, pressure, co2, voc = struct.unpack(
            "<xbxbHHHHHHxxxx", val)
        return {
            "humidity": humidity / 2.0,
            "light": light * 1.0,
            "radon_short": sh_rad,
            "radon_long": lo_rad,
            "temperature": temp / 100.,
            "pressure": pressure / 50.,
            "co2": co2 * 1.,
            "voc": voc * 1.,
        }


class AirthingsWave_mqtt:
    waves: List[Wave]

    def __init__(self, config_file):
        with open(config_file, "r") as f:
            self.config = yaml.safe_load(f)

        self.__parse_config(self.config)
        self.mqtt_client = self.mqtt_connect(self.config)

    def __parse_config(self, conf) -> bool:
        self.waves = []
        for wconf in conf["waves"]:
            name, addr = wconf["name"], wconf["addr"]

            if str(wconf.get("version", "1")) == "2":
                wave = WavePlus(name, addr)
            else:
                wave = Wave(name, addr)

            self.waves.append(wave)

    def mqtt_connect(self, conf):
        conf = conf["mqtt"]

        client = mqtt.Client(client_id=conf.get("client_id"))
        if "username" in conf:
            client.username_pw_set(
                conf["username"],
                conf["password"]
            )
        client.connect(
            conf["broker"],
            int(conf.get("port", "1883"))
        )

        print("mqtt connected")

        client.loop_start()

        return client

    def mqtt_disconnect(self):
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        print("mqtt disconnected")

    def _publish_event(self, topic: str, payload) -> None:
        self.mqtt_client.publish(topic, payload, retain=True, qos=1) \
            .wait_for_publish()

    def _publish_readings(self, wave: Wave):
        print(f"\n{wave.name}:")
        readings = wave.get_readings()
        for key, value in readings.items():
            print("  {0} : {1}".format(key, value))
            self._publish_event(f"{wave.name}/{key}", value)

    def publish_readings(self):
        for wave in self.waves:
            success = False

            try:
                self._publish_readings(wave)
            except BTLEDisconnectError:
                print(f"Couldn't connect to {wave}")
            except Exception:
                print(f"Failed to publish {wave}")
                traceback.print_exc()
            else:
                success = True

            payload = b"ON" if success else b"OFF"
            self._publish_event(f"{wave.name}/online", payload)
