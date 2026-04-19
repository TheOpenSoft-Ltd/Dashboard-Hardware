# AWSIOT Library
import datetime

# import other Library
import json
import os
import time

from awscrt import mqtt5
from awsiot import mqtt5_client_builder
from dotenv import load_dotenv
from pymodbus.client import ModbusTcpClient
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder

# Load the stored environment variable
load_dotenv()

# MQTT Setting
CLIENT_ID = os.getenv("CLIENTID")
ENDPOINT = os.getenv("ENDPOINT")
TOPIC = "{}/value".format(CLIENT_ID)
CERTIFICATE = "../cert/certificate.pem.crt"
PRIVATE_KEY = "../cert/private.pem.key"
AMAZON_ROOT_CA_1 = "../cert/RootCA1.pem"

# Modbus RTU setting
PORT = os.getenv("USBPORT")
HOST = os.getenv("HOST")
SLAVEID = 1

TIME = int(os.getenv("TIME"))
errorcounter = 0

# Setup MQTT
mqtt = mqtt5_client_builder.mtls_from_path(
    endpoint=ENDPOINT,
    port=8883,
    cert_filepath=CERTIFICATE,
    pri_key_filepath=PRIVATE_KEY,
    ca_filepath=AMAZON_ROOT_CA_1,
    http_proxy_options=None,
    client_id=CLIENT_ID,
)

print("MQTT Client Created")
mqtt.start()

# Setup Modbus RTU
client = ModbusTcpClient(HOST, port=502)

while True:
    try:
        response_flowrate = client.read_holding_registers(31001, 2, SLAVEID)
        response_level = client.read_holding_registers(31005, 2, SLAVEID)
        response_totalize = client.read_holding_registers(31009, 2, SLAVEID)
        level = BinaryPayloadDecoder.fromRegisters(
            response_level.registers, byteorder=Endian.BIG, wordorder=Endian.LITTLE
        ).decode_32bit_float()
        distance = 0
        instan_flow = BinaryPayloadDecoder.fromRegisters(
            response_flowrate.registers, byteorder=Endian.BIG, wordorder=Endian.LITTLE
        ).decode_32bit_float()
        total_flow = BinaryPayloadDecoder.fromRegisters(
            response_totalize.registers, byteorder=Endian.BIG, wordorder=Endian.LITTLE
        ).decode_32bit_float()
        current = 0
        dateTime = str(datetime.datetime.now())
        data = {
            "mqtt_name": CLIENT_ID,
            "level": float("{0:.3f}".format(level)),
            "distance": float("{0:.3f}".format(distance)),
            "instanceFlowrate": float("{0:.3f}".format(instan_flow)),
            "totalFlow": float("{0:.3f}".format(total_flow)),
            "current": float("{0:.3f}".format(current)),
            "dateTime": dateTime,
        }
        mqtt.publish(
            mqtt5.PublishPacket(
                topic=TOPIC, payload=json.dumps(data), qos=mqtt5.QoS.AT_LEAST_ONCE
            )
        )
        print("level : {} m".format(level))
        print("distance : {} m".format(distance))
        print("instance flowrate : {} m3/h".format(instan_flow))
        print("total flowrate : {} m3".format(total_flow))
        print("current {} mA".format(current))
        print("dateTime {}".format(dateTime))
        print("error count: ", errorcounter)
        print()
        time.sleep(TIME)
    except Exception as error:
        print("[!] Exception occured: ", error)
        errorcounter = errorcounter + 1
    time.sleep(TIME)
