import logging
import socket
import select
import json

logging.basicConfig(level=logging.DEBUG, format=">>> [%(message)s]")


class ServerError(Exception):
    """Thrown when Server errors during regular operation."""

    pass


class Server:
    def __init__(self, opts):
        """{port}"""
        self.opts = opts

    def create_sock(self):
        """Create a socket."""
        try:
            return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except socket.error as e:
            raise ServerError(f"UDP server error when creating socket: {e}")

    def encode_message(self, message):
        """Convert plaintext user input to serialized message 'packet'."""
        return json.dumps({"message": message}).encode("utf-8")

    def decode_message(self, message):
        """Convert bytes to deserialized JSON."""
        return json.loads(message.decode("utf-8"))

    def listen(self):
        """Listens on specified `port` opt for messages from downstream clients."""
        sock = self.create_sock()
        sock.bind(("", self.opts["port"]))

        logging.info(f"Server started on {self.opts['port']}")
        while True:
            try:
                readables, writables, errors = select.select([sock], [], [], 1)
                for read_socket in readables:
                    data, addr = read_socket.recvfrom(4096)
                    print(addr, self.decode_message(data))
            except socket.error as e:
                raise ServerError(f"UDP server error when parsing message: {e}")
