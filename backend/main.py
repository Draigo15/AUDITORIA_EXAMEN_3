# backend/main.py
import sqlite3
import re
import sys 
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Literal

# --- AÑADIDO: Importaciones para Monitorización ---
from prometheus_fastapi_instrumentator import Instrumentator
from loguru import logger

# LangChain
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_ollama.llms import OllamaLLM
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain.chains import RetrievalQA
from langchain_core.runnables import RunnableBranch, RunnableLambda, RunnablePassthrough

# --- AÑADIDO: CONFIGURACIÓN DE LOGGING ESTRUCTURADO ---
logger.remove()
logger.add(sys.stdout, serialize=True, enqueue=True)

class InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.log(level, record.getMessage())

logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
logging.getLogger("uvicorn").handlers = [InterceptHandler()]
logging.getLogger("uvicorn.access").handlers = [InterceptHandler()]


# --- CONFIGURACIÓN Y MODELOS ---
VECTOR_STORE_DIR = "vector_store"
DB_PATH = "/tmp/tickets.db"
app = FastAPI(title="Corporate EPIS Pilot API - Advanced Flow")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# --- CREAR TABLA DE TICKETS AL INICIAR ---
def init_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        description TEXT NOT NULL,
        status TEXT NOT NULL
    )
    ''')
    conn.commit()
    conn.close()

init_database()

# --- AÑADIDO: INSTRUMENTACIÓN DE PROMETHEUS ---
Instrumentator().instrument(app).expose(app)


llm = OllamaLLM(model="smollm:360m", temperature=0, base_url="http://host.docker.internal:11434")
embeddings = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-large")
vector_store = Chroma(persist_directory=VECTOR_STORE_DIR, embedding_function=embeddings)
retriever = vector_store.as_retriever()

# --- LÓGICA DE LANGCHAIN (MODIFICADA) ---
rag_prompt_template = "Usa el siguiente contexto para responder en español de forma concisa y útil a la pregunta.\nContexto: {context}\nPregunta: {question}\nRespuesta:"
rag_prompt = PromptTemplate.from_template(rag_prompt_template)
rag_chain = RetrievalQA.from_chain_type(llm=llm, chain_type="stuff", retriever=retriever, chain_type_kwargs={"prompt": rag_prompt})

def create_support_ticket(description: str) -> str:
    """Crea un ticket de soporte y devuelve un mensaje de confirmación."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    problem_description = description.replace("ACTION_CREATE_TICKET:", "").strip()
    if not problem_description:
        problem_description = "Problema no especificado por el usuario."

    cursor.execute("INSERT INTO tickets (description, status) VALUES (?, ?)", (problem_description, "Abierto"))
    ticket_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return f"De acuerdo. He creado el ticket de soporte #{ticket_id} con tu problema: '{problem_description}'. El equipo técnico se pondrá en contacto contigo."

# Router de intenciones simplificado para smollm:360m
router_prompt = PromptTemplate(
    template="""Responde SOLO con una de estas palabras: pregunta_general, reporte_de_problema o despedida.
    - pregunta_general: Pregunta sobre información (¿qué?, ¿cuántos?, ¿cómo?)
    - reporte_de_problema: Describe un problema o error
    - despedida: Gracias, adiós, perfecto, vale
    
    Pregunta: {question}
    Respuesta:""",
    input_variables=["question"],
)

def classify_intent(question: str) -> str:
    """Clasifica la intención del usuario de forma robusta."""
    # Primero revisamos keywords para ser más robustos
    question_lower = question.lower()
    goodbye_keywords = ["gracias", "adios", "adiós", "perfecto", "vale", "ok", "grax", "bye"]
    problem_keywords = ["problema", "error", "no funciona", "no puedo", "fallo", "roto", "avería"]
    
    if any(keyword in question_lower for keyword in goodbye_keywords):
        return "despedida"
    if any(keyword in question_lower for keyword in problem_keywords):
        return "reporte_de_problema"
    
    # Si no hay keywords, preguntamos al modelo
    try:
        chain = router_prompt | llm
        response = chain.invoke({"question": question}).strip().lower()
        if "reporte" in response or "problema" in response:
            return "reporte_de_problema"
        if "despedida" in response or "gracias" in response:
            return "despedida"
        return "pregunta_general"
    except:
        return "pregunta_general"

problem_chain = RunnableLambda(lambda x: {"query": x}) | rag_chain

# --- ENDPOINT DE LA API ---
@app.get("/ask")
def ask_question(question: str):
    try:
        if question.startswith("ACTION_CREATE_TICKET:"):
            description = question.split(":", 1)[1]
            return {"answer": create_support_ticket(description), "follow_up_required": False}

        intent = classify_intent(question)
        
        answer = ""
        follow_up = False

        if intent == "pregunta_general":
            result = problem_chain.invoke(question)
            answer = result.get("result", "No se encontró respuesta.")
        elif intent == "reporte_de_problema":
            result = problem_chain.invoke(question)
            solution = result.get("result", "No he encontrado una solución específica en mis documentos.")
            answer = f"{solution}\n\n¿Esta información soluciona tu problema?"
            follow_up = True
        # CAMBIO 3: Añadimos el manejo de la nueva intención
        elif intent == "despedida":
            answer = "De nada, ¡un placer ayudar! Si tienes cualquier otra consulta, aquí estaré. 😊"
            follow_up = False
            
        return {"answer": answer, "follow_up_required": follow_up}

    except Exception as e:
        # AÑADIDO: Usamos logger en lugar de print para un registro estructurado
        logger.error(f"Error en el endpoint /ask: {e}")
        return {"answer": "Lo siento, ha ocurrido un error.", "follow_up_required": False}