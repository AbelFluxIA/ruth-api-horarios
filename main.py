from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Any
import unicodedata
import httpx
from datetime import datetime, timedelta
import json

app = FastAPI(title="Super API Odonto - Match & Schedule")

# --- 1. BANCO DE DADOS DOS PROFISSIONAIS (AGORA COM CORES) ---
PROFESSIONALS = {
    "Dayara": {
        "id": 4773939817545728, 
        "name": "Dayara Boscolo", 
        "keywords": ["dayara"], 
        "color": "#00FFFF" # Cyan
    },
    "Ramon": {
        "id": 5108599479861248, 
        "name": "Ramon Uchoa dos Anjos", 
        "keywords": ["ramon", "uchoa"], 
        "color": "#000080" # Navy
    },
    "Vinicius": {
        "id": 5478954060808192, 
        "name": "Vinicius Targino Gomes de Almeida", 
        "keywords": ["vinicius", "targino"], 
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
        "keywords": ["camylla", "camila", "faria"], 
        "color": "#9932CC" # Dark Orchid
    }
}

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

def find_professional(text: str):
    clean_text = normalize_text(text)
    print(f"LOG: Buscando profissional para o termo: {clean_text}")
    
    # 1. Busca por nome direto
    for key, data in PROFESSIONALS.items():
        for keyword in data["keywords"]:
            if keyword in clean_text:
                return data

    # 2. Busca por procedimento
    if "canal" in clean_text or "endodontia" in clean_text: return PROFESSIONALS["Camylla"]
    if any(word in clean_text for word in ["aparelho", "orto", "botox", "harmonizacao"]): return PROFESSIONALS["Katianne"]
    if any(word in clean_text for word in ["faceta", "lente", "estetica"]): return PROFESSIONALS["Vinicius"]
    if any(word in clean_text for word in ["extracao", "siso", "arrancar"]): return PROFESSIONALS["Mateus"]
    if any(word in clean_text for word in ["protese", "coroa", "gengiva", "implante", "protocolo"]): return PROFESSIONALS["Ramon"]
    if any(word in clean_text for word in ["urgencia", "dor", "infantil", "crianca", "kids", "pediatria"]): return PROFESSIONALS["Gabriela"]
    
    common_procedures = ["clareamento", "limpeza", "profilaxia", "restauracao", "obtura", "dentistica"]
    if any(word in clean_text for word in common_procedures): return PROFESSIONALS["Gabriela"]

    return None

# --- ROTA PRINCIPAL ---
@app.post("/match-and-schedule")
async def match_and_schedule(request: ServiceRequest):
    print("------------------------------------------------")
    print(f"LOG: Iniciando processo para: {request.service_text}")

    # --- PASSO 1 & 2: IDENTIFICAR O PROFISSIONAL ---
    professional = find_professional(request.service_text)
    
    if not professional:
        print("LOG: Erro - Profissional não identificado.")
        return {
            "success": False,
            "message": "Não conseguimos identificar o serviço ou profissional desejado.",
            "cor": None # Retorna nulo para segurança do frontend
        }
    
    # Extração de dados (Incluindo a COR)
    target_id = professional["id"]
    target_name = professional["name"]
    target_color = professional.get("color", "#CCCCCC") # Fallback cor cinza se falhar
    
    print(f"LOG: Profissional identificado: {target_name} (Color: {target_color})")

    # --- PASSO 3: REQUISIÇÃO EXTERNA (CLINICORP) ---
    today = datetime.now()
    end_date = today + timedelta(days=15)
    
    date_from = today.strftime("%Y-%m-%d")
    date_to = end_date.strftime("%Y-%m-%d")
    
    # ! IMPORTANTE: Em produção, mova credentials para variáveis de ambiente (.env)
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
            response = await client.get(url_clinicorp, headers=headers)
            
            if response.status_code != 200:
                print(f"LOG: Erro na Clinicorp. Status: {response.status_code}")
                return {
                    "success": False,
                    "message": f"Erro ao conectar com a agenda. Código: {response.status_code}",
                    "cor": target_color
                }
            
            schedules_raw = response.json()
            if not isinstance(schedules_raw, list):
                 return {
                     "success": False, 
                     "message": "Formato de agenda inválido do sistema externo.",
                     "cor": target_color
                 }

    except Exception as e:
        print(f"LOG: Exceção ao buscar horários: {str(e)}")
        return {
            "success": False,
            "message": "Erro técnico ao buscar horários.",
            "cor": target_color
        }

    # --- PASSO 4: FILTRAR PELO ID ---
    filtered_days = []
    target_id_str = str(target_id)

    try:
        days_objects = [DaySchedule(**item) for item in schedules_raw]
        
        for day in days_objects:
            my_slots = []
            for slot in day.AvaliableTimes:
                if str(slot.professionalId) == target_id_str:
                    my_slots.append(slot)
            
            if my_slots:
                new_day = day.model_copy()
                new_day.AvaliableTimes = my_slots
                filtered_days.append(new_day)

    except Exception as e:
        print(f"LOG: Erro ao filtrar dados: {str(e)}")
        return {
            "success": False,
            "message": "Erro ao processar os horários disponíveis.",
            "cor": target_color
        }

    # --- PASSO 5: RESPOSTA FINAL (COM COR) ---
    count_days = len(filtered_days)
    print(f"LOG: Sucesso. Encontrados {count_days} dias.")

    if count_days == 0:
        return {
            "success": True,
            "message": f"Identificamos {target_name}, mas não há horários livres (15 dias).",
            "professional_id": target_id,
            "professional_name": target_name,
            "cor": target_color, # <--- AQUI
            "schedules": []
        }

    return {
        "success": True,
        "message": "Horários encontrados com sucesso.",
        "professional_id": target_id,
        "professional_name": target_name,
        "cor": target_color, # <--- E AQUI
        "schedules": filtered_days
                }
