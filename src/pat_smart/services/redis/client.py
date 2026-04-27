import json
import threading
from typing import Callable

import redis

from pat_smart.config import Settings

settings = Settings()  # type: ignore


class RedisClient:
    def __init__(self):
        self.host = settings.REDIS_HOST
        self.port = settings.REDIS_PORT
        self._client = redis.Redis(
            host=self.host,
            port=self.port,
            decode_responses=True,
        )
        self._pubsub = self._client.pubsub()
        self._handlers: dict[str, Callable] = {}
        self._stop_event = threading.Event()
        self._listen_thread: threading.Thread | None = None

    def connect(self) -> bool:
        try:
            self._client.ping()
            print(f"[Redis] connected to {self.host}:{self.port}")
            return True
        except redis.ConnectionError as e:
            print(f"[Redis] connection failed: {e}")
            return False

    def disconnect(self):
        self._stop_event.set()
        if self._listen_thread:
            self._listen_thread.join()
        self._pubsub.close()
        self._client.close()

    def subscribe(self, channel: str, handler: Callable):
        self._handlers[channel] = handler
        self._pubsub.subscribe(channel)

    def start_listening(self):
        if self._listen_thread and self._listen_thread.is_alive():
            return

        self._stop_event.clear()
        self._listen_thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
        )
        self._listen_thread.start()

    def _listen_loop(self):
        for message in self._pubsub.listen():
            if self._stop_event.is_set():
                break

            if message["type"] == "message":
                channel = message["channel"]
                data = message["data"]
                handler = self._handlers.get(channel)
                if handler:
                    try:
                        parsed = json.loads(data)
                        handler(parsed)
                    except json.JSONDecodeError:
                        handler(data)
