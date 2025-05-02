FROM python:3.11.0-alpine

WORKDIR /app

# Копируем зависимости, если есть requirements.txt
COPY requirements.txt ./
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Запускаем сервер и бота параллельно
CMD ["sh", "-c", "python main.py & python bot.py"]
