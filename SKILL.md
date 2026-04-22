# SKILL.md — Claude 작업 전 필수 확인 규칙

> 모든 작업 시작 전 이 문서를 확인하고 아래 규칙을 반드시 준수한다.

---

## 규칙 1. cp + Edit 패턴 (Extract Class)

클래스/모듈을 파일로 분리(Extract Class)할 때:

1. **`cp` 명령으로 원본 파일을 새 파일명으로 복사**한다.
2. 복사된 파일에서 **불필요한 부분만 `Edit`으로 삭제**한다.
3. **`Write`로 전체를 재생성하지 않는다.**
4. 삭제 후 **불필요한 내용이 실제로 모두 제거되었는지 점검**한다.
   - 원본에서 추출 대상이 아닌 클래스·함수·import가 새 파일에 남아있지 않은지 확인
   - 새 파일에 필요한 import가 누락되지 않았는지 확인

```bash
# ✅ 올바른 패턴
cp brokers/korea_investment/foo.py brokers/korea_investment/bar.py
# 이후 Edit으로 bar.py에서 불필요한 코드만 제거
# 제거 후: bar.py에 SomeClass 관련 코드만 남았는지 점검
```

**Why:** Write 재생성은 기존 코드를 통째로 날릴 위험이 있고, 검토하기 어렵다.

---

## 규칙 2. Edit 배치 — old_string 길이 제한

`Edit` 호출 시 `old_string`이 **40줄을 초과하면 Edit을 분리**한다.

```python
# ❌ 금지: old_string이 지나치게 긴 단일 Edit
Edit(file, old_string="...80줄짜리 블록...", new_string="...")

# ✅ 올바른 패턴: 논리 단위로 분리
Edit(file, old_string="...20줄 블록 A...", new_string="...")
Edit(file, old_string="...20줄 블록 B...", new_string="...")
```

**Why:** old_string이 길수록 매칭 실패 확률이 높아지고, 오류 시 디버깅이 어렵다.

---

## 규칙 3. 검증 패스 제거 (Edit 후 재Read 금지)

`Edit` 도구 성공 응답 후 **결과 확인을 위해 파일을 다시 `Read`하지 않는다.**

```python
# ❌ 금지
Edit(file, old, new)  →  Read(file)  # 확인용 재읽기 금지

# ✅ 허용
Edit(file, old, new)  # 성공 응답을 신뢰하고 다음 작업으로 진행
```

**Why:** Edit 도구는 실패 시 에러를 반환한다. 성공 응답 이후 재확인은 불필요한 컨텍스트 소모다.

---

## 규칙 4. Targeted Read (부분 읽기)

파일 전체를 읽지 않는다. **변경 지점 ±15줄만 Read**한다.

```python
# 100번째 줄 수정 시
Read(file_path, offset=85, limit=30)

# ❌ 금지
Read(file_path)  # 전체 읽기
```

**점검**: ±15줄 범위가 변경 대상 함수/블록 전체를 포함하는지 확인한다.
함수 시그니처나 블록 경계가 범위 밖이면 범위를 확장한다 (예: `limit=50`).

**Why:** 대형 파일 전체 읽기는 컨텍스트를 낭비한다. 변경에 필요한 범위만 읽는다.

---

## 규칙 5. Changes.md 선작성 (실행 전 압축 문서)

복잡한 작업(리팩토링, 다수 파일 수정 등) 시:

1. **실행 단계 전에 `Changes.md`를 먼저 작성**한다.
2. 실행 중에는 전체 파일을 다시 읽지 않고 **`Changes.md`를 참조**한다.

**필수 3개 섹션**:

```markdown
## Changes Specification
<!-- 변경할 파일, 라인 범위, 변경 내용을 열거 -->
- foo.py L45-80: SomeClass 정의 제거
- bar.py: 신규 생성 (SomeClass 이동)
- main.py L12: import 경로 수정

## Test Update Specification
<!-- 테스트 파일 변경 사항 (신규/수정/삭제) -->
- test_foo.py: import 경로 수정
- test_bar.py: 신규 작성 (SomeClass 단위 테스트)

## Execution Notes
<!-- 실행 순서, 주의사항, 의존 관계 -->
1. bar.py 먼저 생성 → foo.py 수정 → main.py import 갱신
2. bar.py에 누락 import 없는지 점검 후 진행
```

