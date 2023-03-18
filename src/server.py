import logging

logging.basicConfig(level=logging.DEBUG, format=">>> [%(message)s]")


class Server:
    def __init__(self, opts):
        self.opts = opts

    def listen(self):
        logging.info(f"Server started on {self.opts['port']}")
        while True:
            continue
