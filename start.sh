#!/bin/bash
# Start the Employee Portal
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt -q
fi

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║   OOTB Employee Portal               ║"
echo "  ║   http://localhost:8000              ║"
echo "  ║   TV Dashboard: /tv                  ║"
echo "  ╚══════════════════════════════════════╝"
echo ""
echo "  Default logins:"
echo "    admin@company.com   / password123  (Admin)"
echo "    hr@company.com      / password123  (HR Manager)"
echo "    alex@company.com    / password123  (Employee)"
echo ""

.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000 --reload
