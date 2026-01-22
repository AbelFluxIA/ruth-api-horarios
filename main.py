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

app = FastAPI(title="Super API Odonto - Match & Schedule v4.0 Enterprise")

# --- 1. BANCO DE DADOS DOS PROFISSIONAIS (ATUALIZADO) ---
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
        "color": "#008080" # Teal (Nova cor para o Enzo)
    }
}

# --- 2. INTELLIGENT ROUTING & ROUND ROBIN ---

# Definição de grupos de especialidades para rodízio
SPECIALTY_GROUPS = {
    "clareamento_limpeza": ["Ruth", "Gabriela"],
    "estetica_geral": ["Enzo", "Vinicius"] 
    # Nota: Vinicius é focado em facetas, mas se o cliente disser só "estética", 
    # podemos rodar entre eles ou priorizar Enzo. Vou colocar no rodízio.
}

# Estado em memória para controlar o rodízio (Round Robin)
# Armazena o índice do último profissional usado para cada grupo.
_rr_state = {
    "clareamento_limpeza": 0,
    "estetica_geral": 0
}

# Regras de Palavras-Chave -> Grupo ou Profissional Específico
PROCEDURE_RULES = {
    # Procedimentos Específicos (Sem conflito direto)
    "Camylla": ["canal", "endodontia", "nervo", "endo", "matar o nervo"],
    "Katianne": ["aparelho", "orto", "botox", "harmonizacao", "ferrinho", "manutencao", "invisalign", "preenchimento"],
    "Vinicius": ["faceta", "lente", "laminado", "restauracao estetica"], # Termos muito específicos do Vinicius
    "Mateus": ["extracao", "siso", "arrancar", "tirar dente", "cirurgia", "exodontia", "molar", "terceiro molar"],
    "Ramon": ["protese", "coroa", "gengiva", "implante", "protocolo", "dentadura", "pino", "parafuso", "gengivoplastia"],
    "Enzo": ["urgencia", "dor de dente", "quebrou", "caiu"], # Urgência vai pro Enzo
    
    # Procedimentos Compartilhados (Acionam Rodízio)
    "GROUP:clareamento_limpeza": ["limpeza", "clareamento", "raspagem", "tartaro", "profilaxia"],
    "GROUP:estetica_geral": ["estetica", "sorriso", "dente bonito"],

    # Triagem Geral (Gabriela - Default para crianças e geral)
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
    """Remove acentos e coloca em minúsculo."""
    if not text: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', text)
                   if unicodedata.category(c) != 'Mn').lower()

def sanitize_input(text: str) -> str:
    """
    Limpeza cirúrgica para remover datas e lixo de strings complexas.
    Ex: '22/01/2026 12:00 - Dr. Ramon' -> Retorna apenas o relevante para busca.
    """
    # 1. Se tiver padrão de data (DD/MM/YYYY), removemos para não confundir
    text_no_date = re.sub(r'\d{2}/\d{2}/\d{4}', '', text)
    # 2. Removemos horas (HH:MM)
    text_no_time = re.sub(r'\d{2}:\d{2}', '', text_no_date)
    # 3. Removemos caracteres de separação comuns em agendamentos
    clean = text_no_time.replace("-", "").replace("–", "")
    return clean.strip()

def get_round_robin_professional(group_key: str):
    """Lógica de Rodízio para balanceamento de carga."""
    candidates = SPECIALTY_GROUPS.get(group_key)
    if not candidates:
        return None
    
    # Recupera índice atual e calcula o próximo
    current_index = _rr_state.get(group_key, 0)
    selected_prof_name = candidates[current_index % len(candidates)]
    
    # Atualiza o estado para a próxima vez
    _rr_state[group_key] = current_index + 1
    
    logger.info(f"RODÍZIO [{group_key}]: Selecionado {selected_prof_name}")
    return PROFESSIONALS_DB.get(selected_prof_name)

def find_professional(text: str):
    # 1. Sanitização de Input (Resolve o problema da data)
    sanitized_text = sanitize_input(text)
    clean_text = normalize_text(sanitized_text)
    
    logger.info(f"Buscando profissional. Texto original: '{text}' -> Sanitizado: '{clean_text}'")
    
    # 2. Busca por NOME do profissional (Prioridade Absoluta)
    for key, data in PROFESSIONALS_DB.items():
        for keyword in data["keywords"]:
            # Usamos regex boundary (\b) para evitar matches parciais se necessário, 
            # mas 'in' simples costuma funcionar bem para nomes.
            if keyword in clean_text:
                logger.info(f"Match por NOME: {key}")
                return data

    # 3. Busca por PALAVRAS-CHAVE (Procedimentos)
    for rule_key, keywords in PROCEDURE_RULES.items():
        if any(word in clean_text for word in keywords):
            
            # Caso A: É um GRUPO de rodízio
            if rule_key.startswith("GROUP:"):
                group_name = rule_key.split(":")[1]
                return get_round_robin_professional(group_name)
            
            # Caso B: É um profissional específico
            return PROFESSIONALS_DB.get(rule_key)

    # 4. FALLBACK (Rede de Segurança)
    logger.warning("Nenhum termo específico encontrado. Direcionando para Triagem.")
    return PROFESSIONALS_DB[DEFAULT_PROFESSIONAL_KEY]

# --- ROTA PRINCIPAL ---
@app.post("/match-and-schedule")
async def match_and_schedule(request: ServiceRequest):
    logger.info("------------------------------------------------")
    logger.info(f"Iniciando processo para: {request.service_text}")

    # --- PROTEÇÃO CONTRA INPUT SUJO DE DATA ---
    # Se o texto for APENAS uma data mal formatada, podemos rejeitar ou limpar.
    # A função sanitize_input dentro de find_professional já cuida da extração do nome/procedimento.

    # --- PASSO 1: IDENTIFICAR O PROFISSIONAL ---
    professional = find_professional(request.service_text)
    
    if not professional:
        # Fallback de segurança extrema, caso algo dê muito errado no DB
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
    
    # URL e Headers (Mantidos do original, verifique se o Token expirou)
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
            # Validação extra se a resposta for um dicionário de erro ao invés de lista
            if isinstance(schedules_raw, dict) and "error" in schedules_raw:
                 return {"success": False, "message": "Erro na API da clínica.", "cor": target_color}

    except Exception as e:
        logger.error(f"Exceção técnica: {str(e)}")
        return {
            "success": False,
            "message": "Erro de conexão com a agenda.",
            "cor": target_color
        }

    # --- PASSO 3: FILTRAGEM ---
    filtered_days = []
    target_id_str = str(target_id)

    try:
        # Parsing seguro dos dados
        if isinstance(schedules_raw, list):
            days_objects = [DaySchedule(**item) for item in schedules_raw]
            
            for day in days_objects:
                my_slots = []
                for slot in day.AvaliableTimes:
                    # Comparação de String para garantir
                    if str(slot.professionalId) == target_id_str:
                        my_slots.append(slot)
                
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
            "success": True, # É sucesso técnico, mas sem horário
            "message": f"Poxa, o Dr(a). {target_name.split()[0]} não tem horários livres nos próximos 15 dias.",
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

# Endpoint Extra para verificação do Rodízio (Opcional, para debug)
@app.get("/debug/rotation-status")
def debug_rotation():
    return _rr_state

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
