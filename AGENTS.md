# LoL Pacemaker — 설계서 (AGENTS.md)

## 0. 프로젝트 코어 헌장 (절대 변경 금지 vs 지속 개발)
모든 AI 세션과 작업자는 코드를 수정하기 전에 아래 경계를 반드시 따른다.

### A. 절대 고치지 말 것 (Immutable Core — 사용자 명시 승인 없이 변경 금지)
1. **브랜드 & 시그니처 포인트 컬러**:
   - 브랜드명은 `PACE MAKER` (`LoL Pacemaker`), **시그니처 포인트 컬러는 오렌지(`#FF5A1F` 다크 / `#EA4C14` 라이트)**로 고정한다.
   - 데스크톱 HUD(`src/overlay.py`)와 웹 포털(`docs/index.html`, `web/index.html`)의 포인트 컬러를 시안·파랑·보라 등 다른 색으로 임의 변경하지 않는다.
2. **웹 포털 구조 (AI 슬롭 랜딩 금지)**:
   - OP.GG · Blitz.gg · DAK.GG 스타일의 **2단 앱 셸 헤더(상단 다크 바 + 하단 탭/인라인 소환사 검색) + 첫 화면 2단 워크스페이스(좌측 실제 인게임 화면 / 우측 다운로드·스펙 레일) + 리전 필터 전적 검색** 구조를 유지한다.
   - 가짜 HUD 목업 카드, 거대한 마케팅 히어로, 불필요한 장식(예: `01 /` 번호 장식, 오프셋 그림자)으로 되돌리지 않는다.
   - 라이트/다크 토글(`pacemaker_theme`)과 `docs/index.html == web/index.html` 동기화를 유지한다.
3. **Riot 운영방침 준수 & 전 모드·서버비 0원·무광고**:
   - Riot 공식 로컬 API(`127.0.0.1:2999`) 공개 점수판·이벤트만 **읽기 전용**으로 사용한다(메모리 인젝션, OCR, 비공개 상대 와드/스펠 추적 절대 금지).
   - 랭크·일반·칼바람(ARAM)·연습모드 등 **모든 경기 모드**에서 정상 동작해야 한다.
   - 상시 유료 서버, 광고, 추적 스크립트 0개 원칙을 유지한다(GitHub Pages + Cloudflare Workers 무료 한도).
4. **보안·프라이버시·품질 게이트**:
   - 웹 번역 주입은 `textContent` 전용(`innerHTML` 금지) + 엄격한 CSP 유지.
   - 5개 언어(`en`, `ko`, `ja`, `es`, `pt`) 로케일 키·치환자 100% 대칭 및 `pytest` 100% 통과.
   - `RIOT_API_KEY`, 토큰, 개인 이메일을 코드·로그·커밋에 남기지 않는다(Git 커밋 이메일 `140034333+wpalswpa@users.noreply.github.com` 유지).
   - GitHub Actions Artifact 저장소를 쓰지 않는다(릴리스는 `gh release upload` 직접 업로드, 웹은 `main:/docs`).

### B. 지속적인 개발·개선이 필요한 영역 (Active Roadmap)
1. **Windows / 실제 롤 인게임 실측 및 UX 고도화**:
   - `LoLPacemaker-Setup.exe` 원클릭 설치, Windows 레지스트리 자동 실행, `--background` 경기 시작 자동 팝업·종료 자동 숨김, `F8` 클릭 통과·`F9` 숨김을 실제 롤(테두리 없음) 위에서 검증하고 다듬는다.
2. **ML 학습 데이터 확장 & 주기적 재학습**:
   - 현재 KR 90경기 번들 모델(`assets/model_extended.onnx`, `assets/distribution_stats.json`)을 최신 패치별 수천 경기 규모로 확장 재학습해 승률 예측 정확도와 티어별 정규분포 기준선을 지속 고도화한다.
3. **실시간 전략 코칭(Shotcaller) 정교화**:
   - 패치 메타 변화에 맞춰 협곡(오브젝트·강타·귀환·캐리 보호) 및 칼바람(ARAM) 오더의 정확도와 실효성을 높이고 오탐을 줄인다.
