#!/bin/bash
set -o errexit

# Variáveis de cache Rust (mesmo que não usemos, deixa seguro)
export CARGO_HOME=/tmp/cargo
export RUSTUP_HOME=/tmp/rustup
export PATH=$CARGO_HOME/bin:$PATH

# Atualiza pip e ferramentas essenciais
python -m pip install --upgrade pip setuptools wheel

# Instala dependências usando wheels sempre que possível
pip install --prefer-binary -r requirements.txt
