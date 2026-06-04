# Algorithm Team 코딩 컨벤션

> AgileSoda Algorithm Team의 Python 코딩 컨벤션입니다.
> 팀원 모두가 일관된 코드를 작성하고, 서로의 코드를 빠르게 이해할 수 있도록 하는 것이 목표입니다.

---

## 1. Python 기본 스타일

PEP 8을 기본으로 따르되, 팀 상황에 맞게 일부 조정합니다.

### 1.1 네이밍

| 대상 | 규칙 | 예시 |
|------|------|------|
| 변수, 함수 | snake_case | `batch_size`, `load_model()` |
| 클래스 | PascalCase | `ClassificationAgent`, `TableDetector` |
| 상수 | UPPER_SNAKE_CASE | `MAX_RETRY`, `DEFAULT_THRESHOLD` |
| 프라이빗 | 언더스코어 접두사 | `_parse_response()`, `_cache` |
| 모듈/파일 | snake_case | `image_utils.py`, `ocr_pipeline.py` |

**네이밍 원칙:**

```python
# Good — 의도가 드러나는 이름
document_type = classify_document(image)
extraction_result = extract_key_values(document, schema)
is_valid = verify_extraction(result, ground_truth)

# Bad — 의미가 불분명한 이름
d = func1(img)
res = func2(doc, s)
flag = func3(r, gt)
```

약어 사용 기준: 팀 내에서 통용되는 약어만 허용합니다.

| 허용 | 비허용 |
|------|--------|
| `img`, `imgs` (image) | `d` (document) |
| `cfg`, `config` (configuration) | `r`, `res` (result) |
| `gt` (ground truth) | `p` (prediction) |
| `pred`, `preds` (prediction) | `t` (threshold) |
| `lr` (learning rate) | `m` (model) |
| `bs` (batch size) | `f` (feature) |
| `bbox` (bounding box) | |
| `fp`, `fn`, `tp`, `tn` | |

### 1.2 포맷팅

```
들여쓰기:     4 spaces (탭 사용 금지)
최대 줄 길이:  120자
빈 줄:        최상위 함수/클래스 사이에 2줄, 클래스 내 메서드 사이에 1줄
따옴표:       문자열은 큰따옴표("") 사용 통일
```

포맷팅 자동화를 위해 아래 도구를 사용합니다.

```bash
# 자동 포맷팅
black --line-length 120 src/

# import 정렬
isort --profile black --line-length 120 src/

# 린트 체크
flake8 src/ --max-line-length 120
```

**설정 파일 (`pyproject.toml`):**

```toml
[tool.black]
line-length = 120
target-version = ["py310"]

[tool.isort]
profile = "black"
line_length = 120

[tool.flake8]
max-line-length = 120
extend-ignore = ["E203", "W503"]
```

### 1.3 import 순서

```python
# 1. 표준 라이브러리
import os
import json
from pathlib import Path

# 2. 서드파티 라이브러리
import numpy as np
import torch
import cv2
from PIL import Image

# 3. 프로젝트 내부 모듈
from src.models.detector import DocumentDetector
from src.utils.image_utils import resize_image
```

---

## 2. 타입 힌트

함수 시그니처에는 반드시 타입 힌트를 작성합니다. 코드 리뷰와 IDE 자동완성에 큰 도움이 됩니다.

```python
# Good
def classify_document(
    image: np.ndarray,
    model_name: str = "default",
    confidence_threshold: float = 0.5,
) -> dict[str, Any]:
    """문서 이미지를 분류합니다."""
    ...

# Bad
def classify_document(image, model_name="default", confidence_threshold=0.5):
    ...
```

**자주 쓰는 타입 힌트 패턴:**

```python
from typing import Any, Optional
from pathlib import Path

# Optional: None이 될 수 있는 경우
def load_model(checkpoint_path: Optional[Path] = None) -> torch.nn.Module:
    ...

# 복잡한 반환 타입
def evaluate_model(
    predictions: list[dict[str, Any]],
    ground_truths: list[dict[str, Any]],
) -> dict[str, float]:  # {"precision": 0.95, "recall": 0.92, ...}
    ...

# 콜백 / 함수 인자
from collections.abc import Callable

def run_pipeline(
    images: list[np.ndarray],
    preprocess_fn: Callable[[np.ndarray], np.ndarray],
) -> list[dict]:
    ...
```