**Why:** 실행 중 원본 파일을 반복 읽는 것은 cache_read 비용을 유발한다. 미리 압축한 문서를 읽으면 비용이 절감된다.

---

## 규칙 6. JSONL 로그 시간 분석

JSONL 로그에서 성능을 분석할 때 턴 유형에 따라 시간 구분을 다르게 해석한다.

| 구간 | 직전 턴 유형 | 의미 |
|------|-------------|------|
| 타임스탬프 차이 | `assistant` 턴 직전 | **LLM 대기 시간** (모델 응답 생성) |
| 타임스탬프 차이 | `user` 턴 직전 | **Tool 실행 시간** (도구 호출·결과 반환) |

```python
# 분석 예시
for i, entry in enumerate(log):
    if i == 0: continue
    delta = entry.timestamp - log[i-1].timestamp
    if entry.role == "assistant":
        print(f"LLM 대기: {delta}s")   # 이전 user(tool_result) → assistant
    elif entry.role == "user":
        print(f"Tool 실행: {delta}s")  # 이전 assistant(tool_use) → user
```

**Why:** LLM 지연과 Tool 지연을 혼동하면 병목 분석이 틀린다. 역할 기준으로 명확히 구분해야 한다.

---

## 규칙 7. 파일 읽기 실패 시 재시도 순서

PowerShell로 파일을 읽을 때 초기에 접근 오류가 나면 아래 순서로 재시도한다.

1. **먼저 `-NoProfile`로 재시도**한다.
   - `Microsoft.PowerShell_profile.ps1` 로딩 실패 때문에 첫 명령이 막히는 경우가 있다.
2. `Get-Content`가 **`Access is denied` / `UnauthorizedAccessException`** 을 반환하면
   **샌드박스 권한 문제 가능성**을 우선 의심한다.
3. 이 경우 중요한 작업 파일이면 **즉시 권한 상승(`require_escalated`)으로 같은 읽기 명령을 재시도**한다.
4. 읽기 성공 후에는 그 결과를 기준으로 계속 작업하고, 같은 이유로 다른 파일도 막히면
   동일 패턴(`-NoProfile` → 필요 시 escalated read)으로 처리한다.

```powershell
# 1차: 프로필 없이 읽기
powershell -NoProfile -Command "Get-Content -Raw -Encoding UTF8 services\foo.py"

# 2차: Access denied면 동일 명령을 escalated로 재시도
```

**Why:** 초기 실패 원인이 파일 자체 문제가 아니라 PowerShell 프로필 또는 샌드박스 권한인 경우가 많다.  
처음부터 재시도 순서를 정해 두면 불필요한 우회 시도와 시간 낭비를 줄일 수 있다.

---

## 체크리스트

작업 시작 전 다음을 확인한다:

- [ ] Extract Class 시 `Write` 대신 `cp + Edit` 패턴을 사용하는가?
- [ ] cp 후 불필요한 내용이 실제로 모두 제거되었는가?
- [ ] Edit `old_string`이 40줄 이하인가? (초과 시 분리)
- [ ] `Edit` 성공 후 재확인용 `Read`를 하지 않는가?
- [ ] `Read` 시 변경 지점 ±15줄만 읽는가? (블록 경계 포함 여부 확인)
- [ ] 복잡한 작업 전 `Changes.md` 3개 섹션을 모두 작성했는가?
  - [ ] Changes Specification
  - [ ] Test Update Specification
  - [ ] Execution Notes
- [ ] JSONL 로그 분석 시 assistant/user 턴 기준으로 LLM 대기·Tool 실행을 구분하는가?
- [ ] 파일 읽기 실패 시 `-NoProfile`로 먼저 재시도했고, 필요하면 escalated read를 사용했는가?
