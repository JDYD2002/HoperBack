import os
import re
import uuid
import json
from datetime import datetime
from firebase_admin import auth as fb_auth

import asyncio
import httpx
import aiohttp
import requests
from loguru import logger
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, field_validator
from firebase_config import db_firebase
import firebase_admin
from firebase_admin import credentials, firestore

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

# ====================== MODELOS FIREBASE ======================
class Cadastro(BaseModel):
    nome: str
    email: EmailStr
    cep: str
    idade: int
    uid: str | None = None       # preferir usar uid do Firebase Auth
    id_token: str | None = None  # opcional: se enviar, vamos verificar

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
    id_token: str | None = None  # opcional: login via token é o mais seguro

class Mensagem(BaseModel):
    user_id: str
    texto: str

# --- HELPER: normalizador ---
def _email_lower(s: str | None) -> str:
    return (s or "").strip().lower()

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
        "Você é Hoper Saúde, um assistente amigável e empático."
        "Quando o usuário relatar sintomas, responda em 1 a 2 frases curtas" 
        "sugerindo cuidados simples e gerais (hidratação, descanso, boa alimentação, higiene, sombra, ventilação, etc.)."
        "Nunca cite nomes de doenças ou remédios. Sempre finalize recomendando avaliação médica."
        "Finalize sempre orientando qual tipo de unidade de saúde procurar:"
        "Posto de saúde para sintomas leves ou acompanhamento."
        "UPA (Urgência/Pronto Atendimento) para sintomas moderados ou que causem desconforto maior."
        "Hospital para casos graves ou persistentes."
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
            "Quando o usuário relatar sintomas, responda em 1 a 2 frases curtas" 
            "sugerindo cuidados simples e gerais (hidratação, descanso, boa alimentação, higiene, sombra, ventilação, etc.)."
            "Nunca cite nomes de doenças ou remédios. Sempre finalize recomendando avaliação médica."
            "Finalize sempre orientando qual tipo de unidade de saúde procurar:"
            "Posto de saúde para sintomas leves ou acompanhamento."
            "UPA (Urgência/Pronto Atendimento) para sintomas moderados ou que causem desconforto maior."
            "Hospital para casos graves ou persistentes."
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

# ====================== ROTAS ======================
@app.post("/register")
async def register(cad: Cadastro):
    """
    Cadastra ou atualiza o usuário apenas no Firestore.
    """
    try:
        uid = None
        if cad.id_token:
            decoded = fb_auth.verify_id_token(cad.id_token)
            uid = decoded["uid"]
            cad.email = decoded.get("email", cad.email)
        elif cad.uid:
            uid = cad.uid

        if not uid:
            raise HTTPException(status_code=400, detail="UID obrigatório (use Firebase Auth).")

        email_clean = _email_lower(cad.email)
        avatar = avatar_por_idade(cad.idade)

        # Sincronizar Firestore SEM duplicar documento
        db_firebase.collection("users").document(uid).set({
            "nome": cad.nome.strip(),
            "email": email_clean,
            "cep": cad.cep.strip(),
            "idade": cad.idade,
            "avatar": avatar,
            "created_at": datetime.utcnow().isoformat(),
            "posto_enviado": 0
        }, merge=True)

        logger.success(f"Usuário {uid} sincronizado com Firestore com sucesso!")
        return {"user_id": uid, "avatar": avatar}

    except fb_auth.InvalidIdTokenError:
        logger.error("Token inválido recebido no registro.")
        raise HTTPException(status_code=401, detail="Token inválido.")
    except Exception as e:
        logger.error(f"Erro no registro: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao registrar usuário: {str(e)}")

@app.post("/login")
async def login(data: LoginModel):
    logger.info(f"Login chamado — uid={data.uid!r} email={data.email!r}")

    user_doc = None
    user_data = {}

    if data.uid:
        user_doc = db_firebase.collection("users").document(data.uid).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            logger.info(f"Usuário encontrado por UID: {user_doc.id} -> {user_data}")
        else:
            logger.info(f"Auto-provisionado users/{data.uid} a partir do Firebase Auth.")
            firebase_auth_user = firebase_admin.auth.get_user(data.uid)
            user_data = {
                "nome": firebase_auth_user.display_name or "Usuário",
                "email": firebase_auth_user.email,
                "idade": 0,
                "cep": "",
                "avatar": "adulto",
                "posto_enviado": 0,
                "created_at": datetime.utcnow().isoformat()
            }
            db_firebase.collection("users").document(data.uid).set(user_data)

    elif data.email:
        email_clean = data.email.strip().lower()
        users_ref = db_firebase.collection("users").get()
        for doc in users_ref:
            udata = doc.to_dict()
            if udata.get("email", "").strip().lower() == email_clean:
                user_data = udata
                data.uid = doc.id
                break

    if not user_data:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    nome_full = user_data.get("nome", "").strip()
    primeiro_nome = nome_full.split()[0] if nome_full else "Usuário"

    return {
        "user_id": data.uid,
        "nome": primeiro_nome,
        "email": user_data.get("email", ""),
        "idade": user_data.get("idade", 0),
        "avatar": user_data.get("avatar", "adulto"),
        "cep": user_data.get("cep", "")
    }

@app.get("/posto_proximo/{user_id}")
async def posto_proximo(user_id: str):
    user_doc = db_firebase.collection("users").document(user_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    user_data = user_doc.to_dict()
    nome_full = user_data.get("nome", "").strip()
    nome = nome_full.split()[0] if nome_full else "Usuário"
    cep = re.sub(r'\D', '', user_data.get("cep", ""))

    if not cep:
        return {"postos_proximos": []}

    async def buscar_postos(cep, primeiro_nome):
        try:
            async with aiohttp.ClientSession() as session:
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
async def chat(msg: Mensagem):
    logger.info(f"/chat chamado — user_id={msg.user_id} texto={msg.texto!r}")

    user_doc = db_firebase.collection("users").document(msg.user_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="Usuário não encontrado. Faça login ou registre-se primeiro.")

    user_data = user_doc.to_dict()
    nome = user_data.get("nome", "Usuário")
    resposta_ia = await responder_ia(msg.texto, user_id=msg.user_id, nome=nome)
    return {"resposta": resposta_ia}
