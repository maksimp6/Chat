#!/bin/bash
# === TEST_API.SH: Автоматическое тестирование API ===
# Использование: ./test_api.sh

BASE_URL="http://localhost:8080"
PASS=0
FAIL=0

echo "╔══════════════════════════════════════════════════════════╗"
echo "║       Тестирование API Alice Pro                         ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo "Дата: $(date)"
echo "URL: $BASE_URL"
echo ""

# Тест 1: Главная страница
echo -n "Тест 1: GET / ... "
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/")
if [ "$STATUS" = "200" ]; then
  echo "✅ PASS ($STATUS)"
  ((PASS++))
else
  echo "❌ FAIL ($STATUS)"
  ((FAIL++))
fi

# Тест 2: Список моделей
echo -n "Тест 2: GET /api/models ... "
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/api/models")
if [ "$STATUS" = "200" ]; then
  echo "✅ PASS ($STATUS)"
  ((PASS++))
else
  echo "❌ FAIL ($STATUS)"
  ((FAIL++))
fi

# Тест 3: Создание диалога
echo -n "Тест 3: POST /api/conversations ... "
RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/conversations" -H "Content-Type: application/json" -d '{"model":"aliceai-llm"}')
STATUS=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | head -1)
if [ "$STATUS" = "200" ]; then
  CONV_ID=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)
  echo "✅ PASS ($STATUS) ID: $CONV_ID"
  ((PASS++))
else
  echo "❌ FAIL ($STATUS)"
  ((FAIL++))
  CONV_ID=""
fi

# Тест 4: Отправка сообщения
if [ -n "$CONV_ID" ]; then
  echo -n "Тест 4: POST /api/chat ... "
  RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/chat" -H "Content-Type: application/json" -d "{\"message\":\"Привет\",\"conversation_id\":\"$CONV_ID\",\"model\":\"aliceai-llm\"}")
  STATUS=$(echo "$RESP" | tail -1)
  if [ "$STATUS" = "200" ]; then
    echo "✅ PASS ($STATUS)"
    ((PASS++))
  else
    echo "❌ FAIL ($STATUS)"
    ((FAIL++))
  fi
else
  echo "Тест 4: POST /api/chat ... ⏭️ SKIP (нет conversation_id)"
fi

# Итог
echo ""
echo "══════════════════════════════════════════════════════════"
echo "📊 ИТОГ: ✅ PASS: $PASS | ❌ FAIL: $FAIL"
echo "══════════════════════════════════════════════════════════"

if [ $FAIL -gt 0 ]; then
  exit 1
else
  exit 0
fi