4. **5개 언어별 실플레이 데모 영상 제작·연동**:
   - 자막 전 원본 영상 확보 후 언어별 맞춤형 데모 영상을 제작해 웹 포털 언어 전환과 연동한다.
5. **전적 검색 패널 정보 밀도 확장**:
   - Cloudflare Workers 무료 한도 내에서 최근 승률 요약, 게임 모드, 챔피언 아이콘, KDA 상세 등 OP.GG/DAK.GG 수준의 전적 가독성을 확장한다.

## 1. 목적과 최종 목표
- **목적**: Blitz · Porofessor · U.GG 같은 기존 LoL 보조 앱과 **병행 사용(Companion)**하면서, 게임 도중 2초마다 **지금 당장 해야 할 실시간 운영 전략(Shotcall)과 승률 변화**를 지연 없이 띄워 주는 **서버비 0원·무광고·다국어 AI 코치**를 만든다.
- **타겟 사용자**: Reddit(`r/summonerschool`, `r/leagueoflegends`) 등 글로벌 LoL 플레이어 및 국내 플레이어.
- **비목표(이번 v1 범위 제외)**:
  - 게임 전 자동 룬/밴픽, 게임 후 전적 검색 사이트(기존 앱과 겹치며 `RIOT_API_KEY` 서버가 필요하므로 제외).
  - 메모리 변조(DLL 인젝션), 화면 캡처(OCR), 비공개 정보(상대 와드/쿨타임/위치) 읽기.
  - 상시 운영 웹 서버(FastAPI, PostgreSQL, Docker, Lightsail 제거 → **서버 비용 0원**).

## 2. 핵심 아키텍처 (Zero-Server Local Engine)
```
LoL Client (127.0.0.1:2999/liveclientdata/allgamedata)
   │  2s polling (read-only official public scoreboard & events)
   ▼
lol-pacemaker (Local PC App — Server Cost $0)
   ├── src/predictor.py      : Local ONNX Runtime (<1ms): updated model → 18-feature → 8-feature fallback (§10.5)
   ├── src/strategy_core.py  : Real-time priority shotcaller (language-neutral keys + params)
   ├── src/i18n.py           : Locale loader (auto-detect OS/client locale or user override)
   ├── locales/*.json        : Country/language dictionaries (en.json, ko.json, extensible)
   ├── src/engine.py         : 2s 2999 poller → snapshot → localized result (UI-independent)
   ├── src/updater.py        : HTTPS + SHA-256 verified model updates (§11)
   └── src/overlay.py · src/app.py : Tk transparent HUD / browser HUD (§12)
```

## 3. 다국어(i18n) 설계 계약
- **로직과 번역의 완전 분리**:
  - `strategy_core.py`는 절대 특정 언어 문자열을 하드코딩하지 않는다.
  - 모든 전략 카드/오더/경고는 `LocalizedText(key, params)` 형태로 반환한다.
  - `i18n.py`가 `locales/<lang>.json`을 로드해 치환하며, 키가 누락된 경우 기본 폴백 언어(`en`)로 안전하게 렌더링한다.
- **지원 언어 우선순위**:
  1. 사용자 명시 선택 (`--lang en|ko|...`)
  2. OS/클라이언트 로케일 자동 감지 (`ko_*` → `ko`, 그 외 → `en`)
- **테스트 게이트**: `locales/en.json`과 모든 추가 로케일 파일의 키 집합이 100% 일치해야 한다 (`tests/test_i18n.py`).

