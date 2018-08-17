import argparse
import copy
import signal
import socket
import sys
import threading
from json import JSONDecodeError

import layers
from models.packets import Packet
from models.peers import Client, Peer
from modules.default_modules import SendAsJSONModule, Base64EncodeModule

loaded_modules = [SendAsJSONModule(), Base64EncodeModule()]

peers = dict()

parser = argparse.ArgumentParser(description="Server Parameters")
parser.add_argument('--port', type=int, default=51423, help="Server listening port")
parser.add_argument('--limit', type=int, default=1024, help="Maximum number of connections")

args = parser.parse_args()
port = args.port
max_connections = args.limit

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(('', port))


def message_received(message: Packet, peer: Peer):
    peer_ids = copy.deepcopy(list(peers.keys()))
    for peer_id in peer_ids:
        if peer_id == peer.peer_id:
            continue
        if peer_id in peers.keys():  # just in case someone has disconnected during broadcast
            layers.socket_send_data(peers[peer_id]["socket"], message, loaded_modules)


def incoming_message_listener(connection, peer: Peer):
    while peer.peer_id in peers.keys():
        try:
            data = connection.recv(8192)
        except OSError:
            continue

        if data == b'':
            print("Peer", peer.peer_id, "disconnected.")
            if peer.peer_id in peers.keys():
                peers.pop(peer.peer_id)
            break

        try:
            packet = layers.socket_handle_received(connection, data.decode("utf8"), loaded_modules)
            message_received(packet, peer)
            print("Message from", peer.peer_id, ": ", packet.message.text)
        except UnicodeDecodeError:
            print("Corrupted packet from", peer.peer_id)
        except JSONDecodeError:
            print("Invalid packet from", peer.peer_id)
        except KeyError:
            print("Invalid packet from", peer.peer_id)


def incoming_connections_listener():
    while True:
        try:
            if len(peers.keys()) != max_connections:
                connection, address = sock.accept()
                connection.settimeout(2)
                peer = Client(address[0], address[1])
                incoming_message_thread = threading.Thread(target=incoming_message_listener, args=[connection, peer])

                peers[peer.peer_id] = {
                    "peer": peer,
                    "socket": connection,
                    "thread": incoming_message_thread
                }

                incoming_message_thread.setDaemon(True)
                incoming_message_thread.start()
        except OSError:
            continue

sock.listen(max_connections)

incoming_connections_thread = threading.Thread(target=incoming_connections_listener)
incoming_connections_thread.setDaemon(True)
incoming_connections_thread.start()


def exit_handler(sig, frame):
    print("\nGot exit signal")
    peer_ids = copy.deepcopy(list(peers.keys()))

    for peer_id in peer_ids:
        print("disconnecting peer", peer_id)
        peers[peer_id]["thread"].join(0)
        peers[peer_id]["socket"].shutdown(socket.SHUT_RDWR)
        peers[peer_id]["socket"].close()
        peers.pop(peer_id)

    sock.detach()
    sock.close()
    sys.exit(0)


signal.signal(signal.SIGINT, exit_handler)
signal.pause()
