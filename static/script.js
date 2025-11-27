// script.js
// [★디버깅 1★] 파일이 몇 번 로드되는지 확인합니다.
console.log('SCRIPT_LOADED_VERSION_3');
// --- 전역 변수 ---
const chatBox = document.getElementById('chat-box');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const API_URL_CHAT = '/chat';
const API_URL_RESULT = '/get_result/';
const API_URL_FEEDBACK = '/feedback';

let currentResultIds = [];
let currentShownCount = 0;
let currentTotalFound = 0;

let pendingContext = null; // "6개월"과 같은 이전 질문 맥락
let currentQuestion = ""; // 'clarify' 대비, 현재 보낸 질문 임시 저장

const loadingMessages = [
    "복지 정보를 샅샅이 뒤지는 중... 🗺️",
    "필요한 서류를 찾는 중이에요... 📄",
    "까치에게 물어보는 중... 🤔",
    "자료집를 열심히 뛰어다니는 중... 💻",
    "잠시만요, 거의 다 찾았어요! ✨",
    "관련 부서에 연락하는 중... 📞 (농담이에요!)",
    "최신 정보를 확인하고 있어요... 🔄",
    "맞춤 정보를 정리하는 중... ✍️",
    "지도롤 뚫어져라 보는 중... 📍",
    "결과를 보기 좋게 포장하는 중... 🎁"
];

const SHOW_MORE_KEYWORDS = new Set([
    "다음", "더", "더 보여줘", "계속", "이어서",
    "다음거", "다음꺼", "다른거", "다른 거", "또",
    "next", "more"
]);

let chatHistory = []; // [★신규★] 대화 기록을 저장할 배열
const MAX_HISTORY_TURNS = 2; // (기억할 대화 턴 수: 2턴 = 4개 메시지)


// --- 이벤트 리스너 ---
sendBtn.addEventListener('click', handleFormSubmit); // [수정] 함수 이름 변경
userInput.addEventListener('keypress', (event) => {
    if (event.key === 'Enter') {
        handleFormSubmit(); // [수정] 함수 이름 변경
    }
});

// [FIX] 피드백 버튼 클릭 이벤트 리스너 (이벤트 위임 방식)
chatBox.addEventListener('click', async (event) => {
    // [신규] 'clarify' 버튼 클릭 처리
    console.log('CHATBOX_CLICKED');
    if (event.target.classList.contains('clarify-btn')) {
        const buttonText = event.target.innerText;
        handleButtonClick(buttonText);
    }

    // 클릭된 요소가 .feedback-btn 클래스를 가지고 있는지 확인
    if (event.target.classList.contains('feedback-btn')) {
        const button = event.target;
        // 버튼에 저장된 데이터(data- attributes) 가져오기
        const { jobId, question, answer, feedback } = button.dataset;

        // 버튼 비활성화 (중복 클릭 방지)
        button.parentElement.querySelectorAll('.feedback-btn').forEach(btn => {
            btn.disabled = true;
            btn.style.opacity = 0.5;
            btn.style.cursor = 'default';
        });

        try {
            // 피드백 전송 API 호출
            await sendFeedback(jobId, question, answer, feedback);
            
            // 버튼을 "감사" 메시지로 교체
            const thanksMsg = document.createElement('p');
            thanksMsg.className = 'feedback-thanks';
            thanksMsg.textContent = '소중한 의견 감사합니다!';
            button.parentElement.replaceWith(thanksMsg); 
        } catch (error) {
            console.error("Feedback error:", error);
            // 오류 발생 시 메시지 표시
            button.parentElement.innerHTML = '<p class="feedback-error">피드백 전송에 실패했습니다.</p>';
        }
    if (event.target.classList.contains('clarify-btn')) {
        // ... (이하 동일) ...
    }
    }
});

// --- [FIX] 핵심 기능 함수 (비동기 방식) ---
async function handleFormSubmit() {
    const question = userInput.value.trim();
    if (!question) return;

    // [신규] 사용자가 폼으로 새 질문을 입력했으므로,
    // 'clarify' 맥락(pendingContext)을 강제로 초기화합니다. (매우 중요!)
    pendingContext = null;

    // 'clarify'에 대비해 현재 질문을 저장합니다.
    currentQuestion = question; 

    // 화면에 버튼이 남아있다면 삭제
    clearButtons();

    // [★신규★] 사용자의 질문을 히스토리에 추가
    updateChatHistory("user", question);

    let requestBody;
    if (SHOW_MORE_KEYWORDS.has(question.toLowerCase())) {
        // "다음", "더" 등 키워드 입력 시, 저장된 '더 보기' 맥락 전송
        console.log("Sending 'show_more' request with context...");
        requestBody = {
            question: question,
            last_result_ids: currentResultIds,
            shown_count: currentShownCount,
            chat_history: chatHistory
        };
    } else {
        // 그 외 모든 '새 질문'은 맥락 없이 전송
        requestBody = {
            question: question,
            last_result_ids: [],
            shown_count: 0,
            chat_history: chatHistory
        };
    }

    // [수정] addMessageToBox는 이제 'sendMessage'가 아닌 여기서 호출

    addMessageToBox('user', question);
    userInput.value = '';

    await fetchChatResponse(requestBody);
}