---

## 3. Docstring

Google 스타일 docstring을 사용합니다. 공개(public) 함수와 클래스에는 반드시 작성합니다.

```python
def extract_key_values(
    image: np.ndarray,
    schema: dict[str, Any],
    use_cache: bool = True,
) -> dict[str, Any]:
    """문서 이미지에서 스키마에 정의된 key-value를 추출합니다.

    Args:
        image: BGR 형식의 문서 이미지 (H, W, C).
        schema: 추출할 필드 정의. {"필드명": {"type": "str", "required": True}} 형식.
        use_cache: True이면 동일 이미지에 대한 이전 결과를 캐시에서 반환.

    Returns:
        추출 결과 딕셔너리. 예시:
        {
            "fields": {"이름": "홍길동", "생년월일": "1990-01-01"},
            "confidence": {"이름": 0.95, "생년월일": 0.87},
            "raw_response": "...",
        }

    Raises:
        ValueError: schema가 비어있거나 유효하지 않은 경우.
        TimeoutError: VLM API 호출이 30초를 초과한 경우.
    """
    ...
```

**간단한 함수는 한 줄 docstring으로 충분합니다:**

```python
def to_rgb(image: np.ndarray) -> np.ndarray:
    """BGR 이미지를 RGB로 변환합니다."""
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
```

**클래스 docstring:**

```python
class ClassificationAgent:
    """문서 이미지를 자동으로 분류하는 에이전트.

    VLM 기반으로 문서 유형(보험증권, 진단서, 청구서 등)을 판별하며,
    분류 결과에 따라 적절한 추출 스키마를 선택합니다.

    Attributes:
        model_name: 사용할 VLM 모델 이름.
        supported_types: 분류 가능한 문서 유형 목록.

    Example:
        >>> agent = ClassificationAgent(model_name="gpt-4o")
        >>> result = agent.classify(image)
        >>> print(result["document_type"])
        "insurance_policy"
    """
```

---

## 4. 에러 처리

### 4.1 기본 원칙

```python
# Good — 구체적인 예외 타입
try:
    response = call_vlm_api(image, prompt)
except requests.Timeout:
    logger.error("VLM API 타임아웃 (30초 초과)")
    raise
except requests.HTTPError as e:
    logger.error(f"VLM API 에러: {e.response.status_code}")
    raise

# Bad — bare except
try:
    response = call_vlm_api(image, prompt)
except:
    pass
```

### 4.2 커스텀 예외

프로젝트 공통 예외는 `src/exceptions.py`에 정의합니다.

```python
# src/exceptions.py
class AgileSodaError(Exception):
    """프로젝트 공통 기본 예외."""

class ModelLoadError(AgileSodaError):
    """모델 로드 실패."""

class ExtractionError(AgileSodaError):
    """문서 데이터 추출 실패."""

class SchemaValidationError(AgileSodaError):
    """스키마 검증 실패."""
```

### 4.3 외부 호출은 반드시 보호

외부 API, 파일 I/O, 모델 추론 등 실패 가능성이 있는 호출은 반드시 에러 처리를 합니다.

```python
def load_image(path: Path) -> np.ndarray:
    """이미지 파일을 로드합니다."""
    if not path.exists():
        raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {path}")

    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"이미지를 읽을 수 없습니다 (손상된 파일?): {path}")

    return image
```

---

## 5. 로깅

`print()` 대신 `logging` 모듈을 사용합니다. 개발 중 디버깅용 print도 커밋 전에 logging으로 전환하세요.

### 5.1 기본 사용법

```python
import logging

logger = logging.getLogger(__name__)

# 레벨별 사용 기준
logger.debug("입력 이미지 크기: %s", image.shape)        # 개발/디버깅 시에만
logger.info("문서 분류 완료: %s (%.2f)", doc_type, conf)  # 정상적인 처리 흐름
logger.warning("낮은 신뢰도: %.2f < %.2f", conf, threshold)  # 주의가 필요한 상황
logger.error("VLM API 호출 실패: %s", error_msg)          # 오류 발생
```

### 5.2 로깅 설정

프로젝트 진입점에서 한 번만 설정합니다.

```python
# main.py 또는 run_pipeline.py
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
```

