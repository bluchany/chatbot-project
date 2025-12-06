// script.js - Final Version (No Emoji Tips & Error Fix)

console.log('SCRIPT_LOADED_NO_EMOJI_FIX');

// --- 1. 전역 변수 ---
const chatBox = document.getElementById('chat-box');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const micBtn = document.getElementById('mic-btn'); 

const API_URL_CHAT = '/chat';
const API_URL_RESULT = '/get_result/';
const API_URL_FEEDBACK = '/feedback';

let currentResultIds = [];
let currentShownCount = 0;
let currentTotalFound = 0;

let pendingContext = null; 
let currentQuestion = ""; 
let chatHistory = []; 
const MAX_HISTORY_TURNS = 2; 

// --- [로딩 메시지 데이터베이스] ---
// 1. 수사반장 모드 (진행 상황)
const actionMessages = [
    "🔍 질문의 의도를 꼼꼼히 분석하고 있어요...",
    "📂 도봉구 복지 데이터베이스를 뒤지는 중...",
    "🏃‍♀️ 관련 문서를 찾아 열심히 뛰어다니는 중...",
    "🤔 자격 요건이 맞는지 확인하고 있어요...",
    "📝 찾은 정보를 보기 좋게 요약하는 중...",
    "✨ 답변을 예쁘게 포장하고 있어요..."
];