// [신규] 'clarify' 버튼 클릭 시 호출되는 함수
async function handleButtonClick(buttonText) {
    let newQuestion;

    if (pendingContext) {
        // "6개월" (pendingContext) + "의료/재활" (buttonText) 조합
        newQuestion = pendingContext + ' ' + buttonText;
    } else {
        newQuestion = buttonText; // 비상시
    }

    // 맥락 사용 후 즉시 초기화
    pendingContext = null;
    clearButtons(); // 화면에서 버튼 삭제

    // 조합된 *새 질문*을 유저 메시지로 표시
    addMessageToBox('user', newQuestion);

    // [신규] 조합된 새 질문으로 서버에 요청
    // 'currentQuestion'도 이 새 질문으로 업데이트
    currentQuestion = newQuestion; 

    // [★신규★] 사용자의 클릭(질문)을 히스토리에 추가
    updateChatHistory("user", newQuestion);

    const requestBody = {
        question: newQuestion,
        last_result_ids: [],
        shown_count: 0,
        chat_history: chatHistory // [★신규★] 히스토리 전송
    };
    
    await fetchChatResponse(requestBody);
}

// [신규] 실제 API 요청 및 응답 처리를 담당하는 공통 함수
async function fetchChatResponse(requestBody) {
    const loadingElement = addMessageToBox('assistant', '<div class="spinner"></div><p class="loading-text">요청을 접수하는 중...</p>');
    const loadingTextElement = loadingElement.querySelector('.loading-text');
    let messageIntervalId = null;
    let lastIndex = -1; // 로딩 메시지 중복 방지

    try {
        const chatResponse = await fetch(API_URL_CHAT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });

        if (!chatResponse.ok) {
            const errorData = await chatResponse.json().catch(() => ({ detail: "서버 응답 오류" }));
            throw new Error(`Job 생성 실패: ${errorData.detail || chatResponse.statusText}`);
        }

        const chatData = await chatResponse.json();

        // --- [수정] 응답 상태(status) 기반 분기 처리 ---

        // 1. [신규] 'clarify' (되묻기) 상태
        if (chatData.status === 'clarify') {
            clearInterval(messageIntervalId);
            const formattedHtml = marked.parse(chatData.answer);
            loadingElement.innerHTML = formattedHtml;
            
            // "6개월" 같은 원래 질문(currentQuestion)을 'pendingContext'에 저장
            pendingContext = currentQuestion; 
            
            // 버튼 생성
            createButtons(chatData.options);
            // [★신규★] 챗봇의 되묻는 답변을 히스토리에 추가
            updateChatHistory("assistant", chatData.answer);
        }
        // 2. [수정] 'complete' (즉시 답변) 또는 'error' (동기 오류)
        else if (chatData.status === 'complete' || chatData.status === 'error') {
            pendingContext = null; // 맥락 초기화
            clearInterval(messageIntervalId);
            const formattedHtml = marked.parse(chatData.answer);
            loadingElement.innerHTML = formattedHtml;

            // '더 보기' 데이터 저장 (캐시 히트, '더 보기' 성공/실패 시 모두 해당)
            currentResultIds = chatData.last_result_ids || [];
            currentTotalFound = chatData.total_found || 0;
            currentShownCount = chatData.shown_count || Math.min(2, currentResultIds.length);
            // [★신규★] 챗봇의 최종 답변을 히스토리에 추가
            updateChatHistory("assistant", chatData.answer);
        }
        // 3. 'job_id' (비동기 작업)
        else if (chatData.job_id) {
            pendingContext = null; // 맥락 초기화
            const jobId = chatData.job_id;
            loadingTextElement.textContent = loadingMessages[0];
            messageIntervalId = setInterval(() => {
                let randomIndex;
                do {
                    randomIndex = Math.floor(Math.random() * loadingMessages.length);
                } while (randomIndex === lastIndex && loadingMessages.length > 1);
                loadingTextElement.textContent = loadingMessages[randomIndex];
                lastIndex = randomIndex;
            }, 3000);
            
            // [수정] currentQuestion을 전달 (피드백 버튼용)
            pollForResult(jobId, currentQuestion, loadingElement, messageIntervalId);
        }
        // 4. 알 수 없는 응답
        else {
            throw new Error("서버로부터 유효한 응답을 받지 못했습니다.");
        }
        // --- 분기 처리 끝 ---

    } catch (error) {
        console.error('Error in fetchChatResponse:', error);
        loadingElement.innerHTML = `<p>오류 발생: ${error.message || '요청 처리 중 문제 발생'}</p>`;
        if (messageIntervalId) clearInterval(messageIntervalId);
    }
    chatBox.scrollTop = chatBox.scrollHeight;
}

