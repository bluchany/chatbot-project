import os
import requests
import json
from fastapi import FastAPI
from notion_client import Client
from pydantic import BaseModel, Field
from typing import List, Optional

# --- ⚙️ 1. 기본 설정 ---
NOTION_KEY = os.getenv("NOTION_KEY") #수정
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY") # 수정

DATABASE_IDS = {
    "의료재활": "2738ade5021080b786b0d8b0c07c1ea2",
    "교육보육": "2738ade5021080339203d7148d7d943b",
    "가족지원": "2738ade502108041a4c7f5ec4c3b8413",
    "돌봄양육": "2738ade5021080cf842df820fdbeb709",
    "복지": "2738ade5021080579e5be527ff1e80b2"
}
NOTION_PROPERTY_NAMES = {
    "title": "사업명", "category": "분류", "sub_category": "대상 특성",
    "start_age": "시작 연령", "end_age": "종료 연령", "support_detail": "상세 지원 내용",
    "contact": "문의처", "url1": "관련 홈페이지 1", "url2": "관련 홈페이지 2",
    "url3": "관련 홈페이지 3", "extra_req": "추가 자격요건"
}
# ---------------------

# ENHANCEMENT: 마지막 검색 결과를 저장할 전역 변수 (세션 상태 관리)
chat_session = {
    "last_results": [],
    "shown_count": 0
}

notion = Client(auth=NOTION_KEY)
app = FastAPI()

# --- 📥 2. 요청 모델 정의 ---
class SearchRequest(BaseModel):
    age: Optional[int] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    intent: Optional[str] = None # NEW: "더 보여줘" 와 같은 의도를 받기 위한 필드

class ChatRequest(BaseModel):
    question: str

# --- 🧠 3. 핵심 로직 함수들 ---

def extract_info_from_question(question: str) -> dict:
    API_URL = "https://api-inference.huggingface.co/models/meta-llama/Meta-Llama-3-8B-Instruct"
    headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}", "Content-Type": "application/json"}
    
    # ENHANCEMENT: 사용자의 의도(intent)를 파악하는 규칙 추가
    prompt = f"""
    [INST]
    You are a highly skilled specialist in analyzing user queries. Your task is to extract 'age (in months)', 'category', 'sub_category', and 'intent' from the user's question and return it ONLY as a valid JSON object.

    # Rules:
    - If the user asks to see more results (e.g., "더 보여줘", "다음"), set the 'intent' to "show_more".
    - Convert Korean age units like '살' or '돌' to months. (e.g., '두 돌' -> 24, '세 살' -> 36).
    - 'age' must be an integer.
    - 'category' must be one of: ["의료재활", "교육보육", "가족지원", "돌봄양육", "복지"].
    - 'sub_category' must be one of: ["장애/발달지연", "저소득", "임산부", "보호자", "다문화", "다자녀", "한부모"].
    - If a value is not found, use null.
    - Your output MUST be ONLY the JSON object itself.

    # Question: "{question}"
    [/INST]
    ```json
    """
    
    payload = {"inputs": prompt, "parameters": {"max_new_tokens": 150, "return_full_text": False}}

    try:
        response = requests.post(API_URL, headers=headers, json=payload)
        response.raise_for_status()
        response_text = response.json()[0]['generated_text']
        json_block_start = response_text.find('{')
        json_block_end = response_text.rfind('}') + 1
        
        if json_block_start != -1 and json_block_end != -1:
            json_string = response_text[json_block_start:json_block_end]
            extracted_info = json.loads(json_string)
            print(f"LLM 추출 정보: {extracted_info}")
            return extracted_info
        else: return {}
    except Exception as e:
        print(f"LLM 호출 또는 JSON 파싱 오류: {e}")
        return {}

def process_age_filter(age_in_months: int):
    # (이전과 동일)
    start_age_prop, end_age_prop = NOTION_PROPERTY_NAMES["start_age"], NOTION_PROPERTY_NAMES["end_age"]
    return [{"property": start_age_prop, "number": {"less_than_or_equal_to": age_in_months}},
            {"property": end_age_prop, "number": {"greater_than_or_equal_to": age_in_months}}]