// 2. 꿀팁 모드 (전구 이모지 제거됨)
const welfareTips = [
    // [신생아~12개월]
    "[0~12개월] 터미타임의 기적: 생후 1개월부터 깨어있을 때 엎드려 놀게 해주세요. 등 근육이 튼튼해집니다.",
    "[0~12개월] 초점 책보다 엄마 얼굴: 아기가 가장 좋아하는 장난감은 부모의 눈과 입입니다. 눈을 맞춰주세요.",
    "[0~12개월] 울음은 대화예요: 아기가 울 때 즉각 반응해 주세요. 세상에 대한 신뢰가 쌓입니다.",
    "[0~12개월] 까꿍 놀이의 힘: 6개월부터 까꿍 놀이를 해주세요. 대상 영속성을 배웁니다.",
    "[0~12개월] 전신 마사지: 기저귀 갈 때 다리를 쭉쭉 펴주는 마사지는 성장판을 자극합니다.",
    "[0~12개월] 옹알이 리액션: 아기가 '아~' 하면 엄마도 따라 해주세요. 대화의 즐거움을 배웁니다.",
    "[0~12개월] 이유식은 촉감 놀이: 아이가 음식을 손으로 만지고 뭉개도 괜찮아요. 오감 발달 과정입니다.",
    "[0~12개월] 안전한 탐색: 기어 다니기 시작하면 바닥의 작은 물건은 치워주세요. 구강기 사고 예방!",
    
    // [13~36개월]
    "[13~36개월] '내가 할래!' 존중하기: 서툴러도 혼자 해보게 기다려주세요. 자존감이 자라납니다.",
    "[13~36개월] 언어 확장하기: '물'이라고 하면 '시원한 물 줄까?'라고 문장으로 늘려 말해주세요.",
    "[13~36개월] 스티커 놀이: 손가락 끝으로 스티커를 떼고 붙이는 놀이는 소근육 발달에 최고입니다.",
    "[13~36개월] 감정 읽어주기: 떼쓸 땐 혼내기보다 '속상했구나'라고 감정을 먼저 읽어주세요.",
    "[13~36개월] 선택권 주기: '양말 신어' 대신 '파란 양말 줄까, 빨간 양말 줄까?'라고 물어보세요.",
    "[13~36개월] 배변 훈련 타이밍: 아이가 기저귀 젖는 것을 불편해하거나 화장실에 관심을 보일 때가 적기입니다.",
    "[13~36개월] 미디어 프리: 만 2세 이전에는 영상 노출을 피하는 것이 뇌 발달에 가장 좋습니다.",
    "[13~36개월] 역할 놀이: 인형에게 밥을 먹이는 흉내를 내보세요. 상상력과 공감 능력이 자랍니다.",
    "[13~36개월] 잠자리 독서: 자기 전 그림책 한 권은 수면 의식이 되고 언어 발달도 돕습니다.",
    "[13~36개월] 위험할 땐 단호하게: 안전 문제는 길게 설명하지 말고 짧고 단호하게 '안 돼'라고 말해주세요.",
    
    // [37~72개월]
    "[37~72개월] 호기심 대장: 끊임없는 '왜?' 질문에 '너는 어떻게 생각해?'라고 되물어 사고력을 키워주세요.",
    "[37~72개월] 규칙 있는 놀이: 술래잡기나 보드게임을 통해 규칙을 지키고 순서를 기다리는 법을 알려주세요.",
    "[37~72개월] 구체적인 칭찬: '착하네' 대신 '장난감을 제자리에 정리해서 멋지다'라고 구체적으로 칭찬해 주세요.",
    "[37~72개월] 거짓말 대처: 만 4세의 거짓말은 상상의 혼동일 수 있습니다. 혼내기보다 사실을 말하게 유도하세요.",
    "[37~72개월] 감정 단어: '화나' 외에도 '서운해, 억울해, 부끄러워' 등 다양한 감정 단어를 알려주세요.",
    "[37~72개월] 과정 칭찬: 결과보다 과정을 칭찬하면 새로운 도전을 두려워하지 않는 아이가 됩니다.",
    "[37~72개월] 디지털 약속: 영상은 하루 1시간 이내로, 아이와 함께 규칙을 정해서 보세요.",
    "[37~72개월] 성교육의 시작: 신체 부위의 명칭을 알려주고, '내 몸의 주인은 나'라는 것을 가르쳐주세요.",
    "[37~72개월] 스스로 해결: 친구와 다퉜을 때 아이가 어떻게 해결하고 싶은지 먼저 물어봐 주세요.",
    "[37~72개월] 작은 심부름: 수저 놓기 등 집안일에 참여시켜 가족 구성원으로서의 소속감을 느끼게 해주세요.",
    
    // [모든 연령]
    "[부모 꿀팁] 비교 금지: 옆집 아이와 비교하지 마세요. 우리 아이만의 속도가 있습니다.",
    "[부모 꿀팁] 일관성: 부모의 기분에 따라 훈육 기준이 바뀌면 아이는 혼란스러워합니다.",
    "[부모 꿀팁] 부모의 사과: 부모도 실수할 수 있습니다. 솔직하게 사과하는 모습은 최고의 교육입니다.",
    "[부모 꿀팁] 경청: 아이가 말을 더듬더라도 끝까지 들어주세요. 말하는 자신감이 생깁니다.",
    "[부모 꿀팁] 눈높이 대화: 아이와 대화할 때는 무릎을 굽혀 아이의 눈높이에서 바라봐 주세요.",
    "[부모 꿀팁] 사랑의 스킨십: 하루 한 번, 아이를 꽉 안아주세요. 백 마디 말보다 큰 안정감을 줍니다.",
    "[부모 꿀팁] 충분히 좋은 부모: 완벽한 부모가 되려 하지 마세요. 지금도 충분히 잘하고 계십니다.",
    "[부모 꿀팁] 부모의 행복: 부모가 행복해야 아이도 행복합니다. 나를 위한 휴식 시간도 꼭 챙기세요.",
    "[부모 꿀팁] 잠이 보약: 성장 호르몬은 밤 10시~새벽 2시에 나옵니다. 일찍 재우는 습관을 들이세요.",
    "[부모 꿀팁] 식사 예절: 돌아다니며 먹지 않고 식탁에 앉아서 먹는 습관은 이유식 시기부터 잡아주세요.",
    "[부모 꿀팁] 자연 놀이터: 하루 30분, 바깥바람을 쐬게 해주세요. 면역력과 정서 발달에 좋습니다.",
    "[부모 꿀팁] 기다림의 미학: 육아의 8할은 기다림입니다. 아이가 스스로 해낼 때까지 한 템포만 기다려주세요."
];

const SHOW_MORE_KEYWORDS = new Set([
    "다음", "더", "더 보여줘", "계속", "이어서",
    "다음거", "다음꺼", "다른거", "다른 거", "또",
    "next", "more"
]);

// --- 2. 음성 인식 설정 ---
const isInIframe = window.self !== window.top;
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const canUseMic = SpeechRecognition && !isInIframe;

// --- 3. 버튼 토글 ---
function toggleInputButtons() {
    const text = userInput.value.trim();
    if (text.length > 0) {
        sendBtn.style.display = 'flex';
        micBtn.style.display = 'none';
    } else {
        if (canUseMic) {
            sendBtn.style.display = 'none';
            micBtn.style.display = 'flex';
        } else {
            sendBtn.style.display = 'flex';
            micBtn.style.display = 'none';
        }
    }
}
toggleInputButtons();
userInput.addEventListener('input', toggleInputButtons);

// --- 4. 이벤트 리스너 ---
sendBtn.addEventListener('click', () => {
    handleFormSubmit();
    setTimeout(toggleInputButtons, 10); 
});

