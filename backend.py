import os
import re
import uuid
import json
from datetime import datetime

import asyncio
import httpx
import aiohttp
import requests
from loguru import logger

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, field_validator

from firebase_config import db_firebase
import firebase_admin
from firebase_admin import credentials, firestore

# SQLAlchemy
from sqlalchemy import Column, String, Integer, DateTime, create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# ====================== CHAVES ======================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
HF_API_KEY = os.getenv("HF_API_KEY")
AI21_API_KEY = os.getenv("AI21_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Variável do Firebase
FIREBASE_CRED_JSON = os.getenv("FIREBASE_CRED_JSON")
if FIREBASE_CRED_JSON:
    FIREBASE_CRED = json.loads(FIREBASE_CRED_JSON)
else:
    FIREBASE_CRED = None

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

# ====================== BANCO POSTGRES ======================
DATABASE_URL = os.getenv("DATABASE_URL") or \
    "postgresql://hoper_saude_db_user:gZ811HPsJK3ZI3mwG3QEux6b2BbFRKQP@dpg-d2mt93jipnbc73fat1eg-a.oregon-postgres.render.com/hoper_saude_db"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,     # testa e renova conexão antes de usar
    pool_recycle=1800       # recicla conexões a cada 30 min
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ====================== MODELOS ======================
class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    cep = Column(String, nullable=False)
    idade = Column(Integer, nullable=False)
    avatar = Column(String, nullable=False)
    posto_enviado = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Interaction(Base):
    __tablename__ = "interactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False)
    sintomas = Column(String, nullable=False)
    doencas = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)

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


class LoginModel(BaseModel):
    uid: str | None = None
    email: EmailStr | None = None

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
            # Geocode pelo CEP
            geocode_url = (
                f"https://maps.googleapis.com/maps/api/geocode/json"
                f"?components=postal_code:{cep}|country:BR&key={GOOGLE_API_KEY}"
            )
            async with session.get(geocode_url) as resp:
                geocode_data = await resp.json()

            if geocode_data.get("status") != "OK" or not geocode_data.get("results"):
                return f"⚠️ Não consegui localizar o CEP {cep}, {primeiro_nome}."

            location = geocode_data["results"][0]["geometry"]["location"]
            lat, lng = location["lat"], location["lng"]

            # Busca postos de saúde próximos
            places_url = (
                f"https://maps.googleapis.com/maps/api/place/nearbysearch/json"
                f"?location={lat},{lng}&radius=3000&type=hospital&keyword=posto+de+saude&key={GOOGLE_API_KEY}"
            )
            async with session.get(places_url) as resp:
                places_data = await resp.json()

            if places_data.get("status") != "OK" or not places_data.get("results"):
                return f"😔 Não encontrei nenhum posto de saúde perto do CEP {cep}, {primeiro_nome}."

            place = places_data["results"][0]
            nome = place.get("name", "Posto de Saúde")
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


def sugerir_doencas_curto(texto: str, max_itens: int = 3):
    texto_low = texto.lower()
    sugestoes = []
    for sintoma, doencas in DOENCAS_DB.items():
        if sintoma in texto_low:
            sugestoes.extend([d for d in doencas if d not in sugestoes])
    return sugestoes[:max_itens]

# ====================== ROTAS AJUSTADAS ======================

