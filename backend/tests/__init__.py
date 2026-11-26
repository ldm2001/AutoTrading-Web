# 테스트 패키지 — 매수·청산 경로가 남기는 research 기록(wfin/wfout, 평가 기록 evals/)을 임시 디렉터리로 격리
# 격리 없으면 실행마다 로컬 data/research 실데이터 파일에 테스트 행이 누적됨
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from service.trading import research

_TMP = tempfile.TemporaryDirectory(prefix="research-test-")
research._ROOT = Path(_TMP.name)
# 공용 예측 모델 폴더도 없는 임시 경로로 — 로컬 data/research/models/pooled에 가중치가 있어도 테스트가 읽지 않게
settings.pooled_dir = str(Path(_TMP.name) / "models" / "pooled")
