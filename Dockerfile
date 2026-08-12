FROM python:3.11-slim

WORKDIR /app

# psycopg2 precisa de libpq e compilador para build
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Forma exec (lista): o processo vira PID 1 e recebe SIGTERM direto,
# permitindo shutdown limpo. A porta vem de gunicorn.conf.py, que lê
# a variável PORT em Python — sem depender de expansão pelo shell.
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
