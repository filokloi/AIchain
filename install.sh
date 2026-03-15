#!/usr/bin/env bash
# Bootstrap script for aichaind on POSIX (Linux/macOS)

set -e
echo -e "\033[36mStarting AIchain bootstrap for POSIX...\033[0m"

# 1. Verify Python
if ! command -v python3 &> /dev/null; then
    echo -e "\033[31mError: python3 is not installed or not in PATH.\033[0m"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJ=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MIN=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJ" -lt 3 ] || { [ "$PY_MAJ" -eq 3 ] && [ "$PY_MIN" -lt 11 ]; }; then
    echo -e "\033[31mError: AIchain requires Python 3.11+. Found: $PY_VERSION\033[0m"
    exit 1
fi
echo -e "\033[32m1. Python $PY_VERSION verified.\033[0m"

# 2. Install dependencies
echo "2. Installing requirements..."
python3 -m pip install -r requirements.txt
echo -e "\033[32m   Dependencies installed.\033[0m"

# 3. Create config directories
CONFIG_DIR="$HOME/.openclaw/aichain"
if [ ! -d "$CONFIG_DIR" ]; then
    mkdir -p "$CONFIG_DIR"
    echo -e "\033[32m3. Created data directory at $CONFIG_DIR\033[0m"
else
    echo -e "\033[32m3. Data directory exists at $CONFIG_DIR\033[0m"
fi

# 4. Check OpenClaw basic config
OC_CONFIG="$HOME/.openclaw/openclaw.json"
if [ ! -f "$OC_CONFIG" ]; then
    echo "{}" > "$OC_CONFIG"
    echo -e "\033[90m   Created stub OpenClaw config at $OC_CONFIG\033[0m"
fi

# 5. Check Port Availability
if command -v lsof &> /dev/null; then
    if lsof -Pi :8080 -sTCP:LISTEN -t >/dev/null; then
        echo -e "\033[33mWARNING: Port 8080 is currently in use. aichaind usually requires it.\033[0m"
    else
        echo -e "\033[32m4. Port 8080 is free.\033[0m"
    fi
else
    echo -e "\033[33m4. Cannot auto-verify port 8080 (lsof missing). Please ensure it is free.\033[0m"
fi

echo -e "\n\033[36mBootstrap Complete! You can now run aichaind using: ./start-aichain.sh\033[0m"
