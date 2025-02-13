#!/usr/bin/env python3
import zmq
import socket
import logging
import json
import os
import uuid
import subprocess
import time

# Настройка логирования
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

ZMQ_PORT = 5555
UDP_PORT = 9998
CONFIG_FILE = "config.json"
CERTS_BASE_DIR = "certs"

def ensure_file_exists(file, default_content):
    if not os.path.exists(file):
        with open(file, "w") as f:
            json.dump(default_content, f, indent=4)

ensure_file_exists(CONFIG_FILE, {})

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Ошибка при загрузке конфигурации: {e}")
        return {}

def save_config(data):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Ошибка при сохранении конфигурации: {e}")

def create_certificates(client_id):
    cert_dir = os.path.join(CERTS_BASE_DIR, client_id)
    if not os.path.exists(cert_dir):
        os.makedirs(cert_dir)
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-out", os.path.join(cert_dir, "private_key.pem")])
        subprocess.run(["openssl", "rsa", "-in", os.path.join(cert_dir, "private_key.pem"),
                        "-pubout", "-out", os.path.join(cert_dir, "public_key.pem")])
        logging.info(f"Сертификаты для клиента {client_id} успешно сгенерированы.")
    return cert_dir

def get_client_info():
    config = load_config()
    if "client_id" not in config:
        config["client_id"] = str(uuid.uuid4())
        config["hostname"] = socket.gethostname()
        try:
            config["ip"] = socket.gethostbyname(config["hostname"])
        except Exception:
            config["ip"] = "127.0.0.1"
        config["group"] = config.get("group", "default")
        config["certificates"] = create_certificates(config["client_id"])
        save_config(config)
    else:
        if "group" not in config:
            config["group"] = "default"
            save_config(config)
    return config

def discover_server():
    """Автообнаружение сервера через UDP (блокирующий вариант)"""
    logging.info("Запуск автообнаружения сервера через UDP...")
    broadcast_address = "255.255.255.255"
    timeout = 5
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp_socket.settimeout(timeout)
    try:
        udp_socket.sendto(b"DISCOVER", (broadcast_address, UDP_PORT))
        logging.info(f"Отправлен DISCOVER-запрос на порт {UDP_PORT}")
        data, addr = udp_socket.recvfrom(1024)
        if data.decode().strip() == "ACK":
            server_ip = addr[0]
            config = load_config()
            config["server_ip"] = server_ip
            config["port"] = ZMQ_PORT
            save_config(config)
            return server_ip
    except Exception as e:
        logging.error(f"Ошибка в автообнаружении: {e}")
    return None

def execute_command(command):
    """
    Выполняет команду и возвращает результат выполнения.
    Если команда начинается с 'shell:', то выполняется интерактивно с построчным выводом.
    """
    try:
        if command.startswith("shell:"):
            cmd = command[len("shell:"):].strip()
            logging.info(f"Запуск интерактивного режима для команды: {cmd}")
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout_lines = []
            # Читаем вывод построчно и логируем его
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                stdout_lines.append(line)
                logging.info(f"Вывод: {line.strip()}")
            # Читаем оставшийся вывод ошибок
            stderr_output = proc.stderr.read()
            proc.wait()
            return {"stdout": "".join(stdout_lines), "stderr": stderr_output, "returncode": proc.returncode}
        else:
            result = subprocess.run(command, shell=True, text=True, capture_output=True)
            return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
    except Exception as e:
        return {"error": str(e)}

def client():
    context = zmq.Context()
    # Используем DEALER-сокет, чтобы клиент мог получать сообщения от сервера в любое время
    dealer = context.socket(zmq.DEALER)
    client_info = get_client_info()
    identity = client_info["client_id"]
    dealer.setsockopt_string(zmq.IDENTITY, identity)
    # Подключаемся к серверу (если IP сервера не указан, пробуем автообнаружение)
    config = load_config()
    server_ip = config.get("server_ip") or discover_server()
    if not server_ip:
        logging.critical("Сервер не обнаружен!")
        return 
    dealer.connect(f"tcp://{server_ip}:{ZMQ_PORT}")
    logging.info(f"Клиент {identity} подключён к серверу {server_ip}:{ZMQ_PORT}")

    # Отправляем сообщение регистрации
    reg_msg = {"type": "register", "data": client_info}
    dealer.send_json(reg_msg)
    reg_reply_parts = dealer.recv_multipart()
    if len(reg_reply_parts) >= 1:
        reg_reply = json.loads(reg_reply_parts[-1].decode())
        logging.info(f"Ответ сервера на регистрацию: {reg_reply}")
    else:
        logging.error("Неверный ответ на регистрацию.")

    # Основной цикл: ожидание сообщений от сервера
    while True:
        if dealer.poll(5000):  # ждем до 5 секунд сообщения
            msg_parts = dealer.recv_multipart()
            if len(msg_parts) >= 1:
                msg = json.loads(msg_parts[-1].decode())
                if "command" in msg:
                    cmd = msg["command"]
                    logging.info(f"Получена команда для выполнения: {cmd}")
                    result = execute_command(cmd)
                    res_msg = {"type": "command_result", "data": result, "command": cmd}
                    dealer.send_json(res_msg)
                    ack_parts = dealer.recv_multipart()
                    if len(ack_parts) >= 1:
                        ack = json.loads(ack_parts[-1].decode())
                        logging.info(f"Подтверждение сервера: {ack}")
                    else:
                        logging.warning("Неверный формат подтверждения от сервера.")
                else:
                    logging.info(f"Сообщение от сервера: {msg}")
            else:
                logging.warning("Получено некорректное сообщение от сервера.")
        else:
            # Если сообщений нет, отправляем пинг для поддержания связи
            dealer.send_json({"type": "ping"})
            ping_reply_parts = dealer.recv_multipart()
            if len(ping_reply_parts) >= 1:
                ping_reply = json.loads(ping_reply_parts[-1].decode())
                logging.info(f"Ответ на пинг: {ping_reply}")
            else:
                logging.warning("Нет ответа на пинг.")
        time.sleep(1)

if __name__ == "__main__":
    try:
        client()
    except KeyboardInterrupt:
        logging.info("Клиент завершил работу.")
