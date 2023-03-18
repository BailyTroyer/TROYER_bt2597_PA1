import logging

logging.basicConfig(level=logging.DEBUG, format=">>> [%(message)s]")


class Client:
    def __init__(self, opts):
        self.opts = opts

    def start(self):
        logging.info("Welcome, You are registered.")
        while True:
            user_input = input(">>> ")
            print(user_input)