### 5.3 주의사항

```python
# Good — lazy formatting (로그 레벨에 따라 포맷팅 비용 절약)
logger.debug("처리 결과: %s", result)

# Bad — 항상 f-string 평가
logger.debug(f"처리 결과: {result}")

# Good — 예외 정보 포함
try:
    result = process(image)
except Exception:
    logger.exception("이미지 처리 중 오류 발생")  # traceback 자동 포함
```

---

## 6. 설정(Config) 관리

하드코딩된 매직 넘버를 피하고, 설정값은 config 파일로 관리합니다.

### 6.1 매직 넘버 제거

```python
# Bad
if confidence > 0.85:
    results.append(prediction)
image = cv2.resize(image, (640, 640))

# Good
CONFIDENCE_THRESHOLD = 0.85
INPUT_SIZE = (640, 640)

if confidence > CONFIDENCE_THRESHOLD:
    results.append(prediction)
image = cv2.resize(image, INPUT_SIZE)
```

### 6.2 설정 파일 구조

YAML 형식을 기본으로 사용합니다.

```yaml
# configs/ocr_pipeline.yaml
model:
  name: "gpt-4o"
  max_tokens: 4096
  temperature: 0.0

classification:
  confidence_threshold: 0.85
  supported_types:
    - "insurance_policy"
    - "medical_certificate"
    - "claim_form"

extraction:
  max_retry: 3
  timeout_seconds: 30
  batch_size: 8

logging:
  level: "INFO"
```

```python
# config 로드
import yaml
from dataclasses import dataclass

@dataclass
class ModelConfig:
    name: str
    max_tokens: int = 4096
    temperature: float = 0.0

@dataclass
class PipelineConfig:
    model: ModelConfig
    confidence_threshold: float = 0.85
    max_retry: int = 3

def load_config(path: Path) -> dict:
    """YAML 설정 파일을 로드합니다."""
    with open(path) as f:
        return yaml.safe_load(f)
```

---

## 7. ML/DL 관련 컨벤션

### 7.1 GPU 메모리 관리

```python
# 추론 시 gradient 계산 비활성화 (필수)
with torch.no_grad():
    output = model(input_tensor)

# 더 이상 사용하지 않는 텐서 명시적 해제
del input_tensor, output
torch.cuda.empty_cache()

# 대량 추론 시 배치 처리
def batch_inference(
    images: list[np.ndarray],
    model: torch.nn.Module,
    batch_size: int = 16,
) -> list[dict]:
    """이미지를 배치 단위로 나눠서 추론합니다."""
    results = []
    for i in range(0, len(images), batch_size):
        batch = images[i : i + batch_size]
        batch_tensor = preprocess_batch(batch)

        with torch.no_grad():
            outputs = model(batch_tensor.cuda())

        results.extend(postprocess(outputs))

        del batch_tensor, outputs
        torch.cuda.empty_cache()

    return results
```

### 7.2 모델 체크포인트 관리

```python
# 체크포인트 저장
def save_checkpoint(
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    epoch: int,
    metrics: dict[str, float],
    path: Path,
) -> None:
    """학습 체크포인트를 저장합니다."""
    torch.save(
        {
            "epoch": epoch,
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "metrics": metrics,
        },
        path,
    )
    logger.info("체크포인트 저장: %s (epoch %d)", path, epoch)
```

### 7.3 실험 재현성

```python
import random
import numpy as np
import torch

def set_seed(seed: int = 42) -> None:
    """실험 재현성을 위해 모든 랜덤 시드를 고정합니다."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
```

### 7.4 대용량 데이터 처리

메모리에 모든 데이터를 올리지 말고, 제너레이터나 청크 단위로 처리합니다.

```python
# Good — 제너레이터로 하나씩 처리
def load_images(image_dir: Path) -> Iterator[tuple[Path, np.ndarray]]:
    """디렉토리 내 이미지를 하나씩 로드합니다."""
    for path in sorted(image_dir.glob("*.jpg")):
        image = cv2.imread(str(path))
        if image is not None:
            yield path, image

# Bad — 전체를 리스트로 한 번에 로드
def load_images(image_dir: Path) -> list[np.ndarray]:
    return [cv2.imread(str(p)) for p in image_dir.glob("*.jpg")]
```

---

