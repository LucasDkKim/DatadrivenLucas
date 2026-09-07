FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY clipo_cs_bot ./clipo_cs_bot
COPY guide ./guide

# 처리 상태/가이드 캐시는 볼륨으로 유지: -v clipo_state:/app/.state
VOLUME ["/app/.state"]

CMD ["python", "-m", "clipo_cs_bot", "run"]
