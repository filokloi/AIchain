#!/usr/bin/env bash
# One-line AIchain installer for Linux/macOS:
#   curl -fsSL https://raw.githubusercontent.com/filokloi/AIchain/main/scripts/get-aichain.sh | bash
set -euo pipefail
REPO="https://github.com/filokloi/AIchain"
DEST="$HOME/AIchain"

echo ""
echo "  AIchain instalacija"
echo "  ==================="

command -v python3 >/dev/null || { echo "  [!] Treba python3 (3.10+). Instaliraj pa ponovi."; exit 1; }
PYV=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
python3 - <<PY || { echo "  [!] Treba Python 3.10+, nadjen $PYV"; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)
PY
echo "  [1/4] Python $PYV OK"

if [ -d "$DEST/.git" ]; then
  echo "  [2/4] Osvezavam postojecu instalaciju..."
  git -C "$DEST" pull --ff-only >/dev/null
elif command -v git >/dev/null; then
  echo "  [2/4] Preuzimam kod (git clone)..."
  git clone --depth 1 "$REPO" "$DEST" >/dev/null
else
  echo "  [2/4] Preuzimam kod (tar, bez git-a)..."
  mkdir -p "$DEST" && curl -fsSL "$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$DEST" --strip-components=1
fi

echo "  [3/4] Instaliram zavisnosti..."
python3 -m pip install --quiet --user -e "$DEST" 2>/dev/null || python3 -m pip install --quiet --break-system-packages -e "$DEST"

LAUNCH="$HOME/.local/bin/aichain-router"
mkdir -p "$HOME/.local/bin"
cat > "$LAUNCH" <<RUN
#!/usr/bin/env bash
cd "$DEST" && PYTHONPATH=. exec python3 -m aichaind.main "\$@"
RUN
chmod +x "$LAUNCH"
echo "  [4/4] Komanda: aichain-router (u ~/.local/bin)"

echo ""
echo "  GOTOVO. Pokreni:  aichain-router"
echo "  Ruter ispisuje Base URL, API key i model za bilo koju AI aplikaciju."
echo "  (Opciono) Cloud modeli: export OPENROUTER_KEY=\"tvoj-kljuc\""
echo ""
