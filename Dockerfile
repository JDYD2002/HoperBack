FROM python:3.11-slim

# Dependências de compilação
RUN apt-get update && apt-get install -y build-essential curl

# Variáveis de ambiente Rust
ENV CARGO_HOME=/tmp/cargo
ENV RUSTUP_HOME=/tmp/rustup
ENV PATH=/tmp/cargo/bin:$PATH

# Instala Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable

WORKDIR /app
COPY . .

RUN pip install --upgrade pip setuptools wheel
RUN pip install --prefer-binary -r requirements.txt

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