userInput.addEventListener('keypress', (event) => {
    if (event.key === 'Enter') {
        handleFormSubmit();
        setTimeout(toggleInputButtons, 10);
    }
});

chatBox.addEventListener('click', async (event) => {
    if (event.target.classList.contains('clarify-btn')) {
        const buttonText = event.target.innerText;
        handleButtonClick(buttonText);
    }
    if (event.target.classList.contains('card-share-btn')) {
        const btn = event.target;
        const textToCopy = btn.dataset.copy;
        
        if (navigator.share && !isInIframe) {
            try {
                await navigator.share({ title: '복지 정보', text: textToCopy, url: window.location.href });
                return;
            } catch (err) {}
        }
        try {
            await navigator.clipboard.writeText(textToCopy);
            alert("카드 내용이 복사되었습니다!");
        } catch (err) {
            prompt("복사하기:", textToCopy);
        }
    }
});


// --- 5. 메인 로직 ---
async function handleFormSubmit() {
    const question = userInput.value.trim();
    if (!question) return;

    pendingContext = null;
    currentQuestion = question; 
    clearButtons();
    updateChatHistory("user", question);

    let requestBody = {
        question: question,
        last_result_ids: [],
        shown_count: 0,
        chat_history: chatHistory
    };

    if (SHOW_MORE_KEYWORDS.has(question.toLowerCase())) {
        requestBody.last_result_ids = currentResultIds;
        requestBody.shown_count = currentShownCount;
    }

    addMessageToBox('user', question);
    userInput.value = '';
    toggleInputButtons();

    await fetchChatResponse(requestBody);
}

async function handleButtonClick(buttonText) {
    let newQuestion = pendingContext ? `${pendingContext} ${buttonText}` : buttonText;
    pendingContext = null;
    clearButtons();
    addMessageToBox('user', newQuestion);
    currentQuestion = newQuestion; 
    updateChatHistory("user", newQuestion);

    const requestBody = {
        question: newQuestion,
        last_result_ids: [],
        shown_count: 0,
        chat_history: chatHistory
    };
    await fetchChatResponse(requestBody);
}

async function fetchChatResponse(requestBody) {
    // [수정] 변수 선언을 확실하게! (에러 해결)
    const initialMsg = actionMessages[0];
    const initialTip = welfareTips[Math.floor(Math.random() * welfareTips.length)];
    
    const skeletonHTML = `
        <div class="skeleton-container">
            <div class="skeleton-box" style="width: 90%;"></div>
            <div class="skeleton-box" style="width: 70%;"></div>
            <div class="skeleton-box" style="width: 85%;"></div>
            
            <div style="margin-top: 12px;">
                <p class="action-text" style="font-size: 14px; font-weight: 600; color: #333; margin: 0 0 12px 0;">
                    ${initialMsg}
                </p>
                <p class="tip-text" style="font-size: 12px; font-weight: 400; color: #888; margin: 0;">
                    ${initialTip}
                </p>
            </div>
        </div>
    `;

    const loadingElement = addMessageToBox('assistant', skeletonHTML);
    const actionTextEl = loadingElement.querySelector('.action-text');
    const tipTextEl = loadingElement.querySelector('.tip-text');
    
    // [타이머] 7초마다 갱신
    let toggleStep = 0; 
    let messageIntervalId = setInterval(() => {
        toggleStep++;
        
        if (toggleStep % 2 === 0) {
            const actionIndex = (toggleStep / 2) % actionMessages.length;
            if(actionTextEl) actionTextEl.textContent = actionMessages[actionIndex];
        } else {
            const randomTip = welfareTips[Math.floor(Math.random() * welfareTips.length)];
            if(tipTextEl) tipTextEl.textContent = randomTip;
        }
    }, 7000); 

    try {
        const chatResponse = await fetch(API_URL_CHAT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });

        if (!chatResponse.ok) throw new Error(`Server error: ${chatResponse.statusText}`);
        const chatData = await chatResponse.json();

        if (chatData.status === 'clarify') {
            clearInterval(messageIntervalId);
            loadingElement.innerHTML = marked.parse(chatData.answer);
            pendingContext = currentQuestion; 
            createButtons(chatData.options);
            updateChatHistory("assistant", chatData.answer);
        }
        else if (chatData.status === 'complete' || chatData.status === 'error') {
            clearInterval(messageIntervalId);
            
            if (chatData.answer.includes('result-card')) {
                loadingElement.innerHTML = chatData.answer;
            } else {
                loadingElement.innerHTML = marked.parse(chatData.answer);
            }
            
            currentResultIds = chatData.last_result_ids || [];
            currentTotalFound = chatData.total_found || 0;
            currentShownCount = chatData.shown_count || Math.min(2, currentResultIds.length);
            updateChatHistory("assistant", chatData.answer);
            
            if (chatData.job_id) {
                addFeedbackButtons(loadingElement, chatData.job_id, currentQuestion, chatData.answer);
            }
        }
        else if (chatData.job_id) {
            const jobId = chatData.job_id;
            pollForResult(jobId, currentQuestion, loadingElement, messageIntervalId, actionTextEl, tipTextEl);
        }
    } catch (error) {
        loadingElement.innerHTML = `<p>오류 발생: ${error.message}</p>`;
        if (messageIntervalId) clearInterval(messageIntervalId);
    }
    chatBox.scrollTop = chatBox.scrollHeight;
}

