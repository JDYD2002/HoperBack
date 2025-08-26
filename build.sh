#!/bin/bash
set -o errexit

# Configurar Rust
export RUSTUP_INIT_SKIP_PATH_CHECK=yes
export CARGO_HOME=/tmp/cargo
export RUSTUP_HOME=/tmp/rustup
export PATH="/tmp/cargo/bin:$PATH"
export PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1  # ← ADICIONE ESTA LINHA

# Instalar Rust
echo "Instalando Rust..."
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
source "$CARGO_HOME/env"

# Verificar instalações
echo "Python version: $(python --version)"
echo "Rust version: $(rustc --version)"

# Instalar dependências Python
pip install -r requirements.txt
