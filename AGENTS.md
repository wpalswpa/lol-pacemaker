# LoL Pacemaker — 설계서 (AGENTS.md)

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
   ├── src/predictor.py      : Local ONNX Runtime (<1ms) using assets/model_sklearn.onnx
   ├── src/strategy_core.py  : Real-time priority shotcaller (language-neutral keys + params)
   ├── src/i18n.py           : Locale loader (auto-detect OS/client locale or user override)
   ├── locales/*.json        : Country/language dictionaries (en.json, ko.json, extensible)
   └── src/app.py            : Local HTTP HUD state server (stdlib http.server, no FastAPI)
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
기존 앱과 차별화되는 **실시간 운영 오더**를 우선순위 점수 기반으로 생성한다.
1. **수적 우위/열세 & 부활 타이머 윈도우**: 생존 인원 차이(예: 5v3)와 상대 최대 부활 남은 시간(`enemy_max_respawn`)을 결합해 지금 밀 수 있는 안전 시간 창(포탑/바론)을 안내한다.
2. **정글 강타(Smite) 상태 판별**: 2999 `allPlayers[].summonerSpells`에서 강타 보유 정글러를 식별해, **상대 정글러 사망 시** 스틸 위험 없는 오브젝트 즉시 트라이(최우선), **아군 정글러만 사망 시** 50/50 강타 싸움 금지 경고를 띄운다.
3. **오브젝트 타이밍 & 공허 유충**: 드래곤 영혼 직전(3스택), 바론/장로, 초반 공허 유충(`HordeKill`)·전령 타이밍을 구분하고 모순 콜(압박 vs 수성 동시 출력)을 자동 제거한다.
4. **아군 캐리 중심 합류 & 라인전 판단**: 아군 중 최다 킬/성장 캐리 포지션을 식별해 중후반 한타 시 캐리 보호/합류 오더를 제시한다.
5. **귀환 & 골드 타이밍 / 카운터 대응**: 보유 골드(`currentGold`) 기준 코어템/하위템 구매 가능 시점 안내 및 상대 치유·탱커 비중에 따른 카운터 하위템 우선순위를 제시한다. (보유 골드는 승률 계산 피처에 넣지 않는다.)
6. **10분 ONNX 모델 + 실시간 판세 보정**: 8~12분 구간은 `model_sklearn.onnx`(585B) 승률을 그대로 반영하고, 중후반은 실시간 팀 아이템 골드·생존 수·오브젝트 격차를 반영하며 시간대 한계 안내를 표시한다.

## 5. 병행 사용(Companion) HUD 원칙
- 기존 오버레이(Porofessor 등)를 가리지 않도록 **소형 컴팩트 카드(최대 3개 오더)**만 표시한다.
- 드래그 이동, `F8` 클릭 통과, `F9` 숨김/표시, 투명도 조절을 지원한다.

## 6. 성공 및 통과 기준
- 외부 서버·DB·API 키 없이 로컬 단독 실행 및 `pytest` 100% 통과.
- `en.json`, `ko.json` 번역 키 100% 대칭 및 실제 게임 JSON 픽스처 기반 영문/국문 전략 출력 검증.

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

## 9. 글로벌 도메인 웹 랜딩 & 언어 설정 (`web/index.html`)
- **호스팅**: 서버비 0원 정적 배포(Cloudflare Pages / GitHub Pages) 전용 단일 페이지.
- **지원 언어 (5개 권역 기본)**: `en` (Global/NA/EUW), `ko` (KR), `ja` (JP), `es` (LAN/LAS/EUW), `pt` (BR).
- **자동 감지 및 전환**: `?lang=` URL 파라미터 → `localStorage` → `navigator.language` 순으로 자동 선택하고, 상단 언어 선택기에서 새로고침 없이 즉시 전환한다.
- **보안 원칙**: 모든 번역 텍스트는 `textContent`로만 주입(DOM XSS 차단), 외부 광고·추적 스크립트 0개.
