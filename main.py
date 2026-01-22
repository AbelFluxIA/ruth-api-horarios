import re
import unicodedata
import httpx
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, timedelta
from itertools import cycle

# --- CONFIGURAÇÃO DE LOGS & APP ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SuperOdontoAPI")

app = FastAPI(title="Super API Odonto - Match & Schedule v4.1 Enterprise")

# --- 1. BANCO DE DADOS DOS PROFISSIONAIS ---
PROFESSIONALS_DB = {
    "Ramon": {
        "id": 5108599479861248, 
        "name": "Ramon Uchoa dos Anjos", 
        "keywords": ["ramon", "uchoa", "anjos"], 
        "color": "#000080" # Navy
    },
    "Vinicius": {
        "id": 5478954060808192, 
        "name": "Vinicius Targino Gomes de Almeida", 
        "keywords": ["vinicius", "targino", "vini"], 
        "color": "#BDB76B" # Dark Khaki
    },
    "Gabriela": {
        "id": 5859536659349504, 
        "name": "Gabriela Formiga da Silva", 
        "keywords": ["gabriela", "formiga", "gabi"], 
        "color": "#FFB6C1" # Light Pink
    },
    "Ruth": {
        "id": 5897012130873344, 
        "name": "Maria Ruth Costa Rodrigues", 
        "keywords": ["ruth", "maria ruth"], 
        "color": "#FF8C00" # Dark Orange
    },
    "Katianne": {
        "id": 6068925041999872, 
        "name": "Katianne Gomes Dias Bezerra", 
        "keywords": ["katianne", "katiane", "kati"], 
        "color": "#008000" # Green
    },
    "Mateus": {
        "id": 6462444026265600, 
        "name": "Mateus Correia Vidal Ataide", 
        "keywords": ["mateus", "matheus", "ataide"], 
        "color": "#C0C0C0" # Silver
    },
    "Camylla": {
        "id": 6567447868735488, 
        "name": "Camylla Farias Brandão", 
        "keywords": ["camylla", "camila", "faria", "farias"], 
        "color": "#9932CC" # Dark Orchid
    },
    "Enzo": {
        "id": 6595240503345152,
        "name": "Enzo Negreiros Araújo",
        "keywords": ["enzo", "negreiros", "araujo"],
        "color": "#008080" # Teal
    }
}

# --- 2. INTELLIGENT ROUTING & ROUND ROBIN ---

SPECIALTY_GROUPS = {
    "clareamento_limpeza": ["Ruth", "Gabriela"],
    "estetica_geral": ["Enzo", "Vinicius"] 
}

_rr_state = {
    "clareamento_limpeza": 0,
    "estetica_geral": 0
}

PROCEDURE_RULES = {
    "Camylla": ["canal", "endodontia", "nervo", "endo", "matar o nervo"],
    "Katianne": ["aparelho", "orto", "botox", "harmonizacao", "ferrinho", "manutencao", "invisalign", "preenchimento"],
    "Vinicius": ["faceta", "lente", "laminado", "restauracao estetica"],
    "Mateus": ["extracao", "siso", "arrancar", "tirar dente", "cirurgia", "exodontia", "molar", "terceiro molar"],
    "Ramon": ["protese", "coroa", "gengiva", "implante", "protocolo", "dentadura", "pino", "parafuso", "gengivoplastia"],
    "Enzo": ["urgencia", "dor de dente", "quebrou", "caiu"],
    
    "GROUP:clareamento_limpeza": ["limpeza", "clareamento", "raspagem", "tartaro", "profilaxia"],
    "GROUP:estetica_geral": ["estetica", "sorriso", "dente bonito"],

    "Gabriela": [
        "infantil", "crianca", "kids", "pediatria", 
        "restauracao", "obtura", "dentistica", 
        "consulta", "rotina", "avaliacao", "checkup", "olhada", "ver", "orcamento"
    ]
}

DEFAULT_PROFESSIONAL_KEY = "Gabriela"

# --- MODELOS DE DADOS ---
class ServiceRequest(BaseModel):
    service_text: str

class TimeSlot(BaseModel):
    start_time: str = Field(alias="from") 
    end_time: str = Field(alias="to")
    isSelectable: bool
    isSelected: bool
    professionalId: int

class DaySchedule(BaseModel):
    Date: str
    Week: str
    DayWeek: str
    AvaliableTimes: List[TimeSlot] 
    day: int
    month: int
    year: int
    jsonDate: str

# --- FUNÇÕES AUXILIARES ---

def normalize_text(text: str) -> str:
    if not text: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', text)
                   if unicodedata.category(c) != 'Mn').lower()

def sanitize_input(text: str) -> str:
    text_no_date = re.sub(r'\d{2}/\d{2}/\d{4}', '', text)
    text_no_time = re.sub(r'\d{2}:\d{2}', '', text_no_date)
    clean = text_no_time.replace("-", "").replace("–", "")
    return clean.strip()

def get_round_robin_professional(group_key: str):
    candidates = SPECIALTY_GROUPS.get(group_key)
    if not candidates:
        return None
    current_index = _rr_state.get(group_key, 0)
    selected_prof_name = candidates[current_index % len(candidates)]
    _rr_state[group_key] = current_index + 1
    logger.info(f"RODÍZIO [{group_key}]: Selecionado {selected_prof_name}")
    return PROFESSIONALS_DB.get(selected_prof_name)

