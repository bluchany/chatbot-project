import redis
import os
import json
import time
import hashlib
import re
from typing import List, Optional
from dotenv import load_dotenv
import google.generativeai as genai
from notion_client import Client as NotionClient
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from supabase import create_client

# --- 1. 설정 로드 ---
load_dotenv()
NOTION_KEY = os.getenv("NOTION_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# --- 2. 전역 변수 ---
DATABASE_IDS = {
    "의료재활": "2738ade5021080b786b0d8b0c07c1ea2",
    "교육보육": "2738ade5021080339203d7148d7d943b",
    "가족지원": "2738ade502108041a4c7f5ec4c3b8413",
    "돌봄양육": "2738ade5021080cf842df820fdbeb709",
    "생활지원": "2738ade5021080579e5be527ff1e80b2"
}
NOTION_PROPERTY_NAMES = {
    "title": "사업명", "category": "분류", "sub_category": "대상 특성",
    "start_age": "시작 월령(개월)", "end_age": "종료 월령(개월)", "support_detail": "상세 지원 내용",
    "contact": "문의처", "url1": "관련 홈페이지 1", "url2": "관련 홈페이지 2",
    "url3": "관련 홈페이지 3", "extra_req": "추가 자격요건"
}

# --- 3. 클라이언트 초기화 ---
LLM_MODEL = None
if GEMINI_API_KEY:
    try:
        # [핵심 수정] transport='rest' 추가! (이게 통신 안정성을 높여줍니다)
        genai.configure(api_key=GEMINI_API_KEY, transport="rest")
        
        # [체크] 모델명이 '2.5'인지 꼭 확인하세요.
        LLM_MODEL = genai.GenerativeModel('gemini-2.5-flash') 
        print("✅ Utils: Gemini 모델 로드 완료 (Mode: REST)")
    except Exception as e:
        print(f"⚠️ Utils: Gemini API 설정 오류: {e}")

notion = NotionClient(auth=NOTION_KEY) if NOTION_KEY else None

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    print("⚠️ Utils: Supabase 설정이 없습니다.")
    supabase = None

# --- 4. Redis 클라이언트 초기화 ---
redis_client = redis.Redis(host=REDIS_HOST, port=6379, db=0, decode_responses=False)

MAIN_ANSWER_CACHE_KEY = "chatbot:main_answers"
MAIN_ANSWER_CACHE_TTL = 3600

connected = False
for i in range(10):
    try:
        redis_client.ping()
        connected = True
        break
    except redis.exceptions.ConnectionError:
        time.sleep(1)

if not connected:
    raise Exception("Redis connection failed after retries")

# --- 5. 시스템 명령어 ---
SYSTEM_INSTRUCTION_WORKER = (
    "당신은 검색된 정보를 있는 그대로 전달하는 정직한 메신저입니다. "
    "제공된 '검색된 컨텍스트(정보)'의 내용과 형식을 자의적으로 요약하거나 문장으로 바꾸지 마세요. "
    "반드시 원본의 **불렛 포인트(- 지원 내용, - 대상 등)** 형식을 그대로 유지하여 답변해야 합니다. "
    "각 검색 결과의 끝에는 반드시 [출처 번호]를 명시하세요."
)

# --- 6. 핵심 로직 함수들 ---

def get_gemini_embedding(text: str, task_type: str = "SEMANTIC_SIMILARITY") -> Optional[List[float]]:
    if not GEMINI_API_KEY: return None
    try:
        result = genai.embed_content(
            model='text-embedding-004', 
            content=text,
            task_type=task_type 
        )
        return result['embedding']
    except Exception as e:
        print(f"❌ Embed API 오류: {e}")
        return None

# [수정] Gemini API 호출에 안전장치(Decorator) 달기
# 1초 -> 2초 -> 4초 대기 후 재시도 (총 3번 시도)
@retry(
    stop=stop_after_attempt(3), 
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception)
)
# [수정] timeout 기본값을 15 -> 30으로 변경
def generate_content_safe(model, prompt, timeout=120, **kwargs): 
    """안전한 Gemini 호출 래퍼 함수"""
    try:
        return model.generate_content(
            prompt, 
            request_options={"timeout": timeout},
            **kwargs 
        )
    except Exception as e:
        print(f"⚠️ API 호출 실패 (재시도 중...): {e}")
        raise e

