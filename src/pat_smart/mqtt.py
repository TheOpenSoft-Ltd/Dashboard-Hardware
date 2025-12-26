import paho.mqtt.client as mqtt
import json
import time
from datetime import datetime

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"Connected with result code {reason_code}")
    client.subscribe("$SYS/#")

def on_message(client, userdata, msg):
    # print(msg.topic+" "+str(msg.payload))
    pass

def publish_data(mqttc:mqtt.Client):
    mqttc.loop_start()
    while True:
        try:
            data = {
            "mqtt_name": "ST1",
            "level": 1,
            "distance": 2.33,
            "instanceFlowrate": 2.44,
            "totalFlow": 100,
            "current": 22,
            "dateTime": str(datetime.now()) 
            }
            mqttc.publish('ST2/value', json.dumps(data), qos=1)
            print(data)
            time.sleep(2)
        except Exception as e:
            raise e


def run():
    mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqttc.on_connect = on_connect
    mqttc.on_message = on_message
    
    mqttc.connect("localhost", 1883, 60)
    publish_data(mqttc)
