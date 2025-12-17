import json
import uuid
import requests
import time


POSITIVE_PROMPT = "Modern abstract representation of global short-form video marketing solutions, showing professional data flows and integrated mobile screens displaying diverse video content, utilizing a neutral and earthy color palette with high contrast. Clean lines and geometric shapes dominate the composition, evoking a sense of technological sophistication and connectivity. The artwork should convey the dynamic and fast-paced nature of short-form video marketing, with an emphasis on innovation and digital communication. The style should be sleek and contemporary, suitable for a corporate audience interested in cutting-edge marketing strategies."

COMFYUI_URL = "https://qfrtn6he9wnwog-8188.proxy.runpod.net"





def create_workflow():
    workflow= {
  "6": {
    "inputs": {
      "text": POSITIVE_PROMPT,
      "clip": [
        "30",
        1
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
        "30",
        2
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
  "30": {
    "inputs": {
      "ckpt_name": 'flux1-schnell-fp8.safetensors'
    },
    "class_type": "CheckpointLoaderSimple",
    "_meta": {
      "title": "Load Checkpoint"
    }
  },
  "31": {
    "inputs": {
      "seed": 5542493640978,
      "steps": 4,
      "cfg": 1,
      "sampler_name": "euler",
      "scheduler": "simple",
      "denoise": 1,
      "model": [
        "30",
        0
      ],
      "positive": [
        "6",
        0
      ],
      "negative": [
        "33",
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
  "33": {
    "inputs": {
      "text": "",
      "clip": [
        "30",
        1
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Negative Prompt)"
    }
  }
}
    return workflow

def generate_client_id():
    return str(uuid.uuid4())

def submit_workflow():
    workflow = create_workflow()
    client_id = generate_client_id()
    payload = {
        "prompt":workflow,
        "client_id":client_id
    }
    print(f"sending request to comfyui...{COMFYUI_URL}/prompt submit_workflow")
    response = requests.post(f"{COMFYUI_URL}/prompt", json=payload)
    if response.status_code != 200:
        raise Exception(f"Failed to submit workflow: {response.text}")
    
    response_data = response.json()
    prompt_id = response_data.get("prompt_id")
    print(f"Workflow submitted successfully. Prompt ID: {prompt_id}")
    return prompt_id

def wait_for_completion(prompt_id):
    print("Waiting for workflow to complete...")
    while True:
        time.sleep(5)
        response = requests.get(f"{COMFYUI_URL}/history/{prompt_id}")
        if response.status_code != 200:
            raise Exception(f"Failed to get workflow status: {response.text}")
        try:
            status_data = response.json()
           
        except json.JSONDecodeError:
            print("Received invalid JSON response, retrying...")
            continue
        if prompt_id in status_data:
            execution_data = status_data[prompt_id] 
            if 'status' in execution_data and execution_data['status'].get('completed', False):
                print("Workflow completed.")
                return status_data
            if 'status' in execution_data and 'error' in execution_data['status']:
                print(f"Workflow error: {execution_data['status']['error']}")
                return None
        print("Workflow not completed yet, checking again...")
        
def get_image_url(status_data, prompt_id):
    if prompt_id not in status_data or 'outputs' not in status_data[prompt_id]:
        print("No outputs found for the given prompt ID.")
        return
    outputs = status_data[prompt_id]['outputs'] 
    images_downloaded=0
    
    for node_id, node_output in outputs.items():
        if 'images' in node_output:
            for image_info in node_output['images']:
                filename = image_info['filename']
                subfolder = image_info.get('subfolder', '')
                
                view_params ={
                    "filename": filename,
                    "type": "output",
                }
                if subfolder:
                    view_params["subfolder"] = subfolder
                print('downloading image:', filename)
                image_response = requests.get(f"{COMFYUI_URL}/view", params=view_params)
                
                if image_response.status_code == 200:
                    output_filename = f"output_{images_downloaded}_{filename}"
                    with open(output_filename, 'wb') as image_file:
                        image_file.write(image_response.content)
                    print(f"Image saved as {output_filename}")
                    
                    images_downloaded
                else:
                    print(f"Failed to download image {filename}: {image_response.text}")
    if images_downloaded == 0:
        print("No images were downloaded.")
    else:
        print(f"Total images downloaded: {images_downloaded}")
        
def main():
    print("Starting workflow submission...")
    prompt_id = submit_workflow()
    
    if not prompt_id:
        print("Failed to submit workflow.")
        return
    status_data = wait_for_completion(prompt_id=prompt_id)
    if not status_data:
        print("Workflow execution failed.")
        return
    get_image_url(status_data=status_data, prompt_id=prompt_id)
    
    
    
if __name__ == "__main__":
    main()