def extract_info_from_question(question: str, chat_history: list[dict] = []) -> dict:
    history_formatted = "(이전 대화 없음)"
    if chat_history:
        recent_history = chat_history[-6:]
        history_formatted = "\n".join([f"  - {t['role']}: {t['content']}" for t in recent_history])

    cache_key = None
    if not chat_history:
        question_hash = hashlib.md5(question.encode('utf-8')).hexdigest()
        cache_key = f"extract_v2:{question_hash}"
        try:
            cached = redis_client.get(cache_key)
            if cached: return json.loads(cached.decode('utf-8'))
        except Exception: pass

    if not LLM_MODEL: return {"error": "Gemini 모델 로드 실패"}

    # 2. 히스토리 요약 (최근 3개만)
    recent_history = chat_history[-3:] 
    history_str = "\n".join([f"{t['role']}: {t['content']}" for t in recent_history]) if recent_history else "None"

    # 3. [최종 최적화 프롬프트] 
    # 지시어는 영어(토큰 절약), 핵심 키워드는 한국어 예시(정확도 보장)
    prompt = f"""
    You are an intent classifier for a welfare chatbot.
    Analyze the user's input based on history and extract JSON.
    
    [History]
    {history_str}
    
    [Input]
    "{question}"

    [Task]
    Return ONLY a JSON object with keys: "intent", "category", "sub_category", "age" (int), "keywords" (list).

    [Rules]
    1. **intent**:
       - "show_more" (more info), "safety_block" (profanity), "exit", "reset", "out_of_scope" (weather, stocks), "small_talk".
       - "clarify_category": If input has age/target but NO service keyword (e.g., "6개월 아기", "장애 영유아").
       - null: If it is a normal search query.
    
    2. **age**:
       - Convert years('살') or 'dol'('돌') to **MONTHS**. (e.g., "3살" -> 36, "두 돌" -> 24).
       - If only months are given, use as is. Return integer or null.

    3. **category** (Match specific keywords, else null):
       - "의료/재활": 병원, 치료, 검사, 진단, 재활 (hospital, therapy, diagnosis).
       - "교육/보육": 어린이집, 유치원, 교육, 보육, 학습 (school, daycare).
       - "가족 지원": 상담, 부모, 가족 (counseling, family).
       - "돌봄/양육": 돌봄, 양육, 활동지원, 아이돌봄 (care, babysitter).
       - "생활 지원": 바우처, 지원금, 수당, 셔틀, 교통, 차량, 기저귀, 통장 (voucher, money, transport).
       
       * **Priority Rule:** If the input contains generic words like "복지(welfare)" or "서비스(service)" AND specific category keywords are absent, set "category" to null to broaden the search.

    4. **sub_category**:
       - Extract specific traits: "장애", "다문화", "한부모", "저소득", "발달지연".
       - **IGNORE** generic words like "아이", "아기", "영유아" (child, baby).
    
    5. **keywords**:
       - Extract core nouns for search.
       - Resolve pronouns ("그거", "거기") using [History].

    [Output Example]
    {{
        "intent": null,
        "category": "생활 지원",
        "sub_category": "장애",
        "age": 24,
        "keywords": ["바우처", "신청"]
    }}
    """
    try:
        safety_settings=[ {"category": c, "threshold": "BLOCK_NONE"} for c in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]]
        
        # 앞서 추가한 generate_content_safe 함수 사용
        response = generate_content_safe(LLM_MODEL, prompt, timeout=15)
        response.resolve()
        
        response_text = response.text
        json_block_start = response_text.find('{')
        json_block_end = response_text.rfind('}') + 1
        
        if json_block_start != -1 and json_block_end != -1:
             # JSON 파싱
            json_string = response_text[json_block_start:json_block_end]
            default_info = {"age": None, "category": None, "sub_category": None, "intent": None, "keywords": None}
            extracted_info = json.loads(json_string)
            default_info.update(extracted_info)
             
            has_other_criteria = default_info.get("age") is not None or default_info.get("sub_category") is not None
            
            # [수정 2] if 문 아래 들여쓰기 수정
            if has_other_criteria and default_info.get("category") is None and default_info.get("intent") is None and not default_info.get("keywords"): 
                default_info["intent"] = "clarify_category"

            # [수정 3] 캐시 저장 로직 들여쓰기 맞춤
            if cache_key:
                try:
                     redis_client.set(cache_key, json.dumps(default_info).encode('utf-8'))
                except Exception: pass
                 
            return default_info
        
        else: 
             return {"error": "Gemini 응답 JSON 없음"}
             
    # [수정 4] try와 짝이 맞는 except 위치
    except Exception as e: 
        return {"error": f"질문 분석 중 오류: {e}"}

