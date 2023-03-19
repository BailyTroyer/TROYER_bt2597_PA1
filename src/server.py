import socket
import select
import json
from operator import itemgetter

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
        logger.info(f"Server table updated. {self.connections}")
        self.dispatch_connections_change(sock)

    def remove_client(self, metadata, sender_ip, sock):
        """Removes client from connection map & dispatches change to all others."""
        name = metadata.get("name")
        # @todo what happens if name already exists? We HAVE to cleanup old connection names
        # @todo we prob shouldn't delete, but mark as offline (maybe offline map)
        del self.connections[name]
        logger.info(f"Server table updated. {self.connections}")
        self.dispatch_connections_change(sock)

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
                self.groups[group_name] = {}
                logger.info(
                    f"Client {requester_name} created group `{group_name}` successfully!"
                )
                message = self.encode_message("create_group_ack", group_name)
                sock.sendto(message, (sender_ip, client_port))
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
