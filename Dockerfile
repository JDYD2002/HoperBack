# Use imagem base pequena e segura
FROM python:3.11-slim

# Evita bytecode e faz saída de logs unbuffered
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Variáveis Rust (opcionais, mas colocadas para consistência)
ENV CARGO_HOME=/root/.cargo
ENV RUSTUP_HOME=/root/.rustup
ENV PATH=/root/.cargo/bin:$PATH

# Instalar dependências de sistema necessárias para compilar pacotes e mídias
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    gcc \
    libsndfile1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Instalar rustup e toolchain estável (necessário para pacotes que usam maturin/pyo3)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable

WORKDIR /app

# Copia somente arquivos de dependências primeiro (cache build do docker)
COPY requirements.txt /app/requirements.txt

# Atualiza pip e instala dependências (usa --no-cache-dir para não guardar caches no final)
RUN python -m pip install --upgrade pip setuptools wheel \
 && pip install --no-cache-dir -r /app/requirements.txt

# Copia o resto do código
COPY . /app

# Exponha porta que o Uvicorn usará
EXPOSE 8000

# Comando padrão para iniciar sua API (ajuste "main:app" se diferente)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