def format_notion_results(pages: list, total_count: int, start_index: int) -> str:
    if not pages:
        return "표시할 추가 정보가 없어요."

    found_items = []
    for page in pages: # 이미 잘라진 페이지 목록을 받으므로 [:3] 제거
        properties = page.get("properties", {})
        # ... (이전과 동일한 정보 추출 로직)
        def get_rich_text(prop_name):
            prop = properties.get(prop_name, {}).get("rich_text", [{}])
            return prop[0].get("plain_text", "").strip() if prop else ""
        title = properties.get(NOTION_PROPERTY_NAMES["title"], {}).get("title", [{}])[0].get("plain_text", "")
        category = properties.get(NOTION_PROPERTY_NAMES["category"], {}).get("select", {}).get("name", "")
        target_prop = properties.get(NOTION_PROPERTY_NAMES["sub_category"], {}).get("multi_select", [])
        targets = [item.get("name") for item in target_prop]
        targets_text = ", ".join(targets) if targets else ""
        support_detail, contact, extra_req = (get_rich_text(NOTION_PROPERTY_NAMES[key]) for key in ["support_detail", "contact", "extra_req"])
        url1, url2, url3 = (properties.get(NOTION_PROPERTY_NAMES[f"url{i}"], {}).get("url", "") for i in range(1, 4))
        urls = [link for link in [url1, url2, url3] if link]
        urls_text = "\n".join(urls) if urls else ""
        item_text = f"[{category}]\n**{title}**"
        if targets_text: item_text += f"\n\n👥 **대상:** {targets_text}"
        if support_detail: item_text += f"\n\n📝 **지원 내용:**\n{support_detail}"
        if extra_req: item_text += f"\n\n📌 **추가 자격요건:**\n{extra_req}"
        if contact: item_text += f"\n\n📞 **문의처:** {contact}"
        if urls_text: item_text += f"\n\n🌐 **홈페이지:**\n{urls_text}"
        found_items.append(item_text)

    # ENHANCEMENT: 헤더 메시지를 상황에 맞게 변경
    end_index = start_index + len(found_items)
    header = f"총 {total_count}개의 정보 중 {start_index + 1}번째부터 {end_index}번째 결과를 보여드릴게요."
    separator = "\n\n---\n\n"
    final_text = header + separator + separator.join(found_items)
    
    # 남은 결과가 더 있는지 알려주는 안내 문구 추가
    if total_count > end_index:
        final_text += f"\n\n---\n더 보려면 '더 보여줘'라고 말씀해주세요. (남은 결과: {total_count - end_index}개)"
    else:
        final_text += "\n\n---\n📋 모든 결과를 보여드렸어요."
    
    return final_text

# --- 🚀 4. API 엔드포인트 ---
@app.get("/")
def read_root():
    return {"status": "챗봇 서버가 정상적으로 실행 중입니다."}

# @app.post("/search") 는 /chat 내부로 통합

@app.post("/chat")
def chat_with_bot(request: ChatRequest):
    extracted_info = extract_info_from_question(request.question)
    
    # 1. "더 보여줘" 의도 처리
    if extracted_info.get("intent") == "show_more":
        if not chat_session["last_results"]:
            return {"answer": "죄송해요, 먼저 검색을 해주셔야 추가 결과를 보여드릴 수 있어요."}
        
        start = chat_session["shown_count"]
        end = start + 3
        next_pages = chat_session["last_results"][start:end]
        
        if not next_pages:
            return {"answer": "더 이상 보여드릴 결과가 없어요."}
        
        chat_session["shown_count"] = end
        total = len(chat_session["last_results"])
        return {"answer": format_notion_results(next_pages, total, start)}

    # 2. 새로운 검색 처리
    if not extracted_info or not any(v for k, v in extracted_info.items() if k != 'intent'):
        return {"answer": "죄송해요, 질문을 잘 이해하지 못했어요. 나이, 대상 특성 등을 포함해서 다시 질문해주시겠어요?"}
        
    filters = []
    if extracted_info.get("age") is not None: filters.extend(process_age_filter(extracted_info["age"]))
    if extracted_info.get("category"): filters.append({"property": NOTION_PROPERTY_NAMES["category"], "select": {"equals": extracted_info["category"]}})
    if extracted_info.get("sub_category"): filters.append({"property": NOTION_PROPERTY_NAMES["sub_category"], "multi_select": {"contains": extracted_info["sub_category"]}})
    
    if not filters:
        return {"answer": "어떤 정보를 찾아드릴까요? 나이, 분류 등 조건을 알려주세요."}
    
    all_results = []
    for db_id in DATABASE_IDS.values():
        try:
            response = notion.databases.query(database_id=db_id, filter={"and": filters})
            all_results.extend(response.get("results", []))
        except Exception as e: print(f"Error searching database {db_id}: {e}")
    
    if not all_results:
        return {"answer": "요청하신 조건에 맞는 정보를 찾지 못했어요."}

    # 세션 상태 초기화 및 첫 결과 반환
    chat_session["last_results"] = all_results
    chat_session["shown_count"] = 3
    total = len(all_results)
    
    return {"answer": format_notion_results(all_results[:3], total, 0)}