import base64
import configparser
import uuid
import ollama
import asyncio

import websocket
import websockets
import json
import urllib.request
import urllib.parse

from threading import Lock
from typing import Any, Optional, List
from chromadb import PersistentClient # noqa
from chromadb.errors import UniqueConstraintError
from colorama import Fore
from urllib import request

config = configparser.ConfigParser()
config.read("app.ini")

DEBUG_MODE = config.getboolean("DEFAULT", "debug")
DB_PATH = config.get("database", "path")
DB_TABLE = config.get("database", "table")
MODEL_EMBEDDINGS = config.get("model", "embeddings")
WEBSOCKET_HOST = config.get('websocket', 'host')
WEBSOCKET_PORT = config.getint('websocket', 'port')
CHARACTERS = json.loads(config.get("characters", "character_list"))
COMFYUI_CLIENT_ID = str(uuid.uuid4())
COMFYUI_SERVER_ADDRESS = "127.0.0.1:8188"

COMFYUI_WORKFLOW_RAW = """
{
  "6": {
    "inputs": {
      "text": "Depiction of Marcus Aurelius in contemplative pose, with ancient Roman toga draped elegantly around his figure, gazing inward as the phrase 'You have power over your mind - not outside events' etched into stone pillars behind him, softly illuminated by warm candlelight.",
      "clip": [
        "39",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Positive Prompt)"
    }
  },
  "8": {
    "inputs": {
      "samples": [
        "31",
        0
      ],
      "vae": [
        "38",
        0
      ]
    },
    "class_type": "VAEDecode",
    "_meta": {
      "title": "VAE Decode"
    }
  },
  "9": {
    "inputs": {
      "filename_prefix": "ComfyUI",
      "images": [
        "8",
        0
      ]
    },
    "class_type": "SaveImage",
    "_meta": {
      "title": "Save Image"
    }
  },
  "27": {
    "inputs": {
      "width": 1024,
      "height": 1024,
      "batch_size": 1
    },
    "class_type": "EmptySD3LatentImage",
    "_meta": {
      "title": "EmptySD3LatentImage"
    }
  },
  "31": {
    "inputs": {
      "seed": 306387459944462,
      "steps": 20,
      "cfg": 1,
      "sampler_name": "euler",
      "scheduler": "simple",
      "denoise": 1,
      "model": [
        "37",
        0
      ],
      "positive": [
        "35",
        0
      ],
      "negative": [
        "40",
        0
      ],
      "latent_image": [
        "27",
        0
      ]
    },
    "class_type": "KSampler",
    "_meta": {
      "title": "KSampler"
    }
  },
  "35": {
    "inputs": {
      "guidance": 1.5,
      "conditioning": [
        "6",
        0
      ]
    },
    "class_type": "FluxGuidance",
    "_meta": {
      "title": "FluxGuidance"
    }
  },
  "37": {
    "inputs": {
      "unet_name": "flux1-dev-fp8.safetensors",
      "weight_dtype": "fp8_e4m3fn"
    },
    "class_type": "UNETLoader",
    "_meta": {
      "title": "Load Diffusion Model"
    }
  },
  "38": {
    "inputs": {
      "vae_name": "FLUX/ae.safetensors"
    },
    "class_type": "VAELoader",
    "_meta": {
      "title": "Load VAE"
    }
  },
  "39": {
    "inputs": {
      "clip_name1": "t5xxl_fp8_e4m3fn.safetensors",
      "clip_name2": "clip_l.safetensors",
      "type": "flux"
    },
    "class_type": "DualCLIPLoader",
    "_meta": {
      "title": "DualCLIPLoader"
    }
  },
  "40": {
    "inputs": {
      "text": "ignored",
      "clip": [
        "39",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Prompt)"
    }
  }
}
"""


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

        response_text = response["message"]["content"]

        print(f"Response Text: { response_text }")

        image_comfyui = handle_image_creation(response_text) if character["name"] == "Zoe Voss" else None

        return WebSocketMessageHandler.prepare_response_append_character(response, character, image_comfyui)

    def parse_received_message(self):
        return json.loads(self.message_received)

    @staticmethod
    def prepare_response_append_character(response: Any, character: Any, additional: Any):
        prepared_websocket_response = response.model_dump()
        prepared_websocket_response["character"] = character
        prepared_websocket_response["additional"] = additional
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

def handle_image_creation(prompt):

    comfyui_workflow = json.loads(COMFYUI_WORKFLOW_RAW)
    comfyui_workflow["6"]["inputs"]["text"] = prompt
    image_comfyui = None

    try:

        ws_comfyui = websocket.WebSocket()
        ws_comfyui.connect("ws://{}/ws?clientId={}".format(COMFYUI_SERVER_ADDRESS, COMFYUI_CLIENT_ID))
        images_comfyui = comfyui_get_images(ws_comfyui, comfyui_workflow)
        ws_comfyui.close()

        for node_id in images_comfyui:
            if len(images_comfyui[node_id]) > 0:
                image_comfyui = base64.b64encode(images_comfyui[node_id][0]).decode("utf-8")
                image_comfyui = f"data:image/png;base64,{image_comfyui}"

    except Exception as e:
        print(e)

    return image_comfyui

def comfyui_queue_prompt(prompt):
    p = { "prompt": prompt, "client_id": COMFYUI_CLIENT_ID }
    data = json.dumps(p).encode("utf-8")
    # print(data)
    req = urllib.request.Request("http://{}/prompt".format(COMFYUI_SERVER_ADDRESS), data=data)
    return json.loads(urllib.request.urlopen(req).read())

def comfyui_get_image(filename, subfolder, folder_type):
    data = {"filename": filename, "subfolder": subfolder, "type": folder_type}
    url_values = urllib.parse.urlencode(data)
    with urllib.request.urlopen("http://{}/view?{}".format(COMFYUI_SERVER_ADDRESS, url_values)) as response:
        return response.read()

def comfyui_get_history(prompt_id):
    with urllib.request.urlopen("http://{}/history/{}".format(COMFYUI_SERVER_ADDRESS, prompt_id)) as response:
        return json.loads(response.read())

def comfyui_get_images(ws, prompt):

    prompt_id = comfyui_queue_prompt(prompt)['prompt_id']
    output_images = { }

    while True:
        out = ws.recv()
        if isinstance(out, str):
            message = json.loads(out)
            if message['type'] == 'executing':
                data = message['data']
                if data['node'] is None and data['prompt_id'] == prompt_id:
                    break
        else:
            continue

    history = comfyui_get_history(prompt_id)[prompt_id]

    for node_id in history['outputs']:
        node_output = history['outputs'][node_id]
        images_output = []
        if 'images' in node_output:
            for image in node_output['images']:
                image_data = comfyui_get_image(image['filename'], image['subfolder'], image['type'])
                images_output.append(image_data)
        output_images[node_id] = images_output

    return output_images

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


