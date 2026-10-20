#!/bin/bash
# Start both backend and frontend

ROOT="$(cd "$(dirname "$0")" && pwd)"
echo "Starting Video Translator..."

# Start backend
cd "$ROOT/backend"
source venv/bin/activate
./venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# Start frontend
cd "$ROOT/frontend"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "Backend:  http://localhost:8000"
echo "Frontend: http://localhost:3000"
echo ""
echo "Press Ctrl+C to stop both servers"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait