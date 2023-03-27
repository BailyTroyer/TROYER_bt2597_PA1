# bt2597 PA1

ChatApp allows you to spinup a client and server for UDP based chatting.

## Code Architecture

### File Structure

We have the main code inside `src` with docs + supporting tooling inside the root.

```
$ tree
.
├── Makefile
├── README.md
├── design.md
├── src
│   ├── __init__.py
│   ├── __pycache__
│   │   ├── client.cpython-38.pyc
│   │   ├── log.cpython-38.pyc
│   │   ├── registration.cpython-38.pyc
│   │   └── server.cpython-38.pyc
│   ├── client.py
│   ├── log.py
│   ├── main.py
│   └── server.py
├── test.py
└── test.txt

3 directories, 14 files
```

We can break down the abstractions in the codebase into four _objectives_:

1. CLI input validation & parsing
2. Network & Input Communication (User,Client,Server)
3. Inner state management (Client & Server)
4. Misc utilities

### 1. CLI Input Validation

In [main.py](./src/main.py) we handle the root `parse_mode_and_go` method which handles input validation along with starting the respective client/server class (to be explained further down) which listens and creates the required UDP sockets.

A custom exception `InvalidArgException` is used to handle different error states such as invalid argument types, incomplete args and a default message simulating regular terminal CLIs (e.g `kubectl`). Since there was a mandate against public packages I used `sys.argv` instead of a fancier arg parser such as [click](https://click.palletsprojects.com/en/8.1.x/).

### 2. Network & Input Communication

**For the client:**

In [main.py](./src/main.py) we initialize the client and call `start` which handles:
1. Listening to signals (e.g. `^C`)
2. Starting the server listen thread (UDP socket)
3. Listening to the user input via `input`

The signal handling allows us to gracefully exit when the user attempts to force close the running program. However to cancel all threads properly with one SIGINT, we have 2 signal listeners. One for the first call, and another for spamming `^C` to prevent the threading from throwing errors when closing.

Then we start a separate thread for `server_listen` which endlessly loops (unless a stop event is triggered) for data coming inbound from the server/client.

In the main thread we listen for input and call `send_message` which handles commmand validation and pattern matching (via regex) to call the necessary utility method corresponding to the command.

E.g. if you input `dereg foo` then the `send_message` method will go through a series of `if` statements one of which is `re.match("dereg (.*)", user_input)` that matches the proper regex for deregistration.

From `send_message` we simply call single-purpose methods that match the required client functionality (e.g. listing groups, creating groups, sending messages, deregistering).

**For the server:**

In [main.py](./src/main.py) we initialize the server and call `listen` which handles:

* Listening on a UDP socket
  * Calling `handle_request` which pattern matches an inbound message `type` field to respective utility methods corresponding to server-side business logic (e.g. handling registration, group commands, etc)

### 3. Inner State Management

We have separate classes for both [client.py](./src/client.py) and [server.py](./src/server.py) which store the initial CLI args along with the persisted state (in memory) for active connections streamed during batch updates along with syncs between whether an active ack is being waited on (e.g. `waiting_for_ack` in [client.py](./src/client.py)).

As briefly mentioned in section 2, we use JSON for handling data inside the client & server (via dictionaries) along with having utility methods for serializing/deserializing JSON into dictionaries passed through the "wire."

Both client and server have an `encode_message` method that takes a message type (e.g. registration) along with additional per-message data (called "payload"). To aide in future-proofing, I've also included `metadata` inside the message body which includes the initial CLI arguments passed to each client/server (e.g. name of client, ports & IP).

**Table Format**

We don't use a strict relational data format, but rather have a dict following the format `[client_name: {sender_ip, ...metadata}]` with `metadata` being the initial CLI options passed to the client.

Likewise we have active groups stored in a dict following `[group_name: client_name[]]` where `client_name` is the unique identifier for other clients that exist in `connections`. 

### 4. Misc utilities

I noticed half way through the project (I've included the `.git` for tracing commits) that I was writing dupliate code for handling retries based on incoming acks. So I added a decorator `handles_retries` which runs a while loop up to 5 times with a sleep (of 500ms) per iteration. We then call the wrapped method inside that while loop (hence the decorator).

So for example we can do this:

```py
@handles_retries
def list_groups(self, sock):
    """Sends list_group command to server."""
    registration_message = self.encode_message("list_groups")
    self.server_send(sock, registration_message)
```

From there we send a JSON object via UDP like so:

`{"type": "list_groups", "payload": {}, "metadata": {...}}`

I've also included a custom logger using the `logging` package for handling proper formatting of output since we need to wrap most messages in brackets (e.g. `[Server not responding.]`)

This supports handling logs from different threads, and for easier debugging allows printing the thread name (see the commented out formatter in [log.py](./src/log.py)).

## Usage

You can get the main structure of the CLI with no args:

```sh
$ python src/main.py
ChatApp allows you to spinup a client and server for UDP based chatting.

Commands:
    -c      Starts client with required server information.
    -s      Starts server mode at specified port

Usage:
    ChatApp [flags] [options]
```

### Run Server

The following example starts the server on port `5000`:

```sh
$ python src/main.py -s 5000
```

If validation fails it will print an "Invalid" message:

```sh
# Not a valid port number
$ python src/main.py -s not-a-port-number
Invalid <port>: not-a-port-number; Must be within 1024-65535

# Not a valid port range
$ python src/main.py -s 1
Invalid <port>: 1; Must be within 1024-65535

# Missing the port value
$ python src/main.py -s
`-s` only accepts <port>
```

### Run Client(s)

The following example starts a client named `client` on port `5555`
connecting to server `0.0.0.0:5000`:

```sh
$ python src/main.py -c client 0.0.0.0 5000 5556
```

If validation fails it will print an "Invalid" message:

```sh
# Invalid client port
$ python src/main.py -c client4 0.0.0.0 5000 not-a-port-value
Invalid <client-port>: not-a-port-value; Must be within 1024-65535

# Invalid server port
$ python src/main.py -c client4 0.0.0.0 not-a-port-value 5556
Invalid <server-port>: not-a-port-value; Must be within 1024-65535

# Invalid IPv4 format
$ python src/main.py -c client4 not-an-ip 5000 5556
Use only IPv4 addressing

# Missing the argument values
$ python src/main.py -c
`-c` only accepts <name> <server-ip> <server-port> <client-port>
```

## Testing

### 2.1 Registration

> For CLI input validation see [Usage](#Usage) which includes sh snippets on expected cases for error/success states

#### Connecting

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]

```

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> 
```

#### Silent Leave

**Client w/ Ctrl-C:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> ^C
>>> [stopping client-server listener]
```

**Server w/ Ctrl-C:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
^C
>>> [Quitting.]
```

#### Notified Leave

Mentioned in further section 2.3

### 2.2 Chatting

#### Successful send w/ spaces in message

Here we startup the server, then client1 then client2. After all are setup we run `send client hello there :)` on client2 which goes directly to `client1`.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Server table updated.]
```

**client1:**

> Notice the output format gets broken. This is mentioned in [Callouts](#Callouts) below.

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> >>> [Client table updated.]
>>> [client2: hello there :)]
```

**client2:**

```sh
$ python src/main.py -c client2 0.0.0.0 5000 5556
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> send client hello there :)
>>> [Message received by client]
>>> 
```

#### Unsucessful send (ack timeout from client2 to client)

Here the startup is the same as before, except once all three are running we do SIGINT on `client` thus breaking the message comms when trying to send the same message from `client2`

**server:**

> Notice we have a third server table update since the client is offline, and thus auto deregistered.

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Server table updated.]
>>> [Server table updated.]
```

**client1:**

> Notice the output format gets broken. This is mentioned in [Csage](#Callouts) below.

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> >>> [Client table updated.]
^C
>>> [stopping client-server listener]
```

