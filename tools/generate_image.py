import requests

TOOL_DEFINITION = {
    "name": "generate_image",
    "description": "Generates an image from a text prompt.",
    "emoji": "IMG",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "English image prompt; detailed prompts work best"
            },
            "ratio": {
                "type": "string",
                "description": "Image aspect ratio: 1:1, 16:9, or 9:16",
                "default": "1:1"
            }
        },
        "required": ["prompt"]
    }
}

def execute(params):
    prompt = params.get("prompt", "a beautiful digital art")
    ratio = params.get("ratio", "1:1")
    
    # Pollinations Image URL formatÄ±
    # Ã–rnek: https://pollinations.ai/p/[PROMPT]?width=[W]&height=[H]&seed=[S]&model=flux
    
    width, height = 1024, 1024
    if ratio == "16:9":
        width, height = 1280, 720
    elif ratio == "9:16":
        width, height = 720, 1280
        
    import random
    import urllib.parse
    
    seed = str(random.randint(1, 999999999))
    encoded_prompt = urllib.parse.quote(prompt)
    
    # model=zimage ile gÃ¶rsel Ã¼retme
    image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?model=zimage&nologo=true&seed={seed}&width={width}&height={height}"
    
    return {
        "image_url": image_url,
        "prompt": prompt,
        "style": "zimage",
        "seed": seed,
        "info": "Image generated successfully."
    }