## 4. 실시간 전략 고도화(Shotcaller) 기준
기존 앱과 차별화되는 **실시간 운영 오더**를 우선순위 점수 기반으로 생성하며, Riot 2999 `gameData.gameMode`를 자동 감지해 **랭크·일반·칼바람 나락(ARAM)·연습/AI 등 모든 모드**에서 운영방침 위반 없이 유저 실력 향상용 코칭을 제공한다.
1. **수적 우위/열세 & 부활 타이머 윈도우**: 생존 인원 차이(예: 5v3)와 상대 최대 부활 남은 시간(`enemy_max_respawn`)을 결합해 지금 밀 수 있는 안전 시간 창(협곡은 포탑/바론, 칼바람은 포탑)을 안내한다.
2. **정글 강타(Smite) 상태 판별 (협곡 전용)**: 2999 `allPlayers[].summonerSpells`에서 강타 보유 정글러를 식별해, **상대 정글러 사망 시** 스틸 위험 없는 오브젝트 즉시 트라이(최우선), **아군 정글러만 사망 시** 50/50 강타 싸움 금지 경고를 띄운다.
3. **오브젝트 타이밍 & 공허 유충 (협곡 전용)**: 드래곤 영혼 직전(3스택), 바론/장로, 초반 공허 유충(`HordeKill`)·전령 타이밍을 구분하고 모순 콜(압박 vs 수성 동시 출력)을 자동 제거한다.
4. **아군 캐리 중심 합류 & 라인전 판단**: 아군 중 최다 킬/성장 캐리 포지션을 식별해 중후반 한타 시 캐리 보호/합류 오더를 제시한다.
5. **귀환·리셋 골드 타이밍 & 카운터 대응**: 협곡에서는 보유 골드(`currentGold >= 1300`) 기준 귀환 타이밍을, **칼바람(ARAM)에서는 귀환 불가 규칙에 맞춰 과다 골드(`>= 2600G`) 시 교전 후 정비·구매 타이밍**을 안내하며, 모든 모드에서 상대 치유·탱커 카운터 하위템을 제시한다. (보유 골드는 승률 계산 피처에 넣지 않는다.)
6. **ONNX 모델 + 실시간 판세 보정**: 18피처 모델이 있으면 시점(`game_time_min`)을 입력으로 넣어 전 구간에 쓰고(§10.5), 없으면 8~12분 구간만 `model_sklearn.onnx`(585B) 승률을 그대로 반영하며, 비협곡 모드(ARAM)나 중후반은 실시간 운영 오더 위주로 안내한다.

## 5. 병행 사용(Companion) HUD 원칙
- 기존 오버레이(Porofessor 등)를 가리지 않도록 **소형 컴팩트 카드(최대 3개 오더)**만 표시한다.
- 드래그 이동, `F8` 클릭 통과, `F9` 숨김/표시, 투명도 조절, 언어 전환을 지원한다(구현 상세 §12).

## 6. 성공 및 통과 기준
- 외부 서버·DB·API 키 없이 로컬 단독 실행 및 `pytest` 100% 통과.
- 5개 로케일(`en/ko/ja/es/pt`) 키·치환자 100% 대칭 및 실제 게임 JSON 픽스처 기반 전략·브리핑 출력 검증.

## 7. 차기 ML 학습 파이프라인 설계 (티어별 · 다중 시간대 · 패턴 기반 코칭)
로컬 LLM 없이 **데이터 기반 패턴 분석**으로 실전 코칭을 제공하기 위해, 기존 8개 피처(10분 단일)를 넘어 학습-서빙 일치가 보장되는 정교한 파이프라인을 구축한다.

1. **데이터 원천 (티어별 · 패치별 수집)**:
   - Riot `LEAGUE-V4` / `LEAGUE-EXP-V4`로 티어 구간(`low: Iron~Gold`, `mid: Plat~Emerald`, `high: Diamond+`)별 소환사 샘플링.
   - `MATCH-V5 Timeline`에서 한 경기당 다중 시점(`5·10·15·20·25·30분`) 스냅샷을 추출해 최신 패치 메타를 반영한다.
   - *현실적 규모 기준*: Rate Limit을 고려해 패치당 티어별 수천~수만 경기(수만~수십만 분 단위 스냅샷)를 슬라이딩 윈도우로 수집·누적한다.
2. **양질의 데이터 전처리 & 정제 기준**:
   - 리메이크/조기 항복(10분 미만 정상 종료 외), 장시간 AFK 포함 경기, 비정상 패치 버전, 결측 타임라인 자동 제외.
   - 2999 Live Client API와 `MATCH-V5 Timeline` **양쪽에서 정의가 100% 동일한 피처만** 채택한다(예: 총골드 vs 보유 아이템 골드 혼용 금지).
