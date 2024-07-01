import asyncio
import logging
import os
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Union
from polygon import RESTClient, WebSocketClient
from polygon.websocket.models import Market, Feed
import influxdb_client
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
current_datetime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
static_time = datetime(1970, 1, 1, 0, 0).isoformat() + 'Z'

# InfluxDB setup
org = os.getenv("INFLUXDB_ORG")
url = os.getenv("INFLUXDB_URL")
token = os.getenv("INFLUXDB_TOKEN")
bucket = os.getenv("INFLUXDB_BUCKET")

client = influxdb_client.InfluxDBClient(url=url, token=token, org=org)
write_api = client.write_api(write_options=SYNCHRONOUS)


def extract_symbol(self, input_string):
    match = re.search(r"([A-Z]+)", input_string)
    if match:
        return match.group(1)
    else:
        return None

class ApiCallHandler:
    def __init__(self):
        self.api_call_queue = asyncio.Queue()
        self.executor = ThreadPoolExecutor() 
        self.client = RESTClient()

    async def enqueue_api_call(self, stock_ticker):
        await self.api_call_queue.put(stock_ticker)

    async def start_processing_api_calls(self):
        while True:
            stock_ticker = await self.api_call_queue.get()
            try:
                contract = await asyncio.get_running_loop().run_in_executor(
                    self.executor, self.process_aggregate, stock_ticker
                )
                print(contract)  # Or process the contract data as needed
            except Exception as e:
                logging.error(f"Error processing API call for {stock_ticker.sym}: {e}")
            finally:
                self.api_call_queue.task_done()

    def process_aggregate(self, stock_ticker):
        point = Point("current_price") \
                    .tag("ticker", extract_symbol(stock_ticker)) \
                    .field("price", stock_ticker.c) \
                    .field("updated", current_datetime) \
                    .time(static_time)
        write_api.write(bucket=bucket, org=org, record=point)

        point = Point("current_volume") \
                    .tag("ticker", extract_symbol(stock_ticker)) \
                    .field("price", stock_ticker.c) \
                    .field("updated", current_datetime) \
                    .time(static_time)
        write_api.write(bucket=bucket, org=org, record=point)
        logging.info(f"Live Price Updated {stock_ticker.sym}")
       


class MessageHandler:
    def __init__(self, api_call_handler):
        self.handler_queue = asyncio.Queue()
        self.api_call_handler = api_call_handler

    async def add(self, message_response: Optional[Union[str, bytes]]) -> None:
        await self.handler_queue.put(message_response)

    async def start_handling(self) -> None:
        while True:
            message_response = await self.handler_queue.get()
            logging.info(f"Received message: {message_response}")
            try:

                for trade in message_response:
                    ticker = extract_symbol(trade.sym)
                    if ticker.c > 1 and ticker.c < 23:
                        asyncio.create_task(
                            self.api_call_handler.enqueue_api_call(trade)
                        )
            except Exception as e:
                logging.error(f"Error handling message: {e}")
            finally:
                self.handler_queue.task_done()


class MyClient:
    def __init__(self, feed, market, subscriptions):
        api_key = os.getenv("POLYGON_API_KEY")
        self.polygon_websocket_client = WebSocketClient(
            api_key=api_key,
            feed=feed,
            market=market,
            verbose=True,
            subscriptions=subscriptions,
        )
        self.api_call_handler = ApiCallHandler()
        self.message_handler = MessageHandler(self.api_call_handler)

    async def start_event_stream(self):
        try:
            await asyncio.gather(
                self.polygon_websocket_client.connect(self.message_handler.add),
                self.message_handler.start_handling(),
                self.api_call_handler.start_processing_api_calls(),
            )
        except Exception as e:
            logging.error(f"Error in event stream: {e}")


async def main():
    logging.basicConfig(level=logging.INFO)
    my_client = MyClient(
        feed=Feed.Delayed, market=Market.Stocks, subscriptions=["AM.*"]
    )
    await my_client.start_event_stream()


asyncio.run(main())
