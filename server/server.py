import socket
import logging
import json
import time
import os
import threading
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

UDP_PORT = 9999
CLIENTS_FILE = 'clients.json'
TIMEOUT = 10  # Таймаут для ожидания ответа
CONFIG_FILE = 'config.json'  # Файл с конфигурацией клиента

# Сет для хранения зарегистрированных клиентов
registered_clients = {}

# Функция для загрузки клиентов из файла
def load_clients():
    try:
        if os.path.exists(CLIENTS_FILE):  # Проверяем, существует ли файл
            with open(CLIENTS_FILE, 'r') as f:
                data = json.load(f)
                logging.info(f"Загружено {len(data)} клиентов из файла {CLIENTS_FILE}.")
                return data
        else:
            logging.warning(f"Файл {CLIENTS_FILE} не найден, создаем новый.")
            return {}
    except json.JSONDecodeError as e:
        logging.error(f"Ошибка в структуре JSON: {e}")
        return {}

# Функция для сохранения клиентов в файл
def save_clients():
    try:
        with open(CLIENTS_FILE, 'w') as f:
            json.dump(registered_clients, f, indent=4)
        logging.info(f"Список клиентов сохранен в файл {CLIENTS_FILE}.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении клиентов в файл: {e}")

# Регистрация клиента
def handle_registration(client_socket, client_address):
    try:
        data = client_socket.recv(1024).decode()
        logging.info(f"Получены данные от клиента: {data}")

        # Если это HEARTBEAT, ищем клиента в списке
        if data == "HEARTBEAT":
            client_hostname = get_client_hostname_by_address(client_address)  # Получаем хостнейм по адресу
            if client_hostname:
                logging.info(f"Получен heartbeat от клиента {client_hostname}")
                update_client_status(client_hostname, client_address)  # Обновляем статус клиента
            else:
                logging.warning(f"Неизвестный клиент по адресу {client_address}. Не могу обновить heartbeat.")
            return

        # Пытаемся разобрать как JSON для других данных
        try:
            data = json.loads(data)
            client_hostname = data.get("hostname")
        except json.JSONDecodeError as e:
            logging.error(f"Ошибка при разборе JSON: {e}")
            client_socket.sendall(b"ERROR")
            return

        if not client_hostname:
            logging.error("Хостнейм не получен!")
            client_socket.sendall(b"ERROR")
            return

        # Регистрируем клиента, если он новый
        if client_hostname not in registered_clients:
            registered_clients[client_hostname] = {
                "address": client_address,
                "command_port": client_address[1],
                "last_active": time.time(),
                "status": "active",
                "hostname": client_hostname
            }
            client_socket.sendall(b"REGISTERED")
            logging.info(f"Неизвестный клиент {client_hostname}, регистрируем...")
        else:
            # Обновляем статус для существующего клиента
            update_client_status(client_hostname, client_address)
            logging.info(f"Клиент {client_hostname} обновлен.")

        save_clients()  # Сохраняем обновленные данные клиентов

    except Exception as e:
        logging.error(f"Ошибка при обработке регистрации: {e}")
        client_socket.sendall(b"ERROR")
    finally:
        client_socket.close()

def get_client_hostname_by_address(client_address):
    """Получаем хостнейм клиента по его IP-адресу"""
    for hostname, client_info in registered_clients.items():
        if client_info["address"][0] == client_address[0]:  # Сравниваем только IP
            return hostname
    return None

def update_client_status(client_hostname, client_address=None):
    """Обновляем статус клиента"""
    registered_clients[client_hostname]['last_active'] = time.time()
    registered_clients[client_hostname]['status'] = "active"
    if client_address:
        registered_clients[client_hostname]['address'] = client_address
    logging.info(f"Статус клиента {client_hostname} обновлен.")

# Функция для запроса данных у клиента
def request_data(client_socket):
    try:
        logging.info(f"Запрос данных у клиента...")
        client_socket.sendall(b"REQUEST_DATA")  # Отправляем запрос на получение данных
        
        # Получаем данные от клиента
        data = client_socket.recv(1024).decode()
        logging.info(f"Получены данные от клиента: {data}")
        
        if data:
            # Обрабатываем полученные данные
            client_hostname = load_client_hostname()
            if client_hostname in registered_clients:
                registered_clients[client_hostname]["data"] = data  # Сохраняем данные клиента
                logging.info(f"Данные клиента {client_hostname} сохранены.")
            else:
                logging.error(f"Клиент {client_hostname} не зарегистрирован.")
        
        save_clients()  # Сохраняем обновленные данные

    except Exception as e:
        logging.error(f"Ошибка при запросе данных у клиента: {e}")
        client_socket.sendall(b"ERROR")  # Отправляем ошибку клиенту
    finally:
        client_socket.close()  # Закрытие сокета

# Функция для обновления состояния клиентов
def update_all_clients_status():
    while True:
        current_time = time.time()
        
        # Проверка активности клиентов и обновление их статуса
        inactive_clients = [
            client_id for client_id, client_data in registered_clients.items()
            if current_time - client_data['last_active'] > TIMEOUT
        ]
        for client_id in inactive_clients:
            logging.warning(f"Клиент {client_id} неактивен.")
            # Вместо удаления клиента, просто отметим его как неактивного
            registered_clients[client_id]['status'] = 'inactive'
        
        # Проверка всех клиентов на их статус
        for client_id, client_info in list(registered_clients.items()):
            if client_info['status'] == 'inactive' and current_time - client_info['last_active'] <= TIMEOUT:
                # Если клиент снова активен, обновляем статус на "active"
                registered_clients[client_id]['status'] = 'active'
                logging.info(f"Клиент {client_id} снова активен.")

        save_clients()  # Сохранение обновленного списка клиентов
        time.sleep(10)  # Интервал проверки активности клиентов

# UDP сервер для обнаружения
def udp_server():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.bind(("", UDP_PORT))
        udp_socket.settimeout(TIMEOUT)  # Устанавливаем таймаут для ожидания ответа
        logging.info(f"UDP сервер слушает на порту {UDP_PORT}")

        while True:
            try:
                data, address = udp_socket.recvfrom(1024)
                if data.decode() == "DISCOVER":
                    udp_socket.sendto(b"ACK", address)
            except socket.timeout:
                pass

def load_client_hostname():
    """Загружает хостнейм клиента из файла config.json."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as config_file:
            config_data = json.load(config_file)
            client_hostname = config_data.get("client_hostname", "Unknown")
            logging.info(f"Загружен хостнейм клиента: {client_hostname}")
            return client_hostname
    else:
        logging.warning(f"Файл {CONFIG_FILE} не найден!")
    return "Unknown"

# TCP сервер для управления подключениями
def tcp_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("", 9998))
        server_socket.listen(5)
        logging.info(f"TCP сервер запущен на порту 9998")

        while True:
            client_socket, addr = server_socket.accept()
            logging.info(f"Подключение установлено от {addr}")
            try:
                handle_registration(client_socket, addr)
            except Exception as e:
                logging.error(f"Ошибка при обработке подключения: {e}")
                client_socket.close()

# Запуск сервера
if __name__ == "__main__":
    registered_clients = load_clients()

    threading.Thread(target=udp_server, daemon=True).start()
    threading.Thread(target=tcp_server, daemon=True).start()
    threading.Thread(target=update_all_clients_status, daemon=True).start()

    while True:
        time.sleep(1)  # Основной поток не завершится
