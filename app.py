import streamlit as st
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import altair as alt
from datetime import datetime
import time
import pytz

# ==========================================
# 1. 설정 및 UI 숨김 (강력 모드)
# ==========================================
st.set_page_config(
    page_title="춘천시산림조합 CRM", 
    page_icon="🌲", 
    layout="wide", 
    initial_sidebar_state="collapsed" # 사이드바 기본 닫힘
)

# 👇 [핵심] 모든 시스템 UI를 숨기는 CSS 코드
hide_all_ui = """
    <style>
        /* 1. 상단 헤더 전체 숨기기 (햄버거 메뉴 포함) */
        header {visibility: hidden !important;}
        [data-testid="stHeader"] {display: none !important;}
        
        /* 2. 사이드바 관련 요소 숨기기 */
        [data-testid="stSidebar"] {display: none !important;}
        [data-testid="collapsedControl"] {display: none !important;}
        
        /* 3. 푸터(Made with Streamlit) 및 하단 뷰어 배지 숨기기 (모바일 포함) */
        footer {visibility: hidden !important;}
        .stFooter {display: none !important;}
        .viewerBadge_container__1QSob {display: none !important;} /* 뷰어 배지 클래스 */
        
        /* 4. 우측 상단 메뉴, 배포 버튼, 툴바 숨기기 */
        #MainMenu {visibility: hidden !important;}
        .stDeployButton {display:none !important;}
        [data-testid="stToolbar"] {display: none !important;}
        
        /* 5. "Hosted with Streamlit" 등 하단 고정 링크 숨기기 */
        a[href^="https://streamlit.io/cloud"] {display: none !important;}
        div[class*="viewerBadge"] {display: none !important;}
        
        /* 6. 상단 여백 제거 (헤더 사라진 자리) */
        .block-container {
            padding-top: 1rem !important;
        }
    </style>
"""
st.markdown(hide_all_ui, unsafe_allow_html=True)

# [인증 정보 캐싱]
@st.cache_resource
def get_google_sheet_client():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        
        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            creds_dict, 
            ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        )
        return gspread.authorize(creds)
    except Exception as e:
        return None

# [데이터 로드 캐싱] (TTL 10분)
@st.cache_data(ttl=600) 
def get_data(worksheet_name):
    try:
        client = get_google_sheet_client()
        if not client: return pd.DataFrame()
        sheet = client.open('조합원상담관리').worksheet(worksheet_name)
        data = sheet.get_all_values()
        if not data: return pd.DataFrame()
        headers = data.pop(0)
        return pd.DataFrame(data, columns=headers)
    except: return pd.DataFrame()

# AI 설정
ai_available = False
try:
    if "general" in st.secrets and "GOOGLE_API_KEY" in st.secrets["general"]:
        genai.configure(api_key=st.secrets["general"]["GOOGLE_API_KEY"])
        model = genai.GenerativeModel('gemini-2.5-flash')
        ai_available = True
except: pass

# ==========================================
# 2. 로직 함수들
# ==========================================

def add_audit_log(user_name, action, details):
    try:
        client = get_google_sheet_client()
        sheet = client.open('조합원상담관리').worksheet('사용자로그')
        
        # 👇 [수정] 한국 시간으로 강제 설정
        kst = pytz.timezone('Asia/Seoul')
        timestamp = datetime.now(kst).strftime("%Y-%m-%d %H:%M:%S")
        
        sheet.append_row([timestamp, user_name, action, details])
    except: pass

def save_log(date, writer, cust_id, name, contact, raw, polished, summary, tags, dept, status, req):
    client = get_google_sheet_client()
    doc = client.open('조합원상담관리')
    sheet_log = doc.worksheet('상담이력')
    row = [str(date), writer, cust_id, name, contact, raw, polished, summary, dept, status, req, ""]
    sheet_log.append_row(row)
    
    if tags:
        try:
            sheet_user = doc.worksheet('고객정보')
            cell = sheet_user.find(cust_id)
            if cell:
                headers = sheet_user.row_values(1)
                if '태그' in headers:
                    col_idx = headers.index('태그') + 1
                    curr = sheet_user.cell(cell.row, col_idx).value
                    new_list = [t.strip() for t in tags.split(',')]
                    if curr:
                        old_list = [t.strip() for t in curr.split(',')]
                        final = list(set(old_list + new_list))
                        new_str = ", ".join(final)
                    else:
                        new_str = ", ".join(new_list)
                    sheet_user.update_cell(cell.row, col_idx, new_str)
        except: pass
    
    add_audit_log(writer, "상담저장", f"{name}({cust_id}) 상담 저장")
    get_data.clear()

