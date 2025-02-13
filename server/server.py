#!/usr/bin/env python3
import zmq
import socket
import logging
import json
import os
import threading
import time
from datetime import datetime
import sys

# Настройка логирования
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

# Конфигурация портов и файлов
ZMQ_PORT = 5555            # порт для связи с клиентами
UDP_PORT = 9998            # порт для автообнаружения
TCP_COMMAND_PORT = 9997    # порт для внешнего командного интерфейса
CLIENTS_FILE = "clients.json"
COMMAND_HISTORY_FILE = "command_history.json"

# Словарь зарегистрированных клиентов
registered_clients = {}

##############################################
# Работа с файлами и историей команд
##############################################
def load_json_file(filename):
    """Безопасно загружает данные из JSON файла."""
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Ошибка загрузки {filename}: {e}")
    return {}

def save_json_file(filename, data):
    """Сохраняет данные в JSON файл."""
    try:
        with open(filename, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Ошибка сохранения {filename}: {e}")

def save_command_history(command, client_id, result):
    history = load_json_file(COMMAND_HISTORY_FILE)
    if not isinstance(history, list):
        history = []
    history.append({
        "timestamp": datetime.now().isoformat(),
        "client_id": client_id,
        "command": command,
        "result": result
    })
    save_json_file(COMMAND_HISTORY_FILE, history)

##############################################
# UDP автообнаружение
##############################################
def udp_discovery():
    """Запускает UDP-сервер автообнаружения, отвечающий на DISCOVER-запросы."""
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        udp_socket.bind(("", UDP_PORT))
        logging.info(f"UDP сервер автообнаружения слушает на порту {UDP_PORT}")
        while True:
            try:
                data, addr = udp_socket.recvfrom(1024)
                if data.decode().strip() == "DISCOVER":
                    logging.info(f"Получен DISCOVER-запрос от {addr}, отправляем ACK")
                    udp_socket.sendto(b"ACK", addr)
            except Exception as e:
                logging.error(f"Ошибка в UDP сервере: {e}")
    finally:
        udp_socket.close()

##############################################
# Обработка сообщений с ROUTER-сокета
##############################################
def process_router_message(parts):
    """Обрабатывает входящее сообщение от клиента через ROUTER-сокет."""
    if len(parts) < 2:
        logging.warning("Получено некорректное сообщение.")
        return None, None
    try:
        identity = parts[0].decode()
        msg_text = parts[-1].decode()
        msg = json.loads(msg_text)
    except Exception as e:
        logging.error(f"Ошибка обработки сообщения: {e}")
        msg = {"text": msg_text}
    return identity, msg

##############################################
# Обработка внешних команд (через TCP_COMMAND_PORT)
##############################################
def process_command_interface(command_socket, router_socket):
    """
    Обрабатывает внешнюю команду, поступившую через командный интерфейс.
    """
    try:
        cmd_msg = command_socket.recv_json()
        client_id = cmd_msg.get("client_id")
        command = cmd_msg.get("command")
        if not client_id or not command:
            return {"status": "error", "message": "client_id and command are required"}
        if client_id not in registered_clients:
            return {"status": "error", "message": f"Клиент {client_id} не найден"}

        identity = registered_clients[client_id]["identity"]
        msg = {"type": "command", "command": command}
        router_socket.send_multipart([identity.encode(), b'', json.dumps(msg).encode()])
        logging.info(f"Отправлена команда клиенту {client_id} ({identity}): {command}")

        # Ожидаем ответ от клиента
        reply_parts = router_socket.recv_multipart()
        if len(reply_parts) >= 2:
            reply = json.loads(reply_parts[-1].decode())
        else:
            reply = {}

        save_command_history(command, client_id, reply)
        return {"status": "success", "reply": reply}
    except Exception as e:
        logging.error(f"Ошибка обработки команды: {e}")
        return {"status": "error", "message": str(e)}

def send_external_command(client_id, command):
    """Отправка команды клиенту через REQ-сокет."""
    context = zmq.Context()
    socket_req = context.socket(zmq.REQ)
    socket_req.connect(f"tcp://localhost:{TCP_COMMAND_PORT}")
    msg = {"client_id": client_id, "command": command}
    socket_req.send_json(msg)
    reply = socket_req.recv_json()
    print("Ответ от сервера:", reply)
    socket_req.close()
    context.term()

##############################################
# Основной сервер
##############################################
def server():
    context = zmq.Context()
    router = context.socket(zmq.ROUTER)
    router.bind(f"tcp://*:{ZMQ_PORT}")
    logging.info(f"ZeroMQ сервер (ROUTER) запущен на порту {ZMQ_PORT}")

    command_socket = context.socket(zmq.REP)
    command_socket.bind(f"tcp://*:{TCP_COMMAND_PORT}")
    logging.info(f"Командный интерфейс запущен на порту {TCP_COMMAND_PORT}")

    threading.Thread(target=udp_discovery, daemon=True).start()

    poller = zmq.Poller()
    poller.register(router, zmq.POLLIN)
    poller.register(command_socket, zmq.POLLIN)

    while True:
        try:
            socks = dict(poller.poll(1000))  # Исправленный таймаут
            if router in socks and socks[router] == zmq.POLLIN:
                parts = router.recv_multipart()
                identity, msg = process_router_message(parts)
                if identity is None:
                    continue
                msg_type = msg.get("type")
                if msg_type == "register":
                    client_id = msg.get("data", {}).get("client_id", identity)
                    registered_clients[client_id] = {"identity": identity}
                    reply = {"status": "registered"}
                    router.send_multipart([identity.encode(), b'', json.dumps(reply).encode()])
                    logging.info(f"Клиент зарегистрирован: {client_id} ({identity})")
                elif msg_type == "ping":
                    router.send_multipart([identity.encode(), b'', json.dumps({"status": "alive"}).encode()])
                elif msg_type == "command_result":
                    logging.info(f"Результат команды от {identity}: {msg.get('data')}")
                else:
                    logging.info(f"Неизвестное сообщение от {identity}: {msg}")

            if command_socket in socks and socks[command_socket] == zmq.POLLIN:
                reply = process_command_interface(command_socket, router)
                command_socket.send_json(reply)
        except Exception as e:
            logging.error(f"Ошибка в сервере: {e}")
            time.sleep(1)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "fluxops":
        send_external_command(sys.argv[2], " ".join(sys.argv[3:]))
    else:
        try:
            server()
        except KeyboardInterrupt:
            logging.info("Сервер завершил работу.")
