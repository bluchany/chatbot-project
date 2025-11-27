import os
import json
import time
import traceback
from supabase import create_client
from dotenv import load_dotenv

from utils import (
    redis_client,
    get_gemini_embedding,
    MAIN_ANSWER_CACHE_KEY,
    rerank_search_results,
    format_search_results,
    expand_search_query 
)

print("[Worker] 설정 로드 중...")
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
JOB_QUEUE_KEY = "chatbot:job_queue"
JOB_RESULTS_KEY = "chatbot:job_results"

print("[Worker] 클라이언트 초기화 중...")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
print("[Worker] 초기화 완료. 작업 대기 시작.")

# --- 질문 확장 사전 ---
QUERY_EXPANSION_MAP = {
    "장애검사": "영유아 발달 정밀 검사비 지원 장애인 등록 진단서 발급비",
    "발달검사": "영유아 발달 정밀 검사비 지원",
    "치료지원": "발달재활서비스 바우처 짝치료 그룹치료",
    "짝치료": "또래 그룹치료 두리활동 사회성 향상 프로그램 그룹 활동", 
    "그룹치료": "또래 두리활동 사회성 향상 프로그램 짝치료",
    "언어치료": "발달재활서비스 바우처",
    "부모교육": "양육 코칭 상담",
}

# --- 3. 검색 함수 ---
def search_documents_hybrid(query_embedding, keywords, match_count=50):
    try:
        print(f"🔍 [Hybrid Search] 적용된 키워드: {keywords}")
        response = supabase.rpc(
            "match_documents",
            {"query_embedding": query_embedding, "match_count": match_count, "keywords": keywords}
        ).execute()
        return response.data
    except Exception as e:
        print(f"❌ Supabase 검색 오류: {e}")
        return []

# --- [신규] 랭킹 로직 분리 함수 (코드를 깔끔하게!) ---
def assign_tiers(question, documents):
    """문서들을 1티어(성골), 2티어(진골), 일반으로 분류합니다."""
    tier_1 = []
    tier_2 = []
    normal = []

    # 질문 유형 파악
    is_test = "검사" in question 
    is_social = any(k in question for k in ["짝치료", "그룹", "사회성", "두리", "친구"])
    is_org = any(k in question for k in ["부모회", "복지관", "센터", "보건소", "육아종합"])

    # 특수 질문이 아니면 전체를 일반으로 반환
    if not (is_test or is_social or is_org):
        return [], [], documents

    print(f"👮‍♂️ [Title Validator] 특수 질문 감지! (검사={is_test}, 사회성={is_social}, 기관={is_org})")

    for doc in documents:
        title = doc.get("metadata", {}).get("title", "")
        content = doc.get("content", "")
        
        # --- [조건 A] 검사 질문 ---
        if is_test:
            has_test = any(w in title for w in ["검사", "진단", "선별", "스크리닝", "발달", "정밀"])
            has_cost = any(w in title for w in ["지원", "비용", "비", "무료", "바우처"])
            
            if has_test and has_cost: tier_1.append(doc)
            elif has_test: tier_2.append(doc)
            else: normal.append(doc)

        # --- [조건 B] 짝치료/사회성 질문 ---
        elif is_social:
            if any(w in title for w in ["두리", "짝", "그룹"]): tier_1.append(doc)
            elif any(w in title for w in ["사회성", "교실", "친구"]) or "두리" in content: tier_2.append(doc)
            else: normal.append(doc)
        
        # --- [조건 C] 기관 질문 ---
        elif is_org:
            target_orgs = [k for k in ["부모회", "복지관", "센터", "보건소", "육아종합"] if k in question]
            is_match = False
            for org in target_orgs:
                if org in title or org in content:
                    is_match = True
                    break
            if is_match: tier_1.append(doc)
            else: normal.append(doc)

    print(f"👮‍♂️ 랭킹 분류 완료: 1티어({len(tier_1)}) > 2티어({len(tier_2)})")
    return tier_1, tier_2, normal


