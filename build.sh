#!/bin/bash
set -o errexit

# Configurar diretórios para Rust (CRÍTICO)
export CARGO_HOME=/tmp/cargo
export RUSTUP_HOME=/tmp/rustup
export PATH="/tmp/cargo/bin:$PATH"

# Instalar Rust explicitamente
echo "Instalando Rust..."
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable

# Carregar environment do Rust
source "$CARGO_HOME/env"

# Verificar se Rust foi instalado corretamente
echo "Rust version: $(rustc --version)"
echo "Cargo version: $(cargo --version)"

# Instalar dependências Python
pip install -r requirements.txt
