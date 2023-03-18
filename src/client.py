import logging
import sys
import socket
import json
from threading import Thread, Event
import signal
import select

logging.basicConfig(level=logging.DEBUG, format=">>> [%(message)s]")


class ClientError(Exception):
    """Thrown when Client errors during regular operation."""

    pass


class Client:
    def __init__(self, opts):
        """{name,server_ip,server_port,client_port}"""
        self.opts = opts

    def encode_message(self, message):
        """Convert plaintext user input to serialized message 'packet'."""
        return json.dumps({"message": message}).encode("utf-8")

    def decode_message(self, message):
        """Convert bytes to deserialized JSON."""
        return json.loads(message.decode("utf-8"))

    def create_sock(self):
        """Create a socket."""
        try:
            return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except socket.error as e:
            raise ClientError(f"UDP client error when creating socket: {e}")

    def signal_handler(self, signum, frame):
        # `kill -l` to list all signal kill codes
        print()  # this adds a nice newline when `^C` is entered
        self.stop_event.set()
        raise ClientError(f"Client aborted... {signum}")

    def start(self):
        """Start both the user input listener and server event listener."""
        try:
            # Handle signal events (e.g. `^C`)
            self.stop_event = Event()
            signal.signal(signal.SIGINT, self.signal_handler)
            # start server listener
            server_thread = Thread(target=self.server_listen, args=(self.stop_event,))
            server_thread.start()

            sock = self.create_sock()
            server_destination = (self.opts["server_ip"], self.opts["server_port"])

            while server_thread.is_alive() and not self.stop_event.is_set():
                user_input = input(">>> ")
                message = self.encode_message(user_input)
                print("message: ", message)
                try:
                    sock.sendto(message, server_destination)
                except socket.error as e:
                    raise ClientError(f"UDP socket error: {e}")

        except ClientError:
            # Prevent exceptions when quickly spamming `^C`
            signal.signal(signal.SIGINT, lambda s, f: None)

    def server_listen(self, stop_event):
        """Listens on specified `client_port` for messages from server."""
        sock = self.create_sock()
        sock.bind(("", self.opts["client_port"]))

        while True:
            # Listen for kill events
            if stop_event.is_set():
                print()  # this adds a nice newline when `^C` is entered
                logging.info(f"stopping client-server listener")
                break

            readables, writables, errors = select.select([sock], [], [], 1)
            for read_socket in readables:
                print("listening")
                data, addr = read_socket.recvfrom(4096)
                print(addr, self.decode_message(data))