@app.post("/register")
async def register(cad: Cadastro, db: Session = Depends(get_db)):
    avatar = avatar_por_idade(cad.idade)

    # Verifica se já existe usuário com esse email
    user = db.query(User).filter(User.email == cad.email.strip()).first()
    if user:
        # Atualiza dados existentes
        user.nome = cad.nome.strip()
        user.cep = cad.cep.strip()
        user.idade = cad.idade
        user.avatar = avatar
        user_id = user.id
        db.commit()
    else:
        # Cria novo usuário
        user_id = getattr(cad, "uid", None)
        if not user_id:
            user_id = str(uuid.uuid4())
        user = User(
            id=user_id,
            nome=cad.nome.strip(),
            email=cad.email.strip(),
            cep=cad.cep.strip(),
            idade=cad.idade,
            avatar=avatar
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # Atualiza ou cria usuário no Firebase
    db_firebase.collection("users").document(user_id).set({
        "nome": cad.nome.strip(),
        "email": cad.email.strip(),
        "cep": cad.cep.strip(),
        "idade": cad.idade,
        "avatar": avatar,
        "created_at": datetime.utcnow().isoformat(),
        "posto_enviado": 0
    })

    # Retorna user_id para frontend salvar e usar no login
    return {"user_id": user_id, "avatar": avatar}


@app.post("/login")
async def login(data: LoginModel):
    logger.info(f"Login chamado — uid={data.uid!r} email={data.email!r}")

    # Se houver UID
    if data.uid:
        user_doc = db_firebase.collection("users").document(data.uid).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            logger.info(f"Usuário encontrado por UID: {user_doc.id} -> {user_data}")
            return {
                "user_id": data.uid,
                "nome": user_data["nome"],
                "email": user_data["email"],
                "idade": user_data["idade"],
                "avatar": user_data["avatar"],
                "cep": user_data.get("cep", "")
            }
        else:
            logger.warning(f"Nenhum usuário encontrado com UID: {data.uid}")

    # Se houver email
    if data.email:
        email_clean = data.email.strip().lower()
        users_ref = db_firebase.collection("users").get()
        logger.info(f"Total de usuários no Firebase: {len(users_ref)}")
        for user_doc in users_ref:
            user_data = user_doc.to_dict()
            logger.info(f"Verificando usuário {user_doc.id} -> {user_data.get('email')}")
            if user_data.get("email", "").strip().lower() == email_clean:
                logger.info(f"Usuário encontrado por email: {user_doc.id}")
                return {
                    "user_id": user_doc.id,
                    "nome": user_data["nome"],
                    "email": user_data["email"],
                    "idade": user_data["idade"],
                    "avatar": user_data["avatar"],
                    "cep": user_data.get("cep", "")
                }
        logger.warning(f"Nenhum usuário encontrado com email: {email_clean}")

    raise HTTPException(status_code=404, detail="Usuário não encontrado")

@app.get("/posto_proximo/{user_id}")
async def posto_proximo(user_id: str):
    # Pega usuário no Firebase
    user_doc = db_firebase.collection("users").document(user_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    user_data = user_doc.to_dict()
    nome = user_data.get("nome", "Usuário").split()[0]
    cep = re.sub(r'\D', '', user_data.get("cep", ""))

    if not cep:
        return {"postos_proximos": []}

    async def buscar_postos(cep, primeiro_nome):
        try:
            async with aiohttp.ClientSession() as session:
                # Geocode pelo CEP
                geocode_url = (
                    f"https://maps.googleapis.com/maps/api/geocode/json"
                    f"?components=postal_code:{cep}|country:BR&key={GOOGLE_API_KEY}"
                )
                async with session.get(geocode_url) as resp:
                    geocode_data = await resp.json()

                if geocode_data.get("status") != "OK" or not geocode_data.get("results"):
                    return []

                location = geocode_data["results"][0]["geometry"]["location"]
                lat, lng = location["lat"], location["lng"]

                bairro = ""
                cidade = ""
                for comp in geocode_data["results"][0]["address_components"]:
                    if "sublocality_level_1" in comp["types"] or "neighborhood" in comp["types"]:
                        bairro = comp["long_name"]
                    if "administrative_area_level_2" in comp["types"]:
                        cidade = comp["long_name"]

                query = f"posto de saúde, {bairro}, {cidade}"
                places_url = (
                    f"https://maps.googleapis.com/maps/api/place/textsearch/json"
                    f"?query={query}&location={lat},{lng}&radius=4500&key={GOOGLE_API_KEY}"
                )
                async with session.get(places_url) as resp:
                    places_data = await resp.json()

                if places_data.get("status") != "OK" or not places_data.get("results"):
                    return []

                postos_filtrados = []
                for place in places_data["results"]:
                    endereco = place.get("formatted_address") or place.get("vicinity") or ""
                    endereco_cep = re.sub(r'\D', '', endereco)
                    if cep in endereco_cep or (bairro.lower() in endereco.lower()):
                        postos_filtrados.append({
                            "nome": place.get("name", "Posto"),
                            "endereco": endereco
                        })

                if not postos_filtrados:
                    postos_filtrados = [
                        {"nome": place.get("name", "Posto"),
                         "endereco": place.get("formatted_address") or place.get("vicinity") or "Endereço não disponível"}
                        for place in places_data["results"][:10]
                    ]

                return postos_filtrados[:10]

        except Exception as e:
            logger.warning(f"⚠️ Google Maps API falhou: {e}")
            return []

    postos_list = await buscar_postos(cep, nome)
    return {"postos_proximos": postos_list}


@app.post("/chat")
async def chat(msg: Mensagem, db: Session = Depends(get_db)):
    logger.info(f"/chat chamado — user_id={msg.user_id} texto={msg.texto!r}")
    user = db.query(User).filter(User.id == msg.user_id).first()

    if not user:
        # ❌ NÃO cria mais usuário fantasma
        raise HTTPException(status_code=404, detail="Usuário não encontrado. Faça login ou registre-se primeiro.")

    nome = user.nome if user.nome else "Usuário"
    resposta_ia = await responder_ia(msg.texto, user_id=msg.user_id, nome=nome)
    return {"resposta": resposta_ia}













