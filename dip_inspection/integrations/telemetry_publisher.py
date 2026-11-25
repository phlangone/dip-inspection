"""Optional asynchronous MQTT telemetry; broker availability does not gate inspection."""
import json
import logging
import time
import paho.mqtt.client as mqtt


class TelemetryPublisher:
    """Publish robot joint positions, TCP pose, and command status."""
    TOPIC_JOINTS = "futurecom/ur/joints"
    TOPIC_TCP = "futurecom/ur/tcp"
    TOPIC_STATUS = "futurecom/ur/status"

    def __init__(self, host="localhost", port=1883, username="", password="", topic_prefix="futurecom/ur"):
        self.host, self.port = host, port
        self.prefix = topic_prefix.rstrip("/")
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        if username:
            self.client.username_pw_set(username, password)
        self.connected = False
        self.started = False
        self.last_publish = None

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        self.connected = reason_code == 0
        logging.info("MQTT: %s", "conectado" if self.connected else "conexão recusada")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected = False
        logging.warning("MQTT desconectado; reconexão com backoff.")

    def _on_publish(self, client, userdata, mid, reason_code, properties):
        if not reason_code.is_failure:
            self.last_publish = time.time()

    def connect(self):
        self.client.connect_async(self.host, self.port, 60)
        self.client.loop_start()
        self.started = True

    def publish(self, ur):
        if not self.connected:
            return
        joints, tcp = ur.get_joints(), ur.get_tcp_pose()
        if joints is None or tcp is None:
            return
        timestamp = time.time()
        for topic, payload in (("joints", {"timestamp": timestamp, "q": joints}),
                               ("tcp", {"timestamp": timestamp, "pose": tcp}),
                               ("status", {"timestamp": timestamp, "command": ur.get_command()})):
            info = self.client.publish(self.prefix + "/" + topic, json.dumps(payload))
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                logging.warning("MQTT: publicação não aceita.")

    def close(self):
        if self.started:
            self.client.disconnect()
            self.client.loop_stop()
        self.connected = self.started = False
