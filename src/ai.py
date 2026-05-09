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

async def classify_action(action_text: str) -> dict:
    """
    Classifies if an action is 'Instantânea' (Local) or 'Demorada' (External).
    Also attempts to extract 'reino_destino' (string) and 'distancia_estimada' (number).
    Returns a dict with 'classificacao', 'reino_destino', and 'distancia_estimada'.
    """
    prompt = f"""
    Você é o mestre de um RPG de texto. Classifique a seguinte ação do jogador como "Instantânea" ou "Demorada".
    - "Instantânea": Ações locais dentro do próprio castelo/cidade (ex: caminhar, pensar, falar com um servo).
    - "Demorada": Ações externas e logísticas (ex: mover tropas, enviar mensageiros, construir estruturas grandes, ir para outro local).

    Se for "Demorada", tente extrair o nome do reino destino na chave "reino_destino". Se não for mencionado um reino, deixe nulo (null).
    Também forneça uma "distancia_estimada" arbitrária (um número de 100 a 1000) com base no quão longe a ação parece ir. Se for Instantânea, coloque null para ambos.

    Responda em formato JSON estrito com as chaves "classificacao" (valor estrito "Instantânea" ou "Demorada"), "reino_destino" (string ou null) e "distancia_estimada" (número ou null).

    Ação do jogador: "{action_text}"
    """

    result = await query_ollama(prompt, json_format=True)
    default_resp = {"classificacao": "Demorada", "reino_destino": None, "distancia_estimada": 100}
    try:
        data = json.loads(result)
        classification = data.get("classificacao", "Demorada")
        if classification not in ["Instantânea", "Demorada"]:
            classification = "Demorada"

        reino_destino = data.get("reino_destino")
        dist_estimada = data.get("distancia_estimada")
        if not isinstance(dist_estimada, (int, float)):
            dist_estimada = 100

        return {
            "classificacao": classification,
            "reino_destino": reino_destino,
            "distancia_estimada": dist_estimada
        }
    except json.JSONDecodeError:
        return default_resp # Default to delayed on error

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

async def resolve_action(kingdom_status: dict, action_text: str, context_history: list = None, ciclo_completo: bool = False) -> dict:
    """
    Resolves an action, providing both the narrative and the DB update json.
    Injects context history if available.
    If ciclo_completo is True, instructs AI to narrate the passing of a week/year.
    """
    history_str = ""
    if context_history:
        history_str = "Histórico recente de eventos deste reino:\n"
        for doc in context_history:
            history_str += f"- {doc}\n"

    ciclo_str = ""
    if ciclo_completo:
        ciclo_str = "\nIMPORTANTE: Esta ação completa um Ciclo. Faça a narrativa transparecer que semanas se passaram, o tempo avançou e os personagens envelheceram.\n"

    prompt = f"""
    Você é o mestre de um RPG medieval slow-burn. O Soberano do reino realizou a seguinte ação:
    Ação: "{action_text}"

    {history_str}
    {ciclo_str}

    Status atual do reino:
    - Ouro: {kingdom_status.get('gold')}
    - Exército: {kingdom_status.get('army')}
    - Influência: {kingdom_status.get('influence')}

    Avalie a consequência da ação. Se a ação envolver gastar recursos que o reino não possui, a ação falha e a narrativa deve refletir isso (sem gastar recursos).
    O Soberano também pode acabar morrendo dependendo de suas escolhas.

    Responda ESTRITAMENTE em formato JSON com duas chaves:
    1. "narrativa": Um texto descrevendo o resultado e as consequências da ação.
    2. "atualizacao_db": Um objeto JSON contendo as mudanças relativas nos recursos. As chaves válidas são "ouro", "exercito", "influencia" e "soberano_morto" (boolean). Se não houver mudança, coloque 0.

    Exemplo de resposta:
    {{
      "narrativa": "Seus emissários chegaram ao reino vizinho e a oferta foi aceita. O ouro foi entregue e a influência cresceu. Os dias viram semanas, e o peso da idade se torna cada vez mais evidente no seu rosto...",
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
