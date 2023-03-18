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
        self.connections = {}
        self.is_registered = False

    def encode_message(self, type, payload=None):
        """Convert plaintext user input to serialized message 'packet'."""
        metadata = {**self.opts}
        message = {"type": type, "payload": payload, "metadata": metadata}
        return json.dumps(message).encode("utf-8")

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

    def handle_request(self, sender_ip, payload):
        """Handle different request types (e.g. registration_confirmation)."""
        if payload.get("type", "") == "registration_confirmation":
            logging.info(f"Welcome, You are registered.")
            self.is_registered = True
        elif payload.get("type", "") == "state_change":
            self.connections = payload.get("payload")
            logging.info(f"Client table updated.")

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
                ## Only handle input once registered
                if self.is_registered:
                    user_input = input(">>> ")
                    message = self.encode_message("message", user_input)
                    try:
                        sock.sendto(message, server_destination)
                    except socket.error as e:
                        raise ClientError(f"UDP socket error: {e}")

        except ClientError:
            # Prevent exceptions when quickly spamming `^C`
            signal.signal(signal.SIGINT, lambda s, f: None)

    def register(self, sock):
        """Send initial registration message to server. If ack'ed log and continue."""
        server_destination = (self.opts["server_ip"], self.opts["server_port"])
        registration_message = self.encode_message("registration")
        sock.sendto(registration_message, server_destination)

    def server_listen(self, stop_event):
        """Listens on specified `client_port` for messages from server."""
        sock = self.create_sock()
        sock.bind(("", self.opts["client_port"]))

        # register after we start listening on port
        self.register(sock)

        while True:
            # Listen for kill events
            if stop_event.is_set():
                print()  # this adds a nice newline when `^C` is entered
                logging.info(f"stopping client-server listener")
                break

            readables, writables, errors = select.select([sock], [], [], 1)
            for read_socket in readables:
                # print("listening")
                data, (sender_ip, sender_port) = read_socket.recvfrom(4096)
                message = self.decode_message(data)
                self.handle_request(sender_ip, message)