**client2:**

```sh
$ python src/main.py -c client2 0.0.0.0 5000 5556
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> send client hello there :)
>>> [No ACK from client, message not delivered]
>>> [Client table updated.]
>>> [Auto-deregistered client since they were offline.]
>>> 
```

### 2.3 De-registration

#### Valid client name

Here we start a client + server and then call `dereg`

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Server table updated. (removed client)]
```

**client:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> dereg client
>>> [You are Offline. Bye.]
>>> [stopping client-server listener]
```

#### Invalid client name (wrong client)

Here we attempt to dereg `foo` when the client is `client` from the initial CLI args

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
```

**client:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> dereg foo
>>> [You can only deregister yourself.]
>>> 
```

#### Server shut down before dereg sent

Here the server is shut down (SIGINT) before the server attempts a `dereg` with a valid name.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
^C
>>> [Quitting.]
```

**client:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> dereg client
>>> [Server not responding]
>>> [Exiting]
```

### 2.4.1 Group Chat Create

#### Successfully created group chat

Start server, start client, create group chat.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
```

**client:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> create_group group-name
>>> [Group group-name created by Server.]
>>> 
```

#### Unsucessfully created group, server down

The server gets SIGINT after client registers, but before `create_group` is called.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
^C
>>> [Quitting.]
```

**client:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> create_group group-name
>>> [Server not responding]
>>> [Exiting]
>>> [stopping client-server listener]
```