// --- [FIX] 결과 폴링 함수 추가 ---
async function pollForResult(jobId, question, loadingElement, messageIntervalId, pollInterval = 3000, maxAttempts = 40) {
    // pollInterval: 3초마다 확인
    // maxAttempts: 최대 40번 시도 (3초 * 40 = 120초 = 2분 타임아웃)
    let attempts = 0;

    const intervalId = setInterval(async () => {
        attempts++;
        console.log(`Polling attempt ${attempts} for job ID: ${jobId}`); // 콘솔 로그 추가

        if (attempts > maxAttempts) {
            clearInterval(intervalId); // 시도 횟수 초과 시 폴링 중지
            clearInterval(messageIntervalId); // 로딩 메시지 변경 중지
            loadingElement.innerHTML = '<p>시간 초과: 답변을 가져오는 데 너무 오래 걸립니다.</p>';
            chatBox.scrollTop = chatBox.scrollHeight;
            return;
        }

        try {
            const resultResponse = await fetch(`${API_URL_RESULT}${jobId}`); // GET 요청

            if (!resultResponse.ok) {
                // /get_result 호출 자체가 실패한 경우 (네트워크 오류 등)
                // 잠시 후 다시 시도 (clearInterval 하지 않음)
                console.error(`Polling error: HTTP status ${resultResponse.status}`);
                return; // 다음 인터벌에서 재시도
            }

            const resultData = await resultResponse.json();
            const status = resultData.status;

            console.log(`Job status: ${status}`); // 콘솔 로그 추가

            if (status === 'complete') {
                clearInterval(intervalId); 
                clearInterval(messageIntervalId);
                
                const markdownText = resultData.answer;
                const formattedHtml = marked.parse(markdownText);
                loadingElement.innerHTML = formattedHtml; 
                
                // [★신규★] 챗봇의 비동기 최종 답변을 히스토리에 추가
                updateChatHistory("assistant", markdownText);

                // [추가] '더 보기'를 위한 데이터 저장
                currentResultIds = resultData.last_result_ids || [];
                currentTotalFound = resultData.total_found || 0;
                // (표시된 개수 계산: 2개 또는 그보다 적은 수)
                currentShownCount = Math.min(2, currentResultIds.length); 
                
                // [추가] 피드백 버튼 추가
                addFeedbackButtons(loadingElement, jobId, question, markdownText);
            } else if (status === 'error') {
                clearInterval(intervalId); // 오류 시 폴링 중지
                clearInterval(messageIntervalId); // 로딩 메시지 변경 중지
                loadingElement.innerHTML = `<p>오류: ${resultData.message || '알 수 없는 오류 발생'}</p>`;
            } 
            // else if (status === 'pending') {
            //    // 아직 처리 중이면 아무것도 안 하고 다음 인터벌 기다림
            // }

            // 결과 업데이트 후 스크롤 조정
            chatBox.scrollTop = chatBox.scrollHeight;

        } catch (error) {
            console.error('Error during polling:', error);
            // 네트워크 오류 등으로 fetch 자체가 실패해도 계속 시도
        }
    }, pollInterval);
}


// --- 헬퍼 함수 ---
function addMessageToBox(role, content) {
    const messageElement = document.createElement('div');
    messageElement.classList.add('message', role);

    // HTML 문자열이면 innerHTML 사용, 아니면 textContent 사용
    // (로딩 스피너+텍스트 처리를 위해 수정)
    if (content.includes('<div class="spinner">')) {
         messageElement.innerHTML = content;
    } else if (content.startsWith('<p>') || content.startsWith('<hr>')) { // Markdown 변환 결과
         messageElement.innerHTML = content;
    }
     else { // 사용자 질문 또는 단순 텍스트
        const p = document.createElement('p');
        p.textContent = content;
        messageElement.appendChild(p);
    }
    chatBox.appendChild(messageElement);
    chatBox.scrollTop = chatBox.scrollHeight;
    return messageElement; // 로딩 메시지 업데이트를 위해 요소 반환
}

