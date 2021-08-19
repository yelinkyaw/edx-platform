from confluent_kafka import Consumer
import sys
import time

conf = {'bootstrap.servers': "broker:9092",
        'group.id': "test_consumer",
        'auto.offset.reset': 'smallest'}

consumer = Consumer(conf)
consumer.subscribe(["test_topic"])

while True:
    time.sleep(1)
    msg = consumer.poll(timeout=1.0)
    if msg is None:
        print("No message yet.")
        continue

    if msg.error():
        if msg.error().code() == KafkaError._PARTITION_EOF:
            # End of partition event
            sys.stderr.write('%% %s [%d] reached end at offset %d\n' %
                             (msg.topic(), msg.partition(), msg.offset()))
        elif msg.error():
            raise Exception(msg.error())
    else:
        print(f"{msg.key()}: {msg.value()}")


