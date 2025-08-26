import firebase_admin
from firebase_admin import credentials, firestore

# Caminho para sua chave JSON do Firebase
cred = credentials.Certificate("chave_firebase.json")
firebase_admin.initialize_app(cred)

db_firebase = firestore.client()
