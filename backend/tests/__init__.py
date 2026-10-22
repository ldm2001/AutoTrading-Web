# 테스트 패키지 — 매수·청산 경로가 남기는 research 기록(wfin/wfout)을 임시 디렉터리로 격리
# 격리 없으면 실행마다 로컬 data/research 실데이터 파일에 테스트 행이 누적됨
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.trading import research

_TMP = tempfile.TemporaryDirectory(prefix="research-test-")
research._ROOT = Path(_TMP.name)
