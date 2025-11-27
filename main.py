# main.py (Google Forms Version - Clean & Light)
import os
import json
import uuid
import logging
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Dict, Any
from dotenv import load_dotenv

# [최적화] utils import 최상단 배치
from utils import (
    redis_client,
    MAIN_ANSWER_CACHE_KEY,
    extract_info_from_question,
    notion,                     
    LLM_MODEL,
    DATABASE_IDS,
    get_supabase_pages_by_ids, 
    format_search_results      
)

# ------------------------------------

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "your_strong_admin_password_here")

app = FastAPI()

# --- CORS 설정 ---
origins = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "*"  # 배포 시 실제 도메인으로 변경 권장
]
app.add_middleware(
    CORSMiddleware, 
    allow_origins=origins, 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"]
)

# [삭제됨] 불필요한 SessionMiddleware 제거 (Stateless 지향)

# --- 정적 파일 서빙 ---
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Redis 키 이름 ---
JOB_QUEUE_KEY = "chatbot:job_queue"
JOB_RESULTS_KEY = "chatbot:job_results"

# --- 요청 모델 ---
class ChatRequest(BaseModel):
    question: str
    last_result_ids: List[str] = [] 
    shown_count: int = 0
    chat_history: List[Dict[str, Any]] = [] 

# [삭제됨] FeedbackRequest 모델 삭제 (Google Forms 사용)

# --- API 엔드포인트 ---

@app.get("/")
async def read_root():
    if os.path.exists('static/index.html'):
        return FileResponse('static/index.html')
    return {"message": "Server is running. (No index.html found)"}

@app.post("/admin/clear_cache")
def clear_all_caches(secret: str = Query(None)):
    if secret != ADMIN_SECRET_KEY: raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        logger.warning("--- 🔒 관리자 요청: Redis 캐시 초기화 ---")
        keys_to_delete = []
        for key_pattern in ["extract:*", "summary:*", "chatbot:*"]: 
            keys_to_delete.extend(redis_client.keys(key_pattern))
        
        if keys_to_delete:
            redis_client.delete(*keys_to_delete)
        
        return {"status": "Redis 캐시 삭제 완료", "deleted_keys": len(keys_to_delete)}
    except Exception as e:
        logger.error(f"캐시 삭제 오류: {e}")
        raise HTTPException(status_code=500, detail=f"오류: {e}")

