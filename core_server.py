import argparse
import copy
import signal
import socket
import sys
import threading
from json import JSONDecodeError

import layers
from models.actions import ServiceAction, DisconnectAction, ConnectAction
from models.messages import Message
from models.packets import Packet
from models.peers import Client, Peer
from modules.default_modules import SendAsJSONModule, Base64EncodeModule

loaded_modules = [SendAsJSONModule(), Base64EncodeModule()]

peers = dict()

parser = argparse.ArgumentParser(description="Server Parameters")
parser.add_argument('--port', type=int, default=51423, help="Server listening port")
parser.add_argument('--limit', type=int, default=1024, help="Maximum number of connections")
parser.add_argument('--debug', help="Enable debug logs", action="store_true")

args = parser.parse_args()
port = args.port
max_connections = args.limit
debug = args.debug

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(('', port))


def broadcast(packet: Packet, ignore_list: list):
    peer_ids = copy.deepcopy(list(peers.keys()))
    for peer_id in peer_ids:
        if peer_id in peers.keys() \
                and peer_id not in ignore_list \
                and peers[peer_id]["wrote"]:
            layers.socket_send_data(peers[peer_id]["socket"], packet, loaded_modules)


def send_to_single(packet: Packet, peer_id: str):
    if peer_id in peers.keys():
        layers.socket_send_data(peers[peer_id]["socket"], packet, loaded_modules)


def message_received(message: Packet, peer: Peer):
    peer_ids = copy.deepcopy(list(peers.keys()))
    for peer_id in peer_ids:
        if peer_id == peer.peer_id or not peers[peer_id]["wrote"]:
            continue
        if peer_id in peers.keys():  # just in case someone has disconnected during broadcast
            layers.socket_send_data(peers[peer_id]["socket"], message, loaded_modules)


def disconnected_callback(peer_id):
    print("Peer", peer_id, "disconnected.")
    broadcast(
        Packet(
            action=ServiceAction(),
            message=Message(text="{} disconnected.".format(peer_id))
        ), [peer_id]
    )


def connected_callback(peer_id):
    print("Peer", peer_id, "connected.")
    broadcast(
        Packet(
            action=ServiceAction(),
            message=Message(text="{} connected.".format(peer_id))
        ),
        [peer_id]
    )


def incoming_message_listener(connection, peer: Peer):
    while peer.peer_id in peers.keys():
        if peers[peer.peer_id]["muted"]:
            continue

        try:
            data = connection.recv(8192)
        except OSError as error:
            if debug:
                print("Error in message thread (peer {})".format(peer.peer_id))
                print(error)
            continue

        if debug:
            print("Data:", data)

        if data == b'':
            if peer.peer_id in peers.keys():
                peers.pop(peer.peer_id)
            disconnected_callback(peer.peer_id)
            break

        try:
            packet = layers.socket_handle_received(connection, data.decode("utf8"), loaded_modules)
            if packet.action.action == DisconnectAction().action:
                disconnected_callback(peer.peer_id)
                peers[peer.peer_id]["wrote"] = True
                continue
            elif packet.action.action == ConnectAction().action:
                connected_callback(peer.peer_id)
                peers[peer.peer_id]["wrote"] = True
                continue
            else:
                if not peers[peer.peer_id]["wrote"]:
                    send_to_single(
                        Packet(
                            action=ServiceAction(),
                            message=Message(text="Wrong button, buddy :)")
                        ),
                        peer.peer_id
                    )
                    kick(peer.peer_id)
                    break
            peers[peer.peer_id]["wrote"] = True
            message_received(packet, peer)
            print("Message from", peer.peer_id, ": ", packet.message.text)
        except UnicodeDecodeError as error:
            print("Corrupted packet from", peer.peer_id)
            if debug:
                print(error)
        except (JSONDecodeError, KeyError, TypeError) as error:
            print("Invalid packet from", peer.peer_id)
            if debug:
                print(error)


def incoming_connections_listener():
    while True:
        try:
            if len(peers.keys()) != max_connections:
                connection, address = sock.accept()
                connection.settimeout(30)
                peer = Client(address[0], address[1])
                incoming_message_thread = threading.Thread(target=incoming_message_listener, args=[connection, peer])

                peers[peer.peer_id] = {
                    "peer": peer,
                    "socket": connection,
                    "thread": incoming_message_thread,
                    "muted": False,
                    "wrote": False
                }

                incoming_message_thread.setDaemon(True)
                incoming_message_thread.start()
        except OSError:
            continue


def kick(peer_id):
    print("Kicking peer", peer_id)
    peers[peer_id]["socket"].shutdown(socket.SHUT_RDWR)
    peers[peer_id]["socket"].close()
    peers.pop(peer_id)


def mute(peer_id):
    print("Muting peer", peer_id)
    peers[peer_id]["muted"] = True


def unmute(peer_id):
    print("Unmuting peer", peer_id)
    peers[peer_id]["muted"] = False


sock.listen(max_connections)

incoming_connections_thread = threading.Thread(target=incoming_connections_listener)
incoming_connections_thread.setDaemon(True)
incoming_connections_thread.start()


# noinspection PyUnusedLocal
def exit_handler(sig, frame):
    print("\nGot exit signal")
    broadcast(
        Packet(
            action=ServiceAction(),
            message=Message(text="Server shutdown.")
        ), []
    )
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