## 8. LLM/VLM API 호출 패턴

### 8.1 프롬프트 관리

프롬프트는 코드 안에 직접 작성하지 않고, 별도 파일이나 템플릿으로 관리합니다.

```python
# Good — 프롬프트를 별도 파일로 분리
# prompts/classification.txt 또는 prompts/classification.yaml
PROMPT_DIR = Path("prompts")

def load_prompt(name: str, **kwargs) -> str:
    """프롬프트 템플릿을 로드하고 변수를 치환합니다."""
    template = (PROMPT_DIR / f"{name}.txt").read_text()
    return template.format(**kwargs)

# Bad — 코드 안에 긴 프롬프트를 직접 작성
prompt = f"""You are a document classifier. Given the following image...
Please classify into one of: {categories}...
Output format: JSON with keys "type" and "confidence"...
{additional_instructions}..."""
```

### 8.2 API 호출 래퍼

재시도, 타임아웃, 에러 처리를 한 곳에서 관리합니다.

```python
import time

def call_llm_api(
    prompt: str,
    model: str = "gpt-4o",
    max_retry: int = 3,
    timeout: int = 30,
) -> dict[str, Any]:
    """LLM API를 호출합니다. 실패 시 지수 백오프로 재시도합니다."""
    for attempt in range(max_retry):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout,
            )
            return parse_response(response)

        except RateLimitError:
            wait_time = 2 ** attempt
            logger.warning("Rate limit, %d초 후 재시도 (%d/%d)", wait_time, attempt + 1, max_retry)
            time.sleep(wait_time)

        except Timeout:
            logger.warning("타임아웃, 재시도 (%d/%d)", attempt + 1, max_retry)

    raise ExtractionError(f"API 호출 {max_retry}회 실패")
```

### 8.3 응답 파싱

LLM 응답은 항상 검증 후 사용합니다.

```python
import json

def parse_llm_json(response_text: str) -> dict:
    """LLM 응답에서 JSON을 안전하게 파싱합니다."""
    # markdown 코드블록 제거
    text = response_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]  # 첫 줄 제거
        text = text.rsplit("```", 1)[0]  # 마지막 ``` 제거

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("JSON 파싱 실패: %s", e)
        logger.debug("원본 응답: %s", response_text[:500])
        raise ExtractionError(f"LLM 응답이 유효한 JSON이 아닙니다: {e}")

    return result
```

---

## 9. 프로젝트 구조

```
project-root/
├── CLAUDE.md                 # AI 코드 리뷰 규칙 (이 문서 기반으로 작성)
├── README.md                 # 프로젝트 설명, 설치/실행 방법
├── pyproject.toml            # 프로젝트 설정 (black, isort, flake8 등)
├── requirements.txt          # 의존성 목록
│
├── configs/                  # 설정 파일
│   ├── pipeline.yaml
│   └── model.yaml
│
├── prompts/                  # LLM/VLM 프롬프트 템플릿
│   ├── classification.txt
│   └── extraction.txt
│
├── src/                      # 소스 코드
│   ├── __init__.py
│   ├── agents/               # Agentic workflow 에이전트
│   │   ├── __init__.py
│   │   ├── classification_agent.py
│   │   └── extraction_agent.py
│   ├── models/               # DL/ML 모델 정의
│   │   ├── __init__.py
│   │   ├── detector.py
│   │   └── recognizer.py
│   ├── services/             # 비즈니스 로직, 파이프라인
│   │   ├── __init__.py
│   │   └── ocr_pipeline.py
│   ├── utils/                # 공통 유틸리티
│   │   ├── __init__.py
│   │   ├── image_utils.py
│   │   └── file_utils.py
│   └── exceptions.py         # 커스텀 예외 정의
│
├── tests/                    # 테스트
│   ├── unit/
│   └── integration/
│
├── scripts/                  # 실행/평가 스크립트
│   ├── run_pipeline.py
│   └── evaluate.py
│
└── notebooks/                # 실험/분석용 주피터 노트북
    └── exploration.ipynb
```

---

## 10. Git 컨벤션

### 10.1 커밋 메시지

Conventional Commits 형식을 따릅니다.

```
<타입>: <간단한 설명>

