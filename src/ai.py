import aiohttp
import os
import json

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://172.17.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e4b")

async def query_ollama(prompt: str, json_format: bool = False) -> str:
    """
    Sends a prompt to the local Ollama API.
    If json_format is True, it forces the model to respond in JSON.
    """
    url = f"{OLLAMA_BASE_URL}/api/generate"

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False
    }

    if json_format:
        payload["format"] = "json"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, timeout=60) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("response", "")
                else:
                    text = await response.text()
                    print(f"Ollama API error ({response.status}): {text}")
                    return "{}" if json_format else ""
        except Exception as e:
            print(f"Failed to connect to Ollama: {e}")
            return "{}" if json_format else ""

async def classify_action(action_text: str) -> str:
    """
    Classifies if an action is 'Instantânea' (Local) or 'Demorada' (External).
    Returns 'Instantânea' or 'Demorada'.
    """
    prompt = f"""
    Você é o mestre de um RPG de texto. Classifique a seguinte ação do jogador como "Instantânea" ou "Demorada".
    - "Instantânea": Ações locais dentro do próprio castelo/cidade (ex: caminhar, pensar, falar com um servo).
    - "Demorada": Ações externas e logísticas (ex: mover tropas, enviar mensageiros, construir estruturas grandes).

    Responda em formato JSON com uma única chave "classificacao" cujo valor seja estritamente "Instantânea" ou "Demorada".

    Ação do jogador: "{action_text}"
    """

    result = await query_ollama(prompt, json_format=True)
    try:
        data = json.loads(result)
        classification = data.get("classificacao", "Demorada")
        if classification not in ["Instantânea", "Demorada"]:
            return "Demorada"
        return classification
    except json.JSONDecodeError:
        return "Demorada" # Default to delayed on error

async def generate_immediate_feedback(action_text: str) -> str:
    """
    Generates a short in-character confirmation for a delayed action.
    """
    prompt = f"""
    Você é o conselheiro de um rei em um RPG medieval.
    O Soberano acabou de dar a seguinte ordem externa/logística: "{action_text}"

    Escreva uma resposta muita curta e imersiva (1 a 2 frases) confirmando que a ordem foi recebida e que aguardará o desenrolar das ações.
    """
    return await query_ollama(prompt, json_format=False)

async def resolve_action(kingdom_status: dict, action_text: str, context_history: list = None) -> dict:
    """
    Resolves an action, providing both the narrative and the DB update json.
    Injects context history if available.
    """
    history_str = ""
    if context_history:
        history_str = "Histórico recente de eventos deste reino:\n"
        for doc in context_history:
            history_str += f"- {doc}\n"

    prompt = f"""
    Você é o mestre de um RPG medieval. O Soberano do reino realizou a seguinte ação:
    Ação: "{action_text}"

    {history_str}

    Status atual do reino:
    - Ouro: {kingdom_status.get('gold')}
    - Exército: {kingdom_status.get('army')}
    - Influência: {kingdom_status.get('influence')}

    Avalie a consequência da ação. Se a ação envolver gastar recursos que o reino não possui, a ação falha e a narrativa deve refletir isso (sem gastar recursos).
    Se o Soberano tiver mais de 60 anos, e for uma ação arriscada, ele pode morrer.

    Responda ESTRITAMENTE em formato JSON com duas chaves:
    1. "narrativa": Um texto descrevendo o resultado e as consequências da ação.
    2. "atualizacao_db": Um objeto JSON contendo as mudanças relativas nos recursos. As chaves válidas são "ouro", "exercito", "influencia" e "soberano_morto" (boolean). Se não houver mudança, coloque 0.

    Exemplo de resposta:
    {{
      "narrativa": "Seus emissários chegaram ao reino vizinho e a oferta foi aceita. O ouro foi entregue e a influência cresceu.",
      "atualizacao_db": {{
        "ouro": -500,
        "exercito": 0,
        "influencia": 20,
        "soberano_morto": false
      }}
    }}
    """

    result = await query_ollama(prompt, json_format=True)
    try:
        data = json.loads(result)
        return data
    except json.JSONDecodeError:
        print(f"Failed to decode resolve_action JSON: {result}")
        return {
            "narrativa": "Uma névoa cobriu o reino e as notícias sobre suas ordens se perderam no tempo...",
            "atualizacao_db": {}
        }