function updateChatHistory(role, content) {
    // 역할(role)과 내용(content)을 객체로 추가
    chatHistory.push({ "role": role, "content": content });

    // 최대 히스토리 개수 유지 (예: 4개 = 2턴)
    // MAX_HISTORY_TURNS * 2 보다 길어지면
    if (chatHistory.length > (MAX_HISTORY_TURNS * 2)) {
        // 가장 오래된 메시지(배열의 첫 번째 요소) 제거
        chatHistory.shift(); 
    }
}

// --- [신규] 'clarify' 버튼 생성 함수 ---
function createButtons(optionsArray) {
    const buttonContainer = document.createElement('div');
    buttonContainer.className = 'button-container'; // CSS 스타일링용

    optionsArray.forEach(optionText => {
        const button = document.createElement('button');
        button.className = 'clarify-btn'; // CSS 스타일링 및 이벤트 리스너용
        button.innerText = optionText;
        
        buttonContainer.appendChild(button);
    });

    // 버튼 컨테이너를 채팅창의 마지막 메시지(로딩 메시지) 뒤에 추가
    chatBox.appendChild(buttonContainer);
    chatBox.scrollTop = chatBox.scrollHeight;
}

// --- [신규] 버튼 제거 함수 ---
function clearButtons() {
    const existingContainer = document.querySelector('.button-container');
    if (existingContainer) {
        existingContainer.remove();
    }
}

// (파일 맨 아래)

// --- [추가] 피드백 버튼 동적 추가 함수 ---
function addFeedbackButtons(messageElement, jobId, question, answer) {
    // --- 1. Google Form URL 템플릿 설정 ---
    const GOOGLE_FORM_BASE_URL = "https://docs.google.com/forms/d/e/1FAIpQLSfwoqGxXYpUarmyz2fKECfku4-dI7JSXhaMtiRov8nrOP141g/viewform?usp=pp_url";
    // [정상] 이 entry ID들은 올바르게 복사하셨습니다.
    const ENTRY_ID_QUESTION = "entry.1180067422";
    const ENTRY_ID_ANSWER = "entry.1860595640"; // (답변은 URL 길이 문제로 제외)
    const ENTRY_ID_FEEDBACK = "entry.1169968900";
    const ENTRY_ID_JOB = "entry.991310487";

// --- 2. 전송할 데이터 인코딩 ---
    const encodedQuestion = encodeURIComponent(question);
    const encodedJobId = encodeURIComponent(jobId);

    // --- 3. '좋아요' / '싫어요' 링크 생성 (Answer 제외) ---
    // (이제 깨끗한 BASE_URL 뒤에 entry가 올바르게 붙습니다)
    const goodLink = `${GOOGLE_FORM_BASE_URL}&${ENTRY_ID_QUESTION}=${encodedQuestion}&${ENTRY_ID_FEEDBACK}=${encodeURIComponent("👍")}&${ENTRY_ID_JOB}=${encodedJobId}`;
    const badLink = `${GOOGLE_FORM_BASE_URL}&${ENTRY_ID_QUESTION}=${encodedQuestion}&${ENTRY_ID_FEEDBACK}=${encodeURIComponent("👎")}&${ENTRY_ID_JOB}=${encodedJobId}`;
    
    // --- 4. HTML 링크 생성 (기존과 동일) ---
    const feedbackContainer = document.createElement('div');
    feedbackContainer.className = 'feedback-container';

    const feedbackMsg = document.createElement('p');
    feedbackMsg.textContent = '이 답변이 도움이 되었나요?';
    feedbackContainer.appendChild(feedbackMsg);

    const goodBtnLink = document.createElement('a');
    goodBtnLink.className = 'feedback-btn-link';
    goodBtnLink.href = goodLink;
    goodBtnLink.target = "_blank";
    goodBtnLink.textContent = '👍';
    goodBtnLink.title = "유용했어요!";
    feedbackContainer.appendChild(goodBtnLink);

    const badBtnLink = document.createElement('a');
    badBtnLink.className = 'feedback-btn-link';
    badBtnLink.href = badLink;
    badBtnLink.target = "_blank";
    badBtnLink.textContent = '👎';
    badBtnLink.title = "아쉬워요";
    feedbackContainer.appendChild(badBtnLink);

    messageElement.appendChild(feedbackContainer);
}

// --- [추가] 피드백 전송 API 함수 ---
// (sendMessage 함수 위에 두는 것이 좋습니다)
async function sendFeedback(jobId, question, answer, feedback) {
    const response = await fetch(API_URL_FEEDBACK, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            job_id: jobId,
            question: question,
            answer: answer,
            feedback: feedback
        })
    });
    if (!response.ok) {
        throw new Error(`Feedback API error! status: ${response.status}`);
    }
    return await response.json();
}