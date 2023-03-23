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
        self.active_group = None
        self.waiting_for_ack = False
        self.waiting_for_input = False
        self.inbox = []

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

    def print_inbox(self):
        """Prints queued direct messages while inside group."""
        for inbox_message in self.inbox:
            message = inbox_message.get("message", "")
            sender = inbox_message.get("sender", "")
            print(f"{sender}: {message}")
        self.inbox = []

    def handle_request(self, sock, sender_ip, payload):
        """Handle different request types (e.g. registration_confirmation)."""
        request_type = payload.get("type", "")

        if request_type == "registration_confirmation":
            logger.info(f"Welcome, You are registered.")
            self.is_registered = True
        elif request_type == "registration_error":
            # @todo clean this up, maybe payload contains error message
            logger.info(payload.get("payload", {}).get("message", ""))
            self.stop_event.set()
        elif request_type == "state_change":
            self.connections = payload.get("payload")
            show_newline = "\n" if self.waiting_for_input else ""
            # if show_newline:
            #     print("")
            print(f"Client table updated.", flush=True)
        elif request_type == "deregistration_confirmation":
            self.is_registered = False
            logger.info("You are Offline. Bye.")
            self.stop_event.set()
        elif request_type == "create_group_ack":
            group_name = payload.get("payload")
            self.waiting_for_ack = False
            logger.info(f"Group {group_name} created by Server.")
        elif request_type == "create_group_error":
            group_name = payload.get("payload")
            self.waiting_for_ack = False
            logger.info(payload.get("payload", {}).get("message", ""))
        elif request_type == "join_group_ack":
            group_name = payload.get("payload")
            self.waiting_for_ack = False
            self.active_group = group_name
            logger.info(f"Entered group {group_name} successfully!")
        elif request_type == "join_group_error":
            group_name = payload.get("payload")
            self.waiting_for_ack = False
            logger.info(payload.get("payload", {}).get("message", ""))
        elif request_type == "list_groups_ack":
            groups = payload.get("payload", {}).get("groups", [])
            self.waiting_for_ack = False
            logger.info("Available group chats:")
            for group in groups:
                # @todo not all messages should be wrapped in []
                logger.info(group)
        elif request_type == "message":
            sender_name = payload.get("metadata", {}).get("name")
            message = payload.get("payload", "")
            if not self.active_group:
                print(f"{sender_name}: {message}")
                # send ack back to user
                self.send_dm_ack(sock, sender_name)
            else:
                self.send_dm_ack(sock, sender_name)
                self.inbox.append({"sender": sender_name, "message": message})
        elif request_type == "message_ack":
            self.waiting_for_ack = False
            recipient_name = payload.get("payload", "")
            logger.info(f"Message received by {recipient_name}")
        elif request_type == "client_offline_ack":
            self.waiting_for_ack = False
            offline_client_name = payload.get("payload", "")
            logger.info(
                f"Auto-deregistered {offline_client_name} since they were offline."
            )
        elif request_type == "group_message":
            message = payload.get("payload", {}).get("message")
            sender = payload.get("payload", {}).get("sender")
            print(f">>> ({self.active_group}) Group_Message <{sender}> {message}")

            ## send ack back to server of recieved group_message
        elif request_type == "members_list":
            self.waiting_for_ack = False
            members = payload.get("payload", {}).get("members")
            print(
                f">>> ({self.active_group}) Members in the group {self.active_group}:"
            )
            for member in members:
                print(f">>> ({self.active_group}) {member}")
        elif request_type == "leave_group_ack":
            self.waiting_for_ack = False
            logger.info(f"Leave group chat {self.active_group}")
            self.active_group = None
            self.print_inbox()
        elif request_type == "group_message_ack":
            self.waiting_for_ack = False
            print(f">>> ({self.active_group}) [Message received by Server.]")
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

        retries = 0
        self.waiting_for_ack = True
        while self.waiting_for_ack and retries <= 5:  ## Wait for ack 5x 500ms each
            sock.sendto(message, client_destination)
            # We don't want to sleep on the 5th time we just exit
            if retries <= 4:
                time.sleep(self.delay)
            retries += 1
        if self.waiting_for_ack:
            logger.info(f"No ACK from {recipient_name}, message not delivered")
            # We still need to see if server is online, otherwise that means OUR client is offline
            self.notify_server_client_offline(sock, recipient_name)

    def list_groups(self, sock):
        """Sends list_group command to server."""
        retries = 0
        self.waiting_for_ack = True
        while self.waiting_for_ack and retries <= 5:  ## Wait for ack 5x 500ms each
            server_destination = (self.opts["server_ip"], self.opts["server_port"])
            registration_message = self.encode_message("list_groups")
            sock.sendto(registration_message, server_destination)
            # We don't want to sleep on the 5th time we just exit
            if retries <= 4:
                time.sleep(self.delay)
            retries += 1
        if self.waiting_for_ack:
            logger.info("Server not responding")
            logger.info("Exiting")
            self.stop_event.set()

    def create_group(self, sock, group_name):
        """Sends create_group command to server."""
        retries = 0
        self.waiting_for_ack = True
        while self.waiting_for_ack and retries <= 5:  ## Wait for ack 5x 500ms each
            server_destination = (self.opts["server_ip"], self.opts["server_port"])
            create_group_message = self.encode_message("create_group", group_name)
            sock.sendto(create_group_message, server_destination)
            # We don't want to sleep on the 5th time we just exit
            if retries <= 4:
                time.sleep(self.delay)
            retries += 1
        if self.waiting_for_ack:
            logger.info("Server not responding")
            logger.info("Exiting")
            self.stop_event.set()

    def notify_server_client_offline(self, sock, client_name):
        """Notifies server a client didn't respond."""
        retries = 0
        self.waiting_for_ack = True
        while self.waiting_for_ack and retries <= 5:  ## Wait for ack 5x 500ms each
            server_destination = (self.opts["server_ip"], self.opts["server_port"])
            offline_message = self.encode_message("client_offline", client_name)
            sock.sendto(offline_message, server_destination)
            # We don't want to sleep on the 5th time we just exit
            if retries <= 4:
                time.sleep(self.delay)
            retries += 1
        if self.waiting_for_ack:
            logger.info("Server not responding")
            logger.info("Exiting")
            self.stop_event.set()

    # @todo is this OK that we don't retry? I assume so
    def send_group_message_ack(self, sock):
        """Sends an ack back to server of recieved group message."""
        server_destination = (self.opts["server_ip"], self.opts["server_port"])
        group_message = self.encode_message(
            "group_message_ack", {"group": self.active_group}
        )
        sock.sendto(group_message, server_destination)

    def send_group_message(self, sock, message):
        """Sends group chat message to server."""
        retries = 0
        self.waiting_for_ack = True
        while self.waiting_for_ack and retries <= 5:  ## Wait for ack 5x 500ms each
            server_destination = (self.opts["server_ip"], self.opts["server_port"])
            group_message = self.encode_message(
                "group_message", {"message": message, "group": self.active_group}
            )
            sock.sendto(group_message, server_destination)
            # We don't want to sleep on the 5th time we just exit
            if retries <= 4:
                time.sleep(self.delay)
            retries += 1
        if self.waiting_for_ack:
            logger.info("Server not responding")
            logger.info("Exiting")
            self.stop_event.set()

    def join_group(self, sock, group_name):
        """Sends join_group command to server."""
        retries = 0
        self.waiting_for_ack = True
        while self.waiting_for_ack and retries <= 5:  ## Wait for ack 5x 500ms each
            server_destination = (self.opts["server_ip"], self.opts["server_port"])
            registration_message = self.encode_message("join_group", group_name)
            sock.sendto(registration_message, server_destination)
            # We don't want to sleep on the 5th time we just exit
            if retries <= 4:
                time.sleep(self.delay)
            retries += 1
        if self.waiting_for_ack:
            logger.info("Server not responding")
            logger.info("Exiting")
            self.stop_event.set()

    def send_dm_ack(self, sock, recipient_name):
        """Sends an ACK to the sender of an incoming DM."""
        recipient_metadata = self.connections.get(recipient_name, {})
        client_port = recipient_metadata.get("client_port")
        client_ip = "0.0.0.0"  # @todo we need to track this on client INIT
        client_destination = (client_ip, client_port)
        message = self.encode_message("message_ack", self.opts["name"])
        try:
            sock.sendto(message, client_destination)
        except socket.error as e:
            raise ClientError(f"UDP socket error: {e}")

    def send_list_group_members(self, sock):
        """Sends list_members command to server."""
        retries = 0
        self.waiting_for_ack = True
        while self.waiting_for_ack and retries <= 5:  ## Wait for ack 5x 500ms each
            server_destination = (self.opts["server_ip"], self.opts["server_port"])
            group_message = self.encode_message(
                "list_members", {"group": self.active_group}
            )
            sock.sendto(group_message, server_destination)
            # We don't want to sleep on the 5th time we just exit
            if retries <= 4:
                time.sleep(self.delay)
            retries += 1
        if self.waiting_for_ack:
            logger.info("Server not responding")
            logger.info("Exiting")
            self.stop_event.set()

    def send_leave_group(self, sock):
        """Sends leave_group command to server."""
        retries = 0
        self.waiting_for_ack = True
        while self.waiting_for_ack and retries <= 5:  ## Wait for ack 5x 500ms each
            server_destination = (self.opts["server_ip"], self.opts["server_port"])
            group_message = self.encode_message(
                "leave_group", {"group": self.active_group}
            )
            sock.sendto(group_message, server_destination)
            # We don't want to sleep on the 5th time we just exit
            if retries <= 4:
                time.sleep(self.delay)
            retries += 1
        if self.waiting_for_ack:
            logger.info("Server not responding")
            logger.info("Exiting")
            self.stop_event.set()

    def send_message(self, sock, user_input):
        """Parses user plaintext and sends to proper destination."""
        ### CHECK IF IN GROUP CHAT, NO DM IF IN GROUPCHAT
        if re.match("dereg (.*)", user_input):
            # we don't need this since its already known at startup
            name = user_input.split(" ")[1]
            self.deregister(sock)
        elif re.match("send (.*) (.*)", user_input):
            name = user_input.split(" ")[1]
            message = " ".join(user_input.split(" ")[2:])
            self.send_dm(sock, name, message)
        elif re.match("create_group (.*)", user_input):
            group_name = user_input.split(" ")[1]
            self.create_group(sock, group_name)
        elif re.match("list_groups", user_input):
            self.list_groups(sock)
        elif re.match("list_members", user_input):
            # if not in group show err
            if not self.active_group:
                logger.info("Invalid command. You need to be in a group first!")
            else:
                self.send_list_group_members(sock)
        elif re.match("join_group (.*)", user_input):
            # @todo what if you join a group from another group?
            group_name = user_input.split(" ")[1]
            self.join_group(sock, group_name)
        elif re.match("send_group (.*)", user_input):
            message = " ".join(user_input.split(" ")[1:])
            self.send_group_message(sock, message)
        elif re.match("leave_group", user_input):
            self.send_leave_group(sock)
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
                    in_groupchat_prefix = (
                        f"({self.active_group}) " if self.active_group else ""
                    )
                    self.waiting_for_input = True
                    user_input = input(f">>> {in_groupchat_prefix}")
                    self.waiting_for_input = False
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