def summarize_content_with_llm(context: str, original_question: str, chat_history: list[dict] = []) -> str:
    if not context: return ""
    
    # [버전 업] v11 (불렛 스타일 적용)
    context_hash = hashlib.md5(context.encode('utf-8')).hexdigest()
    cache_key = f"summary_v10:{context_hash}" 
    
    try:
        cached = redis_client.get(cache_key)
        if cached: return cached.decode('utf-8')
    except Exception: pass

    if not LLM_MODEL: return "Gemini 모델 로드 실패"

    prompt = f"""
    # 사용자 원본 질문: "{original_question}"
        
    ---원본 텍스트---
    {context}

    ---
    # 지시사항:
    위 '원본 텍스트'를 바탕으로 사용자의 질문에 답변하기 위한 핵심 정보를 요약하세요.

    당신은 복지 정보 요약 전문가입니다.
        
    아래 "---원본 텍스트---"를 바탕으로 사용자의 질문에 맞춰 요약해 주세요.

    # ★★★ [매우 중요] 예외 처리 규칙 ★★★
    1. 만약 텍스트가 제목, 목차, 또는 아주 짧은 문장만 포함하고 있어서 요약할 정보가 부족하다면,
       "정보가 부족합니다"라고 말하지 말고, **입력된 텍스트를 그대로 출력**하세요.
    2. 각 항목의 내용은 **명사형**으로 간결하게 작성하세요. (예: ~지원, ~운영)
    3. **중요:** 원본 텍스트에 해당 항목의 정보가 없다면, **그 항목 자체를 아예 적지 말고 생략하세요.**
    4. "정보가 없습니다", "링크를 확인하세요", "👉" 같은 불필요한 문구는 **절대** 적지 마세요.
        
    이 텍스트의 내용을 바탕으로, **특히 사용자의 원본 질문과 관련성이 높은 정보**를 중심으로 [출력 예시]와 같이 **표준 Markdown 문법**을 사용하여 간결하게 요약해 주세요.
    # 추출 항목:
    1. 지원 내용
    2. 대상
    3. 지원 금액 (정보가 있다면 "지원 금액" 또는 "비용 부담" 항목으로 요약)
    4. 신청 방법 (정보가 있다면 "신청 장소/방법" 항목으로 요약)
    5. 문의처 (정보가 있다면)
    
    # [출력 스타일 가이드 - 스크린샷 스타일]:
    1. **불렛 포인트 사용:** 모든 항목은 반드시 `* ` (별표+공백)으로 시작하세요. (화면에서 동그라미로 변환됩니다.)
    2. **헤더 볼드 처리:** 항목의 제목은 `**제목**`으로 감싸고, 뒤에 콜론(:)을 붙이세요.
       - 형식: `* **지원 내용** : 내용`
    3. **대괄호/이모지 금지:** `[지원 내용]`이나 이모지를 쓰지 마세요.
    4. **계층 구조:** 하위 항목은 스페이스 2칸 들여쓰기 후 `* `로 작성하세요.

    # [출력 예시]
    * **지원 내용** : 장애인 등록 진단서 발급비 및 검사비 지원
    * **대상** : 도봉구 거주 영유아 (0~6세)
      * 의료급여수급자 및 차상위계층
    * **지원 혜택** : 
      * 진단서 발급비: 최대 4만원
      * 검사비: 최대 10만원
    * **문의처** : 도봉구 보건소 (☎ 02-xxx-xxxx)

    (여기서부터 요약을 시작하세요):
    """
    
    try:
        # 안전 설정 (의료 용어 차단 방지)
        safety_settings=[ 
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]

        # [수정] safety_settings를 인자로 전달!
        # 이제 generate_content_safe가 **kwargs로 받아서 처리해 줄 거야.
        response = generate_content_safe(
            LLM_MODEL, 
            prompt, 
            timeout=20, 
            safety_settings=safety_settings # <--- 여기 추가!
        )
        
        summary = response.text.strip()
        
        try:
            redis_client.set(cache_key, summary.encode('utf-8'))
        except Exception: pass
        
        return summary

    except Exception as e: 
        print(f"⚠️ 요약 실패: {e}")
        return context[:300] + "..."

