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
    based on coordinates and generates a royal family AND the 5 council members.
    """
    prompt = f"""
    Crie o background inicial para um novo reino no RPG de texto "Thronebound" (estilo Crusader Kings).

    Dados do Reino:
    Nome: {kingdom_name}
    Soberano: {sovereign_name}
    Governo: {gov_type}
    Foco Inicial: {build_type}
    Coordenadas no mapa: X={pos_x}, Y={pos_y} (O centro 500,500 é uma montanha impenetrável, as bordas são mais férteis ou costeiras).

    Gere uma lore épica e descritiva detalhando a fundação, a capital e a geografia.
    INCLUA DE FORMA ORGÂNICA NESSE TEXTO A APRESENTAÇÃO DE QUEM É A FAMÍLIA REAL E QUEM SÃO OS CONSELHEIROS QUE ASSUMIRAM O PODER. O texto deve ser épico e imersivo (máx 3 parágrafos).

    Além disso, você deve criar obrigatoriamente um array "personagens" que contenha:
    - A Família Real: Pelo menos 1 Consorte e 1 ou 2 Filhos/Irmãos.
    - O Conselho: Exatamente 5 personagens ocupando os cargos: Chanceler, Tesoureiro, Marechal, Espião e Capelão. (Um familiar pode ocupar um cargo, mas tem que haver as 5 posições de conselho preenchidas).

    Para CADA personagem, gere: "nome", "idade" (int), "relacao_familiar" ("Filho", "Irmão", "Consorte", "Nenhum"), "cargo_conselho" ("Chanceler", "Tesoureiro", "Marechal", "Espião", "Capelão", "Nenhum"), "poder" (int de 0 a 100), "lealdade" (int de 0 a 100) e "personalidade" (string, ex: "Ambicioso e cruel").

    Responda ESTRITAMENTE em formato JSON com as seguintes chaves:
    1. "lore": (string) O texto de background narrativo.
    2. "personagens": (array de objetos com as propriedades citadas acima).
    """

    result = await query_ollama(prompt, json_format=True)
    try:
        data = json.loads(result)
        return data
    except json.JSONDecodeError:
        return {
            "lore": "Os registros antigos se perderam, mas a corte foi estabelecida.",
            "personagens": [
                {"nome": "Desconhecido", "idade": 30, "relacao_familiar": "Consorte", "cargo_conselho": "Nenhum", "poder": 50, "lealdade": 50, "personalidade": "Silencioso"},
                {"nome": "Sábio", "idade": 50, "relacao_familiar": "Nenhum", "cargo_conselho": "Chanceler", "poder": 50, "lealdade": 80, "personalidade": "Leal"}
            ]
        }

async def answer_oracle(kingdom_status: dict, question_text: str, context_history: list = None, characters: list = None) -> str:
    """
    Answers player questions about the world without mutating the game state.
    """
    history_str = ""
    if context_history:
        history_str = "Histórico recente:\n"
        for doc in context_history:
            history_str += f"- {doc}\n"

    char_str = ""
    if characters:
        char_str = "Corte (Família e Conselho):\n"
        for c in characters:
            cargo = c.cargo_conselho if c.cargo_conselho != "Nenhum" else c.relacao_familiar
            char_str += f"- {c.nome} ({cargo}, {c.idade} anos, Poder: {c.poder}, Lealdade: {c.lealdade}, Perfil: {c.personalidade})\n"

    prompt = f"""
    Você é o Oráculo/Conselheiro do reino em um RPG de texto (estilo Crusader Kings). Responda à pergunta do Soberano.
    Não tome decisões por ele, apenas informe como o mundo está baseado nos dados e na situação da corte.

    Dados do Reino:
    Ouro: {kingdom_status.get('gold')} | Exército: {kingdom_status.get('army')} | Influência: {kingdom_status.get('influence')}
    Estabilidade: {kingdom_status.get('estabilidade')}/100
    Leis: {kingdom_status.get('leis')}
    Ações Restantes Hoje: {kingdom_status.get('acoes_restantes')}

    {char_str}
    {history_str}

    Pergunta do Soberano: "{question_text}"

    Dê uma resposta narrativa, imersiva e direta. (Máx 2 parágrafos).
    """
    return await query_ollama(prompt, json_format=False)

async def validate_legal_heir(family_members: list, lei_sucessao: str, lei_genero: str) -> str:
    """
    Evaluates the current living family members against the realm's succession laws
    and returns the name of the legally mandated heir.
    """
    if not family_members:
        return "Nenhum"

    fam_str = "Membros da Família Real Vivos:\n"
    for c in family_members:
        fam_str += f"- Nome: {c.nome} | Relação: {c.relacao_familiar} | Idade: {c.idade}\n"

    prompt = f"""
    Você é o Grande Magistrado do reino em um RPG estilo Crusader Kings. O Soberano acaba de falecer.
    Você deve avaliar estritamente as leis vigentes e a lista da Família Real viva para determinar quem é o herdeiro legal de direito ao trono.

    Leis Vigentes:
    - Sucessão: {lei_sucessao}
    - Gênero: {lei_genero}

    {fam_str}

    Regras gerais (interprete com base na string da lei):
    - Se for preferência masculina, tente achar o filho/irmão homem mais velho aplicável.
    - Se for filho, tem preferência sobre irmão, a menos que a lei diga o contrário (Senhorio).
    - O Herdeiro Legal DEVE ser alguém que tenha relação de sangue (Filho, Filha, Irmão, Irmã, Sobrinho, Neto). Consortes NUNCA herdam o trono legalmente.
    - Se ninguém se encaixar perfeitamente, escolha o parente de sangue mais próximo.

    Responda ESTRITAMENTE em formato JSON com uma única chave "herdeiro_legal", cujo valor deve ser o nome EXATO do personagem escolhido.
    Exemplo: {{"herdeiro_legal": "Aegon"}}
    """

    result = await query_ollama(prompt, json_format=True)
    try:
        data = json.loads(result)
        nome_legal = data.get("herdeiro_legal", "Desconhecido")
        return nome_legal
    except json.JSONDecodeError:
        # Fallback to the first blood relative if parsing fails
        for c in family_members:
            if c.relacao_familiar not in ["Consorte", "Nenhum"]:
                return c.nome
        return "Desconhecido"

async def resolve_action(kingdom_status: dict, action_text: str, context_history: list = None, ciclo_completo: bool = False, characters: list = None) -> dict:
    """
    Resolves an action, providing both the narrative and the DB update json.
    Injects context history and character/council state.
    If ciclo_completo is True, instructs AI to narrate the passing of a week/year.
    """
    history_str = ""
    if context_history:
        history_str = "Histórico recente de eventos deste reino:\n"
        for doc in context_history:
            history_str += f"- {doc}\n"

    char_str = ""
    if characters:
        char_str = "Corte Atual:\n"
        for c in characters:
            cargo = c.cargo_conselho if c.cargo_conselho != "Nenhum" else c.relacao_familiar
            char_str += f"- {c.nome} (ID:{c.id}, {cargo}, Poder: {c.poder}, Lealdade: {c.lealdade}, Perfil: {c.personalidade})\n"

    ciclo_str = ""
    if ciclo_completo:
        ciclo_str = "\nIMPORTANTE: Esta ação completa um Ciclo. Faça a narrativa transparecer que semanas se passaram, o tempo avançou e os personagens envelheceram.\n"

    prompt = f"""
    Você é o mestre de um RPG medieval slow-burn estilo Crusader Kings. O Soberano realizou a seguinte ação:
    Ação: "{action_text}"

    {history_str}
    {ciclo_str}

    Status atual do reino:
    Ouro: {kingdom_status.get('gold')} | Exército: {kingdom_status.get('army')} | Influência: {kingdom_status.get('influence')}
    Estabilidade: {kingdom_status.get('estabilidade')}/100
    Leis Atuais: {kingdom_status.get('leis')}

    {char_str}

    Avalie a consequência da ação. Considere intrigas: conselheiros desleais podem sabotar o reino ou assassinar o rei. Leis rígidas baixam a estabilidade.
    Se a ação envolver gastar recursos inexistentes, ela falha (não deduza os recursos, mas o rei pode passar vergonha diminuindo estabilidade/influência).

    Responda ESTRITAMENTE em formato JSON com três chaves:
    1. "narrativa": Um texto descrevendo o resultado e as consequências.
    2. "atualizacao_db": Objeto JSON com as mudanças relativas (ex: +50, -10) nos recursos: "ouro", "exercito", "influencia", "estabilidade". Também inclua "soberano_morto" (boolean, default false).
    3. "atualizacao_personagens": Array opcional contendo atualizações relativas (ex: +10, -20) para os personagens que reagiram à ação. Chaves do objeto: "id" (int do personagem), "lealdade", "poder", "is_alive" (boolean).

    Exemplo de resposta:
    {{
      "narrativa": "Você ordenou um novo imposto. O ouro fluiu, mas a estabilidade caiu. O Mestre dos Espiões, achando a lei injusta, perdeu lealdade.",
      "atualizacao_db": {{
        "ouro": 500,
        "exercito": 0,
        "influencia": 0,
        "estabilidade": -10,
        "soberano_morto": false
      }},
      "atualizacao_personagens": [
        {{ "id": 3, "lealdade": -15, "poder": 0, "is_alive": true }}
      ]
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
