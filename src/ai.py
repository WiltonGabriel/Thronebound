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
    Evaluates if the action is meaningful enough to spend a Decree/Action point.
    Returns a dict with 'classificacao', 'reino_destino', 'distancia_estimada', and 'gasta_acao'.
    """
    prompt = f"""
    Você é o mestre de um RPG de texto. Avalie a seguinte ação do jogador:
    Ação do jogador: "{action_text}"

    1. A ação é "Instantânea" (ações locais como caminhar, falar com servo) ou "Demorada" (ações externas/logísticas como mover tropas, enviar cartas)?
    2. A ação é significativa o suficiente para gastar um Decreto oficial (gasta_acao = true)? Ações puramente contemplativas como "olhar pro teto" ou "suspirar" não devem gastar ações (false).
    3. Se for "Demorada", qual o possível "reino_destino" (string ou null) e uma "distancia_estimada" (número entre 100 e 1000, ou null se Instantânea).

    Responda em formato JSON estrito com as chaves: "classificacao" ("Instantânea" ou "Demorada"), "gasta_acao" (boolean), "reino_destino" (string ou null), e "distancia_estimada" (número ou null).
    """

    result = await query_ollama(prompt, json_format=True)
    default_resp = {"classificacao": "Demorada", "gasta_acao": True, "reino_destino": None, "distancia_estimada": 100}
    try:
        data = json.loads(result)
        classification = data.get("classificacao", "Demorada")
        if classification not in ["Instantânea", "Demorada"]:
            classification = "Demorada"

        gasta_acao = data.get("gasta_acao", True)
        reino_destino = data.get("reino_destino")
        dist_estimada = data.get("distancia_estimada")
        if not isinstance(dist_estimada, (int, float)):
            dist_estimada = 100

        return {
            "classificacao": classification,
            "gasta_acao": bool(gasta_acao),
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

async def generate_kingdom_lore(kingdom_name: str, sovereign_name: str, gov_type: str, build_type: str, pos_x: float, pos_y: float) -> dict:
    """
    Generates the initial lore for a newly founded kingdom, including geographical details
    based on coordinates and generates a royal family.
    """
    prompt = f"""
    Crie o background inicial para um novo reino no RPG de texto "Thronebound".

    Dados do Reino:
    Nome: {kingdom_name}
    Soberano: {sovereign_name}
    Governo: {gov_type}
    Foco Inicial: {build_type}
    Coordenadas no mapa: X={pos_x}, Y={pos_y} (O centro 500,500 é uma montanha impenetrável, as bordas são mais férteis ou costeiras).

    Gere uma lore épica e descritiva de no máximo 2 parágrafos detalhando a capital e a geografia.
    Além disso, crie a Família Real do Soberano para que o jogador tenha laços (esposa/marido, e de 1 a 2 irmãos ou filhos).

    Responda ESTRITAMENTE em formato JSON com as seguintes chaves:
    1. "lore": (string) O texto de background narrativo.
    2. "familia": (array de objetos) Contendo chaves "nome", "relacao" (ex: "Filho", "Esposa", "Irmão") e "idade" (int).
    """

    result = await query_ollama(prompt, json_format=True)
    try:
        data = json.loads(result)
        return data
    except json.JSONDecodeError:
        return {
            "lore": "Os registros antigos se perderam, mas a dinastia permanece forte.",
            "familia": [{"nome": "Desconhecido", "relacao": "Consorte", "idade": 30}]
        }

async def answer_oracle(kingdom_status: dict, question_text: str, context_history: list = None, family_members: list = None) -> str:
    """
    Answers player questions about the world without mutating the game state.
    """
    history_str = ""
    if context_history:
        history_str = "Histórico recente:\n"
        for doc in context_history:
            history_str += f"- {doc}\n"

    fam_str = ""
    if family_members:
        fam_str = "Membros da Família Real:\n"
        for fm in family_members:
            fam_str += f"- {fm.name} ({fm.relation}, {fm.age} anos)\n"

    prompt = f"""
    Você é o Oráculo/Conselheiro do reino em um RPG de texto. Responda à pergunta do Soberano.
    Não tome decisões por ele, apenas informe como o mundo está baseado nos dados que você tem.

    Dados do Reino:
    Ouro: {kingdom_status.get('gold')}
    Exército: {kingdom_status.get('army')}
    Influência: {kingdom_status.get('influence')}
    Ações Restantes Hoje: {kingdom_status.get('acoes_restantes')}

    {fam_str}
    {history_str}

    Pergunta do Soberano: "{question_text}"

    Dê uma resposta narrativa, imersiva e direta. (Máx 2 parágrafos).
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