def expand_search_query(question: str) -> list:
    """
    [Upgrade Ver] 검색어 확장 및 노이즈 제거 통합 함수
    1. AI 호출을 1회로 통합하여 속도 향상
    2. 불용어(Stop words) 대폭 강화로 '하셨는데' 같은 노이즈 제거
    3. 짝치료/그룹치료 -> '두리활동' 강제 매핑
    """
    
    # ---------------------------------------------------------
    # 1. 노이즈 제거 (강력한 전처리)
    # ---------------------------------------------------------
    # 특수문자 제거
    clean_question = re.sub(r'[^\w\s]', '', question) 
    
    # [업그레이드] 한국어 문장형 질문에서 자주 나오는 불필요한 단어들
    STOP_WORDS = [
        # 의문사/요청
        "있어", "있니", "있나요", "어디", "어디야", "알려줘", "해줘", "궁금해", 
        "무엇", "뭐야", "대한", "관한", "관련", "알고", "싶어", "해요", "되나요",
        "나와", "저기", "그거", "이거", "요", "좀", "수", "것", "등", "및", "자세히",
        # 서술어/어미 (로그에서 발견된 노이즈)
        "하는", "있는", "좋을", "같다고", "하셨는데", "하셨습니다", "가야하는지",
        "받아보는", "의심된다고", "같습니다", "합니다", "입니다",
        # 호칭/주어 (검색에 방해됨)
        "선생님께서", "섲ㄴ생님꼐서", "어린이집에서", "아이를", "아이가", "키우고", "우리", "제가"
    ]
    
    # 사용자 입력 단어 1차 필터링
    raw_tokens = clean_question.split()
    refined_user_keywords = [
        k for k in raw_tokens 
        if len(k) >= 2 and k not in STOP_WORDS
    ]

    # ---------------------------------------------------------
    # 2. 비상용 키워드 (Rule Base - 즉시 주입)
    # ---------------------------------------------------------
    fallback_keywords = []
    
    # 검사 관련
    if "검사" in question: 
        fallback_keywords.extend(["비용", "지원", "진단서", "발급"])
        
    # [중요] 짝치료/그룹치료 -> 두리활동 (누락 방지)
    if any(w in question for w in ["짝치료", "그룹", "사회성", "두리"]):
        fallback_keywords.extend(["두리활동", "프로그램", "사회성", "또래"])


    # ---------------------------------------------------------
    # 3. AI 확장 (Smart Expansion - 1회 통합 호출)
    # ---------------------------------------------------------
    
    ai_keywords = []
    if LLM_MODEL:
        prompt = f"""
        사용자가 복지 정보를 찾고 있습니다. 검색을 위한 핵심 키워드 5개를 추출하세요.
        
        질문: "{question}"
        
        [필수 확장 규칙]
        1. **장애 유형 구체화 (가장 중요):**
           - 질문에 '장애'가 있다면 -> **'발달', '뇌병변', '지적', '자폐'** 같은 구체적인 장애 유형 키워드를 반드시 추가하세요.
           - 예: "장애검사" -> "장애 발달 뇌병변 지적 자폐 검사"
    
        2. **서비스 성격 구체화:**
           - '검사/진단' -> **'비용', '지원', '바우처', '정밀', '선별', '진단서'**
           - '치료' -> **'재활', '언어', '행동', '심리', '감각'**
    
        3. **출력 형식:** 설명 없이 오직 단어만 쉼표(,)로 구분하여 나열하세요.

        [예시]
        질문: "장애검사"
        답변: 장애, 발달, 뇌병변, 검사, 정밀, 비용, 지원, 진단서
        """
        try:
            # 타임아웃 60초 (넉넉하게)
            response = generate_content_safe(LLM_MODEL, prompt, timeout=60)
            ai_keywords = [k.strip() for k in response.text.strip().split(',')]
            print(f"⚡️ [AI 확장] {ai_keywords}")
        except Exception as e:
            print(f"⚠️ AI 확장 실패: {e}")

    # ---------------------------------------------------------
    # 4. 최종 합체 (우선순위: AI > Rule > User)
    # ---------------------------------------------------------
    # set으로 중복 제거 후 리스트 변환
    final_keywords = list(set(ai_keywords + fallback_keywords + refined_user_keywords))
    
    # 마지막 안전장치: 2글자 미만이나 불용어가 혹시라도 섞여있으면 제거
    return [k for k in final_keywords if len(k) >= 2 and k not in STOP_WORDS]