3. **정교한 피처 확장 (기존 8개 → 18+개 후보군)**:
   - 시간 문맥: `game_time_min`
   - 기존 8개 공개 지표: `first_blood`, `kills`, `assists`, `cs`, `avg_level`, `dragons`, `heralds`, `towers`
   - 확장 공개 지표: `voidgrubs` (공허 유충), `barons`, `elder_dragons`, `inhibitors`, `item_gold_diff` (양 팀 공개 아이템 가치 합), `recent_3m_kill_diff` (최근 3분 교전 모멘텀), `lane_cs_diff` (탑·미드·원딜 라인 격차), `alive_diff` (교전 직후 생존 수 차이)
4. **패턴 기반 모델링 & 서버비 0원 자동 배포**:
   - 로지스틱 회귀 외에 트리 기반 경량 모델(LightGBM/XGBoost)을 비교·검증하고, **피처 기여도(SHAP / 계수)를 추출해 "지금 승률을 깎고 있는 핵심 원인"을 실시간 코칭 패턴으로 연결**한다.
   - 모든 모델은 ONNX로 변환(Parity 오차 ≤ 0.0001)해 GitHub Actions로 주기적 재학습 → GitHub Releases에 배포하고, 사용자 앱은 시작 시 무료로 내려받아 로컬 실행한다.

## 8. 정규분포 기반 상위 % 진단 & 스마트 브리핑
- **티어·시간대별 정규분포 기준선**: 학습 시 각 티어/시간대별 핵심 지표(`CS/분`, `레벨`, `킬 관여`)의 `mean`·`std`를 함께 저장한다.
- **실시간 상위 % 계산 (SciPy 없이 `math.erf` 0ms 연산)**: 현재 플레이어 수치를 `z = (x - mean) / std`로 환산해 **"현재 티어 평균 대비 상위 몇 % 플레이 중인지"**를 실시간 출력한다.
- **스마트 브리핑 결합**: 단순 나열이 아니라 `(상위 % 약점/강점) + (승률 하락 핵심 피처) + (실시간 강타/오브젝트 창)`을 묶어 구체적인 행동 지침을 브리핑한다.

## 9. 글로벌 도메인 웹 랜딩 & 언어 설정 (`docs/index.html` · `web/index.html`)
- **호스팅**: 서버비 0원 정적 배포(GitHub Pages `main:/docs` → `https://wpalswpa.github.io/lol-pacemaker/`, 추후 개인 도메인 CNAME 연결 가능). Actions Artifact 저장소를 쓰지 않는다.
- **지원 언어 (5개 권역 기본)**: `en` (Global/NA/EUW), `ko` (KR), `ja` (JP), `es` (LAN/LAS/EUW), `pt` (BR).
- **자동 감지 및 전환**: `?lang=` URL 파라미터 → `localStorage` → `navigator.language` 순으로 자동 선택하고, 상단 언어 선택기에서 새로고침 없이 즉시 전환한다.
- **보안 원칙**: 모든 번역 텍스트는 `textContent`로만 주입(DOM XSS 차단), 외부 광고·추적 스크립트 0개.
- **테마(라이트/다크)**: 색은 CSS 변수 토큰으로만 정의한다. 기본은 OS 설정(`prefers-color-scheme`)을 따르고, 상단 토글 버튼이 `data-theme="light|dark"`를 `<html>`에 찍고 `localStorage(pacemaker_theme)`에 저장한다(저장값 > OS 설정). `<head>`의 인라인 스크립트가 첫 페인트 전에 적용해 깜빡임이 없다. 토글의 접근성 이름(`aria-label`)은 5개 언어로 둔다. 저장소 접근은 try/catch.
- **시각 방향(2026-11-26 개편 — OP.GG · Blitz.gg · DAK.GG 포털 구조)**:
  - 거대한 AI 마케팅 히어로 대신 **실제 LoL 전적/컴패니언 포털 구조**를 따른다.
  - **2단 앱 셸 헤더**: ① 상단 다크 유틸리티 바(브랜드 워드마크, 게임/버전 배지, 5개 언어 선택, 라이트/다크 토글, 주황 다운로드 CTA) + ② 하단 서브 내비게이션 & 인라인 소환사 검색 바(`KR | 소환사#태그`).
  - **2단 워크스페이스(첫 화면)**: 좌측 메인 컬럼(실제 인게임 HUD 스크린샷 패널 + 모드 배지) + 우측 고정 액션 사이드바(Windows 원클릭 `.exe` / macOS 다운로드, 4행 스펙 테이블, Riot 정책 준수 배지 — 광고 0개).
  - **전적 검색 패널**: OP.GG식 리전 필터 버튼 바(`KR·NA·EUW·JP·BR·LAN·LAS·SEA`)와 승(파랑)/패(빨강) 좌측 띠·고정폭 숫자(`tabular-nums`) 매치 행.
  - **색상 체계**: 데스크톱 HUD(`src/overlay.py`)와 통일된 PACE MAKER 시그니처 오렌지(`#FF5A1F` 다크 / `#EA4C14` 라이트) + Blitz/OP.GG 딥 차콜 다크(`#0E1015`/`#161922`) 및 DAK.GG 클린 라이트(`#F2F4F7`/`#FFFFFF`).
  - 문구·보안 규칙(`textContent` 전용, `innerHTML` 금지, 엄격한 CSP, 5개 언어 대칭)은 그대로 유지한다.

