#!/bin/bash
set -o errexit

# Configurar Rust
export RUSTUP_INIT_SKIP_PATH_CHECK=yes
export CARGO_HOME=/tmp/cargo
export RUSTUP_HOME=/tmp/rustup
export PATH="/tmp/cargo/bin:$PATH"
export PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1

# Instalar Rust (somente se não existir ainda, para builds mais rápidos em cache)
if ! command -v rustc &> /dev/null
then
  echo "Instalando Rust..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
fi

# Carregar ambiente do cargo
source "$CARGO_HOME/env"

# Atualizar toolchain
rustup update stable

# Verificar instalações
echo "Python version: $(python --version)"
echo "Rust version: $(rustc --version)"
echo "Cargo version: $(cargo --version)"

# Atualizar pip e instalar dependências
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