def rerank_search_results(question: str, candidates: list) -> list:
    if not candidates or not LLM_MODEL: return candidates

    rerank_candidates = candidates[:10] 
    remaining_candidates = candidates[10:]

    candidate_texts = []
    for i, doc in enumerate(rerank_candidates):
        meta = doc.get("metadata", {})
        title = meta.get("title", "")
        # [최적화] 1500자 제한
        content_preview = doc.get("content", "")[:1500].replace("\n", " ")
        candidate_texts.append(f"[{i}] {title} (내용: {content_preview})")

    candidates_str = "\n".join(candidate_texts)

    prompt = f"""
    사용자 질문: "{question}"
    아래 후보 목록에서 질문에 가장 적합한 복지 서비스를 골라 순서대로 나열하세요.
    
    [★★심사 기준 - 시나리오별 엄격 적용★★]
    
    0. **[가산점] 우선 추천 표식:**
       - 후보 문서 제목에 **"★(우선추천)"**이 있다면, 시스템이 키워드 매칭으로 찾은 유력 후보입니다.
       - **이 문서가 질문의 의도와 일치한다면 최우선 순위로 배치하세요.**
       - 단, 질문 내용과 전혀 맞지 않는다면(예: '학교 검사'를 묻는데 '의료비'가 추천된 경우) 무시해도 됩니다.

    # CASE A. 사용자가 '검사', '진단', '판별'을 물어본 경우:
    1. **[강제 매핑] '장애 검사' = '영유아 발달 정밀 검사':**
       - 사용자가 "장애 검사"라고 묻는 것은 90% 이상 **'영유아 발달 정밀검사비 지원'**을 찾는 것입니다.
       - 따라서 **'영유아 발달 정밀검사비 지원'** 문서를 발견하면 **무조건 1순위**로 올리세요.
       - 그 다음으로 **'장애인 등록 진단서 발급비 지원'**을 2순위로 배치하세요.

    2. **[절대 제외] 특수교육/학교/선별검사:**
       - 질문에 "학교", "입학", "교육청"이라는 단어가 **명시적으로 없다면**,
       - **'특수교육대상자 선정'**, **'장애선별검사(교육청)'**, **'배치'** 관련 문서는 **결과에서 아예 삭제(제외)하세요.**
       - (이유: 이것은 병원 진단이 아니라 학교 행정 절차이므로, 일반적인 '검사' 질문의 정답이 아닙니다.)

    3. **[예외] 특정 질환:**
       - "난청", "대사이상" 등 구체적 병명이 질문에 있을 때만 해당 질환 검사를 1순위로 하세요.

    # CASE B. 사용자가 '짝치료', '그룹치료', '사회성'을 물어본 경우:
    1. **[동의어 매핑]** "짝치료"와 "그룹치료"는 **"두리활동"**, **"사회성 향상 프로그램"**과 같은 의미입니다.
    2. **[정확도]** 제목이나 내용에 **"두리", "짝", "그룹", "사회성"**이 포함된 구체적인 프로그램 문서를 1순위로 선택하세요.
    3. **[제외]** 단순 "놀이치료", "베이비 마사지", "부모 상담" 등은 짝치료가 아니므로 순위를 내리세요.

    # CASE C. '기관/장소' 지정 질문 시:
       - 질문에 "부모회", "보건소", "복지관" 등 특정 운영 기관이 언급되었다면,
       - 제목뿐만 아니라 **'내용(문의처)'에 해당 기관명이 포함된 문서**를 1순위로 올리세요.

    # CASE D. 공통 필터링 규칙:
    1. **나이/조건:** 대상 연령이나 자격 요건이 질문과 맞지 않으면 과감하게 제외하세요.
    2. **관련성 Cut-off:** 질문과 전혀 관련 없는 문서는 순위 번호를 적지 말고 아예 제외하세요.
    3. **내용 확인:** 제목뿐만 아니라 '내용(content)'도 반드시 확인하여 판단하세요.

    
    [후보 목록]
    {candidates_str}
    
    [작성 규칙]
    - 가장 적합한 문서의 번호 **3개**만 쉼표로 구분해 적으세요.
    """
    if not candidates or not LLM_MODEL:
        return candidates

    # [전략 수정] 50개는 너무 많습니다. Supabase가 찾아준 상위 10개만 봅니다.
    # (하이브리드 검색 덕분에 10등 안에 정답이 있을 확률이 매우 높습니다.)
    rerank_candidates = candidates[:10] 
    remaining_candidates = candidates[10:]

    candidate_texts = []
    for i, doc in enumerate(rerank_candidates):
        meta = doc.get("metadata", {})
        title = meta.get("title", "")
        # [핵심] 원본 내용을 자르지 않고 (거의) 다 보냅니다.
        # 2000자면 대부분의 Notion 페이지 전체가 들어갑니다. '짝치료' 같은 세부 단어를 놓치지 않습니다.
        raw_content = doc.get("content", "")[:1500].replace("\n", " ")
        candidate_texts.append(f"[{i}] 제목: {title} | 내용: {raw_content}")

    candidates_str = "\n".join(candidate_texts)

    # 2. 랭킹 프롬프트 (기존과 동일)
    prompt = f"""
    사용자 질문: "{question}"
    
    위 질문에 가장 적합한 복지 서비스를 아래 후보 목록에서 찾아, 적합한 순서대로 [번호]를 나열하세요.
    
    [★★통합 랭킹 심사 기준★★]
    1. **키워드 우선:** 질문의 핵심 단어(검사, 진단서 등)가 제목에 포함된 것을 우선하세요. 질문의 핵심 단어(짝치료, 언어치료 등)가 포함된 문서는 최우선 순위입니다.
    2. **[중요] 문맥 구분 (검사/치료):**
       - 질문이 "특수교육", "학교" 언급 없이 단순 "**장애 검사**", "**치료**"라면 -> **병원/행정(의료비, 바우처, 검사비 지원)** 사업을 학교(특수교육)보다 우선하세요.
       - 질문이 '검사'일 때, 직접적인 검사뿐만 아니라 **'비용 지원(발달검사비 지원)'**도 매우 중요한 정답입니다. **반드시 상위 3위 안에 포함**시키세요.
       - "선천성 대사이상", "난청" 같은 특정 질환은 질문에 해당 병명이 없으면 후순위입니다. 일반적인 "발달 검사"나 "장애 진단"을 우선하세요.
       - **"심리 상담", "양육 상담", "부모 교육", "돌봄 서비스"는 검사가 아닙니다.** - 질문이 명확히 검사를 요구한다면, 상담/돌봄 문서는 순위를 낮추거나 제외하세요.
    3. **의미 매칭:** 질문의 단어가 정확히 없더라도 의미가 통하면 정답입니다.
    4. **유사 서비스 주의:** "놀이치료"와 "짝치료"는 다릅니다. "베이비 마사지"와 같은 단순 프로그램은 '검사'가 아닙니다. 제외하세요.
    5. **나이/조건 필터링:** 대상 연령이나 자격 요건이 맞지 않으면 순위를 내리세요.
    6. **내용 확인:** 제목뿐만 아니라 '내용' 필드도 확인하세요.
    7. **정확도:** 질문의 키워드(검사, 비용 등)가 제목에 포함된 것을 우선하세요.
    8. **'짝치료/그룹치료' 질문 시**
       - 이 질문은 **'사회성 향상'**이나 **'또래'**, **'두리활동'** 프로그램을 찾는 질문입니다.
       - 제목이나 내용에 **'두리', '짝', '그룹', '사회성'**이 포함된 문서를 무조건 1순위로 올리세요.
       - 단순 상담이나 부모 교육은 후순위 입니다.
    
    [후보 목록]
    {candidates_str}
    
    [작성 규칙]
    - 가장 적합한 후보의 번호 **5개**를 쉼표로 구분하여 적으세요.
    - 예시: 3, 10, 1, 5, 2
    """

    try:
        response = generate_content_safe(LLM_MODEL, prompt, timeout=60)
        raw_indices = [int(s) for s in re.findall(r'\b\d+\b', response.text.strip())]
        
        final_results = []
        seen = set()
        
        for idx in raw_indices:
            if idx not in seen and 0 <= idx < len(rerank_candidates):
                final_results.append(rerank_candidates[idx])
                seen.add(idx)
            
        for i, doc in enumerate(rerank_candidates):
            if i not in seen: final_results.append(doc)
        
        final_results.extend(remaining_candidates)
        return final_results

    except Exception as e:
        print(f"⚠️ 랭킹 실패: {e}")
        return candidates
    