def complete_action_logic(target_date, target_id, result_text, actor_name):
    client = get_google_sheet_client()
    sheet = client.open('조합원상담관리').worksheet('상담이력')
    try:
        data = sheet.get_all_values()
        h = data[0]
        idx_date = h.index('날짜')
        idx_id = h.index('고객번호')
        idx_status = h.index('조치상태') + 1
        idx_res = h.index('조치결과') + 1
        
        for i in range(len(data)-1, 0, -1):
            if data[i][idx_date] == target_date and data[i][idx_id] == target_id:
                final_result = f"{result_text} ({actor_name})"
                sheet.update_cell(i+1, idx_status, "완료")
                sheet.update_cell(i+1, idx_res, final_result)
                add_audit_log(actor_name, "조치완료", f"{target_id} 건 조치 완료")
                get_data.clear()
                return True
        return False
    except: return False

def update_info_cell(cust_id, col, val, actor_name):
    try:
        client = get_google_sheet_client()
        sheet = client.open('조합원상담관리').worksheet('고객정보')
        cell = sheet.find(cust_id)
        if cell:
            h = sheet.row_values(1)
            c_idx = h.index(col) + 1
            sheet.update_cell(cell.row, c_idx, val)
            add_audit_log(actor_name, "정보수정", f"{cust_id} - {col} 수정")
            get_data.clear()
            return True
    except: return False

def login_check(uid, upw):
    df = get_data('사용자관리')
    if df.empty: return None
    user = df[(df['아이디'] == uid) & (df['비밀번호'] == upw)]
    if not user.empty: return user.iloc[0]['이름']
    return None

# ==========================================
# 3. 화면 UI
# ==========================================

if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False

if not st.session_state['logged_in']:
    st.markdown("<br><br>", unsafe_allow_html=True)
    st.title("🌲춘천시산림조합")
    with st.container(border=True):
        uid = st.text_input("아이디")
        upw = st.text_input("비밀번호", type="password")
        if st.button("로그인", use_container_width=True, type="primary"):
            real_name = login_check(uid, upw)
            if real_name:
                st.session_state['logged_in'] = True
                st.session_state['user_name'] = real_name
                add_audit_log(real_name, "로그인", "접속 성공")
                st.rerun()
            else:
                st.error("정보 불일치")
