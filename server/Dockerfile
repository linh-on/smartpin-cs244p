FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DATABASE_URL=sqlite:////data/trash_can.db
EXPOSE 5001
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5001", "--proxy-headers", "--forwarded-allow-ips", "*"]