## 10. 18피처 학습 파이프라인 확정 사양 (2026-11-26 개정, §7 보강)
### 10.1 피처 계약의 정직한 범위 (§7-2 "정의 100% 동일" 적용)
- ONNX 입력은 18칸(`ml/features.py::EXTENDED_FEATURES`)을 유지한다.
- 그중 `item_gold_diff`(타임라인에서 아이템 가치 복원에 가격표·되돌리기 재생이 필요), `alive_diff`(타임라인에는 사망 타이머가 없어 2999의 `respawnTimer`와 정의가 다름)는 **학습·서빙 양쪽에서 동일하게 0으로 마스킹**한다(`MASKED_FEATURES`). 가짜 값으로 채우지 않고, 브리핑에도 쓰지 않는다. 유효 입력은 16칸이다. 생존 수·아이템은 §4의 실시간 오더(`strategy_core`)에서만 쓴다.
- 서빙 쪽 피처 생성은 `ScoreboardState.to_feature_dict()` 한 곳이다(학습 `extract_timeline_snapshots`와 키·부호·의미가 같다: 모두 `아군 팀 − 상대 팀`을 블루 기준으로 계산).

### 10.2 수집 CLI (`python -m ml.collector`)
- 환경변수 `RIOT_API_KEY`만 읽는다(인자·로그·출력·산출물에 키를 쓰지 않는다).
- 흐름: `LEAGUE-V4` 엔트리(Iron~Diamond는 tier/division 페이지, Master+는 `*leagues/by-queue`)에서 티어 구간(`low/mid/high`)별 소환사 표본 → `MATCH-V5` ids(queue 420) → match + timeline → 정제 → JSONL 한 줄씩 저장.
- 중단 재개: `--checkpoint`(JSON)에 처리한 소환사·경기 id를 저장하고 다시 실행하면 이어서 한다. 속도 제한: 호출 간 최소 간격 + 429 `Retry-After` + 5xx 지수 백오프(sleep 주입 가능).
- 정제: 큐 420 외·10분 미만·참가자 10명 아님·`gameEndedInEarlySurrender`·타임라인 결측 제외. `--patch 16.x`로 패치 필터. 한 경기에서 팀 단위 행(`kind: snapshot`)과 개인 단위 행(`kind: player`: `cs_per_min`·`level`·`kill_participation`)을 함께 낸다.
- 같은 경기의 행은 `match_id`를 가진다(학습 분할 기준).

### 10.3 학습 (`python -m ml.train`)
- 분할은 **경기 단위**다(같은 경기의 10·15·20·25분 행이 학습·평가에 섞이지 않게 `match_id` 해시로 나눈다).
- 후보: 로지스틱 회귀(표준화 포함)와 경량 트리(`GradientBoostingClassifier`, 소형). 평가 log-loss가 낮고 **ONNX Parity ≤ 1e-4**를 통과한 모델만 채택하며, 비교표를 `metrics_extended.json`에 남긴다. 어느 쪽도 Parity를 못 넘으면 학습을 실패로 처리한다.
- 피처 기여도는 평가 세트 permutation importance(모델 종류 무관)로 `top_patterns`에 저장한다.
- 산출물: `model_extended.onnx`, `distribution_stats.json`, `metrics_extended.json`.