# [utils.py] 파일 맨 아래에 추가

# --- 7. [신규] '더 보기' 및 포맷팅 헬퍼 함수 ---

def get_supabase_pages_by_ids(page_ids: list) -> list:
    """ID 목록으로 Supabase 데이터 조회"""
    if not page_ids or not supabase: return []
    try:
        response = supabase.table("site_pages").select("*").in_("page_id", page_ids).execute()
        
        # 중복 제거 및 정렬
        unique_pages = {item['page_id']: item['metadata'] for item in response.data}
        return [unique_pages[pid] for pid in page_ids if pid in unique_pages]
    except Exception as e:
        print(f"❌ Supabase 조회 오류: {e}")
        return []

# --- 8. 포맷팅 함수 ---

def clean_summary_text(text: str) -> str:
    """
    [수정] 이모지 제거 버전:
    대괄호 [헤더]를 인식하여 앞줄을 띄워줍니다.
    """
    if not text: return "요약 정보가 없습니다."

    lines = text.split('\n')
    final_lines = []
    
    # 관리할 헤더 키워드
    target_keywords = ["지원 내용", "대상", "지원 혜택", "지원 금액", "신청 방법", "문의처"]
    
    # 정규식: * **제목** :  형태 감지
    # ^\s*[\*\-]\s* : 불렛(* 또는 -)으로 시작
    # \*\*(.+?)\*\* : 볼드체 제목 그룹
    header_pattern = re.compile(r'^\s*[\*\-]\s*\*\*(.+?)\*\*.*$')

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped: continue
        if stripped in ["---", "***", "```"]: continue
        if "👉" in stripped or "세부 내용" in stripped: continue

        match = header_pattern.match(stripped)
        if match:
            header_content = match.group(1) # 볼드 안의 텍스트
            
            # 유효한 헤더인지 확인
            if any(k in header_content for k in target_keywords):
                # 빈 헤더인지 확인 (Look-ahead)
                has_content = False
                for j in range(i + 1, len(lines)):
                    next_line = lines[j].strip()
                    if not next_line: continue 
                    # 다음 줄도 헤더거나 링크면 -> 현재 헤더는 빈 것
                    if header_pattern.match(next_line) or "🔗" in next_line:
                        has_content = False
                    else:
                        has_content = True
                    break
                
                if not has_content: continue 

                # 헤더 앞에 빈 줄 추가 (가독성 확보)
                if final_lines: 
                    final_lines.append("") 

        final_lines.append(line)

    return "\n".join(final_lines).strip()

