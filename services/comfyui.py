#!/usr/bin/env python3
""" comfyui.py service to talk to ComfyUI, request an Image """

import base64
import uuid
import json
import websocket
import urllib.request
import urllib.parse


_COMFYUI_CLIENT_ID = str(uuid.uuid4())
_COMFYUI_SERVER_ADDRESS = "127.0.0.1:8188"
_COMFYUI_WORKFLOW_RAW = """
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

class ComfyUI:

    def __init__(self, prompt):
        self.prompt = prompt

    def handle_image_creation(self):

        comfyui_workflow = json.loads(_COMFYUI_WORKFLOW_RAW)
        comfyui_workflow["6"]["inputs"]["text"] = self.prompt
        image_comfyui = None

        try:

            ws_comfyui = websocket.WebSocket()
            ws_comfyui.connect("ws://{}/ws?clientId={}".format(_COMFYUI_SERVER_ADDRESS, _COMFYUI_CLIENT_ID))
            images_comfyui = self.comfyui_get_images(ws_comfyui, comfyui_workflow)
            ws_comfyui.close()

            for node_id in images_comfyui:
                if len(images_comfyui[node_id]) > 0:
                    image_comfyui = base64.b64encode(images_comfyui[node_id][0]).decode("utf-8")
                    image_comfyui = f"data:image/png;base64,{image_comfyui}"

        except Exception as e:
            print(e)

        return image_comfyui

    def comfyui_queue_prompt(self, prompt):
        p = { "prompt": prompt, "client_id": _COMFYUI_CLIENT_ID }
        data = json.dumps(p).encode("utf-8")
        # print(data)
        req = urllib.request.Request("http://{}/prompt".format(_COMFYUI_SERVER_ADDRESS), data=data)
        return json.loads(urllib.request.urlopen(req).read())

    def comfyui_get_image(self, filename, subfolder, folder_type):
        data = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        url_values = urllib.parse.urlencode(data)
        with urllib.request.urlopen("http://{}/view?{}".format(_COMFYUI_SERVER_ADDRESS, url_values)) as response:
            return response.read()

    def comfyui_get_history(self, prompt_id):
        with urllib.request.urlopen("http://{}/history/{}".format(_COMFYUI_SERVER_ADDRESS, prompt_id)) as response:
            return json.loads(response.read())

    def comfyui_get_images(self, ws, prompt):

        prompt_id = self.comfyui_queue_prompt(prompt)['prompt_id']
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

        history = self.comfyui_get_history(prompt_id)[prompt_id]

        for node_id in history['outputs']:
            node_output = history['outputs'][node_id]
            images_output = []
            if 'images' in node_output:
                for image in node_output['images']:
                    image_data = self.comfyui_get_image(image['filename'], image['subfolder'], image['type'])
                    images_output.append(image_data)
            output_images[node_id] = images_output

        return output_images