#### Unsucessfully created group, name already exists

We create a group `group-name` and try again, which throws an already exists error message.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
>>> [Client client creating group `group-name` failed, group already exists]
```

**client:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> create_group group-name
>>> [Group group-name created by Server.]
>>> create_group group-name
>>> [Group `group-name` already exists.]
>>> 
```

### 2.4.2 List All Group Chats

#### Successfully lists existing groups

We create a group and then list it, followed by creating another group and listing both created.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
>>> [Client client requested listing groups, current groups:]
>>> [group-name]
>>> [Client client created group `group-name-2` successfully!]
>>> [Client client requested listing groups, current groups:]
>>> [group-name]
>>> [group-name-2]
```

**client:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> create_group group-name
>>> [Group group-name created by Server.]
>>> list_groups
>>> [Available group chats:]
>>> [group-name]
>>> create_group group-name-2
>>> [Group group-name-2 created by Server.]
>>> list_groups
>>> [Available group chats:]
>>> [group-name]
>>> [group-name-2]
>>> 
```

#### Unsuccessfully lists existing groups, server down

We start the client & server, create a group and stop the server before client can send `list_groups`

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
^C
>>> [Quitting.]
```

**client:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> create_group group-name
>>> [Group group-name created by Server.]
>>> list_groups
>>> [Server not responding]
>>> [Exiting]
```

### 2.4.3 Join

#### Succesfully joins existing group

Create group, join group.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
>>> [Client client joined group `group-name`]
```

**client:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> create_group group-name
>>> [Group group-name created by Server.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name)
```

#### Unsuccesfully joins non-existing group

Try to join group `group-name` that doesn't exist.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Client client joining group `group-name` failed, group does not exist]
```

**client:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> join_group group-name
>>> [Group `group-name` does not exist.]
>>> 
```

#### Unsuccesfully joins existing group, server down

Create group, but stop the server before `join_group` is called on client

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
^C
>>> [Quitting.]
```

**client:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> create_group group-name
>>> [Group group-name created by Server.]
>>> join_group group-name
>>> [Server not responding]
>>> [Exiting]
>>> [stopping client-server listener]
```

### 2.4.4 Chat in the Group

#### Succesfully chats between 2 clients in group

Here we start the server and 2 clients. Then `client` creates a group `group-name` followed by joining. Then `client2` joins the group and `client1` sends a message to the group (which `client2` prints).

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
>>> [Client client joined group `group-name`]
>>> [Client client2 joined group `group-name`]
>>> [Client client sent group message: hey there :)]
>>> [Client client2 acked group message]
```

**client1:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> >>> [Client table updated.]
create_group group-name
>>> [Group group-name created by Server.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) send_group hey there :)
>>> (group-name) [Message received by Server.]
>>> (group-name) 
```

**client2:**

> As mentioned in callouts (just placing focus here), the output is slightly broken since the input thread is hogging the newline so formatting gets wonky. Note typing input on client2 still works and nothing is "broken" regarding messaging.