@app.post("/chat")
def chat_with_bot(chat_request: ChatRequest):
    question = chat_request.question.strip()
    chat_history = chat_request.chat_history
    logger.info(f"📩 받은 질문: {question}")

    if not notion: raise HTTPException(status_code=503, detail="Notion API Key 설정 오류")

    normalized_input = question.strip().lower()

    # 1. AI 의도 분석
    try:
        extracted_info = extract_info_from_question(question, chat_history)
        if extracted_info.get("error"):
             logger.error(f"Intent Error: {extracted_info['error']}")
             raise HTTPException(status_code=500, detail=extracted_info["error"])
    except Exception as e:
        logger.error(f"질문 분석 예외: {e}")
        raise HTTPException(status_code=500, detail=f"질문 분석 중 오류: {e}")

    # 2. 안전 및 기본 의도 처리
    intent = extracted_info.get("intent")

    if intent == "safety_block":
        return {"status": "complete", "answer": "비속어는 삼가주세요. 😥 복지 정보에 대해 질문해 주세요.", "last_result_ids": [], "total_found": 0}
    
    if intent == "exit":
        return {"status": "complete", "answer": "네, 알겠습니다. 언제든 다시 찾아주세요! 😊", "last_result_ids": [], "total_found": 0}
    
    if intent == "reset":
        return {"status": "complete", "answer": "대화를 초기화했습니다. 무엇이 궁금하신가요? 🤖", "last_result_ids": [], "total_found": 0}

    if intent == "out_of_scope":
        return {"status": "complete", "answer": "저는 도봉구 영유아 복지 정보만 알려드릴 수 있어요. 😅", "last_result_ids": [], "total_found": 0}

    if intent == "small_talk":
        answer = "안녕하세요! 도봉구 영유아 복지 챗봇입니다. 무엇을 도와드릴까요?"
        if "고마" in normalized_input or "감사" in normalized_input: 
            answer = "도움이 되어 기쁩니다! 😊 언제든 또 물어봐 주세요."
        return {"status": "complete", "answer": answer, "last_result_ids": [], "total_found": 0}

    if intent == "clarify_category":
        age_info = extracted_info.get("age")
        age_text = f"{age_info}개월 아기" if age_info else "자녀"
        return {
            "status": "clarify", 
            "answer": f"{age_text}를 위한 어떤 정보가 궁금하신가요?", 
            "options": list(DATABASE_IDS.keys()), 
            "last_result_ids": [], 
            "total_found": 0
        }

    # 3. '더 보기' 처리
    show_more_keywords = ["더", "다음", "계속", "more", "next"]
    is_show_more = (any(k in normalized_input for k in show_more_keywords) or intent == "show_more")
    
    if is_show_more and chat_request.last_result_ids:
        logger.info("[API] '더 보기' 요청 처리")
        try:
            start = chat_request.shown_count
            end = start + 2
            target_ids = chat_request.last_result_ids[start:end]
            
            if not target_ids:
                return {
                    "status": "complete", 
                    "answer": "더 이상 표시할 결과가 없습니다.", 
                    "last_result_ids": chat_request.last_result_ids, 
                    "total_found": len(chat_request.last_result_ids),
                    "shown_count": chat_request.shown_count
                }

            next_pages = get_supabase_pages_by_ids(target_ids)
            formatted_body = format_search_results(next_pages)
            
            header = f"🔎 **추가 정보 ({start+1}~{start+len(next_pages)}번째)**"
            answer_text = f"{header}\n\n<hr>\n\n{formatted_body}"
            
            remaining = len(chat_request.last_result_ids) - end
            if remaining > 0:
                answer_text += f"\n\n<hr>\n\n🔍 **아직 결과가 더 남아있습니다.**\n'더 보여줘' 또는 '다음'을 입력해 보세요."
            else:
                answer_text += "\n\n<hr>\n\n✅ **모든 결과를 확인했습니다.**"

            return {
                "status": "complete", 
                "answer": answer_text, 
                "last_result_ids": chat_request.last_result_ids,
                "total_found": len(chat_request.last_result_ids),
                "shown_count": end 
            }
        except Exception as e:
            logger.error(f"❌ 더 보기 처리 오류: {e}")
            return {"status": "error", "answer": "추가 정보를 불러오는 중 오류가 발생했습니다."}

    # 4. 일반 검색
    try:
        cached_data = redis_client.hget(MAIN_ANSWER_CACHE_KEY, question)
        if cached_data:
            logger.info(f"✅ [API] Cache Hit!")
            return json.loads(cached_data.decode('utf-8'))
    except Exception: pass

    logger.info("[API] Cache Miss. Job 생성.")
    try: 
        job_id = str(uuid.uuid4())
        job_data = {
            "job_id": job_id, 
            "question": question, 
            "chat_history": chat_history
        }
        redis_client.rpush(JOB_QUEUE_KEY, json.dumps(job_data, ensure_ascii=False).encode('utf-8'))
        return {"message": "요청 접수 완료.", "job_id": job_id}
    except Exception as e: 
        logger.error(f"Job 생성 실패: {e}")
        raise HTTPException(status_code=500, detail=f"Job 생성 오류: {e}")

@app.get("/get_result/{job_id}")
def get_job_result(job_id: str):
    try:
        result_bytes = redis_client.hget(JOB_RESULTS_KEY, job_id)
        if result_bytes:
            return json.loads(result_bytes.decode('utf-8'))
        else:
            return {"status": "pending"}
    except Exception as e: 
        raise HTTPException(status_code=500, detail=f"오류: {e}")

# [삭제됨] /feedback 엔드포인트 삭제
# 이제 프론트엔드에서 Google Form 링크(<a> 태그)를 직접 띄우면 됩니다.

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)