// [수정] 폴링 함수
async function pollForResult(jobId, question, loadingElement, messageIntervalId, actionTextEl, tipTextEl, pollInterval = 1000) {
    let attempts = 0;
    const intervalId = setInterval(async () => {
        attempts++;
        if (attempts > 120) {
            clearInterval(intervalId); clearInterval(messageIntervalId);
            loadingElement.innerHTML = '<p>시간 초과</p>';
            return;
        }
        try {
            const resultResponse = await fetch(`${API_URL_RESULT}${jobId}`);
            if (!resultResponse.ok) return; 
            const resultData = await resultResponse.json();

            if (resultData.status === 'complete') {
                clearInterval(intervalId); clearInterval(messageIntervalId);
                
                if (resultData.answer.includes('result-card')) {
                    loadingElement.innerHTML = resultData.answer;
                } else {
                    loadingElement.innerHTML = marked.parse(resultData.answer);
                }
                
                updateChatHistory("assistant", resultData.answer);
                currentResultIds = resultData.last_result_ids || [];
                currentTotalFound = resultData.total_found || 0;
                currentShownCount = Math.min(2, currentResultIds.length); 
                
                addFeedbackButtons(loadingElement, jobId, question, resultData.answer);
            } else if (resultData.status === 'error') {
                clearInterval(intervalId); clearInterval(messageIntervalId);
                loadingElement.innerHTML = `<p>오류: ${resultData.message}</p>`;
            }
            chatBox.scrollTop = chatBox.scrollHeight;
        } catch (error) {
            console.error('Polling loop error:', error);
        }
    }, pollInterval);
}

// --- 6. 헬퍼 함수 ---
function addMessageToBox(role, content) {
    const rowElement = document.createElement('div');
    rowElement.classList.add('message-row', role);

    if (role === 'assistant') {
        const iconImg = document.createElement('img');
        iconImg.src = "/static/bot-icon.png"; 
        iconImg.className = "bot-profile-icon";
        iconImg.alt = "bot";
        rowElement.appendChild(iconImg);
    }

    const messageBubble = document.createElement('div');
    messageBubble.classList.add('message', role);

    if (content.includes('<div') || content.includes('<p>') || content.includes('<hr>')) {
         messageBubble.innerHTML = content;
    } else { 
        const p = document.createElement('p');
        p.textContent = content;
        messageBubble.appendChild(p);
    }

    rowElement.appendChild(messageBubble);
    chatBox.appendChild(rowElement);
    chatBox.scrollTop = chatBox.scrollHeight;
    return messageBubble; 
}

function updateChatHistory(role, content) {
    chatHistory.push({ "role": role, "content": content });
    if (chatHistory.length > MAX_HISTORY_TURNS * 2) chatHistory.shift(); 
}

function createButtons(optionsArray) {
    const buttonContainer = document.createElement('div');
    buttonContainer.className = 'button-container';
    optionsArray.forEach(optionText => {
        const button = document.createElement('button');
        button.className = 'clarify-btn';
        button.innerText = optionText;
        buttonContainer.appendChild(button);
    });
    chatBox.appendChild(buttonContainer);
    chatBox.scrollTop = chatBox.scrollHeight;
}

function clearButtons() {
    const existingContainer = document.querySelector('.button-container');
    if (existingContainer) existingContainer.remove();
}