### 10.4 `distribution_stats.json` 스키마 (§8 구체화)
```
{"schema": 1, "min_n": 30,
 "tiers": {"low|mid|high": {"<분: 10|15|20|25>": {"<ALL|TOP|JUNGLE|MIDDLE|BOTTOM|UTILITY>":
     {"cs_per_min": {"mean","std","n"}, "level": {...}, "kill_participation": {...}}}}}}
```
- 표본 수 `n < min_n`인 칸은 저장하지 않는다. 상위 %는 `math.erf` 정규 근사(§8)이며, 실제 분포가 정규가 아니므로 화면에는 "근사"로 표기한다.

### 10.5 서빙 연결과 폴백 순서
- 모델 해석 순서: ① 업데이터가 검증해 둔 최신 모델 폴더 → ② 번들 `assets/model_extended.onnx` → ③ 번들 8피처 `assets/model_sklearn.onnx`. 앞 단계가 없거나 로드·입력 형상 검증에 실패하면 다음 단계로 내려간다.
- 승률 하락 원인(`explain`): ONNX만으로 계산한다. 피처 하나씩 중립값(차이 0)으로 되돌린 승률과의 차이를 한 번의 배치 추론으로 구해, 승률을 가장 깎는 피처 상위를 고른다(마스킹된 피처 제외). 모델 종류에 상관없다.
- 상위 %는 `distribution_stats.json`이 있고 게임 시간이 8분 이상일 때만 개인 지표(`CS/분`·레벨·킬 관여)로 계산한다(가장 가까운 기준 분·내 포지션, 없으면 `ALL`). 통계가 없으면 §8 이전의 팀 격차 방식으로 폴백하되 `rank_mode: legacy`로 표시한다.
- 브리핑 = (상위 % 약점/강점) + (승률 하락 핵심 원인) + (실시간 오더 1순위). 모두 로케일 키 + 파라미터로 만들고 5개 언어 키 대칭 테스트를 건다.
- **현재 번들 상태**: KR 서버 실데이터 90경기(`low`·`mid`·`high` 각 30경기, 총 313개 팀 스냅샷 및 72개 정규분포 셀)로 학습한 `assets/model_extended.onnx`(로지스틱 채택: 평가 LogLoss `0.3969`, Brier `0.1312`, AUC `0.9275`, ONNX 오차 `1.19e-7`)와 `assets/distribution_stats.json`이 번들되어 있어 앱이 기본적으로 ② 18피처 확장 모델 + 티어별 정규분포 상위 % 모드로 동작한다. `assets/model_sklearn.onnx`(8피처)는 ③ 최후 폴백으로 유지한다.

## 11. 무서버 모델 업데이터 (`src/updater.py`)
- 입력: GitHub Releases `latest` JSON → 자산 `model_extended.onnx`, `distribution_stats.json`, `checksums.sha256`(`<sha256>  <파일명>` 형식).
- 규칙: ① HTTPS만 허용(리다이렉트 포함, http 거부) ② 허용 파일명 화이트리스트·크기 상한 ③ `checksums.sha256`에 있는 SHA-256과 다운로드 바이트가 같을 때만 통과 ④ ONNX는 로드·입력 18칸, 통계 JSON은 `tiers` 구조 검증 후 통과 ⑤ 전부 임시 폴더에 받은 뒤 버전 폴더로 옮기고 `current.json` 포인터를 `os.replace`로 한 번에 교체(부분 교체 없음) ⑥ 어떤 실패든 기존 모델을 건드리지 않고 `UpdateResult(status=...)`를 반환하며 앱은 §10.5 폴백으로 계속 동작한다.
- 저장 위치: 사용자 데이터 폴더(`~/.lol-pacemaker/models/`, `LOL_PACEMAKER_HOME`으로 변경 가능). 시작 시 백그라운드 스레드로 확인하고 적용은 다음 실행부터다.
- 한계: 체크섬이 모델과 같은 릴리스에서 오므로 **릴리스 계정이 탈취된 경우는 막지 못한다**(전송 변조·손상·부분 다운로드만 막는다). 서명 검증은 이번 범위 밖이다.

