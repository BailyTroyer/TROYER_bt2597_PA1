import re
import sys

from client import Client, ClientError
from server import Server, ServerError


help_message = """ChatApp allows you to spinup a client and server for UDP based chatting.

Commands:
    -c      Starts client with required server information.
    -s      Starts server mode at specified port

Usage:
    ChatApp [flags] [options]

Use "ChatApp <command> --help" for more information about a given command"""

client_help_message = """Starts client with required server information.

Examples:
    # Start a server on port 5555
    ChatApp name 1.2.3.4 4.3.2.1 5555

Options:
    <name>: The port to serve on UDP.
    <server-ip>: The already running server IPv4 addr.
    <server-port>: The already running server port.
    <client-port>: The port of the listening client.
"""

server_help_message = """Starts server mode at specified port.

Examples:
    # Start a server on port 5555
    ChatApp -c 5555

Options:
    <port>: The port to serve on UDP.
"""


class InvalidArgException(Exception):
    """Thrown when CLI input arguments don't match expected type/structure/order."""

    pass


def valid_ip(value):
    """Validate an IP address arg text is valid IPv4."""
    ipv4_pattern = "^(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
    if re.match(ipv4_pattern, value):
        return value
    else:
        raise InvalidArgException("Use only IPv4 addressing")


def valid_port(value):
    """Validate port matches expected range 1024-65535."""
    if value.isdigit():
        val = int(value)
        return val >= 1024 and val <= 65535
    return False


def parse_client_mode(args):
    """Validate -c mode args: `<name> <server-ip> <server-port> <client-port>`."""

    if list(filter(lambda arg: arg == "-h", args)):
        raise InvalidArgException(client_help_message)

    if len(args) != 4:
        raise InvalidArgException(
            "`-c` only accepts <name> <server-ip> <server-port> <client-port>"
        )

    [name, server_ip, server_port, client_port] = args

    if not valid_ip(server_ip):
        raise InvalidArgException(f"Invalid <server-ip>: {server_ip}; Must be IPv4")
    if not valid_port(server_port):
        raise InvalidArgException(
            f"Invalid <server-port>: {server_port}; Must be within 1024-65535"
        )
    if not valid_port(client_port):
        raise InvalidArgException(
            f"Invalid <client-port>: {client_port}; Must be within 1024-65535"
        )

    return {
        "name": name,
        "server_ip": server_ip,
        "server_port": int(server_port),
        "client_port": int(client_port),
    }


def parse_server_mode(args):
    """Validate -s mode args: `<port>`."""

    if list(filter(lambda arg: arg == "-h", args)):
        raise InvalidArgException(server_help_message)
    if len(args) != 1:
        raise InvalidArgException("`-s` only accepts <port>")

    port = args[0]
    if not valid_port(port):
        raise InvalidArgException(f"Invalid <port>: {port}; Must be within 1024-65535")
    return {"port": int(port)}


def parse_mode_and_go():
    """Validate root mode args: `-s` or `-c`."""

    args = sys.argv[1:]
    if len(args) == 0:
        raise InvalidArgException(help_message)

    mode = args[0]
    if mode == "-s":
        server_opts = parse_server_mode(args[1:])
        server = Server(server_opts)
        server.listen()
    elif mode == "-c":
        client_opts = parse_client_mode(args[1:])
        client = Client(client_opts)
        client.start()
    else:
        raise InvalidArgException(f"{mode} is not a valid mode")


if __name__ == "__main__":
    """
    # Server Mode: `ChatApp -s <port>`
    # Client Mode: `ChatApp -c <name> <server-ip> <server-port> <client-port>`
    """
    try:
        parse_mode_and_go()
    except InvalidArgException as e:
        print("Invalid arg: ", e)
        sys.exit(1)
    except ClientError as e:
        print("Client error: ", e)
        sys.exit(1)
    except ServerError as e:
        print("server error: ", e)
        sys.exit(1)
    except KeyboardInterrupt:
        print("exiting...")
        sys.exit(1)
    except Exception as e:
        print("Unknown error: ", e)
        sys.exit(1)
