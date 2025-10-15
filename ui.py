import streamlit as st
import requests

# --- 페이지 설정 ---
st.set_page_config(page_title="영유아 복지 정보 챗봇", page_icon="👶", layout="wide") # wide 레이아웃으로 변경
st.title("👶 영유아 복지 정보 탐색기")

# --- 화면을 두 개의 단으로 분할 ---
col1, col2 = st.columns([1, 2]) # 1:2 비율로 좌우 분할

# --- 왼쪽 단 (col1) ---
with col1:
    st.subheader("💡 챗봇 소개")
    st.write(
        "도봉구 영유아 및 가족을 위한 복지 정보 탐색을 도와드리는 AI 챗봇입니다.\n\n"
        "나이(개월 수), 특성(예: 다문화, 한부모) 등을 포함하여 질문해주시면 더 정확한 정보를 찾을 수 있습니다."
    )
    # 로고나 관련 이미지를 넣을 수도 있습니다.
    # st.image("your_logo.image.png") 

# --- 오른쪽 단 (col2) ---
with col2:
    # --- API 서버 주소 ---
    CHATBOT_URL = "http://127.0.0.1:8000/chat"

    # --- 채팅 기록 관리 ---
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- 사용자 입력 처리 ---
    if prompt := st.chat_input("질문을 입력해주세요. (예: 6개월 된 아이 혜택 알려줘)"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("생각 중..."):
                try:
                    response = requests.post(CHATBOT_URL, json={"question": prompt})
                    response.raise_for_status()
                    bot_response = response.json().get("answer", "죄송해요, 답변을 생성하는 데 실패했습니다.")
                    st.markdown(bot_response)
                    st.session_state.messages.append({"role": "assistant", "content": bot_response})
                except requests.exceptions.RequestException as e:
                    error_message = f"서버에 연결할 수 없습니다: {e}"
                    st.error(error_message)
                    st.session_state.messages.append({"role": "assistant", "content": error_message})