def format_search_results(pages_metadata: list) -> str:
    cards = []
    for meta in pages_metadata:
        title = meta.get("title", "제목 없음")
        category = meta.get("category", "기타")
        summary = clean_summary_text(meta.get("pre_summary", ""))
        url = meta.get("page_url", "")
        
        # [★핵심 수정★] 제목과 본문 사이에 '\n\n'을 넣어서 확실하게 떨어뜨립니다.
        card = f"**[{category}] {title}**\n\n{summary}"
        
        # '자세히 보기' 앞에도 줄바꿈을 넉넉하게 줍니다.
        if url: 
            card += f"\n\n🔗 **[자세히 보기]({url})**"
            
        cards.append(card)
    
    # 카드 사이에도 구분선을 확실히 둡니다.
    return "\n\n<hr>\n\n".join(cards)

# --- 9. 의미 기반 캐시 (Semantic Cache) 함수 ---

def check_semantic_cache(query_embedding: list) -> str | None:
    """
    Supabase에서 의미가 유사한(0.92 이상) 질문이 있었는지 확인하고,
    있다면 저장된 답변을 반환합니다.
    """
    try:
        # [★수정★] 기준을 0.92 -> 0.98로 대폭 상향합니다.
        # 0.98 이상이어야만 '같은 질문'으로 인정하고 캐시를 반환합니다.
        response = supabase.rpc(
            "match_chat_cache",
            {
                "query_embedding": query_embedding,
                "match_threshold": 0.92, # <--- 여기를 수정하세요!
                "match_count": 1
            }
        ).execute()
        
        if response.data and len(response.data) > 0:
            cached_answer = response.data[0]['answer']
            print(f"♻️ [Semantic Cache] 의미가 같은 질문 발견! (유사도: {response.data[0]['similarity']:.4f})")
            return cached_answer
            
    except Exception as e:
        print(f"⚠️ 캐시 확인 중 오류: {e}")
    
    return None

