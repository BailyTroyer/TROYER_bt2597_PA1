## 2.1 Registration

**Client Mode:**

- client -> server using IP/port of server (clients already know this)

  - `ChatApp <mod> <args>`
    - `-c` for client (4 args for client name, server IP, server port and client port) -- IP in decimal
    - `-s` for server (arg for listen port) -- port in range 1024-65535
  - if correct args display `>>>`
  - error check IP addr valid numbers, ports within range

- successful registration of client on server display status message `>>> [Welcome, You are registered.]`
- client maintain table w/ info (name,ip,port,status). Client update/overwrite table when server sends state
- When table updated client display `>>> [Client table updated.]`

- 2 ways to close
  - _Silent leave:_ Once client disconnect/close server not notified. Client will **not** register again using same info after Silent leave. To exit/close client uses `>>> ctrl + c` or closes SSH window. BOTH IMPLEMENT AND NOT CRASH
  - _Notified leave:_ De-register client, and de-register notified to server. Client status in server table changed offline. More in 2.3

**Server Mode:**

- maintain table to hold name,IP,port of client
- when client sends registration req, add (name,ip,port,status) to table
- when server update table print message to term of update
- server broadcast complete table to active clients (online) whenever table updates
- _server offline == not come back online again_

## 2.2 Chatting

## 2.3 De-registration

## 2.4 Group Chat

## 2.5 Special Notes
