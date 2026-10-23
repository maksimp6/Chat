#!/usr/bin/env bash
set -e

echo "=== 1. Установка системного protobuf и сборочных инструментов ==="
pkg update -y
pkg install -y protobuf clang python make openssl-tool

echo "=== 2. Установка Python wheel и setuptools ==="
pip install --upgrade pip setuptools wheel

echo "=== 3. Установка pure-Python реализации protobuf ==="
# Установка protobuf без попытки компилировать платформо-зависимые C-расширения
pip install --no-binary=protobuf protobuf || pip install protobuf

echo "=== 4. Проверка импорта google.protobuf ==="
python3 -c "
from google.protobuf.timestamp_pb2 import Timestamp
from google.protobuf.struct_pb2 import Struct
ts = Timestamp()
ts.GetCurrentTime()
print('Protobuf успешно импортирован! Время:', ts.ToJsonString())
"

echo "=== 5. Установка yandexcloud SDK ==="
# Флаг предотвращает сбои на поиске системного openssl при компиляции gRPC
export GRPC_PYTHON_BUILD_SYSTEM_OPENSSL=1
pip install --no-deps yandexcloud
# Доустанавливаем только необходимые runtime-зависимости SDK
pip install grpcio six
