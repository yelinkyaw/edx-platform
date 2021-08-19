from confluent_kafka import Producer
import socket
import time

conf = {'bootstrap.servers': "broker:9092,",
                'client.id': socket.gethostname()}

producer = Producer(conf)

while True:
    producer.produce("test_topic", key="key", value=f"value{time.time()}")
    print("Sent a message!")
    time.sleep(5)