```sh
$ python src/main.py -c client2 0.0.0.0 5000 5556
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) >>> (group-name) Group_Message client: hey there :)
```

#### Succesfully deregisters offline client when group message sent

Here we follow the previous steps except we stop `client2` right before `client` sends its message. Then the server properly deregisters the missing client from the unresponsive ack.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
>>> [Client client joined group `group-name`]
>>> [Client client2 joined group `group-name`]
>>> [Client client sent group message: can you hear me?? :P]
>>> [Client client2 not responsive, removed from group group-name]
```

**client1:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> >>> [Client table updated.]
create_group group-name
>>> [Group group-name created by Server.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) send_group can you hear me?? :P
>>> (group-name) [Message received by Server.]
>>> (group-name) 
```

**client2:**

```sh
$ python src/main.py -c client2 0.0.0.0 5000 5556
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) ^C
>>> [stopping client-server listener]
```

#### Client exits when server is non-responsive

We start the server, create and join the group then stop the server right before `client` attempts to send a message.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
>>> [Client client joined group `group-name`]
^C
>>> [Quitting.]
```

**client1:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> create_group group-name
>>> [Group group-name created by Server.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) send_group hello world?
>>> [Server not responding]
>>> [Exiting]
>>> [stopping client-server listener]
```

### 2.4.5 List Group Members

#### Lists members in group

Start the server and have both clients join group.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
>>> [Client client joined group `group-name`]
>>> [Client client2 joined group `group-name`]
>>> [Client client requested listing members of group group-name]
>>> [client]
>>> [client2]
>>> [Client client2 requested listing members of group group-name]
>>> [client]
>>> [client2]
```

**client1:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> >>> [Client table updated.]
create_group group-name
>>> [Group group-name created by Server.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) list_members
>>> (group-name) [Members in the group group-name:]
>>> (group-name) client
>>> (group-name) client2
>>> (group-name) 
```

**client2:**

```sh
$ python src/main.py -c client2 0.0.0.0 5000 5556
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) list_members
>>> (group-name) [Members in the group group-name:]
>>> (group-name) client
>>> (group-name) client2
>>> (group-name) 
```

#### Handles server down

Start server, create group, then close server before clients can run `list_members`

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
>>> [Client client joined group `group-name`]
>>> [Client client2 joined group `group-name`]
^C
>>> [Quitting.]
```

**client1:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> >>> [Client table updated.]
create_group group-name
>>> [Group group-name created by Server.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) list_members
>>> [Server not responding]
>>> [Exiting]
>>> [stopping client-server listener]
```

**client2:**

```sh
$ python src/main.py -c client2 0.0.0.0 5000 5556
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) list_members
>>> [Server not responding]
>>> [Exiting]
>>> [stopping client-server listener]
```

### 2.4.6 Leave

#### Properly leaves group

Start server, create & join group and leave. The 2nd client then runs list_members which doesn't include the client that left.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
>>> [Client client joined group `group-name`]
>>> [Client client2 joined group `group-name`]
>>> [Client client requested listing members of group group-name]
>>> [client]
>>> [client2]
>>> [Client client2 requested listing members of group group-name]
>>> [client]
>>> [client2]
>>> [Client client left group group-name]
>>> [Client client2 requested listing members of group group-name]
>>> [client2]
```

**client1:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> >>> [Client table updated.]
create_group group-name
>>> [Group group-name created by Server.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) list_members
>>> (group-name) [Members in the group group-name:]
>>> (group-name) client
>>> (group-name) client2
>>> (group-name) leave_group
>>> [Leave group chat group-name]
>>> 
```

**client2:**

```sh
$ python src/main.py -c client2 0.0.0.0 5000 5556
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) list_members
>>> (group-name) [Members in the group group-name:]
>>> (group-name) client
>>> (group-name) client2
>>> (group-name) list_members
>>> (group-name) [Members in the group group-name:]
>>> (group-name) client2
>>> (group-name) 
```

#### Handles down server

