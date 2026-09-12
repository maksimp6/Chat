# 1. Получаем ID первого диалога (более надежный парсинг)
CONV_ID=$(curl -s http://localhost:5000/api/conversations | grep -o '"id": *"[^"]*"' | head -1 | cut -d'"' -f4)

if [ -z "$CONV_ID" ]; then
  echo "❌ Не удалось получить ID диалога. Создаем новый..."
  CONV_ID=$(curl -s -X POST http://localhost:5000/api/conversations -H "Content-Type: application/json" -d '{"title":"Test"}' | grep -o '"id": *"[^"]*"' | cut -d'"' -f4)
fi

echo "✅ Используем диалог ID: $CONV_ID"
echo "---------------------------------------------------"

# 2. Отправляем запрос на чат с триггером Git MCP
echo "⏳ Отправляем запрос: 'Сделай git status'..."
curl -s -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d "{
    \"conversation_id\": \"$CONV_ID\",
    \"message\": \"Сделай git status и кратко объясни результат\",
    \"params\": {
      \"model\": \"aliceai-llm\",
      \"temperature\": 0.7
    }
  }" | python3 -m json.tool

echo "---------------------------------------------------"

# 3. Смотрим историю
echo "📜 История сообщений:"
curl -s "http://localhost:5000/api/conversations/$CONV_ID/messages" | python3 -m json.tool
