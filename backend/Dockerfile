FROM python:3.11-slim

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
COPY api/ api/
COPY service/ service/

# 데이터 볼륨 마운트 지점을 app 소유로 미리 생성 — 빈 named volume이 이 소유권으로 초기화됨
RUN mkdir -p service/data service/trades data/research && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