Similar to the case before but we stop the server before the client can try leaving.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
>>> [Client client joined group `group-name`]
>>> [Client client2 joined group `group-name`]
>>> [Client client requested listing members of group group-name]
>>> [client]
>>> [client2]
>>> [Client client2 requested listing members of group group-name]
>>> [client]
>>> [client2]
^C
>>> [Quitting.]
```

**client1:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> >>> [Client table updated.]
create_group group-name
>>> [Group group-name created by Server.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) list_members
>>> (group-name) [Members in the group group-name:]
>>> (group-name) client
>>> (group-name) client2
>>> (group-name) leave_group
>>> [Server not responding]
>>> [Exiting]
>>> [stopping client-server listener]
```

**client2:**

```sh
$ python src/main.py -c client2 0.0.0.0 5000 5556
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) list_members
>>> (group-name) [Members in the group group-name:]
>>> (group-name) client
>>> (group-name) client2
>>> (group-name) 
```

### 2.5.1 Private Messages in Group Mode

#### Properly sends message to user in group chat. Printing when leaving

Start server, create and join group, then client2 sends message to `client` which is in group. Then `client` leaves group and inbox is printed.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
>>> [Server table updated.]
>>> [Client client created group `group-name` successfully!]
>>> [Client client joined group `group-name`]
>>> [Client client left group group-name]
```

**client1:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> >>> [Client table updated.]
create_group group-name
>>> [Group group-name created by Server.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) leave_group
>>> [Leave group chat group-name]
>>> [>>> client2: call me back :(]
>>> 
```

**client2:**

```sh
$ python src/main.py -c client2 0.0.0.0 5000 5556
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> send client call me back :(
>>> [Message received by client]
>>> 
```

### 2.5.2 Command scope

#### Shows error when sending normal commands in group mode

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> create_group group-name
>>> [Group group-name created by Server.]
>>> join_group group-name
>>> [Entered group group-name successfully!]
>>> (group-name) create_group anotha-one
>>> (group-name) Invalid command: create_group
>>> (group-name) 
```

#### Shows error when sending group commands in normal mode

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> send_group hi
>>> [Invalid command: send_group]
>>> leave_group
>>> [Invalid command: leave_group]
>>> list_members
>>> [Invalid command: list_members]
```

### Edge Cases

#### Sending messages to yourself

In this case there's no issue if you want to send messages to yourself. Not sure why you'd want to, but we don't filter messages out.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
```

**client:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> send client yo dawg heard you like clients
>>> [client: yo dawg heard you like clients]
>>> [Message received by client]
>>> 
```

#### Client already exists

Here we can simulate this by hard closing the client and starting again with the same name. In this case nobody has tried to send a message and it was offline. If the server were restarted, then this wouldn't be an issue since the servers state would reset.

**server:**

```sh
$ python src/main.py -s 5000
>>> [Server started on 5000]
>>> [Server table updated.]
```

**client:**

```sh
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [Welcome, You are registered.]
>>> [Client table updated.]
>>> ^C
>>> [stopping client-server listener]
$ python src/main.py -c client 0.0.0.0 5000 5555
>>> [`client` already exists!]
>>> [stopping client-server listener]
```

## Callouts

### 1. @todos

I've decided to keep my todos that I didn't address for transparency, which are commented and include questions and/or tech debt that I would fix if this were a longer term project (not sure if we plan on using this implementation in the next project or we'll start from scratch).

### 2. Logging output is wack

Sometimes the logger prints in an unordered fashion where parallel actions (e.g. client table updates) cause output go get printed out while we're waiting for input. This means the input can go on a newline which is very suboptimal.

I'm not entirely sure how to prevent this, since I've already included the `QueueHandler` to handle queueing messages from different threads. I know this has to do with `input` blocking stdout when we have incoming messages. I found _a_ solution that invloved using carriage returns `\r` and replacing newlines after each log `\n` but it wasn't a clean solution so I didn't include it.

For reference:

```
from time import sleep
from threading import Thread


def test():
    while True:
        sleep(3)
        print("\r[*]Hello...\n>>> ", end="")


if __name__ == "__main__":
    test_thread = Thread(target=test)
    test_thread.start()
    while True:
        msg = input(">>> ")
        print("Your input: " + msg)
```