# --- 4. 작업 처리 함수 (Main) ---
def process_job(job_data):
    start_time = time.time()
    question = job_data.get("question")
    print(f"\n▶️ 작업 시작: {question}")

    # [Step 1] 키워드 전략
    forced_keywords = []
    for trigger, expansion in QUERY_EXPANSION_MAP.items():
        if trigger in question.replace(" ", ""): 
            forced_keywords.extend(expansion.split())
            print(f"⚡️ [Rule] '{trigger}' 감지! -> 강제 키워드 주입: {forced_keywords}")

    ai_keywords = expand_search_query(question)
    target_keywords = list(dict.fromkeys(forced_keywords + ai_keywords))
    print(f"🗝️ [최종 검색 키워드] {target_keywords}")

    # [Step 2] 검색
    embedding_text = f"{question} {' '.join(forced_keywords)}"
    query_embedding = get_gemini_embedding(embedding_text, task_type="RETRIEVAL_QUERY")
    if not query_embedding: return "일시적인 오류가 발생했습니다.", [], 0

    raw_results = search_documents_hybrid(query_embedding, target_keywords, match_count=100)
    if not raw_results: return "관련 정보를 찾지 못했습니다.", [], 0

    # [중복 제거]
    seen_ids = set()
    unique_results = []
    for doc in raw_results:
        pid = doc.get("metadata", {}).get("page_id")
        if pid not in seen_ids:
            seen_ids.add(pid)
            unique_results.append(doc)
    raw_results = unique_results

    # [Step 2.5] 블랙리스트 필터링
    is_medical = "검사" in question and not any(w in question for w in ["학교", "입학", "교육청", "특수", "선별"])
    if is_medical:
        raw_results = [d for d in raw_results if not any(x in d.get("metadata", {}).get("title", "") for x in ["특수교육", "선별", "배치", "입학", "교육청"])]

    # [Step 3] 랭킹 분류 (함수로 분리하여 깔끔해짐)
    tier_1_docs, tier_2_docs, normal_docs = assign_tiers(question, raw_results)

    # [Step 4] 랭킹 준비 (힌트 주입)
    marked_candidates = []
    for doc in tier_1_docs:
        new_doc = doc.copy()
        new_meta = doc.get("metadata", {}).copy()
        new_meta["title"] = f"★(우선추천) {new_meta.get('title')}"
        new_doc["metadata"] = new_meta
        marked_candidates.append(new_doc)
        
    # AI 후보군 (힌트 달린 1티어 + 2티어 + 일반)
    candidates_for_ai = marked_candidates + tier_2_docs + normal_docs

    # [Step 5] AI 랭킹
    print(f"🤖 Gemini에게 {len(candidates_for_ai)}개 문서를 보냅니다. (힌트 포함)")
    reranked_results = rerank_search_results(question, candidates_for_ai)

    # Fallback: AI 실패 시, 파이썬이 정한 순서(1티어->2티어->일반) 그대로 사용
    if not reranked_results:
        print("⚠️ AI 랭킹 실패 -> 파이썬 우선순위 적용")
        reranked_results = candidates_for_ai[:2]

    # [Step 6] 최종 결과 선정 및 조립
    display_count = min(len(reranked_results), 2)
    display_results = reranked_results[:display_count]
    
    # 더 보기용 ID 리스트
    all_page_ids = [r.get("metadata", {}).get("page_id") for r in reranked_results]
    remaining = [d for d in raw_results if d.get("metadata", {}).get("page_id") not in all_page_ids]
    all_page_ids.extend([d.get("metadata", {}).get("page_id") for d in remaining])
    
    # 화면 표시용 메타데이터 정제 (힌트 태그 제거)
    final_display_metadata = []
    for res in display_results:
        meta = res.get("metadata", {})
        clean_title = meta.get("title", "").replace("★(우선추천) ", "")
        meta["title"] = clean_title
        final_display_metadata.append(meta)

    body = format_search_results(final_display_metadata)
    header = "🔎 **정보를 찾았습니다!**\n자세한 정보는 '자세히 보기'를 확인해주세요."
    final_answer = f"{header}\n\n<hr>\n\n{body}"

    if len(all_page_ids) > display_count:
        final_answer += f"\n\n<hr>\n\n🔍 **아직 결과가 더 남아있습니다.**\n'더 보여줘' 또는 '다음'을 입력해 보세요."

    elapsed = time.time() - start_time
    print(f"✅ 답변 조립 완료 (소요시간: {elapsed:.2f}초)")
    return final_answer, all_page_ids, len(all_page_ids)

# --- 5. 메인 루프 ---
def start_worker():
    print("🚀 Worker 가동! Redis 큐 대기 중...")
    while True:
        try:
            result = redis_client.blpop(JOB_QUEUE_KEY, timeout=0)
            if result:
                _, job_json = result
                job_data = json.loads(job_json.decode('utf-8'))
                job_id = job_data.get("job_id")
                
                answer_text, all_ids, total_found = process_job(job_data)

                final_result = {
                    "status": "complete",
                    "answer": answer_text,
                    "last_result_ids": all_ids, 
                    "total_found": total_found 
                }
                redis_client.hset(JOB_RESULTS_KEY, job_id, json.dumps(final_result).encode('utf-8'))
                print(f"💾 결과 저장 완료 (Job ID: {job_id})")

        except Exception as e:
            print(f"🔥 Worker 루프 오류: {e}")
            traceback.print_exc()
            time.sleep(1)

if __name__ == "__main__":
    start_worker()