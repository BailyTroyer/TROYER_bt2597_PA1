import re
import time
import socket
import json
from threading import Thread, Event
import signal
import select
from functools import wraps

from log import logger


def handles_retries(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        retries = 0
        self.waiting_for_ack = True
        while self.waiting_for_ack and retries <= 5:
            method(self, *args, **kwargs)
            # We don't want to sleep on the 5th time we just exit
            if retries <= 4:
                time.sleep(self.delay)
            retries += 1

        if self.waiting_for_ack:
            self.exit_server_not_respond()

        return

    return wrapper


class ClientError(Exception):
    """Thrown when Client errors during regular operation."""

    pass


class Client:
    def __init__(self, opts):
        """{name,server_ip,server_port,client_port}"""
        self.opts = opts
        self.connections = {}
        self.is_registered = False
        self.delay = 500 / 1000  # 500ms
        self.active_group = None
        self.waiting_for_ack = False
        self.inbox = []
        self.client_ip = socket.gethostbyname(socket.gethostname())

    def encode_message(self, type, payload=None):
        """Convert plaintext user input to serialized message 'packet'."""
        metadata = {**self.opts, "client_ip": self.client_ip}
        message = {"type": type, "payload": payload, "metadata": metadata}
        return json.dumps(message).encode("utf-8")

    def exit_server_not_respond(self):
        """Prints error message and exits client."""

        logger.info("Server not responding")
        logger.info("Exiting")
        # logger.info("\rServer not responding")
        # logger.info("\rExiting")
        self.stop_event.set()

    def decode_message(self, message):
        """Convert bytes to deserialized JSON."""
        return json.loads(message.decode("utf-8"))

    # def log(self, message, is_end=False):
    #     """Custom logger w/ prefixing based on group chat."""
    #     gc_prefix = f"({self.active_group}) " if self.active_group else ""
    #     message_postfix = "" if is_end else "\n>>> "
    #     print(f"\r>>> {gc_prefix}[{message}]{message_postfix}", end="")

    def create_sock(self):
        """Create a socket."""
        try:
            return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except socket.error as e:
            raise ClientError(f"UDP client error when creating socket: {e}")

    def signal_handler(self, signum, _frame):
        """Custom wrapper that throws error when exit signal received."""
        print()  # this adds a nice newline when `^C` is entered
        self.stop_event.set()
        raise ClientError(f"Client aborted... {signum}")

    def print_inbox(self):
        """Prints queued direct messages while inside group."""
        for inbox_message in self.inbox:
            message = inbox_message.get("message", "")
            sender = inbox_message.get("sender", "")
            logger.info(f">>> {sender}: {message}")
        self.inbox = []

    def handle_request(self, sock, sender_ip, payload):
        """Handle different request types (e.g. registration_confirmation)."""
        request_type = payload.get("type", "")

        if request_type == "registration_confirmation":
            logger.info("Welcome, You are registered.")
            self.is_registered = True
            self.waiting_for_ack = False
        elif request_type == "registration_error":
            logger.info(payload.get("payload", {}).get("message", ""))
            self.stop_event.set()
        elif request_type == "state_change":
            self.connections = payload.get("payload")
            logger.info("Client table updated.")
        elif request_type == "deregistration_confirmation":
            self.waiting_for_ack = False
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
                logger.info(group)
        elif request_type == "message":
            sender_name = payload.get("metadata", {}).get("name")
            message = payload.get("payload", "")
            if not self.active_group:
                logger.info(f"{sender_name}: {message}")
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
            print(f">>> ({self.active_group}) Group_Message {sender}: {message}")
            self.send_group_message_ack(sock)

            ## send ack back to server of recieved group_message
        elif request_type == "members_list":
            self.waiting_for_ack = False
            members = payload.get("payload", {}).get("members")
            print(
                f">>> ({self.active_group}) [Members in the group {self.active_group}:]"
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
            logger.info(f"got unknown message: {payload}")

    def send_dm(self, sock, recipient_name, user_input):
        """Sends a private DM to another client."""
        message = self.encode_message("message", user_input)
        if recipient_name not in self.connections:
            logger.info(f"Unable to send to non-existent {recipient_name}.")
            return

        recipient_metadata = self.connections.get(recipient_name, {})
        client_port = recipient_metadata.get("client_port")
        client_ip = recipient_metadata.get("client_ip")
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

    def server_send(self, sock, message):
        """Sends already encoded message to server."""
        server_destination = (self.opts["server_ip"], self.opts["server_port"])
        sock.sendto(message, server_destination)

    @handles_retries
    def list_groups(self, sock):
        """Sends list_group command to server."""
        registration_message = self.encode_message("list_groups")
        self.server_send(sock, registration_message)

    @handles_retries
    def create_group(self, sock, group_name):
        """Sends create_group command to server."""
        create_group_message = self.encode_message("create_group", group_name)
        self.server_send(sock, create_group_message)

    @handles_retries
    def notify_server_client_offline(self, sock, client_name):
        """Notifies server a client didn't respond."""
        offline_message = self.encode_message("client_offline", client_name)
        self.server_send(sock, offline_message)

    def send_group_message_ack(self, sock):
        """Sends an ack back to server of recieved group message."""
        group_ack_payload = {"group": self.active_group}
        group_ack_message = self.encode_message("group_message_ack", group_ack_payload)
        self.server_send(sock, group_ack_message)

    @handles_retries
    def send_group_message(self, sock, message):
        """Sends group chat message to server."""
        group_message_payload = {"message": message, "group": self.active_group}
        group_message = self.encode_message("group_message", group_message_payload)
        self.server_send(sock, group_message)

    @handles_retries
    def join_group(self, sock, group_name):
        """Sends join_group command to server."""
        join_group_message = self.encode_message("join_group", group_name)
        self.server_send(sock, join_group_message)

    def send_dm_ack(self, sock, recipient_name):
        """Sends an ACK to the sender of an incoming DM."""
        recipient_metadata = self.connections.get(recipient_name, {})
        client_port = recipient_metadata.get("client_port")
        client_ip = recipient_metadata.get("client_ip")
        client_destination = (client_ip, client_port)
        message = self.encode_message("message_ack", self.opts["name"])
        try:
            sock.sendto(message, client_destination)
        except socket.error as e:
            raise ClientError(f"UDP socket error: {e}")

    @handles_retries
    def send_list_group_members(self, sock):
        """Sends list_members command to server."""
        list_members_payload = {"group": self.active_group}
        list_members_message = self.encode_message("list_members", list_members_payload)
        self.server_send(sock, list_members_message)

    @handles_retries
    def send_leave_group(self, sock):
        """Sends leave_group command to server."""
        leave_group_payload = {"group": self.active_group}
        leave_group_message = self.encode_message("leave_group", leave_group_payload)
        self.server_send(sock, leave_group_message)

    def is_invalid_cmd(self, user_input):
        """Checks if command is supported based on mode (group,dm)."""
        both_commands = ["dereg", "send"]
        gc_commands = ["send_group", "list_members", "leave_group"] + both_commands
        dm_commands = ["create_group", "list_groups", "join_group"] + both_commands
        command = user_input.split(" ")[0]
        # if not a normal command at all throw
        cmd_not_exist = command not in gc_commands + dm_commands
        # if in group and command is not in gc_commands throw
        invalid_group_cmd = self.active_group is not None and command not in gc_commands
        # if not in group and command in gc_commands throw
        invalid_dm_cmd = self.active_group is None and command not in dm_commands
        return cmd_not_exist or invalid_group_cmd or invalid_dm_cmd

    def send_message(self, sock, user_input):
        """Parses user plaintext and sends to proper destination."""
        if self.is_invalid_cmd(user_input):
            cmd_literal = user_input.split(" ")[0]
            group_prefix = f"({self.active_group}) " if self.active_group else ""
            print(f">>> {group_prefix}Invalid command: {cmd_literal}")
            return

        # Pattern match inputs to command methods
        if re.match("dereg (.*)", user_input):
            dereg_name = user_input.split(" ")[1]
            if dereg_name != self.opts["name"]:
                logger.info("You can only deregister yourself.")
            else:
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
            self.send_list_group_members(sock)
        elif re.match("join_group (.*)", user_input):
            if self.active_group:
                logger.info(f"Already in {self.active_group}. Run `leave_group` first.")
            else:
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
            # Init sock for outbound messages
            sock = self.create_sock()
            # Deadloop input 'till client ends
            while server_thread.is_alive() and not self.stop_event.is_set():
                server_thread.join(1)
                ## Only handle input once registered
                if self.is_registered:
                    group_prefix = (
                        f"({self.active_group}) " if self.active_group else ""
                    )
                    user_input = input(f">>> {group_prefix}")
                    self.send_message(sock, user_input)
        except ClientError:
            # Prevent exceptions when quickly spamming `^C`
            signal.signal(signal.SIGINT, lambda s, f: None)

    @handles_retries
    def deregister(self, sock):
        """Sends deregistration request to server."""
        deregistration_message = self.encode_message("deregistration")
        self.server_send(sock, deregistration_message)

    # @handles_retries
    def register(self, sock):
        """Send initial registration message to server. If ack'ed log and continue."""
        registration_message = self.encode_message("registration")
        self.server_send(sock, registration_message)

    def server_listen(self, stop_event):
        """Listens on specified `client_port` for messages from server."""
        sock = self.create_sock()
        sock.bind(("", self.opts["client_port"]))

        sent_initial_register = False

        while True:
            # Listen for kill events
            if stop_event.is_set():
                logger.info("stopping client-server listener")
                break

            if not sent_initial_register:
                # register after we start listening on port
                sent_initial_register = True
                self.register(sock)

            readables, writables, errors = select.select([sock], [], [], 1)
            for read_socket in readables:
                # logger.info("listening")
                data, (sender_ip, sender_port) = read_socket.recvfrom(4096)
                message = self.decode_message(data)
                self.handle_request(read_socket, sender_ip, message)
