import socket
import select
import json
from operator import itemgetter
from threading import Thread, Lock
import time

from log import logger


class ServerError(Exception):
    """Thrown when Server errors during regular operation."""

    pass


class Server:
    def __init__(self, opts):
        """{port}"""
        self.opts = opts
        self.connections = {}
        self.groups = {}
        ## keyed by group and each entry is an array of clients
        ## thus each group chat stops & waits for all acks until taking new messages
        self.outbound_group_ack_lock = Lock()
        self.outbound_group_acks = {}
        self.delay = 500 / 1000  # 500ms (500ms/1000ms = 0.5s)

    def create_sock(self):
        """Create a socket."""
        try:
            return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except socket.error as e:
            raise ServerError(f"UDP server error when creating socket: {e}")

    def encode_message(self, type, payload=None):
        """Convert plaintext user input to serialized message 'packet'."""
        metadata = {**self.opts}
        message = {"type": type, "payload": payload, "metadata": metadata}
        return json.dumps(message).encode("utf-8")

    def decode_message(self, message):
        """Convert bytes to deserialized JSON."""
        return json.loads(message.decode("utf-8"))

    def dispatch_connections_change(self, sock):
        """For all connections, send state change."""
        for name, metadata in self.connections.items():
            ## SEND MESSAGE
            client_port, sender_ip = itemgetter("client_port", "sender_ip")(metadata)
            message = self.encode_message("state_change", self.connections)
            sock.sendto(message, (sender_ip, client_port))

    def new_client(self, metadata, sender_ip, sock):
        """Adds new client metadata to connections map & dispatches change to all others."""
        name = metadata.get("name")
        # @todo what happens if name already exists? We HAVE to cleanup old connection names
        self.connections[name] = {**metadata, "sender_ip": sender_ip}
        logger.info(f"Server table updated.")
        self.dispatch_connections_change(sock)

    def remove_client(self, metadata, sender_ip, sock):
        """Removes client from connection map & dispatches change to all others."""
        # @todo this isn't generic enough for both dereg and `client_offline`
        name = metadata.get("name")
        # @todo what happens if name already exists? We HAVE to cleanup old connection names
        # @todo we prob shouldn't delete, but mark as offline (maybe offline map)
        del self.connections[name]
        ## DON'T TOUCH GROUP CHAT LIST UNTIL ACK IS MISSING AND THEN WE FORCEFULLY REMOVE
        logger.info(f"Server table updated. (removed client)")
        self.dispatch_connections_change(sock)

    def dispatch_group_message(self, sock, sender_name, group, message):
        """Dispatches group message to clients in group except sender."""
        group_clients = self.groups[group]
        for client in list(filter(lambda user: user != sender_name, group_clients)):
            client_metadata = self.connections[client]
            client_port, sender_ip = itemgetter("client_port", "sender_ip")(
                client_metadata
            )
            group_message = self.encode_message(
                "group_message", {"message": message, "sender": sender_name}
            )
            sock.sendto(group_message, (sender_ip, client_port))

    def wait_for_group_acks(self, sender_name, group, sock):
        """Waits for ACK from all clients in dispatch list for a group message."""

        # with self.outbound_group_ack_lock:
        #     print(
        #         "waiting for group acks: ", sender_name, group, self.outbound_group_acks
        #     )
        # we don't want to wait for ack from sender
        expected_acks = list(filter(lambda u: u != sender_name, self.groups[group]))
        # print(f"expected acks: {expected_acks}")

        retries = 0
        waiting_for_acks = True
        while waiting_for_acks and retries <= 5:  ## Wait for ack 5x 500ms each
            with self.outbound_group_ack_lock:
                # order both lists and compare
                waiting_for_acks = sorted(self.outbound_group_acks[group]) != sorted(
                    expected_acks
                )

            # We don't want to sleep on the 5th time we just exit
            if retries <= 4:
                time.sleep(self.delay)
            retries += 1
        if waiting_for_acks:
            unacked_clients = list(
                set(expected_acks) - set(self.outbound_group_acks[group])
            )
            # logger.info(f"Error; Unacked messages from {unacked_clients}")
            for unacked_client in unacked_clients:
                self.groups[group].remove(unacked_client)
                logger.info(
                    f"Client {unacked_client} not responsive, removed from group {group}"
                )
        # else:
        #     # logger.info(f"got proper acks!")

    def handle_request(self, sock, sender_ip, payload):
        """Handles different request types (e.g. registration)."""
        request_type = payload.get("type", "")
        if request_type == "registration":
            ## Send back registration ack
            metadata = payload.get("metadata")
            name = metadata.get("name")
            client_port = metadata.get("client_port")

            if name in self.connections:
                ## We don't allow duplicate names in table
                error_payload = {"message": f"`{name}` already exists!"}
                message = self.encode_message("registration_error", error_payload)
                sock.sendto(message, (sender_ip, client_port))
            else:
                message = self.encode_message("registration_confirmation")
                sock.sendto(message, (sender_ip, client_port))
                self.new_client(metadata, sender_ip, sock)
        elif request_type == "deregistration":
            ## Send back deregistration ack
            metadata = payload.get("metadata")
            client_port = metadata.get("client_port")
            message = self.encode_message("deregistration_confirmation")
            sock.sendto(message, (sender_ip, client_port))
            ## Update table
            self.remove_client(metadata, sender_ip, sock)
        elif request_type == "create_group":
            metadata = payload.get("metadata")
            requester_name = metadata.get("name")
            group_name = payload.get("payload")
            client_port = metadata.get("client_port")
            if group_name in self.groups.keys():
                logger.warning(
                    f"Client {requester_name} creating group `{group_name}` failed, group already exists"
                )
                error_payload = {"message": f"Group `{group_name}` already exists."}
                message = self.encode_message("create_group_error", error_payload)
                sock.sendto(message, (sender_ip, client_port))
            else:
                self.groups[group_name] = []
                logger.info(
                    f"Client {requester_name} created group `{group_name}` successfully!"
                )
                message = self.encode_message("create_group_ack", group_name)
                sock.sendto(message, (sender_ip, client_port))
        elif request_type == "list_groups":
            metadata = payload.get("metadata")
            client_name = metadata.get("name")
            client_port = metadata.get("client_port")
            groups = list(self.groups.keys())
            logger.info(
                f"Client {client_name} requested listing groups, current groups:"
            )
            for group in groups:
                logger.info(group)
            message = self.encode_message("list_groups_ack", {"groups": groups})
            sock.sendto(message, (sender_ip, client_port))
        elif request_type == "join_group":
            metadata = payload.get("metadata")
            requester_name = metadata.get("name")
            group_name = payload.get("payload")
            client_port = metadata.get("client_port")
            if group_name not in self.groups.keys():
                logger.warning(
                    f"Client {requester_name} joining group `{group_name}` failed, group does not exist"
                )
                error_payload = {"message": f"Group `{group_name}` does not exist."}
                message = self.encode_message("join_group_error", error_payload)
                sock.sendto(message, (sender_ip, client_port))
            else:
                self.groups[group_name].append(requester_name)
                logger.info(f"Client {requester_name} joined group `{group_name}`")
                message = self.encode_message("join_group_ack", group_name)
                sock.sendto(message, (sender_ip, client_port))
        elif request_type == "client_offline":
            ## Send back deregistration ack
            offline_client_name = payload.get("payload")
            # deregister auto based on disconnected state sending DM between clients
            del self.connections[offline_client_name]
            logger.info(f"Server table updated.")
            self.dispatch_connections_change(sock)
            metadata = payload.get("metadata")
            client_port = metadata.get("client_port")
            # send dereg ack to client
            message = self.encode_message("client_offline_ack", offline_client_name)
            sock.sendto(message, (sender_ip, client_port))
        elif request_type == "group_message":
            ## send message to all clients within group
            ## @todo if the ack gets lost does that mean client sends duplicate messages?
            metadata = payload.get("metadata", {})
            sender_name = metadata.get("name", "")
            client_port = metadata.get("client_port")
            message = payload.get("payload", {}).get("message", "")
            ## Send ack to sender
            message_ack = self.encode_message("group_message_ack")
            sock.sendto(message_ack, (sender_ip, client_port))
            logger.info(f"Client {sender_name} sent group message: {message}")
            ## Dispatch message
            group = payload.get("payload", {}).get("group", "")

            # reset the group acks that we wait for in thread
            with self.outbound_group_ack_lock:
                self.outbound_group_acks[group] = []

            self.dispatch_group_message(sock, sender_name, group, message)

            wait_for_acks_thread = Thread(
                target=self.wait_for_group_acks,
                args=(sender_name, group, sock),
            )
            wait_for_acks_thread.start()
        elif request_type == "group_message_ack":
            group = payload.get("payload", {}).get("group", "")
            metadata = payload.get("metadata", {})
            sender_name = metadata.get("name", "")
            logger.info(f"Client {sender_name} acked group message")
            with self.outbound_group_ack_lock:
                self.outbound_group_acks[group].append(sender_name)

        elif request_type == "list_members":
            group = payload.get("payload", {}).get("group", "")
            client_name = payload.get("metadata", {}).get("name", "")
            # get list of users in group
            group_members = self.groups[group]
            metadata = payload.get("metadata", {})
            message_ack = self.encode_message(
                "members_list", {"members": group_members}
            )
            logger.info(
                f"Client {client_name} requested listing members of group {group}"
            )
            for members in group_members:
                logger.info(members)
            client_port = metadata.get("client_port")
            sock.sendto(message_ack, (sender_ip, client_port))

        elif request_type == "leave_group":
            group = payload.get("payload", {}).get("group", "")
            metadata = payload.get("metadata", {})
            sender_name = metadata.get("name", "")
            # remove user from list in group
            self.groups[group].remove(sender_name)
            message_ack = self.encode_message("leave_group_ack")
            client_port = metadata.get("client_port")
            sock.sendto(message_ack, (sender_ip, client_port))
            logger.info(f"Client {sender_name} left group {group}")
        else:
            print("got another request: ", sender_ip, payload)

    def listen(self):
        """Listens on specified `port` opt for messages from downstream clients."""
        sock = self.create_sock()
        sock.bind(("", self.opts["port"]))

        logger.warning(f"Server started on {self.opts['port']}")
        while True:
            try:
                readables, writables, errors = select.select([sock], [], [], 1)
                for read_socket in readables:
                    data, (sender_ip, sender_port) = read_socket.recvfrom(4096)
                    message = self.decode_message(data)
                    self.handle_request(read_socket, sender_ip, message)
            except socket.error as e:
                raise ServerError(f"UDP server error when parsing message: {e}")
