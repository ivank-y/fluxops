import logging
import socket
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
from typing import List
from fastapi.middleware.cors import CORSMiddleware

# Создаем экземпляр приложения FastAPI
app = FastAPI()

# Разрешаем доступ с фронтенда (localhost:3000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Разрешить доступ с фронтенда
    allow_credentials=True,
    allow_methods=["*"],  # Разрешить все HTTP-методы
    allow_headers=["*"],  # Разрешить все заголовки
)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Модель для обновления информации о клиенте
class ClientUpdate(BaseModel):
    address: str
    status: str
    hostname: str

# Путь к файлу
CLIENTS_FILE = "server/clients.json"

# Загружаем клиентов из clients.json
def load_clients():
    try:
        with open(CLIENTS_FILE, "r") as file:
            clients_data = json.load(file)
        return clients_data
    except FileNotFoundError:
        logger.error("Clients file not found")
        raise HTTPException(status_code=404, detail="Clients file not found")
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding clients file: {e}")
        raise HTTPException(status_code=500, detail=f"Error decoding clients file: {e}")

# Сохраняем изменения в clients.json
def save_clients(clients_data):
    try:
        with open(CLIENTS_FILE, "w") as file:
            json.dump(clients_data, file, indent=4)
        logger.info(f"Сохранены данные клиентов в {CLIENTS_FILE}")
    except Exception as e:
        logger.error(f"Error saving clients file: {e}")
        raise HTTPException(status_code=500, detail=f"Error saving clients file: {e}")

# Список для хранения подключенных WebSocket-клиентов
connected_clients: List[WebSocket] = []

# Уведомление всем подключенным клиентам через WebSocket
async def notify_clients(message: str):
    for connection in connected_clients:
        try:
            await connection.send_text(message)
        except Exception as e:
            logger.error(f"Error sending message to connected client: {e}")
            connected_clients.remove(connection)

@app.get("/api/clients")
def get_clients():
    clients_data = load_clients()
    clients = [{"id": key, "address": value["address"][0]} for key, value in clients_data.items()]
    return clients

@app.get("/api/active_clients")
def get_active_clients():
    clients_data = load_clients()
    active_clients = [{"id": key, "address": value["address"][0]} for key, value in clients_data.items() if value["status"] == "active"]
    return active_clients

@app.get("/api/connected_clients")
def get_connected_clients():
    return connected_clients

@app.post("/api/update_client/{client_id}")
async def update_client(client_id: str, client_update: ClientUpdate):
    logger.info(f"Received update for client {client_id}: {client_update}")
    clients_data = load_clients()
    if client_id not in clients_data:
        raise HTTPException(status_code=404, detail="Client not found")

    # Создаем новый ключ для клиента (новое имя)
    new_client_id = client_update.hostname  # Мы используем новое имя как новый идентификатор

    if new_client_id in clients_data:
        raise HTTPException(status_code=400, detail="Client with this hostname already exists")

    # Обновляем данные клиента
    client_data = clients_data.pop(client_id)
    client_data["address"] = [client_update.address, client_data["address"][1]]
    client_data["status"] = client_update.status
    client_data["hostname"] = client_update.hostname

    # Сохраняем данные с новым ключом
    clients_data[new_client_id] = client_data

    save_clients(clients_data)
    
    # Уведомляем всех подключенных клиентов о изменении
    await notify_clients(f"Client {client_id} updated to {new_client_id} successfully")

    return {"message": f"Client {client_id} updated to {new_client_id} successfully"}

@app.delete("/api/delete_client/{client_id}")
async def delete_client(client_id: str):
    clients_data = load_clients()
    if client_id not in clients_data:
        raise HTTPException(status_code=404, detail="Client not found")

    # Удаляем клиента
    del clients_data[client_id]
    save_clients(clients_data)
    
    # Уведомляем всех подключенных клиентов о удалении
    await notify_clients(f"Client {client_id} deleted successfully")

    return {"message": f"Client {client_id} deleted successfully"}

@app.websocket("/ws/connect")
async def websocket_endpoint(websocket: WebSocket):
    client_id = None  # Определяем переменную client_id заранее, чтобы использовать ее в блоке except
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        client_id = await websocket.receive_text()  # Получаем ID от клиента
        logger.info(f"Client {client_id} connected via WebSocket")
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
        logger.info(f"Client {client_id} disconnected")

class CommandRequest(BaseModel):
    command: str

@app.post("/api/send_command/{client_id}")
async def send_command(client_id: str, command_request: CommandRequest):
    command = command_request.command  # Теперь получаем команду через объект
    registered_clients = load_clients()

    if client_id not in registered_clients:
        raise HTTPException(status_code=404, detail="Client not found")

    client_address = registered_clients[client_id]["address"][0]  # Получаем IP-адрес клиента

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as command_socket:
            command_socket.connect((client_address, 9997))  # Изменено на порт 9997
            # Отправляем команду
            command_socket.sendall(f"{client_id} {command}".encode())
            response = command_socket.recv(1024).decode()
            return {"message": "Command sent successfully", "response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending command to client {client_id}: {e}")