def find_professional(text: str):
    sanitized_text = sanitize_input(text)
    clean_text = normalize_text(sanitized_text)
    
    logger.info(f"Buscando profissional. Texto original: '{text}' -> Sanitizado: '{clean_text}'")
    
    for key, data in PROFESSIONALS_DB.items():
        for keyword in data["keywords"]:
            if keyword in clean_text:
                logger.info(f"Match por NOME: {key}")
                return data

    for rule_key, keywords in PROCEDURE_RULES.items():
        if any(word in clean_text for word in keywords):
            if rule_key.startswith("GROUP:"):
                group_name = rule_key.split(":")[1]
                return get_round_robin_professional(group_name)
            return PROFESSIONALS_DB.get(rule_key)

    logger.warning("Nenhum termo específico encontrado. Direcionando para Triagem.")
    return PROFESSIONALS_DB[DEFAULT_PROFESSIONAL_KEY]

# --- ROTA PRINCIPAL ---
@app.post("/match-and-schedule")
async def match_and_schedule(request: ServiceRequest):
    logger.info("------------------------------------------------")
    logger.info(f"Iniciando processo para: {request.service_text}")

    # --- PASSO 1: IDENTIFICAR O PROFISSIONAL ---
    professional = find_professional(request.service_text)
    
    if not professional:
        professional = PROFESSIONALS_DB[DEFAULT_PROFESSIONAL_KEY]
    
    target_id = professional["id"]
    target_name = professional["name"]
    target_color = professional.get("color", "#CCCCCC")
    
    logger.info(f"Profissional Definido: {target_name} (ID: {target_id})")

    # --- PASSO 2: REQUISIÇÃO EXTERNA (CLINICORP) ---
    today = datetime.now()
    end_date = today + timedelta(days=15)
    
    date_from = today.strftime("%Y-%m-%d")
    date_to = end_date.strftime("%Y-%m-%d")
    
    url_clinicorp = (
        f"https://api.clinicorp.com/rest/v1/appointment/get_avaliable_days"
        f"?subscriber_id=odontomaria&code_link=57762"
        f"&from={date_from}&to={date_to}"
        f"&includeHolidays=&showAvailableTimes=X"
    )

    headers = {
        "accept": "application/json",
        "Authorization": "Basic b2RvbnRvbWFyaWE6NmZhMTUzMDItNmQ4Ni00MGNiLTlmZTMtNTk3NTY4Y2M2N2E1"
    }

    schedules_raw = []

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url_clinicorp, headers=headers, timeout=10.0)
            
            if response.status_code != 200:
                logger.error(f"Erro na Clinicorp. Status: {response.status_code}")
                return {
                    "success": False,
                    "message": "Sistema de agenda indisponível no momento.",
                    "cor": target_color
                }
            
            schedules_raw = response.json()
            if isinstance(schedules_raw, dict) and "error" in schedules_raw:
                 return {"success": False, "message": "Erro na API da clínica.", "cor": target_color}

    except Exception as e:
        logger.error(f"Exceção técnica: {str(e)}")
        return {
            "success": False,
            "message": "Erro de conexão com a agenda.",
            "cor": target_color
        }

    # --- PASSO 3: FILTRAGEM (ATUALIZADO COM REGRAS DE DIA E HORA) ---
    filtered_days = []
    target_id_str = str(target_id)

    try:
        if isinstance(schedules_raw, list):
            days_objects = [DaySchedule(**item) for item in schedules_raw]
            
            for day in days_objects:
                # -----------------------------------------------------
                # REGRA 1: Filtro de Fim de Semana (Sábado/Domingo)
                # -----------------------------------------------------
                try:
                    # Converte string "YYYY-MM-DD" para objeto data
                    current_date_obj = datetime.strptime(day.Date, "%Y-%m-%d")
                    # .weekday(): 0=Seg, 1=Ter, ..., 5=Sab, 6=Dom
                    if current_date_obj.weekday() >= 5:
                        # Se for Sábado (5) ou Domingo (6), pula esse dia inteiro
                        continue
                except ValueError:
                    # Se a data vier estranha, logamos e seguimos (segurança)
                    logger.warning(f"Erro ao processar data: {day.Date}")
                    pass

                my_slots = []
                for slot in day.AvaliableTimes:
                    
                    # REGRA 2: Filtro por ID do Profissional
                    if str(slot.professionalId) != target_id_str:
                        continue
                    
                    # -----------------------------------------------------
                    # REGRA 3: Filtro de Horário Limite (18:00)
                    # -----------------------------------------------------
                    # Comparamos strings diretamente: "18:00" > "09:00"
                    if slot.start_time >= "18:00":
                        continue

                    # Se passou nos filtros, adiciona
                    my_slots.append(slot)
                
                # Só adicionamos o dia se sobrou algum horário após os filtros
                if my_slots:
                    new_day = day.model_copy()
                    new_day.AvaliableTimes = my_slots
                    filtered_days.append(new_day)
        else:
             logger.warning("Formato de resposta inesperado da Clinicorp.")

    except Exception as e:
        logger.error(f"Erro ao filtrar dados: {str(e)}")
        return {
            "success": False,
            "message": "Erro ao processar os horários disponíveis.",
            "cor": target_color
        }

    # --- PASSO 4: RETORNO ---
    count_days = len(filtered_days)
    
    if count_days == 0:
        return {
            "success": True,
            "message": f"Poxa, o Dr(a). {target_name.split()[0]} não tem horários livres nos próximos 15 dias (Seg-Sex até as 18h).",
            "professional_id": target_id,
            "professional_name": target_name,
            "cor": target_color,
            "schedules": []
        }

    return {
        "success": True,
        "message": f"Encontrei horários para {target_name}.",
        "professional_id": target_id,
        "professional_name": target_name,
        "cor": target_color,
        "schedules": filtered_days
    }

@app.get("/debug/rotation-status")
def debug_rotation():
    return _rr_state

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
