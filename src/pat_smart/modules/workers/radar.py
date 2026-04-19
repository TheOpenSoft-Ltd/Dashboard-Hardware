# AWSIOT Library
import datetime

# import other Library
import json
import os
import time

from awscrt import mqtt5
from awsiot import mqtt5_client_builder
from dotenv import load_dotenv
from pymodbus.client import ModbusSerialClient
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder

# Load environment variables
load_dotenv()

# MQTT Setting
CLIENT_ID = os.getenv("CLIENTID")
ENDPOINT = os.getenv("ENDPOINT")
TOPIC = f"{CLIENT_ID}/dropler"
CERTIFICATE = "../cert/certificate.pem.crt"
PRIVATE_KEY = "../cert/private.pem.key"
AMAZON_ROOT_CA_1 = "../cert/RootCA1.pem"

# Modbus TCP
HOST = os.getenv("HOST")
SLAVEID = 1
PORT = os.getenv("USBPORT")

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

# Setup Modbus RS485
client = ModbusSerialClient(
    port=PORT,
    method="rtu",
    baudrate=9600,
    stopbits=1,
    bytesize=8,
    parity="N",
    timeout=1,
)

connection = client.connect()
if connection:
    print("Connected.")
else:
    print("Connection failed.")
    exit()

print("Modbus Client Ready")


def read_float(address):
    """Read 32-bit IEEE754 float from two registers"""
    resp = client.read_holding_registers(address, 2, SLAVEID)
    decoder = BinaryPayloadDecoder.fromRegisters(
        resp.registers,
        byteorder=Endian.BIG,
        wordorder=Endian.BIG,  # Important for your sensor !!
    )
    return decoder.decode_32bit_float()


while True:
    try:
        # ===== Read sensor values =====
        velocity = read_float(2)  # velocity (m/s)
        flowrate = read_float(3)  # flowrate (m3/h)
        temp = read_float(4)  # flowrate (m3/h)
        cumulative_flow = read_float(8)  # flowrate (m3/h)

        dateTime = str(datetime.datetime.now())

        # Prepare JSON payload
        data = {
            "mqtt_name": CLIENT_ID,
            "velocity": float(f"{velocity:.6f}"),
            "flowrate": float(f"{flowrate:.6f}"),
            "temperature": float(f"{temp:.3f}"),
            "cumulative_flow": float(f"{cumulative_flow:.2f}"),
            "dateTime": dateTime,
        }

        # Publish to AWS IoT
        mqtt.publish(
            mqtt5.PublishPacket(
                topic=TOPIC, payload=json.dumps(data), qos=mqtt5.QoS.AT_LEAST_ONCE
            )
        )

        # Console Debug
        print(f"Velocity: {velocity:.6f} m/s")
        print(f"Flowrate: {flowrate:.6f} m3/h")
        print(f"Temperature: {temp:.3f} degree")
        print(f"Cumulative Flow: {cumulative_flow:.2f} m3")
        print(f"error count: {errorcounter}")
        print(f"dateTime: {dateTime}")
        print()

        time.sleep(TIME)

    except Exception as error:
        print("[!] Exception occurred:", error)
        errorcounter += 1
        time.sleep(TIME)
