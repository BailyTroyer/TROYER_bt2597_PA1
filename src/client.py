import re
import time
import socket
import json
from threading import Thread, Event
import signal
import select

from log import logger


class ClientError(Exception):
    """Thrown when Client errors during regular operation."""

    pass


class Client:
    def __init__(self, opts):
        """{name,server_ip,server_port,client_port}"""
        self.opts = opts
        self.connections = {}
        self.is_registered = False
        self.delay = 500 / 1000  # 500ms (500ms/1000ms = 0.5s)
        self.is_in_groupchat = False

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

    def handle_request(self, sock, sender_ip, payload):
        """Handle different request types (e.g. registration_confirmation)."""
        if payload.get("type", "") == "registration_confirmation":
            logger.info(f"Welcome, You are registered.")
            self.is_registered = True
        elif payload.get("type", "") == "registration_error":
            logger.info(payload.get("payload", {}).get("message", ""))
            self.stop_event.set()
        elif payload.get("type", "") == "state_change":
            self.connections = payload.get("payload")
            logger.info(f"Client table updated.")
        elif payload.get("type", "") == "deregistration_confirmation":
            self.is_registered = False
            logger.info("You are Offline. Bye.")
            self.stop_event.set()
        elif payload.get("type", "") == "message":
            sender_name = payload.get("metadata", {}).get("name")
            message = payload.get("payload", "")
            if not self.is_in_groupchat:
                print(f"{sender_name}: {message}")
                # send ack back to user
                self.send_dm_ack(sock, sender_name)
            else:
                ## @todo enqueue and then send once out of groupchat
                print("@todo")
        else:
            print(f"got unknown message: {payload}")

    def send_dm(self, sock, recipient_name, user_input):
        """Sends a private DM to another client."""
        message = self.encode_message("message", user_input)
        # @todo what happens if they DON'T EXIST IN TABLE
        if recipient_name not in self.connections:
            logger.info(f"Unable to send to non-existent {recipient_name}.")
            return

        recipient_metadata = self.connections.get(recipient_name, {})
        client_port = recipient_metadata.get("client_port")
        client_ip = "0.0.0.0"  # @todo we need to track this on client INIT
        client_destination = (client_ip, client_port)

        try:
            sock.sendto(message, client_destination)
        except socket.error as e:
            raise ClientError(f"UDP socket error: {e}")

    def send_dm_ack(self, sock, recipient_name):
        """Sends an ACK to the sender of an incoming DM."""
        recipient_metadata = self.connections.get(recipient_name, {})
        client_port = recipient_metadata.get("client_port")
        client_ip = "0.0.0.0"  # @todo we need to track this on client INIT
        client_destination = (client_ip, client_port)
        message = self.encode_message("message_ack")
        try:
            sock.sendto(message, client_destination)
        except socket.error as e:
            raise ClientError(f"UDP socket error: {e}")

    def send_message(self, sock, user_input):
        """Parses user plaintext and sends to proper destination."""
        ### CHECK IF IN GROUP CHAT, NO DM IF IN GROUPCHAT
        if re.match("dereg (.*)", user_input):
            # we don't need this since its already known at startup
            name = user_input.split(" ")[1]
            self.deregister(sock)
        elif re.match("send (.*) (.*)", user_input):
            print("USER INPUT: ", user_input)
            name = user_input.split(" ")[1]
            message = " ".join(user_input.split(" ")[2:])
            print("sending this: ", message)
            self.send_dm(sock, name, message)

            # Determine if private message (regex match `send <name> <message>`)
            # Lookup IP,port for recipient `<name>`
            # Send to recipient, wait for ack, send ack back
            # if timeout 500ms -> notify server to upate table
        else:
            logger.info(f"Unknown command `{user_input}`.")

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

            while server_thread.is_alive() and not self.stop_event.is_set():
                server_thread.join(1)
                ## Only handle input once registered
                if self.is_registered:
                    user_input = input(">>> ")
                    self.send_message(sock, user_input)

        except ClientError:
            # Prevent exceptions when quickly spamming `^C`
            signal.signal(signal.SIGINT, lambda s, f: None)

    def deregister(self, sock):
        """Sends deregistration request to server."""
        retries = 0
        while self.is_registered and retries <= 5:  ## Wait for ack 5x 500ms each
            server_destination = (self.opts["server_ip"], self.opts["server_port"])
            deregistration_message = self.encode_message("deregistration")
            sock.sendto(deregistration_message, server_destination)
            # We don't want to sleep on the 5th time we just exit
            if retries <= 4:
                time.sleep(self.delay)
            retries += 1
        if self.is_registered:
            logger.info("Server not responding")
            logger.info("Exiting")
            self.stop_event.set()

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
                logger.info(f"stopping client-server listener")
                break

            readables, writables, errors = select.select([sock], [], [], 1)
            for read_socket in readables:
                # print("listening")
                data, (sender_ip, sender_port) = read_socket.recvfrom(4096)
                message = self.decode_message(data)
                self.handle_request(read_socket, sender_ip, message)