function addFeedbackButtons(messageElement, jobId, question, answer) {
    const GOOGLE_FORM_BASE_URL = "https://docs.google.com/forms/d/e/1FAIpQLSfwoqGxXYpUarmyz2fKECfku4-dI7JSXhaMtiRov8nrOP141g/viewform?usp=pp_url";
    const ENTRY_ID_QUESTION = "entry.1180067422";
    const ENTRY_ID_FEEDBACK = "entry.1169968900";
    const ENTRY_ID_JOB = "entry.991310487";

    const encodedQuestion = encodeURIComponent(question);
    const encodedJobId = encodeURIComponent(jobId);

    const goodLink = `${GOOGLE_FORM_BASE_URL}&${ENTRY_ID_QUESTION}=${encodedQuestion}&${ENTRY_ID_FEEDBACK}=${encodeURIComponent("👍")}&${ENTRY_ID_JOB}=${encodedJobId}`;
    const badLink = `${GOOGLE_FORM_BASE_URL}&${ENTRY_ID_QUESTION}=${encodedQuestion}&${ENTRY_ID_FEEDBACK}=${encodeURIComponent("👎")}&${ENTRY_ID_JOB}=${encodedJobId}`;
    
    const feedbackContainer = document.createElement('div');
    feedbackContainer.className = 'feedback-container';

    const feedbackMsg = document.createElement('p');
    feedbackMsg.textContent = '이 답변이 도움이 되었나요?';
    feedbackContainer.appendChild(feedbackMsg);

    const btnGroup = document.createElement('div');
    btnGroup.className = 'feedback-btn-group';

    const goodBtnLink = document.createElement('a');
    goodBtnLink.className = 'feedback-btn-link';
    goodBtnLink.href = goodLink;
    goodBtnLink.target = "_blank";
    goodBtnLink.textContent = '👍';
    btnGroup.appendChild(goodBtnLink);

    const badBtnLink = document.createElement('a');
    badBtnLink.className = 'feedback-btn-link';
    badBtnLink.href = badLink;
    badBtnLink.target = "_blank";
    badBtnLink.textContent = '👎';
    btnGroup.appendChild(badBtnLink);

    feedbackContainer.appendChild(btnGroup);
    messageElement.appendChild(feedbackContainer);
}

// --- 7. 음성 인식 로직 ---
let recognition;
if (canUseMic) { 
    recognition = new SpeechRecognition();
    recognition.lang = 'ko-KR'; 
    recognition.interimResults = false; 
    recognition.maxAlternatives = 1; 
    micBtn.addEventListener('click', () => { if (micBtn.classList.contains('listening')) recognition.stop(); else recognition.start(); });
    recognition.addEventListener('start', () => { micBtn.classList.add('listening'); userInput.placeholder = "말씀해주세요..."; });
    recognition.addEventListener('end', () => { micBtn.classList.remove('listening'); userInput.placeholder = "무엇이 궁금하신가요?"; });
    recognition.addEventListener('result', (event) => { userInput.value = event.results[0][0].transcript; toggleInputButtons(); });
    recognition.addEventListener('error', (event) => {
        console.error('Speech error:', event.error);
        micBtn.classList.remove('listening');
        userInput.placeholder = "음성 인식 실패";
        setTimeout(() => { userInput.placeholder = "무엇이 궁금하신가요?"; }, 2000);
    });
} else {
    if(micBtn) micBtn.style.display = 'none';
    if(sendBtn) sendBtn.style.display = 'flex';
}

// [모바일 키보드 대응] 화면 크기가 변하면(키보드 등) 스크롤을 맨 아래로 내림
window.visualViewport.addEventListener('resize', () => {
    // 100ms 뒤에 실행 (키보드 올라오는 애니메이션 시간 고려)
    setTimeout(() => {
        chatBox.scrollTop = chatBox.scrollHeight;
    }, 100);
});

// [신규] 추천 질문 클릭 시 실행
function sendSuggestion(text) {
    const userInput = document.getElementById('user-input');
    userInput.value = text; // 입력창에 텍스트 넣기
    
    // 버튼 상태 갱신 (비행기 버튼 보이기)
    toggleInputButtons();
    
    // 0.3초 뒤 자동 전송 (사용자가 입력된 걸 볼 시간 줌)
    setTimeout(() => {
        document.getElementById('send-btn').click();
    }, 300);
}

// [신규] 토글 버튼 로직
const toggleBtn = document.getElementById('suggestion-toggle-btn');
const suggestionContainer = document.querySelector('.suggestion-container');

if (toggleBtn && suggestionContainer) {
    toggleBtn.addEventListener('click', () => {
        // 1. 컨테이너 보이기/숨기기 토글
        suggestionContainer.classList.toggle('hidden');
        
        // 2. 버튼 화살표 회전 토글
        toggleBtn.classList.toggle('active');
    });
}