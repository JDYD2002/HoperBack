import os, json
from firebase_admin import credentials, firestore
import firebase_admin

cred_json = os.getenv("FIREBASE_CRED_JSON")
cred_dict = json.loads(cred_json)  # Aqui não deve dar erro
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)

db_firebase = firestore.client()