## 12. 컴패니언 HUD와 패키징
- 구조: `src/engine.py`(2999 폴링·평가, UI 무관) → `src/hud_state.py`(설정·창 상태 순수 로직, 테스트 대상) → `src/autostart.py`(OS 로그인 자동 실행 등록·해제) → `src/overlay.py`(Tk 투명 카드 HUD, 얇은 뷰) / `src/app.py`(브라우저용 HUD, 보조). 진입점은 `python -m src.main`.
- HUD: 약 320px 폭·최대 3개 오더 카드, PACE MAKER 다크 오렌지-블랙 테마(`#0E1117` 카드 배경, `#FF5A1F` 테두리·승률·강조 바, `#34D399` 최우선 콜·티어 배지), 투명 무테 최상단 창. 드래그 이동, 투명도 `[` `]` 및 − + 버튼(0.3~1.0), 언어 칩 클릭으로 5개 언어 순환(즉시 반영·저장), **`AUTO` 칩으로 OS 부팅 자동 실행 1클릭 ON/OFF**, **F8 클릭 통과 토글, F9 숨김/표시**. 설정(`x,y,opacity,lang,tier,click_through`)은 사용자 폴더 `settings.json`에 저장하며 값은 범위 보정한다.
- 자동 실행 & 게임 자동 감지 팝업(`--background`): `src/autostart.py`가 Windows(`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`)와 macOS(`~/Library/LaunchAgents/com.lolpacemaker.app.plist`)에 `--background` 인자로 자동 실행을 등록한다. `--background`로 켜지면 평소에는 창을 숨긴 채 대기하다가 롤 경기(`127.0.0.1:2999`)가 시작되면 HUD가 자동으로 표시되고, 경기가 끝나면 자동으로 숨긴다(언제든 `F9`로 수동 전환 가능).
- 전역 단축키는 `pynput`(선택 의존성)으로 등록하고, 없거나 권한이 없으면 창 포커스 키 바인딩으로 폴백하며 HUD에 그 사실을 표시한다. 클릭 통과는 Windows는 `WS_EX_TRANSPARENT`(ctypes), macOS는 `NSWindow.ignoresMouseEvents`(`pyobjc-framework-Cocoa`, macOS에서만 설치되는 의존성)로 구현한다. 호출이 실패하면 HUD 하단에 표시한다. 개발 Mac에서 창 표시·언어/투명도/숨김 동작과 macOS 클릭 통과 호출 성공까지 확인했고, **Windows 동작과 실제 게임 위 동작은 미검증**이다.
- 패키징: `scripts/build.py`(PyInstaller `--windowed`, assets·locales 포함; Windows는 onefile `.exe`, macOS는 `.app` 번들을 zip — macOS GUI onefile은 PyInstaller가 비권장), `scripts/installer.iss`(Windows Inno Setup 원클릭 설치 파일 `LoLPacemaker-Setup.exe` — 관리자 권한 없이 사용자 폴더 설치, 바탕화면 바로가기 및 `--background` 자동 실행 기본 포함), `scripts/make_checksums.py`(`checksums.sha256` 생성, 업데이터와 같은 형식), `.github/workflows/release.yml`(태그 `v*` 푸시 때만: 테스트 통과 → Windows·macOS 클린 빌드 → Windows 인스톨러 생성 → `checksums.sha256` 생성 → **초안(draft)** 릴리스. 시크릿 미사용). macOS 빌드는 개발 Mac에서 실제로 만들어 번들 자원 로드와 `/state` 응답까지 확인했고(Windows 빌드·인스톨러 실행은 Actions 빌드 후 Windows 실측 필요), 앱 크기는 약 120MB(zip 43MB)다. 서명(Windows 코드서명·macOS 공증)이 없어 첫 실행 경고가 뜬다.