[선택] 상세 설명
```

| 타입 | 설명 | 예시 |
|------|------|------|
| `feat` | 새로운 기능 | `feat: 문서 분류 에이전트 추가` |
| `fix` | 버그 수정 | `fix: 이미지 회전 시 bbox 좌표 보정` |
| `refactor` | 리팩토링 (기능 변화 없음) | `refactor: OCR 파이프라인 클래스 분리` |
| `perf` | 성능 개선 | `perf: 배치 추론 적용으로 처리 속도 2배 향상` |
| `docs` | 문서 수정 | `docs: API 사용법 README 추가` |
| `test` | 테스트 추가/수정 | `test: 분류 에이전트 단위 테스트 추가` |
| `chore` | 빌드, 설정, 의존성 등 | `chore: torch 2.1로 업데이트` |
| `exp` | 실험 관련 | `exp: VLM 프롬프트 변형 A/B 비교` |

한글/영어 모두 허용하되, 하나의 프로젝트 내에서는 통일합니다.

### 10.2 브랜치 전략

```
main (또는 master)
 └── develop
      ├── feature/classification-agent
      ├── feature/tsr-prototype
      ├── fix/bbox-rotation-bug
      └── exp/vlm-prompt-comparison
```

| 브랜치 | 용도 |
|--------|------|
| `main` | 배포 가능한 안정 버전 |
| `develop` | 개발 통합 브랜치 |
| `feature/*` | 기능 개발 |
| `fix/*` | 버그 수정 |
| `exp/*` | 실험 (머지 없이 기록용으로 유지 가능) |

### 10.3 .gitignore 필수 항목

```gitignore
# Python
__pycache__/
*.pyc
*.egg-info/
.venv/

# IDE
.vscode/
.idea/
*.swp

# 데이터 & 모델 (용량이 크므로 Git에 포함하지 않음)
data/
models/weights/
*.pt
*.pth
*.onnx
checkpoints/

# 실험 산출물
outputs/
logs/
wandb/

# 환경 변수 & 시크릿
.env
*.key

# OS
.DS_Store
Thumbs.db

# Jupyter
.ipynb_checkpoints/
```

---

## 11. 코드 리뷰 체크리스트

MR을 제출하기 전에 스스로 한 번 확인합니다.

### 필수 항목

- [ ] 타입 힌트가 함수 시그니처에 포함되어 있는가
- [ ] 공개 함수에 docstring이 있는가
- [ ] `print()` 대신 `logging`을 사용하고 있는가
- [ ] 매직 넘버 없이 상수 또는 config로 관리하고 있는가
- [ ] bare except 없이 구체적인 예외 타입을 사용하고 있는가
- [ ] API 키, 비밀번호 등 시크릿이 하드코딩되어 있지 않은가

### ML/DL 관련

- [ ] 추론 시 `torch.no_grad()`를 사용하고 있는가
- [ ] GPU 메모리 누수 가능성은 없는가 (텐서 해제, `empty_cache`)
- [ ] 배치 처리가 가능한 곳에서 배치로 처리하고 있는가
- [ ] 대용량 데이터를 한 번에 메모리에 올리지 않는가
- [ ] 랜덤 시드 고정으로 재현 가능한 실험인가

### LLM/VLM 관련

- [ ] 프롬프트가 코드 안에 하드코딩되어 있지 않은가
- [ ] API 호출에 타임아웃과 재시도 로직이 있는가
- [ ] LLM 응답 파싱 시 JSON 검증을 하고 있는가
- [ ] API 비용이 과도하게 발생할 수 있는 루프는 없는가

---

## 12. 도구 설정 한눈에 보기

프로젝트 시작 시 아래 명령으로 환경을 세팅합니다.

```bash
# 의존성 설치
pip install -r requirements.txt

# 개발 도구 설치
pip install black isort flake8 pytest mypy

# 포맷팅 + 린트 한번에 실행
black --line-length 120 src/ && isort --profile black --line-length 120 src/ && flake8 src/ --max-line-length 120

# 테스트 실행
pytest tests/ -v

# 타입 체크 (선택)
mypy src/ --ignore-missing-imports
```

---

## 부록: 참고 자료

- [PEP 8 — Python 스타일 가이드](https://peps.python.org/pep-0008/)
- [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)
- [Conventional Commits](https://www.conventionalcommits.org/ko/v1.0.0/)
