import os, json
import firebase_admin
from firebase_admin import credentials, firestore

cred_json = os.getenv("FIREBASE_CRED_JSON")
cred_dict = json.loads(cred_json)

# Substitui os "\n" escapados por quebras de linha reais
cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")

cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)

db_firebase = firestore.client()
