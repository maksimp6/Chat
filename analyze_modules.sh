#!/bin/bash
# Скрипт для анализа использования модулей

echo "╔══════════════════════════════════════════════════════════╗"
echo "║       Анализ использования модулей Alice Pro             ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

if [ ! -d "logs" ]; then
    echo "❌ Папка logs не найдена"
    exit 1
fi

echo "📊 Статистика по модулям:"
echo ""

# Vision API
VISION_CALLS=$(grep -c "\[ANALYZE\]" logs/vision.txt 2>/dev/null || echo 0)
echo "  🖼️  Vision API (анализ изображений): $VISION_CALLS вызовов"

# Embeddings API
EMBEDDING_CALLS=$(grep -c "\[GET\]" logs/embeddings.txt 2>/dev/null || echo 0)
echo "  🔢 Embeddings API (векторизация): $EMBEDDING_CALLS вызовов"

# TTS API
TTS_CALLS=$(grep -c "\[SYNTH\]" logs/tts.txt 2>/dev/null || echo 0)
echo "  🔊 TTS API (синтез речи): $TTS_CALLS вызовов"

# Tokenizer API
TOKENIZE_CALLS=$(grep -c "\[TOKENIZE\]" logs/tokenizer.txt 2>/dev/null || echo 0)
echo "  📝 Tokenizer API (подсчёт токенов): $TOKENIZE_CALLS вызовов"

# Realtime API
VOICE_SESSIONS=$(grep -c "\[CONNECT\]" logs/voice.txt 2>/dev/null || echo 0)
echo "  🎤 Realtime API (голосовой режим): $VOICE_SESSIONS сессий"

# Responses API
RESPONSES_CALLS=$(grep -c "\[ASK\]" logs/api_debug.txt 2>/dev/null || echo 0)
echo "  💬 Responses API (текстовые ответы): $RESPONSES_CALLS вызовов"

echo ""
echo "🔍 Ошибки по модулям:"
echo ""

VISION_ERRORS=$(grep -c "ERROR" logs/vision.txt 2>/dev/null || echo 0)
echo "  Vision API: $VISION_ERRORS ошибок"

EMBEDDING_ERRORS=$(grep -c "ERROR" logs/embeddings.txt 2>/dev/null || echo 0)
echo "  Embeddings API: $EMBEDDING_ERRORS ошибок"

TTS_ERRORS=$(grep -c "ERROR" logs/tts.txt 2>/dev/null || echo 0)
echo "  TTS API: $TTS_ERRORS ошибок"

TOKENIZE_ERRORS=$(grep -c "ERROR" logs/tokenizer.txt 2>/dev/null || echo 0)
echo "  Tokenizer API: $TOKENIZE_ERRORS ошибок"

VOICE_ERRORS=$(grep -c "ERROR" logs/voice.txt 2>/dev/null || echo 0)
echo "  Realtime API: $VOICE_ERRORS ошибок"

echo ""
echo "💰 Оценка стоимости (на основе логов):"
echo ""

# Подсчёт токенов из логов
TOTAL_TOKENS=$(grep "input_tokens" logs/api_debug.txt 2>/dev/null | grep -o '"input_tokens": [0-9]*' | awk '{sum+=$2} END {print sum}')
echo "  Всего входных токенов: ${TOTAL_TOKENS:-0}"

echo ""
echo "✅ Анализ завершен"
