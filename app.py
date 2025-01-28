
import configparser
import uuid
import ollama
import asyncio
import websockets
import json

from threading import Lock
from typing import Any, Optional, List
from chromadb import PersistentClient # noqa
from chromadb.errors import UniqueConstraintError
from colorama import Fore

config = configparser.ConfigParser()
config.read("app.ini")

DEBUG_MODE = config.getboolean("DEFAULT", "debug")
DB_PATH = config.get("database", "path")
DB_TABLE = config.get("database", "table")
MODEL_EMBEDDINGS = config.get("model", "embeddings")
WEBSOCKET_HOST = config.get('websocket', 'host')
WEBSOCKET_PORT = config.getint('websocket', 'port')
CHARACTERS = json.loads(config.get("characters", "character_list"))

class ConversationByCharacterId:
    _instance = None
    _lock = Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.conversations = { }
            return cls._instance
    def add_message(self, key: int, message: Any):
        if key not in self.conversations:
            self.conversations[key] = [ ]
        self.conversations[key].append(message)
    def get_messages(self, key: int) -> Optional[List[Any]]:
        return self.conversations.get(key, None)
    def clear(self, key: int):
        if key in self.conversations:
            self.conversations[key] = [ ]

class Database:
    def __init__(self, db_path: str, db_table: str, model_name: str):
        self.db_path = db_path
        self.db_table = db_table
        self.db_client = PersistentClient(path=db_path + model_name)
    def create_table_if_not_exists(self):
        try:
            self.db_client.create_collection(name=self.db_table)
            print(Fore.LIGHTWHITE_EX + "DB for character created.")
        except UniqueConstraintError:
            return True
    def update_database(self, prompt: str, response_llm: str):
        serialized_conversation = f"prompt: { prompt } response: { response_llm }"
        Logger.log("Conversation to store in DB: ", serialized_conversation)
        embeddings = Embeddings(MODEL_EMBEDDINGS)
        response_embeddings = embeddings.create_embeddings(serialized_conversation)
        self.add(response_embeddings, serialized_conversation)
    def add(self, embedding: Any, serialized_conversation: str):
        self.create_table_if_not_exists()
        table = self.db_client.get_collection(name=self.db_table)
        entry_id = str(uuid.uuid4())
        Logger.log("Add to DB: ", entry_id, serialized_conversation, embedding)
        table.add(
            ids=[entry_id],
            embeddings=[embedding],
            documents=[serialized_conversation]
        )
        Logger.log("All DB entries: ", table.get())
    def query_embeddings(self, prompt_embedding: Any):
        table = self.db_client.get_collection(name=self.db_table)
        return table.query(query_embeddings=[prompt_embedding], n_results=1)

class Embeddings:
    def __init__(self, model_embeddings: str):
        self.model_embeddings = model_embeddings
    def create_embeddings(self, serialized_conversation: str):
        embeddings = ollama.embeddings(model=self.model_embeddings, prompt=serialized_conversation)
        return embeddings["embedding"]
    def retrieve_embeddings(self, prompt: str, character_id: int):
        response_embeddings = ollama.embeddings(model=self.model_embeddings, prompt=prompt)
        prompt_embedding = response_embeddings["embedding"]
        Logger.log("Prompt as Embedding: ", prompt_embedding)
        db_client = Database(DB_PATH, DB_TABLE, CHARACTERS[character_id]["model"])
        results = db_client.query_embeddings(prompt_embedding)
        Logger.log("Received Embeddings from DB: ", results)
        return results["documents"][0][0] if len(results["documents"]) > 0 and len(results["documents"][0]) > 0 else ""

class Chat:
    def __init__(self, model: str, character_id: int):
        self.model = model
        self.character_id = character_id
        self.conversation = ConversationByCharacterId()
    def send_to_model_and_receive_response(self, prompt_with_context: str, use_history: bool):
        if not use_history:
            Logger.log("Clear Session History.")
            self.conversation.clear(self.character_id)
        self.conversation.add_message(self.character_id, { "role": "user", "content": prompt_with_context })
        session_conversation = self.conversation.get_messages(self.character_id)
        Logger.log("Session Conversation: ", session_conversation)
        response = ollama.chat(model=self.model, messages=session_conversation, stream=False)
        response_text = response["message"]["content"]
        self.conversation.add_message(self.character_id, { "role": "assistant", "content": response_text })
        return response

class WebSocketMessageHandler:
    def __init__(self, message_received: str):
        self.message_received = message_received
    def handle(self):
        prompt_parsed = self.parse_received_message()
        print(Fore.LIGHTBLUE_EX + "Received prompt: ", prompt_parsed)

        character_id = prompt_parsed["characterId"]
        prompt_received = prompt_parsed["prompt"]
        is_auto_mode = prompt_parsed["isAutoMode"]
        character = CHARACTERS[character_id]

        db_client = Database(DB_PATH, DB_TABLE, character["model"])
        db_client.create_table_if_not_exists()

        use_embeddings = character["embeddings"] and not is_auto_mode

        if use_embeddings:
            embeddings = Embeddings(MODEL_EMBEDDINGS)
            context = embeddings.retrieve_embeddings(prompt_received, character_id)
            Logger.log("Context received: ", context)
        else:
            context = ""

        chat = Chat(CHARACTERS[character_id]["model"], character_id)
        prompt_with_context = f"prompt: { prompt_received } \ncontext: { context }"
        response = chat.send_to_model_and_receive_response(prompt_with_context, not is_auto_mode)

        if use_embeddings:
            db_client.update_database(prompt_received, response["message"]["content"])

        return WebSocketMessageHandler.prepare_response_append_character(response, character)

    def parse_received_message(self):
        return json.loads(self.message_received)

    @staticmethod
    def prepare_response_append_character(response: Any, character: Any):
        prepared_websocket_response = response.model_dump()
        prepared_websocket_response["character"] = character
        return prepared_websocket_response

class MiniWebSocket:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.websocket_client = None
    async def start_websocket_server(self):
        await websockets.serve(self.client_connected, self.host, self.port)
        print(Fore.GREEN + f"WebSocket started at ws://{ self.host }:{ self.port }, you can now open index.html in your browser.")
    async def send_websocket_message(self, message: str):
        try:
            await self.websocket_client.send(message)
        except Exception as e:
            print(Fore.LIGHTRED_EX + f"Websocket Error: { e }")
    async def client_connected(self, client: Any):
        print(Fore.GREEN + "Websocket client connected.")
        self.websocket_client = client

        try:
            while True:
                message_received = await client.recv()
                Logger.log("Received", message_received)
                message_handler = WebSocketMessageHandler(message_received)
                prepared_websocket_response = message_handler.handle()
                await self.send_websocket_message(json.dumps(prepared_websocket_response))
        except websockets.exceptions.ConnectionClosedOK as e:
            print(Fore.GREEN + f"Websocket client closed: { e }")

class Logger:
    @staticmethod
    def log(*args, **kwargs):
        if DEBUG_MODE:
            print(Fore.LIGHTBLACK_EX + "[DEBUG] ", *args, **kwargs)

if __name__ == '__main__':
    miniWebSocket = MiniWebSocket(WEBSOCKET_HOST, WEBSOCKET_PORT)
    event_loop = asyncio.get_event_loop()
    event_loop.run_until_complete(miniWebSocket.start_websocket_server())
    event_loop.run_forever()
