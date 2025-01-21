import socket
import logging
import json
import time
import os
import platform
import subprocess

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

UDP_PORT = 9999
SERVER_ADDRESS = ('255.255.255.255', UDP_PORT)
CONFIG_FILE = 'config.json'

# Флаг регистрации
registered = False
COMMAND_PORT = 9997
CLIENT_ID = platform.node()  # Используем hostname клиента в качестве ID

def save_server_info(server_ip, server_port):
    """Сохранить информацию о сервере в config.json."""
    config_data = {
        "server_ip": server_ip,
        "server_port": server_port,
        "client_hostname": CLIENT_ID  # Сохраняем hostname клиента
    }
    with open(CONFIG_FILE, "w") as config_file:
        json.dump(config_data, config_file, indent=4)
    logging.info("Информация о сервере сохранена в config.json")

def load_server_info():
    """Загрузить информацию о сервере из config.json."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as config_file:
            try:
                return json.load(config_file)
            except json.JSONDecodeError:
                logging.warning("Ошибка при чтении файла конфигурации.")
    return None

def discover_server():
    global registered  # Используем глобальный флаг
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp_socket.sendto(b"DISCOVER", SERVER_ADDRESS)
        logging.info("Отправлено широковещательное сообщение на 255.255.255.255:9999")
        try:
            data, server = udp_socket.recvfrom(1024)
            logging.info(f"Сервер найден: {server}")
            if data == b"ACK" and not registered:  # Проверяем, что не зарегистрированы
                # Подключаемся к серверу для регистрации
                register_to_server(server)
                registered = True  # Устанавливаем флаг регистрации в True
        except socket.timeout:
            logging.warning("Таймаут ожидания ответа от сервера.")
        time.sleep(1)

def register_to_server(server):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
        try:
            client_socket.connect((server[0], 9998))  # Подключение к серверу для регистрации
            # Чтение хостнейма из файла конфигурации
            server_info = load_server_info()
            if server_info and "client_hostname" in server_info:
                client_hostname = server_info["client_hostname"]
            else:
                client_hostname = CLIENT_ID  # Используем hostname клиента в случае отсутствия конфигурации

            message = json.dumps({"hostname": client_hostname})
            client_socket.sendall(message.encode())
            response = client_socket.recv(1024).decode()
            logging.info(f"Ответ от сервера: {response}")  # Логируем полный ответ от сервера
            if response == "REGISTERED":
                save_server_info(server[0], 9998)  # Сохраняем информацию о сервере в файл
                logging.info("Клиент успешно зарегистрирован на сервере.")
            else:
                logging.warning(f"Неожиданный ответ от сервера при регистрации: {response}")
        except Exception as e:
            logging.error(f"Ошибка при регистрации на сервере: {e}")

def listen_for_commands():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("", COMMAND_PORT))
        server_socket.listen(5)
        logging.info(f"Клиент слушает команды на порту {COMMAND_PORT}")

        while True:
            client_socket, addr = server_socket.accept()
            with client_socket:
                command = client_socket.recv(1024).decode()
                logging.info(f"Получена команда: {command}")
                try:
                    output = execute_command(command)
                    client_socket.sendall(output.encode())
                except Exception as e:
                    client_socket.sendall(f"Ошибка при выполнении команды: {str(e)}".encode())

def execute_command(command):
    try:
        # Разделяем команду на части
        command_parts = command.split()
        
        if len(command_parts) < 2:
            return "Неверная команда. Убедитесь, что указаны команда и аргументы."

        # Определяем клиентский ID (если он передан в команде)
        client_id = command_parts[0]  # Например, dc-1.rit.local
        cmd = command_parts[1]  # Команда (например, touch)
        args = command_parts[2:]  # Аргументы команды (например, 1.txt)

        # Если команда - touch, создаем файл
        if cmd == "touch":
            if len(args) < 1:
                return "Ошибка: требуется указать имя файла."
            filename = args[0]
            with open(filename, "w") as file:
                file.write("Создан файл")
            return f"Файл {filename} создан."

        # Если команда - ls, выполняем её с помощью subprocess
        elif cmd == "ls":
            result = subprocess.run(["ls"] + args, capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout
            else:
                return f"Ошибка при выполнении команды ls: {result.stderr}"

        # Если команда - cat, выполняем её с помощью subprocess
        elif cmd == "cat":
            if len(args) < 1:
                return "Ошибка: требуется указать файл для чтения."
            result = subprocess.run(["cat"] + args, capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout
            else:
                return f"Ошибка при выполнении команды cat: {result.stderr}"

        # Для других команд можно добавить дополнительные условия
        else:
            # Выполнение любой другой команды
            result = subprocess.run([cmd] + args, capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout
            else:
                return f"Ошибка при выполнении команды {cmd}: {result.stderr}"

    except Exception as e:
        return f"Не удалось выполнить команду: {str(e)}"

def monitor_connection():
    server_info = load_server_info()
    if not server_info:
        logging.warning("Информация о сервере отсутствует. Проверьте регистрацию.")
        return

    server_ip = server_info["server_ip"]
    server_port = server_info["server_port"]

    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
                client_socket.settimeout(5)
                client_socket.connect((server_ip, server_port))
                client_socket.sendall(b"HEARTBEAT")  # Отправляем heartbeat
                response = client_socket.recv(1024).decode()
                if response == "ALIVE":
                    logging.info("Соединение с сервером активно.")
                else:
                    logging.warning(f"Неожиданный ответ от сервера.")
        except socket.error:
            logging.warning("Соединение с сервером потеряно.")
        time.sleep(5)

if __name__ == "__main__":
    while not registered:
        discover_server()

    # Параллельно запускаем мониторинг соединения и прослушивание команд
    import threading
    threading.Thread(target=listen_for_commands, daemon=True).start()
    monitor_connection()
