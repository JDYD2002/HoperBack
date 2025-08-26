FROM python:3.10-slim

# Dependências do sistema (ajuste conforme necessidade)
RUN apt-get update && apt-get install -y build-essential curl git ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*

# Instala o rustup para compilar pacotes que precisem (se necessário)
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"
ENV CARGO_HOME=/root/.cargo
ENV RUSTUP_HOME=/root/.rustup
RUN rustup default stable

WORKDIR /app
COPY requirements.txt /app/requirements.txt

RUN pip install --upgrade pip setuptools wheel
RUN pip install --no-cache-dir --prefer-binary -r /app/requirements.txt

COPY . /app

ENV PORT=8000
CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8000"]
