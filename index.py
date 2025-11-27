import os
import json
import time
import traceback
from supabase import create_client
from notion_client import Client as NotionClient
from dotenv import load_dotenv
from utils import (
    LLM_MODEL,  
    summarize_content_with_llm, 
    _get_title, 
    _get_number, 
    _get_rich_text,
    _get_url,
    get_gemini_embedding,
    _get_multi_select
)

print("[Indexer] 설정 로드 중...")
load_dotenv()

NOTION_KEY = os.getenv("NOTION_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not NOTION_KEY: raise ValueError("NOTION_KEY 설정 필요")
if not SUPABASE_URL or not SUPABASE_KEY: raise ValueError("SUPABASE 설정 필요")

DATABASE_IDS = {
    "의료/재활": "2738ade5021080b786b0d8b0c07c1ea2",
    "교육/보육": "2738ade5021080339203d7148d7d943b",
    "가족 지원": "2738ade502108041a4c7f5ec4c3b8413",
    "돌봄/양육": "2738ade5021080cf842df820fdbeb709",
    "생활 지원": "2738ade5021080579e5be527ff1e80b2"
}
NOTION_PROPERTY_NAMES = {
    "title": "사업명", "category": "분류", "sub_category": "대상 특성",
    "start_age": "시작 월령(개월)", "end_age": "종료 월령(개월)", "support_detail": "상세 지원 내용",
    "contact": "문의처", "url1": "관련 홈페이지 1", "url2": "관련 홈페이지 2",
    "url3": "관련 홈페이지 3", "extra_req": "추가 자격요건"
}

STATE_FILE_PATH = "./chroma-data/indexing_state.json"

print("[Indexer] 클라이언트 초기화 중...")
notion = NotionClient(auth=NOTION_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
print("[Indexer] 초기화 완료.")

def load_state():
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, "r", encoding="utf-8") as f: return json.load(f)
        except: pass
    return {}

def save_state(state):
    try:
        with open(STATE_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception: pass

def run_indexing():
    print("\n🔥🔥🔥 [코드 업데이트] 문서 임베딩 최적화 모드 (RETRIEVAL_DOCUMENT) 🔥🔥🔥\n")
    print("[Indexer] 🚀 Supabase 증분 색인 시작...")
    
    if not LLM_MODEL:
        print("❌ [Indexer] FATAL: Gemini 모델 로드 실패.")
        return

    # prev_state = load_state()  <-- 이걸 주석 처리하고
    prev_state = {} 
    current_state = {}
    total_processed = 0
    total_skipped = 0
    has_critical_error = False
    
    for category_name, db_id in DATABASE_IDS.items():
        print(f"\n[Indexer] '{category_name}' DB 확인 중...")
        try:
            results = []
            
            # [수정 2] 안전한 페이지네이션(Pagination) 로직
            has_more = True
            next_cursor = None
            
            while has_more:
                # cursor가 있으면 넣고, 없으면 뺌
                query_params = {"database_id": db_id}
                if next_cursor: query_params["start_cursor"] = next_cursor
                
                response = notion.databases.query(**query_params)
                
                results.extend(response.get("results", []))
                has_more = response.get("has_more")
                next_cursor = response.get("next_cursor")
                time.sleep(0.3) # API 속도 제한 준수
            
            print(f" - {len(results)}개 페이지 발견.")

            for page in results:
                page_id = page.get("id")
                last_edited = page.get("last_edited_time")
                if not page_id: continue
                
                current_state[page_id] = last_edited

                if page_id in prev_state and prev_state[page_id] == last_edited:
                    total_skipped += 1
                    continue

                print(f"\n[Indexer] ⚡️ 처리 시작 (ID: {page_id})")

                try:
                    supabase.table("site_pages").delete().eq("page_id", page_id).execute()
                except: pass

                # 데이터 추출
                props = page.get("properties", {})
                title = _get_title(props, NOTION_PROPERTY_NAMES["title"])
                support_detail = _get_rich_text(props, NOTION_PROPERTY_NAMES["support_detail"])
                extra_req = _get_rich_text(props, NOTION_PROPERTY_NAMES["extra_req"])
                contact = _get_rich_text(props, NOTION_PROPERTY_NAMES["contact"])
                page_url = page.get("url", "")
                start_age = _get_number(props, NOTION_PROPERTY_NAMES["start_age"])
                end_age = _get_number(props, NOTION_PROPERTY_NAMES["end_age"])
                if end_age == -1: end_age = 99999

                targets = _get_multi_select(props, NOTION_PROPERTY_NAMES["sub_category"])
                targets_text = ", ".join(targets) if targets else ""
                
                age_text = ""
                if start_age != -1 and start_age is not None:
                    if end_age != 99999 and end_age is not None: age_text = f"{int(start_age)}~{int(end_age)}개월"
                    else: age_text = f"{int(start_age)}개월 이상"
                elif end_age != 99999 and end_age is not None: age_text = f"~{int(end_age)}개월"
                
                final_target = f"{age_text} ({targets_text})" if targets_text else age_text

                # =========================================================
                # [1] 요약용 텍스트 생성 (사용자에게 보여줄 전체 정보)
                text_parts = [
                    f"사업명: {title}",
                    f"대상: {final_target}",
                    support_detail,
                    f"추가 자격요건: {extra_req}",
                    f"문의처: {contact}"
                ]
                full_text_for_summary = "\n".join([p.strip() for p in text_parts if p and p.strip()])

                # [2] 임베딩용 텍스트 생성 (검색 정확도 향상용)
                # [★전략 수정★] 중요도에 따라 반복 횟수를 다르게 적용합니다.
                
                search_keywords = f"{title} {category_name} {targets_text}".replace(" ", ", ")
                req_text = f"자격요건: {extra_req}" if extra_req and extra_req != "—" else ""
                
                # 가중치 설정 (반복 횟수)
                weight_title = 3        # 제목: 절대적 기준
                weight_target = 2       # 대상 특성: 장애, 다문화 등 중요
                weight_req = 1          # 자격요건: 소득, 거주지 등
                
                # 리스트 컴프리헨션으로 반복 생성
                title_repeats = [f"문서제목: {title}" for _ in range(weight_title)]
                target_repeats = [f"대상특성: {targets_text}" for _ in range(weight_target)] if targets_text else []
                req_repeats = [f"자격요건: {req_text}" for _ in range(weight_req)] if req_text else []
                
                embedding_parts = [
                    f"핵심키워드: {search_keywords}",
                    f"카테고리: {category_name}",
                    f"대상: {final_target}",
                    f"내용: {support_detail}",
                ] + title_repeats + target_repeats + req_repeats
                
                # (내용 support_detail은 노이즈 방지를 위해 여전히 제외합니다)
                
                full_text_for_embedding = "\n".join([p.strip() for p in embedding_parts if p and p.strip()])
                
                # [★확인용★] 
                if total_processed == 0: 
                     print(f"🔍 [X-RAY] 가중치 적용된 검색 데이터 예시:\n{full_text_for_embedding[:300]}...")
                # =========================================================

                # =========================================================
                
                # 페이지 전체를 하나의 청크로 처리
                chunks = [full_text_for_summary] 
                records_to_insert = []
                
                for i, chunk_text in enumerate(chunks):
                    if len(chunk_text.strip()) < 10: continue
                    chunk_id = f"{page_id}_{i}"

                    print(f"[Indexer] ... '{title}' 요약 및 임베딩 중...")
                    
                    # 1. 요약
                    pre_summary = summarize_content_with_llm(chunk_text, title, [])

                    # 2. 임베딩 [★수정 1★] 문서 저장용 태스크 타입 사용!
                    # 검색할 때(Query)와 저장할 때(Document)의 타입이 달라야 정확도가 올라갑니다.
                    embedding = get_gemini_embedding(
                        full_text_for_embedding, 
                        task_type="RETRIEVAL_DOCUMENT" # <--- 핵심 수정!
                    )

                    if not embedding:
                        print(f"❌ 임베딩 실패! 건너뜀.")
                        continue

                    metadata = {
                        "page_id": page_id,
                        "category": category_name,
                        "sub_category_list": targets, # [★수정 3] 리스트 원본 저장 (필터링용)
                        "start_age": start_age,
                        "end_age": end_age,
                        "title": title,
                        "page_url": page_url,
                        "pre_summary": pre_summary
                    }

                    records_to_insert.append({
                        "id": chunk_id,
                        "page_id": page_id,
                        "content": full_text_for_summary, # DB에는 전체 내용 저장
                        "metadata": metadata,
                        "embedding": embedding # 벡터는 핵심 내용으로만 계산
                    })

                if records_to_insert:
                    try:
                        supabase.table("site_pages").upsert(records_to_insert).execute()
                        total_processed += 1
                    except Exception as e:
                        print(f"❌ 저장 실패: {e}")

        except Exception as e:
            print(f"❌ 오류 ({category_name}): {e}")
            traceback.print_exc()
            has_critical_error = True

    # 삭제 처리 로직
    if has_critical_error:
        print("\n[Indexer] ⚠️ 오류 발생으로 삭제 단계 건너뜀.")
    else:
        deleted_ids = list(set(prev_state.keys()) - set(current_state.keys()))
        if deleted_ids:
            print(f"\n[Indexer] 🗑️ 삭제된 페이지 {len(deleted_ids)}건 정리 중...")
            for del_id in deleted_ids:
                try:
                    supabase.table("site_pages").delete().eq("page_id", del_id).execute()
                except: pass
        
        save_state(current_state)
        print(f"\n[Indexer] ✨ 완료. (업데이트: {total_processed}, 건너뜀: {total_skipped})")

if __name__ == "__main__":
    run_indexing()