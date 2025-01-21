import socket
import logging
import sys
import json

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

TCP_PORT = 9998  # Порт сервера для команд
clients_data_file = 'clients/clients.json'  # Файл для сохранения зарегистрированных клиентов

def load_registered_clients():
    """Загружает данные о зарегистрированных клиентах из файла"""
    try:
        with open(clients_data_file, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def send_command_to_client(client_id, command):
    """Отправляет команду на клиента"""
    registered_clients = load_registered_clients()
    
    if client_id not in registered_clients:
        logging.error(f"Клиент с ID {client_id} не зарегистрирован.")
        return

    client_address = registered_clients[client_id]

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as command_socket:
            command_socket.connect((client_address[0], TCP_PORT))
            # Отправка команды
            command_socket.sendall(f"{client_id} {command}".encode())
            response = command_socket.recv(1024).decode()
            logging.info(f"Ответ от сервера: {response}")
    except Exception as e:
        logging.error(f"Ошибка при отправке команды клиенту {client_id}: {e}")

def main():
    if len(sys.argv) != 3:
        logging.error("Использование: fluxops <client_id> <command>")
        sys.exit(1)
    
    client_id = sys.argv[1]
    command = sys.argv[2]
    
    send_command_to_client(client_id, command)

if __name__ == "__main__":
    main()