def save_semantic_cache(question: str, answer: str, embedding: list):
    """
    새로운 질문과 답변, 벡터를 Supabase 캐시 테이블에 저장합니다.
    """
    try:
        data = {
            "question": question,
            "answer": answer,
            "embedding": embedding
        }
        supabase.table("chat_cache").insert(data).execute()
        print("💾 [Semantic Cache] 새로운 대화 기억 저장 완료")
    except Exception as e:
        print(f"⚠️ 캐시 저장 실패: {e}")

# --- 6. 헬퍼 함수들 ---

def _get_rich_text(properties, prop_name: str) -> str:
    prop = properties.get(prop_name, {}).get("rich_text", [])
    return "\n".join([text_part.get("plain_text", "") for text_part in prop]).strip()

def _get_number(properties, prop_name: str):
     return properties.get(prop_name, {}).get("number")

def _get_title(properties, prop_name: str) -> str:
    title_prop = properties.get(prop_name, {}).get("title", [])
    return title_prop[0].get("plain_text", "") if title_prop and title_prop[0] else "제목 없음"

def _get_select(properties, prop_name: str) -> str:
    category_prop = properties.get(prop_name, {}).get("select")
    return category_prop.get("name", "") if category_prop else "분류 없음"

def _get_multi_select(properties, prop_name: str) -> list:
    target_prop = properties.get(prop_name, {}).get("multi_select", [])
    return [item.get("name") for item in target_prop if item]

def _get_url(properties, prop_name: str) -> str:
     return properties.get(prop_name, {}).get("url", "")