else:
    # ------------------------------------------------
    # [상단 영역] 제목 + 사용자정보 + 새로고침 버튼
    # ------------------------------------------------
    c_top1, c_top2 = st.columns([8, 2])
    
    with c_top1:
        st.title("🌲고객관리 시스템")
        st.caption(f"👤 로그인: **{st.session_state['user_name']}**님 환영합니다.")
        
    with c_top2:
        # [요청 1] 데이터 새로고침 버튼 맨 위로 이동
        st.markdown("<br>", unsafe_allow_html=True) # 줄맞춤용 공백
        if st.button("🔄 데이터 최신화", use_container_width=True):
            get_data.clear()
            st.toast("데이터를 새로 불러왔습니다!")
            time.sleep(1)
            st.rerun()

    # ------------------------------------------------
    # [메인 영역] 탭 및 기능들
    # ------------------------------------------------
    t1, t2, t3 = st.tabs(["🏠 최근 활동", "🔎 고객 상담", "🚨 업무 협조"])

    # [Tab 1] 최근 활동
    with t1:
        st.subheader("📢 실시간 상담이력")
        df = get_data('상담이력')
        if not df.empty:
            df = df.iloc[::-1]
            for i, row in df.head(15).iterrows():
                with st.container(border=True):
                    c1, c2 = st.columns([1, 4])
                    c1.markdown(f"**{row['고객명']}**")
                    c1.caption(f"ID: {row['고객번호']}\n{row['날짜']} | {row['작성자']}")
                    
                    txt = row['정제된내용'] if row['정제된내용'] else row['원본내용']
                    c2.info(f"📄 {txt}")
                    
                    if row.get('조치결과'):
                        c2.success(f"✅ {row['조치결과']}")
                    elif row['조치상태'] == '조치필요':
                        req = row['요청사항'] if row['요청사항'] else ""
                        c2.error(f"🚨 후속조치 요청 ({row['조치부서']}): {req}")
        else: st.info("데이터 없음")

    # [Tab 2] 고객 상담
    with t2:
        st.markdown("##### **고객 검색**")
        df_c = get_data('고객정보')
        all_tags = set()
        if not df_c.empty and '태그' in df_c.columns:
            for t in df_c['태그'].dropna():
                for sub in t.split(','):
                    if sub.strip(): all_tags.add(sub.strip())

        c_a, c_b = st.columns([2,1])
        q = c_a.text_input("이름/연락처/고객번호", label_visibility="collapsed")
        sel_tags = c_b.multiselect("태그", list(all_tags), label_visibility="collapsed")

        target = None
        if not df_c.empty and (q or sel_tags):
            mask = pd.Series([True]*len(df_c))
            if q: mask &= (df_c['이름'].str.contains(q)|df_c['연락처'].str.contains(q)|df_c['고객번호'].str.contains(q))
            if sel_tags: mask &= df_c['태그'].apply(lambda x: any(t in str(x) for t in sel_tags))
            res = df_c[mask]
            
            if not res.empty:
                s = st.selectbox("검색 결과", [f"{r['이름']} (ID: {r['고객번호']} / {r['연락처']})" for i,r in res.iterrows()], label_visibility="collapsed")
                sel_id = s.split('ID: ')[1].split(' /')[0]
                target = res[res['고객번호'] == sel_id].iloc[0]
            else: st.warning("결과 없음")

        if target is not None:
            if st.session_state.get('last_viewed') != target['고객번호']:
                add_audit_log(st.session_state['user_name'], "조회", f"{target['이름']}({target['고객번호']}) 조회")
                st.session_state['last_viewed'] = target['고객번호']

            st.divider()
            with st.container(border=True):
                c1, c2 = st.columns([2,1])
                m_num = str(target.get('조합원번호', ''))
                if "-01-" in m_num:
                    member_badge = "🏅 조합원"
                    badge_color = "#e3f2fd"
                elif "-02-" in m_num:
                    member_badge = "🥈 준조합원"
                    badge_color = "#f3e5f5"
                else:
                    member_badge = "👤 일반고객"
                    badge_color = "#eeeeee"
                
                c1.markdown(f"### **{target['이름']}** <span style='font-size:0.6em; background:{badge_color}; padding:3px 6px; border-radius:5px;'>{member_badge}</span>", unsafe_allow_html=True)
                c1.caption(f"🆔 고객번호: **{target['고객번호']}**")
                c1.caption(f"🎂 {target.get('생년월일','-')} | 📞 {target['연락처']}")
                c1.caption(f"🏠 {target['주소']}")
                if target.get('태그'): c1.markdown(f"🏷️ `{target['태그']}`")
                
                if "-01-" in m_num or "-02-" in m_num:
                    c2.metric("출자금", f"{target['출자금']}")
                    c2.caption(f"조합원No: {m_num}")
                else:
                    c2.info("조합원 번호가 없습니다.")
                
                with st.expander("정보 수정"):
                    nj = st.text_input("직업", value=target.get('직업_사업장',''))
                    nf = st.text_input("가족", value=target.get('가족관계',''))
                    nr = st.text_input("지인", value=target.get('지인관계',''))
                    nb = st.text_input("생년월일", value=target.get('생년월일',''))
                    if st.button("수정 저장", use_container_width=True):
                        update_info_cell(target['고객번호'], '직업_사업장', nj, st.session_state['user_name'])
                        update_info_cell(target['고객번호'], '가족관계', nf, st.session_state['user_name'])
                        update_info_cell(target['고객번호'], '지인관계', nr, st.session_state['user_name'])
                        update_info_cell(target['고객번호'], '생년월일', nb, st.session_state['user_name'])
                        st.toast("저장됨")
                        time.sleep(1)
                        st.rerun()

            df_fin = get_data('금융이력')
            if not df_fin.empty:
                if '고객번호' in df_fin.columns:
                    u_fin = df_fin[df_fin['고객번호'] == target['고객번호']].copy()
                    if not u_fin.empty:
                        st.markdown("#### 📊 금융 자산 현황")
                        u_fin['여신금액'] = pd.to_numeric(u_fin['여신금액'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                        u_fin['수신금액'] = pd.to_numeric(u_fin['수신금액'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                        chart_data = u_fin.melt('기준년월', value_vars=['여신금액', '수신금액'], var_name='종류', value_name='금액')
                        chart = alt.Chart(chart_data).mark_line(point=True).encode(
                            x='기준년월', y='금액', color='종류', tooltip=['기준년월', '금액']
                        ).interactive()
                        st.altair_chart(chart, use_container_width=True)
                    else: st.caption("금융 거래 내역 없음")
                else: st.error("금융이력 시트 오류 (고객번호 열 확인)")

            st.markdown("#### 💬 상담 작성")
            kst = pytz.timezone('Asia/Seoul')
            d_date = st.date_input("날짜", datetime.now(kst))
            raw_txt = st.text_area("내용", height=100)
            needs_act = st.checkbox("🚨 후속 조치 필요")
            dept, req_note = "-", ""
            if needs_act:
                c_x, c_y = st.columns([1,2])
                dept = c_x.selectbox("부서", ["사업과", "지도과", "유통과", "금융과"])
                req_note = c_y.text_input("요청사항")

            if st.button("💾 저장하기", type="primary", use_container_width=True):
                if raw_txt:
                    status = "조치필요" if needs_act else "완료"
                    polished, summary, new_tags = raw_txt, "", ""
                    
                    if ai_available:
                        with st.spinner("AI 분석 중..."):
                            try:
                                p = f"역할:비서. 내용:{raw_txt}. 1.정제(격식), 2.요약(한줄), 3.태그(3개)"
                                resp = model.generate_content(p).text
                                for l in resp.split('\n'):
                                    if l.startswith("정제:"): polished = l.replace("정제:","").strip()
                                    elif l.startswith("요약:"): summary = l.replace("요약:","").strip()
                                    elif l.startswith("태그:"): new_tags = l.replace("태그:","").strip()
                            except Exception as e:
                                # 🚨 AI 분석 실패 시 에러 메시지 출력
                                st.error(f"AI 분석 실패: {e}")
                                st.caption("원본 내용으로 저장합니다.")
                                time.sleep(2)
                    
                    # 저장 함수 실행
                    save_log(d_date, st.session_state['user_name'], target['고객번호'], target['이름'], target['연락처'], 
                             raw_txt, polished, summary, new_tags, dept, status, req_note)
                    st.success("저장 완료!")
                    time.sleep(1)
                    st.rerun()

            st.markdown("#### 📜 이력")
            df_log = get_data('상담이력')
            if not df_log.empty:
                logs = df_log[df_log['고객번호'] == target['고객번호']].iloc[::-1]
                if not logs.empty:
                    with st.container(height=350):
                        for _, r in logs.iterrows():
                            with st.container(border=True):
                                st.caption(f"{r['날짜']} | {r['작성자']}")
                                show = r['정제된내용'] if r['정제된내용'] else r['원본내용']
                                if r['AI요약']: st.markdown(f"**💡 {r['AI요약']}**")
                                st.write(show)
                                if r.get('조치결과'): st.success(f"✅ {r['조치결과']}")
                                elif r['조치상태'] == '조치필요': st.error(f"⏳ 대기중 ({r['조치부서']}): {r['요청사항']}")

    # [Tab 3] 업무 협조
    with t3:
        st.subheader("🚨 후속 조치 대기")
        df_all = get_data('상담이력')
        if not df_all.empty:
            pending = df_all[df_all['조치상태'] == '조치필요']
            if pending.empty:
                st.success("업무 없음")
            else:
                depts = pending['조치부서'].unique()
                for d in depts:
                    tasks = pending[pending['조치부서'] == d]
                    with st.expander(f"📂 {d} ({len(tasks)}건)", expanded=True):
                        for i, r in tasks.iterrows():
                            with st.container(border=True):
                                c1, c2 = st.columns([3, 1])
                                c1.markdown(f"**[{r['고객명']}]** {r['요청사항']}")
                                c1.caption(f"ID: {r['고객번호']} | 요청자: {r['작성자']} ({r['날짜']})")
                                with c1.expander("상담 내용"):
                                    st.write(r['정제된내용'])
                                ans = c2.text_input("결과", key=f"a_{i}")
                                if c2.button("완료", key=f"b_{i}", use_container_width=True):
                                    if ans:
                                        ok = complete_action_logic(r['날짜'], r['고객번호'], ans, st.session_state['user_name'])
                                        if ok:
                                            st.toast("완료")
                                            time.sleep(1)
                                            st.rerun()
                                    else: st.warning("내용 입력")
        else: st.info("데이터 없음")

    # ------------------------------------------------
    # [하단 영역] 로그아웃 버튼 (맨 아래 고정)
    # ------------------------------------------------
    st.divider()
    # [요청 2] 로그아웃 버튼 맨 아래로 배치
    if st.button("🚪 로그아웃", type="secondary", use_container_width=True):
        add_audit_log(st.session_state['user_name'], "로그아웃", "종료")
        st.session_state['logged_in'] = False
        st.rerun()





