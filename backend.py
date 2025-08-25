import os
import re
import uuid
import sqlite3
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, field_validator
import requests
import asyncio
import httpx
from loguru import logger
import aiohttp

# ====================== CHAVES ======================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
HF_API_KEY = os.getenv("HF_API_KEY")
AI21_API_KEY = os.getenv("AI21_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# ====================== Inicializa clientes OpenAI ======================
try:
    from openai import OpenAI as OpenAIClient
    client_openai = OpenAIClient(api_key=OPENAI_API_KEY)
    logger.info("OpenAI inicializado.")
except Exception as e:
    client_openai = None
    logger.warning(f"Falha ao inicializar OpenAI: {e}")

# ====================== FASTAPI ======================
app = FastAPI(title="Hoper Saúde API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====================== BANCO ======================
DB_PATH = "hoper.db"

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        nome TEXT NOT NULL,
        email TEXT NOT NULL,
        cep TEXT NOT NULL,
        idade INTEGER NOT NULL,
        avatar TEXT NOT NULL,
        posto_enviado INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS interactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        sintomas TEXT NOT NULL,
        doencas TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );""")
    conn.commit()
    conn.close()

init_db()

# ====================== SCHEMAS ======================
class Cadastro(BaseModel):
    nome: str
    email: EmailStr
    cep: str
    idade: int
    uid: str | None = None

    @field_validator("idade")
    @classmethod
    def valida_idade(cls, v):
        if v < 0 or v > 120:
            raise ValueError("Idade inválida")
        return v

    @field_validator("cep")
    @classmethod
    def valida_cep(cls, v):
        cep_clean = re.sub(r'\D', '', v or "")
        if len(cep_clean) != 8:
            raise ValueError("CEP inválido, deve conter 8 números")
        return cep_clean

class Mensagem(BaseModel):
    user_id: str
    texto: str

# ====================== UTIL ======================
def avatar_por_idade(idade: int) -> str:
    return "jovem" if idade <= 17 else "adulto"

# ====================== GOOGLE MAPS FUNÇÕES ======================
async def call_google_maps(cep: str, primeiro_nome: str):
    try:
        async with aiohttp.ClientSession() as session:
            # 1. Converter CEP em coordenadas
            geocode_url = f"https://maps.googleapis.com/maps/api/geocode/json?address={cep}&key={GOOGLE_API_KEY}"
            async with session.get(geocode_url) as resp:
                geocode_data = await resp.json()

            if geocode_data["status"] != "OK":
                return f"⚠️ Não consegui localizar o CEP {cep}, {primeiro_nome}."

            location = geocode_data["results"][0]["geometry"]["location"]
            lat, lng = location["lat"], location["lng"]

            # 2. Buscar posto de saúde mais próximo
            places_url = (
                f"https://maps.googleapis.com/maps/api/place/nearbysearch/json"
                f"?location={lat},{lng}&radius=3000&type=hospital&keyword=posto+de+saude&key={GOOGLE_API_KEY}"
            )
            async with session.get(places_url) as resp:
                places_data = await resp.json()

            if places_data["status"] != "OK" or not places_data["results"]:
                return f"😔 Não encontrei nenhum posto de saúde perto do CEP {cep}, {primeiro_nome}."

            place = places_data["results"][0]
            nome = place["name"]
            endereco = place.get("vicinity", "Endereço não disponível")

            return (
                f"🏥 Posto de Saúde mais próximo:\n\n"
                f"➡️ Nome: {nome}\n"
                f"📍 Endereço: {endereco}\n"
            )

    except Exception as e:
        logger.warning(f"⚠️ Google Maps API falhou: {e}")
        return None

# ====================== IA ======================
DOENCAS_DB = {
    "febre": ["gripe", "dengue", "covid-19", "infecção bacteriana"],
    "tosse": ["resfriado", "asma", "bronquite", "covid-19"],
    "dor de cabeça": ["enxaqueca", "sinusite", "tensão", "desidratação"],
    "dor abdominal": ["gastrite", "úlcera", "infecção intestinal"],
    "fraqueza": ["anemia", "hipotensão", "diabetes"],
}

CONVERSA_BASE = [
    {"role": "system", "content":
        "Você é Hoper Saúde, assistente amigável e empático. "
        "Diante de um sintoma fornecido, liste possíveis condições médicas e remédios comuns em 2-3 frases. "
        "Nunca faça perguntas ao usuário. Sempre finalize recomendando avaliação médica."
    }
]

# ====================== FALLBACK ======================
async def responder_ia(texto_usuario: str, user_id: str = None, nome: str = "usuário"):
    if not hasattr(responder_ia, "historico"):
        responder_ia.historico = {}
    if user_id not in responder_ia.historico:
        responder_ia.historico[user_id] = CONVERSA_BASE.copy()

    primeiro_nome = (nome or "usuário").split()[0]
    messages = [
        {"role": "system", "content":
            f"Converse com {primeiro_nome}, seja amigável e empático. "
            "Liste possíveis condições médicas e remédios comuns em 2-3 frases. "
            "Finalize recomendando avaliação médica."
        },
        {"role": "user", "content": texto_usuario}
    ]

    # OpenAI
    if client_openai is not None:
        try:
            resp = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.8,
                max_tokens=300
            )
            texto_resposta = resp.choices[0].message.content.strip()
            responder_ia.historico[user_id].append({"role": "assistant", "content": texto_resposta})
            return texto_resposta
        except Exception as e:
            logger.error(f"❌ OpenAI falhou: {e}")

    # OpenRouter
    async def call_openrouter():
        modelos = ["mistralai/devstral-small:free"]
        async with httpx.AsyncClient(timeout=30) as cli:
            for modelo in modelos:
                try:
                    r = await cli.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                        json={"model": modelo, "messages": messages}
                    )
                    r.raise_for_status()
                    data = r.json()
                    if "choices" in data and data["choices"]:
                        resposta = data["choices"][0]["message"]["content"]
                        return resposta.strip()
                except Exception as e:
                    logger.warning(f"⚠️ OpenRouter falhou: {e}")
        return None

    for func in (call_openrouter,):
        try:
            resultado = await func()
            if resultado:
                responder_ia.historico[user_id].append({"role": "assistant", "content": resultado})
                return resultado
        except Exception:
            continue

    return f"Desculpe {primeiro_nome}, não consegui responder no momento. 🙏"

# ====================== AUX ======================
def sugerir_doencas_curto(texto: str, max_itens: int = 3):
    texto_low = texto.lower()
    sugestoes = []
    for sintoma, doencas in DOENCAS_DB.items():
        if sintoma in texto_low:
            sugestoes.extend([d for d in doencas if d not in sugestoes])
    return sugestoes[:max_itens]

# ====================== ROTAS ======================
@app.post("/register")
async def register(cad: Cadastro):
    user_id = getattr(cad, "uid", None) or str(uuid.uuid4())
    avatar = avatar_por_idade(cad.idade)
    conn = db()
    cur = conn.cursor()

    # Verifica se usuário já existe
    cur.execute("SELECT id FROM users WHERE id=?", (user_id,))
    if cur.fetchone():
        cur.execute(
            "UPDATE users SET nome=?, email=?, cep=?, idade=?, avatar=? WHERE id=?",
            (cad.nome.strip(), cad.email, cad.cep.strip(), cad.idade, avatar, user_id)
        )
    else:
        cur.execute(
            "INSERT INTO users (id,nome,email,cep,idade,avatar,posto_enviado,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, cad.nome.strip(), cad.email, cad.cep.strip(), cad.idade, avatar, 0, datetime.utcnow().isoformat())
        )

    conn.commit()
    conn.close()

    # Retorna apenas dados do usuário, sem posto
    return {
        "user_id": user_id,
        "avatar": avatar,
    }

@app.get("/users/{user_id}")
def get_user(user_id: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id,nome,email,cep,idade,avatar,posto_enviado,created_at FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return dict(row)


@app.post("/login")
async def login(cad: Cadastro):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id, nome, email, cep, idade, avatar FROM users WHERE email=?", (cad.email,))
    user = cur.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    nome = user["nome"]
    cep = user["cep"]
    user_id = user["id"]

    # Buscar posto
    async def format_posto(cep, primeiro_nome):
        res = await call_google_maps(cep, primeiro_nome)
        if res:
            lines = res.split("\n")
            nome_line = next((l for l in lines if l.startswith("➡️ Nome:")), "")
            end_line = next((l for l in lines if l.startswith("📍 Endereço:")), "")
            return {
                "nome": nome_line.replace("➡️ Nome: ", "") if nome_line else "Posto",
                "endereco": end_line.replace("📍 Endereço: ", "") if end_line else "Endereço não informado"
            }
        return None

    posto_obj = await format_posto(cep, nome.split()[0])

    return {
        "user_id": user_id,
        "nome": nome,
        "email": user["email"],
        "idade": user["idade"],
        "avatar": user["avatar"],
        "posto_proximo": posto_obj
    }
@app.get("/posto_proximo/{user_id}")
async def posto_proximo(user_id: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT cep, nome FROM users WHERE id=?", (user_id,))
    user = cur.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    nome = user["nome"].split()[0] if user["nome"] else "Usuário"
    cep = user["cep"]

    if not cep:
        return {"postos_proximos": []}

    async def buscar_postos(cep, primeiro_nome):
        try:
            async with aiohttp.ClientSession() as session:
                # 1. Converter CEP em coordenadas
                geocode_url = f"https://maps.googleapis.com/maps/api/geocode/json?address={cep}&key={GOOGLE_API_KEY}"
                async with session.get(geocode_url) as resp:
                    geocode_data = await resp.json()

                if geocode_data["status"] != "OK":
                    return []

                location = geocode_data["results"][0]["geometry"]["location"]
                lat, lng = location["lat"], location["lng"]

                # 2. Buscar postos próximos
                places_url = (
                    f"https://maps.googleapis.com/maps/api/place/nearbysearch/json"
                    f"?location={lat},{lng}&radius=3000&type=hospital&keyword=posto+de+saude&key={GOOGLE_API_KEY}"
                )
                async with session.get(places_url) as resp:
                    places_data = await resp.json()

                if places_data["status"] != "OK" or not places_data["results"]:
                    return []

                postos = []
                for place in places_data["results"][:5]:  # pegar até 5
                    postos.append({
                        "nome": place.get("name", "Posto"),
                        "endereco": place.get("vicinity", "Endereço não disponível")
                    })
                return postos
        except Exception as e:
            logger.warning(f"⚠️ Google Maps API falhou: {e}")
            return []

    postos_list = await buscar_postos(cep, nome)
    return {"postos_proximos": postos_list}

@app.post("/chat")
async def chat(msg: Mensagem):
    logger.info(f"/chat chamado — user_id={msg.user_id} texto={msg.texto!r}")
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT cep, nome FROM users WHERE id=?", (msg.user_id,))
    user = cur.fetchone()

    if not user:
        default_nome = "Usuário"
        default_cep = ""
        default_idade = 30
        avatar = avatar_por_idade(default_idade)
        cur.execute(
            "INSERT INTO users (id,nome,email,cep,idade,avatar,posto_enviado,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (msg.user_id, default_nome, "", default_cep, default_idade, avatar, 0, datetime.utcnow().isoformat())
        )
        conn.commit()
        cur.execute("SELECT cep, nome FROM users WHERE id=?", (msg.user_id,))
        user = cur.fetchone()

    nome = user["nome"] if user["nome"] else "Usuário"

    # só chamar a IA, sem salvar nada
    resposta_ia = await responder_ia(msg.texto, user_id=msg.user_id, nome=nome)

    conn.close()

    return {"resposta": resposta_ia}  # sem "doencas_sug"
